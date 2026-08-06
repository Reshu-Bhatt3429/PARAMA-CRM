# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

from datetime import datetime
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.demo.whatsapp_demo import (
	DEMO_CONVERSATIONS,
	DEMO_LEADS,
	insert_demo_message,
	seed_demo_messages,
)


class TestWhatsAppDemoSeeder(FrappeTestCase):
	"""The seeder must never reach the Meta Graph API.

	frappe_whatsapp's WhatsAppMessage.before_insert sends every Outgoing message
	over HTTP, so these tests pin the db_insert() bypass in place.
	"""

	def tearDown(self):
		frappe.db.rollback()

	def _lead(self):
		return {**DEMO_LEADS[0], "name": "CRM-LEAD-2026-00001"}

	def test_message_is_written_with_db_insert_and_never_through_the_lifecycle(self):
		doc = MagicMock()

		with patch("crm.demo.whatsapp_demo.frappe.new_doc", return_value=doc):
			insert_demo_message(
				"demo-whatsapp-1-01",
				self._lead(),
				{"minutes_ago": 10, "type": "Outgoing", "message": "Hello"},
				datetime(2026, 8, 1, 10, 0, 0),
				"Demo WhatsApp Account",
			)

		doc.db_insert.assert_called_once_with()
		doc.insert.assert_not_called()
		doc.save.assert_not_called()
		doc.submit.assert_not_called()

	def test_message_sets_every_column_the_framework_would_have_filled_in(self):
		doc = MagicMock()
		timestamp = datetime(2026, 8, 1, 10, 0, 0)

		with patch("crm.demo.whatsapp_demo.frappe.new_doc", return_value=doc):
			insert_demo_message(
				"demo-whatsapp-1-01",
				self._lead(),
				{"minutes_ago": 10, "type": "Outgoing", "message": "Hello"},
				timestamp,
				"Demo WhatsApp Account",
			)

		self.assertEqual(doc.name, "demo-whatsapp-1-01")
		self.assertEqual(doc.creation, timestamp)
		self.assertEqual(doc.modified, timestamp)
		self.assertEqual(doc.docstatus, 0)
		self.assertEqual(doc.idx, 0)

	def test_outgoing_message_addresses_the_lead_and_leaves_sender_empty(self):
		doc = MagicMock()

		with patch("crm.demo.whatsapp_demo.frappe.new_doc", return_value=doc):
			insert_demo_message(
				"demo-whatsapp-1-01",
				self._lead(),
				{"minutes_ago": 10, "type": "Outgoing", "message": "Hello", "status": "read"},
				datetime(2026, 8, 1, 10, 0, 0),
				"Demo WhatsApp Account",
			)

		payload = doc.update.call_args.args[0]
		self.assertEqual(payload["to"], DEMO_LEADS[0]["mobile_no"])
		self.assertEqual(payload["from"], "")
		self.assertEqual(payload["status"], "read")
		self.assertEqual(payload["reference_doctype"], "CRM Lead")
		self.assertEqual(payload["reference_name"], "CRM-LEAD-2026-00001")

	def test_incoming_message_stores_the_meta_style_sender_id(self):
		doc = MagicMock()

		with patch("crm.demo.whatsapp_demo.frappe.new_doc", return_value=doc):
			insert_demo_message(
				"demo-whatsapp-1-01",
				self._lead(),
				{"minutes_ago": 10, "type": "Incoming", "message": "Hi"},
				datetime(2026, 8, 1, 10, 0, 0),
				"Demo WhatsApp Account",
			)

		payload = doc.update.call_args.args[0]
		# Meta sends E.164 digits without the leading plus.
		self.assertEqual(payload["from"], DEMO_LEADS[0]["mobile_no"].lstrip("+"))
		self.assertEqual(payload["to"], "")
		self.assertEqual(payload["status"], "")
		self.assertEqual(payload["profile_name"], "Amara Okafor")

	def test_seeding_is_idempotent(self):
		leads = [{**data, "name": f"CRM-LEAD-2026-0000{index}"} for index, data in enumerate(DEMO_LEADS)]
		expected = sum(len(script) for script in DEMO_CONVERSATIONS)

		with (
			patch("crm.demo.whatsapp_demo.frappe.db.exists", return_value=False),
			patch("crm.demo.whatsapp_demo.insert_demo_message") as mock_insert,
		):
			self.assertEqual(seed_demo_messages(leads, "Demo WhatsApp Account"), expected)

		self.assertEqual(mock_insert.call_count, expected)

		with (
			patch("crm.demo.whatsapp_demo.frappe.db.exists", return_value=True),
			patch("crm.demo.whatsapp_demo.insert_demo_message") as mock_insert,
		):
			self.assertEqual(seed_demo_messages(leads, "Demo WhatsApp Account"), 0)

		mock_insert.assert_not_called()

	def test_demo_script_covers_both_directions_and_media(self):
		messages = [message for script in DEMO_CONVERSATIONS for message in script]
		content_types = {message.get("content_type", "text") for message in messages}

		self.assertEqual(len(DEMO_LEADS), len(DEMO_CONVERSATIONS))
		self.assertEqual(len(messages), 20)
		self.assertEqual({message["type"] for message in messages}, {"Incoming", "Outgoing"})
		self.assertTrue({"image", "document"} <= content_types)
		# Every timestamp offset stays inside the advertised three-day window.
		self.assertTrue(all(0 < message["minutes_ago"] <= 3 * 24 * 60 for message in messages))
