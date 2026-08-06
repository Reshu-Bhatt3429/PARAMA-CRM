# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.api.whatsapp import compute_priority
from crm.api.whatsapp_followups import FOLLOWUP_DUE_HOURS
from crm.demo.whatsapp_demo import (
	DEMO_CONVERSATIONS,
	DEMO_LEADS,
	DEMO_SALES_USERS,
	DEMO_USER_PASSWORD,
	assign_demo_leads,
	demo_lead_values,
	ensure_demo_sales_users,
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
		self.assertEqual(len(messages), 17)
		self.assertEqual({message["type"] for message in messages}, {"Incoming", "Outgoing"})
		self.assertTrue({"image", "document"} <= content_types)
		# Every timestamp offset stays inside the advertised ten-day window.
		self.assertTrue(all(0 < message["minutes_ago"] <= 10 * 24 * 60 for message in messages))


class TestWhatsAppDemoStory(FrappeTestCase):
	"""The scripted offsets are the demo: they must produce what the UI advertises.

	Everything here is computed from DEMO_CONVERSATIONS with the same functions
	the inbox uses, so a careless edit to a `minutes_ago` breaks the test rather
	than the demo.
	"""

	NOW = datetime(2026, 8, 1, 12, 0, 0)

	def _state(self, script):
		"""(last_incoming_at, last_outgoing_at, messages in the last 7 days)."""
		stamps = {"Incoming": [], "Outgoing": []}
		recent = 0
		for message in script:
			at = self.NOW - timedelta(minutes=message["minutes_ago"])
			stamps[message["type"]].append(at)
			if at >= self.NOW - timedelta(days=7):
				recent += 1

		return (
			max(stamps["Incoming"], default=None),
			max(stamps["Outgoing"], default=None),
			recent,
		)

	def test_the_inbox_shows_a_hot_a_warm_and_a_cold_conversation(self):
		priorities = [compute_priority(*self._state(script), now=self.NOW) for script in DEMO_CONVERSATIONS]

		self.assertEqual(set(priorities), {"hot", "warm", "cold"})

	def test_one_conversation_is_overdue_for_a_reply(self):
		overdue = []
		for index, script in enumerate(DEMO_CONVERSATIONS):
			last_incoming_at, last_outgoing_at, _ = self._state(script)
			if not last_incoming_at:
				continue
			if last_outgoing_at and last_outgoing_at > last_incoming_at:
				continue
			if last_incoming_at <= self.NOW - timedelta(hours=FOLLOWUP_DUE_HOURS):
				overdue.append(index)

		self.assertTrue(overdue, "no conversation is waiting long enough to trigger a follow-up nudge")

	def test_every_demo_lead_carries_the_travel_fields(self):
		for data in DEMO_LEADS:
			values = demo_lead_values(data)
			self.assertTrue(values["destination"])
			self.assertGreater(values["group_size"], 0)
			self.assertGreater(values["budget"], 0)
			self.assertGreater(values["travel_end_date"], values["travel_start_date"])
			# Derived inputs must not leak through as CRM Lead fields.
			self.assertNotIn("travel_start_in_days", values)
			self.assertNotIn("travel_days", values)

	def test_demo_leads_spread_across_the_status_ladder(self):
		statuses = {data["status"] for data in DEMO_LEADS}

		ladder = {
			"New",
			"Contacted",
			"Qualified",
			"Proposal Sent",
			"Negotiation",
			"Booked",
			"Lost",
		}

		self.assertGreaterEqual(len(statuses), 3)
		self.assertTrue(statuses <= ladder)


class TestWhatsAppDemoUsersAndAssignment(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_sales_users_are_created_once_with_the_sales_user_role(self):
		user = MagicMock()
		user.name = DEMO_SALES_USERS[0]["email"]
		user.flags = MagicMock()

		with (
			patch("crm.demo.whatsapp_demo.frappe.db.exists", return_value=False),
			patch("crm.demo.whatsapp_demo.frappe.get_doc", return_value=user) as mock_get_doc,
		):
			ensure_demo_sales_users()

		self.assertEqual(mock_get_doc.call_count, len(DEMO_SALES_USERS))
		payload = mock_get_doc.call_args.args[0]
		self.assertEqual(payload["doctype"], "User")
		self.assertEqual(payload["roles"], [{"role": "Sales User"}])
		self.assertEqual(payload["new_password"], DEMO_USER_PASSWORD)
		self.assertEqual(payload["send_welcome_email"], 0)
		self.assertTrue(user.flags.ignore_password_policy)
		user.insert.assert_called_with(ignore_permissions=True)

		with (
			patch("crm.demo.whatsapp_demo.frappe.db.exists", return_value=True),
			patch("crm.demo.whatsapp_demo.frappe.get_doc") as mock_get_doc,
		):
			existing = ensure_demo_sales_users()

		mock_get_doc.assert_not_called()
		self.assertEqual(existing, [data["email"] for data in DEMO_SALES_USERS])

	def test_demo_leads_are_shared_with_the_live_assignment_helper(self):
		leads = [{"name": "CRM-LEAD-2026-00001"}, {"name": "CRM-LEAD-2026-00002"}]

		with patch(
			"crm.demo.whatsapp_demo.assign_whatsapp_lead",
			side_effect=["priya@demo.crm", None],
		) as mock_assign:
			assignments = assign_demo_leads(leads)

		self.assertEqual(mock_assign.call_count, 2)
		# An already-assigned lead returns None and is left out of the report.
		self.assertEqual(assignments, {"CRM-LEAD-2026-00001": "priya@demo.crm"})
