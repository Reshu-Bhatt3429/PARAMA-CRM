# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

from datetime import datetime
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.api.whatsapp import (
	WHATSAPP_LEAD_SOURCE,
	create_lead_from_whatsapp_message,
	get_conversation_references,
	get_counterpart_number,
	get_last_conversation_messages,
	get_whatsapp_conversations,
	normalize_whatsapp_number,
	notify_agent,
	truncate_preview,
	validate,
	whatsapp_message_preview,
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


class TestWhatsAppConversations(FrappeTestCase):
	"""Unit tests for the shared team inbox conversation list.

	frappe_whatsapp is not installed in CI, so every WhatsApp Message read is
	patched out — same approach as TestWhatsAppHooks.
	"""

	def tearDown(self):
		frappe.db.rollback()

	# --- whatsapp_message_preview() ---

	def test_preview_strips_html_from_text_message(self):
		preview = whatsapp_message_preview(
			{"type": "Incoming", "content_type": "text", "message": "<b>Hi</b> there"}
		)
		self.assertEqual(preview, "Hi there")

	def test_preview_labels_media_without_caption(self):
		self.assertEqual(
			whatsapp_message_preview(
				{"content_type": "image", "message": "/files/quote.png", "attach": "/files/quote.png"}
			),
			"📷 Image",
		)
		self.assertEqual(
			whatsapp_message_preview({"content_type": "document", "message": "", "attach": "/files/q.pdf"}),
			"📄 Document",
		)

	def test_preview_keeps_media_caption(self):
		preview = whatsapp_message_preview(
			{"content_type": "image", "message": "Our new office", "attach": "/files/office.png"}
		)
		self.assertEqual(preview, "📷 Image · Our new office")

	def test_preview_labels_template_messages(self):
		preview = whatsapp_message_preview(
			{"content_type": "text", "message_type": "Template", "message": "Template message"}
		)
		self.assertEqual(preview, "📋 Template message")

	def test_preview_of_missing_message_is_empty(self):
		self.assertEqual(whatsapp_message_preview({}), "")
		self.assertEqual(whatsapp_message_preview(None), "")

	def test_truncate_preview_collapses_whitespace_and_clips(self):
		self.assertEqual(truncate_preview("hello\n  world"), "hello world")
		self.assertEqual(truncate_preview("abcdef", length=4), "abc…")

	# --- get_counterpart_number() ---

	def test_counterpart_number_uses_sender_for_incoming(self):
		self.assertEqual(
			get_counterpart_number({"type": "Incoming", "from": "14155552671", "to": ""}),
			"+14155552671",
		)

	def test_counterpart_number_uses_recipient_for_outgoing(self):
		self.assertEqual(
			get_counterpart_number({"type": "Outgoing", "from": "", "to": "+14155552671"}),
			"+14155552671",
		)

	def test_counterpart_number_of_empty_message_is_empty(self):
		self.assertEqual(get_counterpart_number({}), "")
		self.assertEqual(get_counterpart_number({"type": "Outgoing", "to": ""}), "")

	# --- get_last_conversation_messages() ---

	def test_last_messages_ignore_rows_from_another_conversation(self):
		"""A shared `creation` timestamp must not leak one conversation's last message into another."""
		shared_time = datetime(2026, 8, 1, 10, 0, 0)
		aggregates = [
			{
				"reference_doctype": "CRM Lead",
				"reference_name": "LEAD-0001",
				"last_at": shared_time,
				"message_count": 3,
			},
			{
				"reference_doctype": "CRM Lead",
				"reference_name": "LEAD-0002",
				"last_at": datetime(2026, 8, 1, 9, 0, 0),
				"message_count": 1,
			},
		]
		rows = [
			{"reference_doctype": "CRM Lead", "reference_name": "LEAD-0001", "creation": shared_time},
			# Same timestamp, different conversation whose last_at is earlier.
			{"reference_doctype": "CRM Lead", "reference_name": "LEAD-0002", "creation": shared_time},
		]

		with patch("crm.api.whatsapp.frappe.get_all", return_value=rows) as mock_get_all:
			last_messages = get_last_conversation_messages(aggregates)

		mock_get_all.assert_called_once()
		self.assertEqual(list(last_messages), [("CRM Lead", "LEAD-0001")])

	def test_last_messages_skips_query_when_nothing_to_look_up(self):
		with patch("crm.api.whatsapp.frappe.get_all") as mock_get_all:
			self.assertEqual(get_last_conversation_messages([]), {})

		mock_get_all.assert_not_called()

	# --- get_conversation_references() ---

	def test_references_build_display_names_for_leads_and_deals(self):
		aggregates = [
			{"reference_doctype": "CRM Lead", "reference_name": "LEAD-0001"},
			{"reference_doctype": "CRM Deal", "reference_name": "DEAL-0001"},
		]
		leads = [
			frappe._dict(
				name="LEAD-0001",
				lead_name="",
				first_name="Ada",
				last_name="Lovelace",
				organization="Analytical Ltd",
				mobile_no="+14155552671",
			)
		]
		deals = [
			frappe._dict(
				name="DEAL-0001", organization="Acme Corp", lead_name="Bob", mobile_no="+14155552672"
			)
		]

		with patch("crm.api.whatsapp.frappe.get_list", side_effect=[leads, deals]):
			references = get_conversation_references(aggregates)

		self.assertEqual(
			references[("CRM Lead", "LEAD-0001")],
			{"display_name": "Ada Lovelace", "phone": "+14155552671"},
		)
		self.assertEqual(
			references[("CRM Deal", "DEAL-0001")],
			{"display_name": "Acme Corp", "phone": "+14155552672"},
		)

	def test_references_query_only_the_doctypes_present(self):
		aggregates = [{"reference_doctype": "CRM Lead", "reference_name": "LEAD-0001"}]

		with patch("crm.api.whatsapp.frappe.get_list", return_value=[]) as mock_get_list:
			get_conversation_references(aggregates)

		self.assertEqual(mock_get_list.call_count, 1)
		self.assertEqual(mock_get_list.call_args.args[0], "CRM Lead")

	# --- get_whatsapp_conversations() ---

	def test_conversations_return_empty_when_app_is_absent(self):
		with (
			patch("crm.api.whatsapp.validate_access"),
			patch("crm.api.whatsapp.frappe.get_installed_apps", return_value=["frappe", "crm"]),
			patch("crm.api.whatsapp.frappe.db.exists", return_value=False),
			patch("crm.api.whatsapp.get_conversation_aggregates") as mock_aggregates,
		):
			self.assertEqual(get_whatsapp_conversations(), [])

		mock_aggregates.assert_not_called()

	def test_conversations_return_empty_when_twilio_is_installed(self):
		with (
			patch("crm.api.whatsapp.validate_access"),
			patch(
				"crm.api.whatsapp.frappe.get_installed_apps",
				return_value=["frappe", "crm", "twilio_integration"],
			),
			patch("crm.api.whatsapp.get_conversation_aggregates") as mock_aggregates,
		):
			self.assertEqual(get_whatsapp_conversations(), [])

		mock_aggregates.assert_not_called()

	def test_conversations_are_sorted_newest_first_and_drop_unreadable_references(self):
		aggregates = [
			{
				"reference_doctype": "CRM Lead",
				"reference_name": "LEAD-0001",
				"last_at": datetime(2026, 8, 1, 9, 0, 0),
				"message_count": 2,
			},
			{
				"reference_doctype": "CRM Lead",
				"reference_name": "LEAD-0002",
				"last_at": datetime(2026, 8, 1, 12, 0, 0),
				"message_count": 5,
			},
			# No reference row is returned for this one -> not readable, must be dropped.
			{
				"reference_doctype": "CRM Deal",
				"reference_name": "DEAL-0009",
				"last_at": datetime(2026, 8, 1, 13, 0, 0),
				"message_count": 1,
			},
		]
		last_messages = {
			("CRM Lead", "LEAD-0001"): {
				"type": "Outgoing",
				"content_type": "text",
				"message": "Sending the quote now",
				"to": "+14155552671",
			},
			("CRM Lead", "LEAD-0002"): {
				"type": "Incoming",
				"content_type": "image",
				"message": "/files/site.png",
				"attach": "/files/site.png",
				"from": "14155552672",
			},
		}
		references = {
			("CRM Lead", "LEAD-0001"): {"display_name": "Ada Lovelace", "phone": "+14155552671"},
			("CRM Lead", "LEAD-0002"): {"display_name": "Grace Hopper", "phone": ""},
		}

		with (
			patch("crm.api.whatsapp.validate_access"),
			patch("crm.api.whatsapp.frappe.get_installed_apps", return_value=["frappe", "crm"]),
			patch("crm.api.whatsapp.frappe.db.exists", return_value=True),
			patch("crm.api.whatsapp.get_conversation_aggregates", return_value=aggregates),
			patch("crm.api.whatsapp.get_last_conversation_messages", return_value=last_messages),
			patch("crm.api.whatsapp.get_conversation_references", return_value=references),
		):
			conversations = get_whatsapp_conversations()

		self.assertEqual(
			[conversation["reference_name"] for conversation in conversations],
			["LEAD-0002", "LEAD-0001"],
		)
		self.assertEqual(conversations[0]["display_name"], "Grace Hopper")
		self.assertEqual(conversations[0]["phone"], "+14155552672")
		self.assertEqual(conversations[0]["last_message"], "📷 Image")
		self.assertEqual(conversations[0]["last_message_type"], "Incoming")
		self.assertEqual(conversations[0]["message_count"], 5)
		self.assertEqual(conversations[1]["last_message"], "Sending the quote now")
		self.assertEqual(conversations[1]["last_message_type"], "Outgoing")

	def test_conversations_are_limited_before_the_follow_up_lookups(self):
		aggregates = [
			{
				"reference_doctype": "CRM Lead",
				"reference_name": f"LEAD-{index:04d}",
				"last_at": datetime(2026, 8, 1, 0, index, 0),
				"message_count": 1,
			}
			for index in range(5)
		]

		with (
			patch("crm.api.whatsapp.validate_access"),
			patch("crm.api.whatsapp.frappe.get_installed_apps", return_value=["frappe", "crm"]),
			patch("crm.api.whatsapp.frappe.db.exists", return_value=True),
			patch("crm.api.whatsapp.get_conversation_aggregates", return_value=aggregates),
			patch(
				"crm.api.whatsapp.get_last_conversation_messages", return_value={}
			) as mock_last_messages,
			patch("crm.api.whatsapp.get_conversation_references", return_value={}),
		):
			get_whatsapp_conversations(limit=2)

		trimmed = mock_last_messages.call_args.args[0]
		self.assertEqual([row["reference_name"] for row in trimmed], ["LEAD-0004", "LEAD-0003"])

	def test_conversations_require_a_sales_role(self):
		with (
			patch(
				"crm.api.whatsapp.validate_access", side_effect=frappe.PermissionError
			) as mock_validate,
			patch("crm.api.whatsapp.get_conversation_aggregates") as mock_aggregates,
		):
			with self.assertRaises(frappe.PermissionError):
				get_whatsapp_conversations()

		mock_validate.assert_called_once_with()
		mock_aggregates.assert_not_called()
