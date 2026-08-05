# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.api.whatsapp import (
	WHATSAPP_LEAD_SOURCE,
	create_lead_from_whatsapp_message,
	normalize_whatsapp_number,
	notify_agent,
	validate,
)


class TestWhatsAppHooks(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()

	# --- validate() ---

	def test_validate_sets_reference_when_contact_found(self):
		"""validate() links the doc when a matching Contact/Lead is found"""
		doc = MagicMock()
		doc.type = "Incoming"
		doc.get.return_value = "+15551234567"

		with patch(
			"crm.api.whatsapp.get_contact_lead_or_deal_from_number",
			return_value=("LEAD-0001", "CRM Lead"),
		):
			validate(doc, None)

		self.assertEqual(doc.reference_doctype, "CRM Lead")
		self.assertEqual(doc.reference_name, "LEAD-0001")

	def test_validate_creates_lead_when_incoming_sender_is_unknown(self):
		"""validate() creates and links a Lead for an unknown incoming sender"""
		doc = MagicMock()
		doc.type = "Incoming"
		doc.get.side_effect = lambda fieldname: {
			"from": "14155552671",
			"profile_name": "New WhatsApp Lead",
		}.get(fieldname)
		doc.reference_doctype = None
		doc.reference_name = None

		with (
			patch(
				"crm.api.whatsapp.get_contact_lead_or_deal_from_number",
				return_value=(None, None),
			),
			patch(
				"crm.api.whatsapp.create_lead_from_whatsapp_message",
				return_value=("CRM-LEAD-2026-00001", "CRM Lead"),
			) as mock_create,
		):
			validate(doc, None)

		mock_create.assert_called_once_with(doc, "+14155552671")
		self.assertEqual(doc.reference_doctype, "CRM Lead")
		self.assertEqual(doc.reference_name, "CRM-LEAD-2026-00001")

	def test_validate_does_not_create_lead_for_unknown_outgoing_recipient(self):
		doc = MagicMock()
		doc.type = "Outgoing"
		doc.get.return_value = "14155552671"

		with (
			patch(
				"crm.api.whatsapp.get_contact_lead_or_deal_from_number",
				return_value=(None, None),
			),
			patch("crm.api.whatsapp.create_lead_from_whatsapp_message") as mock_create,
		):
			validate(doc, None)

		mock_create.assert_not_called()

	def test_normalize_whatsapp_number_adds_plus_to_meta_sender_id(self):
		self.assertEqual(normalize_whatsapp_number("14155552671"), "+14155552671")

	def test_normalize_whatsapp_number_preserves_invalid_input(self):
		self.assertEqual(normalize_whatsapp_number("invalid"), "invalid")

	def test_create_lead_uses_profile_name_and_whatsapp_source(self):
		doc = MagicMock()
		doc.get.return_value = "Ada Lovelace"
		lead = MagicMock(name="inserted_lead")
		lead.name = "CRM-LEAD-2026-00001"
		lead.doctype = "CRM Lead"
		lead.insert.return_value = lead
		lock = MagicMock()
		lock.owned.return_value = True

		with (
			patch("crm.api.whatsapp.frappe.cache.lock", return_value=lock),
			patch(
				"crm.api.whatsapp.get_contact_lead_or_deal_from_number",
				return_value=(None, None),
			),
			patch("crm.api.whatsapp.ensure_whatsapp_lead_source") as mock_source,
			patch("crm.api.whatsapp.frappe.get_doc", return_value=lead) as mock_get_doc,
		):
			result = create_lead_from_whatsapp_message(doc, "+14155552671")

		lock.acquire.assert_called_once_with(blocking=True)
		mock_source.assert_called_once_with()
		mock_get_doc.assert_called_once_with(
			{
				"doctype": "CRM Lead",
				"first_name": "Ada Lovelace",
				"mobile_no": "+14155552671",
				"source": WHATSAPP_LEAD_SOURCE,
			}
		)
		lead.insert.assert_called_once_with(ignore_permissions=True)
		lock.release.assert_not_called()
		self.assertEqual(result, ("CRM-LEAD-2026-00001", "CRM Lead"))

	def test_create_lead_reuses_record_found_inside_lock(self):
		doc = MagicMock()
		lock = MagicMock()
		lock.owned.return_value = True

		with (
			patch("crm.api.whatsapp.frappe.cache.lock", return_value=lock),
			patch(
				"crm.api.whatsapp.get_contact_lead_or_deal_from_number",
				return_value=("CRM-LEAD-2026-00001", "CRM Lead"),
			),
			patch("crm.api.whatsapp.frappe.get_doc") as mock_get_doc,
		):
			result = create_lead_from_whatsapp_message(doc, "+14155552671")

		mock_get_doc.assert_not_called()
		lock.release.assert_called_once_with()
		self.assertEqual(result, ("CRM-LEAD-2026-00001", "CRM Lead"))

	def test_create_lead_skips_invalid_sender_number(self):
		doc = MagicMock()

		with patch("crm.api.whatsapp.frappe.cache.lock") as mock_lock:
			result = create_lead_from_whatsapp_message(doc, "invalid")

		mock_lock.assert_not_called()
		self.assertEqual(result, (None, None))

	def test_validate_logs_error_on_exception(self):
		"""validate() catches lookup exceptions and logs them instead of raising"""
		doc = MagicMock()
		doc.type = "Incoming"
		doc.get.return_value = "invalid-number"

		with (
			patch(
				"crm.api.whatsapp.get_contact_lead_or_deal_from_number",
				side_effect=Exception("parse error"),
			),
			patch("frappe.log_error") as mock_log,
		):
			validate(doc, None)  # must not raise

		mock_log.assert_called_once()

	# --- notify_agent() ---

	def test_notify_agent_returns_early_when_no_reference(self):
		"""notify_agent() skips notification when reference_doctype and reference_name are absent"""
		doc = MagicMock()
		doc.type = "Incoming"
		doc.reference_doctype = None
		doc.reference_name = None

		with patch("crm.api.whatsapp.get_assigned_users") as mock_users:
			notify_agent(doc)  # must not raise

		mock_users.assert_not_called()

	def test_notify_agent_returns_early_when_reference_doctype_missing(self):
		"""notify_agent() skips notification when only reference_doctype is absent"""
		doc = MagicMock()
		doc.type = "Incoming"
		doc.reference_doctype = ""
		doc.reference_name = "LEAD-0001"

		with patch("crm.api.whatsapp.get_assigned_users") as mock_users:
			notify_agent(doc)

		mock_users.assert_not_called()
