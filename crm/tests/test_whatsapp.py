# Copyright (c) 2024, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

from datetime import datetime
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.api.whatsapp import (
	WHATSAPP_LEAD_SOURCE,
	assign_whatsapp_lead,
	choose_round_robin_assignee,
	compute_priority,
	conversation_belongs_to,
	create_lead_from_whatsapp_message,
	get_conversation_references,
	get_counterpart_number,
	get_last_conversation_messages,
	get_whatsapp_conversations,
	is_unanswered,
	normalize_whatsapp_number,
	notify_agent,
	parse_assigned_users,
	resolve_conversation_scope,
	truncate_preview,
	validate,
	whatsapp_message_preview,
)
from crm.api.whatsapp_followups import (
	build_digest_summary,
	create_followup_notification,
	get_pending_conversations,
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
			patch("crm.api.whatsapp.assign_whatsapp_lead") as mock_assign,
		):
			result = create_lead_from_whatsapp_message(doc, "+14155552671")

		mock_assign.assert_called_once_with("CRM-LEAD-2026-00001")

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
				lead_owner="priya@demo.crm",
				_assign='["priya@demo.crm"]',
			)
		]
		deals = [
			frappe._dict(
				name="DEAL-0001",
				organization="Acme Corp",
				lead_name="Bob",
				mobile_no="+14155552672",
				deal_owner="",
				_assign=None,
			)
		]

		with patch("crm.api.whatsapp.frappe.get_list", side_effect=[leads, deals]):
			references = get_conversation_references(aggregates)

		self.assertEqual(
			references[("CRM Lead", "LEAD-0001")],
			{
				"display_name": "Ada Lovelace",
				"phone": "+14155552671",
				"owner_user": "priya@demo.crm",
				"assigned_users": ["priya@demo.crm"],
			},
		)
		self.assertEqual(
			references[("CRM Deal", "DEAL-0001")],
			{
				"display_name": "Acme Corp",
				"phone": "+14155552672",
				"owner_user": "",
				"assigned_users": [],
			},
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
			patch("crm.api.whatsapp.resolve_conversation_scope", return_value="all"),
			patch("crm.api.whatsapp.get_conversation_aggregates", return_value=aggregates),
			patch("crm.api.whatsapp.get_last_conversation_messages", return_value=last_messages),
			patch("crm.api.whatsapp.get_conversation_references", return_value=references),
			patch("crm.api.whatsapp.get_unanswered_since", return_value={}),
		):
			conversations = get_whatsapp_conversations(scope="all")

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
			patch("crm.api.whatsapp.resolve_conversation_scope", return_value="all"),
			patch("crm.api.whatsapp.get_conversation_aggregates", return_value=aggregates),
			patch(
				"crm.api.whatsapp.get_last_conversation_messages", return_value={}
			) as mock_last_messages,
			patch("crm.api.whatsapp.get_conversation_references", return_value={}),
			patch("crm.api.whatsapp.get_unanswered_since", return_value={}),
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

	# --- scope ---

	def test_scope_all_is_downgraded_without_a_manager_role(self):
		with patch("crm.api.whatsapp.can_view_all_conversations", return_value=False):
			self.assertEqual(resolve_conversation_scope("all"), "mine")

		with patch("crm.api.whatsapp.can_view_all_conversations", return_value=True):
			self.assertEqual(resolve_conversation_scope("all"), "all")
			# Anything that is not "all" stays personal, whatever the role.
			self.assertEqual(resolve_conversation_scope("mine"), "mine")
			self.assertEqual(resolve_conversation_scope(""), "mine")

	def test_conversation_belongs_to_assignee_or_owner(self):
		assigned = {"assigned_users": ["priya@demo.crm"], "owner_user": "rahul@demo.crm"}
		self.assertTrue(conversation_belongs_to(assigned, "priya@demo.crm"))
		self.assertTrue(conversation_belongs_to(assigned, "rahul@demo.crm"))
		self.assertFalse(conversation_belongs_to(assigned, "someone@demo.crm"))
		self.assertFalse(conversation_belongs_to({}, "priya@demo.crm"))

	def test_parse_assigned_users_survives_malformed_values(self):
		self.assertEqual(parse_assigned_users('["a@x.com", "b@x.com"]'), ["a@x.com", "b@x.com"])
		self.assertEqual(parse_assigned_users(["a@x.com", ""]), ["a@x.com"])
		self.assertEqual(parse_assigned_users(None), [])
		self.assertEqual(parse_assigned_users("not json"), [])
		self.assertEqual(parse_assigned_users("[1"), [])

	def test_mine_scope_keeps_only_the_session_users_conversations(self):
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
		]
		references = {
			("CRM Lead", "LEAD-0001"): {
				"display_name": "Ada Lovelace",
				"phone": "",
				"owner_user": "priya@demo.crm",
				"assigned_users": ["priya@demo.crm"],
			},
			("CRM Lead", "LEAD-0002"): {
				"display_name": "Grace Hopper",
				"phone": "",
				"owner_user": "rahul@demo.crm",
				"assigned_users": ["rahul@demo.crm"],
			},
		}

		with (
			patch("crm.api.whatsapp.validate_access"),
			patch("crm.api.whatsapp.frappe.get_installed_apps", return_value=["frappe", "crm"]),
			patch("crm.api.whatsapp.frappe.db.exists", return_value=True),
			patch("crm.api.whatsapp.resolve_conversation_scope", return_value="mine"),
			patch("crm.api.whatsapp.frappe.session") as mock_session,
			patch("crm.api.whatsapp.get_conversation_aggregates", return_value=aggregates),
			patch("crm.api.whatsapp.get_last_conversation_messages", return_value={}),
			patch("crm.api.whatsapp.get_conversation_references", return_value=references),
			patch("crm.api.whatsapp.get_unanswered_since", return_value={}),
			patch("crm.api.whatsapp.add_assignee_full_names"),
		):
			mock_session.user = "priya@demo.crm"
			conversations = get_whatsapp_conversations()

		self.assertEqual([row["reference_name"] for row in conversations], ["LEAD-0001"])
		self.assertEqual(conversations[0]["assigned_to"]["user"], "priya@demo.crm")

	# --- needs_reply / waiting_since / priority on a conversation row ---

	def test_conversation_row_carries_followup_state(self):
		aggregates = [
			{
				"reference_doctype": "CRM Lead",
				"reference_name": "LEAD-0001",
				"last_at": datetime(2026, 8, 1, 12, 0, 0),
				"message_count": 3,
				"last_incoming_at": datetime(2026, 8, 1, 12, 0, 0),
				"last_outgoing_at": datetime(2026, 8, 1, 8, 0, 0),
				"message_count_7d": 3,
			}
		]
		references = {
			("CRM Lead", "LEAD-0001"): {
				"display_name": "Ada Lovelace",
				"phone": "",
				"owner_user": "",
				"assigned_users": [],
			}
		}

		with (
			patch("crm.api.whatsapp.validate_access"),
			patch("crm.api.whatsapp.frappe.get_installed_apps", return_value=["frappe", "crm"]),
			patch("crm.api.whatsapp.frappe.db.exists", return_value=True),
			patch("crm.api.whatsapp.resolve_conversation_scope", return_value="all"),
			patch("crm.api.whatsapp.get_conversation_aggregates", return_value=aggregates),
			patch("crm.api.whatsapp.get_last_conversation_messages", return_value={}),
			patch("crm.api.whatsapp.get_conversation_references", return_value=references),
			patch(
				"crm.api.whatsapp.get_unanswered_since",
				return_value={("CRM Lead", "LEAD-0001"): datetime(2026, 8, 1, 11, 0, 0)},
			),
			patch("crm.api.whatsapp.compute_priority", return_value="hot"),
		):
			conversations = get_whatsapp_conversations(scope="all")

		self.assertTrue(conversations[0]["needs_reply"])
		self.assertEqual(conversations[0]["waiting_since"], datetime(2026, 8, 1, 11, 0, 0))
		self.assertEqual(conversations[0]["priority"], "hot")
		self.assertIsNone(conversations[0]["assigned_to"])


class TestWhatsAppAssignment(FrappeTestCase):
	"""Round-robin assignment of WhatsApp-captured leads."""

	def tearDown(self):
		frappe.db.rollback()

	def test_least_loaded_candidate_wins(self):
		candidates = ["priya@demo.crm", "rahul@demo.crm"]
		self.assertEqual(
			choose_round_robin_assignee(candidates, {"priya@demo.crm": 3, "rahul@demo.crm": 1}),
			"rahul@demo.crm",
		)

	def test_candidate_without_open_leads_counts_as_zero(self):
		candidates = ["priya@demo.crm", "rahul@demo.crm"]
		self.assertEqual(
			choose_round_robin_assignee(candidates, {"priya@demo.crm": 2}),
			"rahul@demo.crm",
		)

	def test_ties_break_deterministically_on_user_id(self):
		counts = {"priya@demo.crm": 2, "rahul@demo.crm": 2}
		self.assertEqual(
			choose_round_robin_assignee(["rahul@demo.crm", "priya@demo.crm"], counts),
			"priya@demo.crm",
		)
		self.assertEqual(
			choose_round_robin_assignee(["priya@demo.crm", "rahul@demo.crm"], counts),
			"priya@demo.crm",
		)

	def test_no_candidates_means_no_assignee(self):
		self.assertIsNone(choose_round_robin_assignee([], {}))

	def test_assignment_uses_the_native_assign_to_mechanism(self):
		with (
			patch("crm.api.whatsapp.get_assigned_users", return_value=[]),
			patch(
				"crm.api.whatsapp.get_sales_user_candidates",
				return_value=["priya@demo.crm", "rahul@demo.crm"],
			),
			patch("crm.api.whatsapp.get_open_whatsapp_lead_counts", return_value={"priya@demo.crm": 1}),
			patch("crm.api.whatsapp.assign") as mock_assign,
		):
			assignee = assign_whatsapp_lead("CRM-LEAD-2026-00001")

		self.assertEqual(assignee, "rahul@demo.crm")
		mock_assign.assert_called_once_with(
			{
				"assign_to": ["rahul@demo.crm"],
				"doctype": "CRM Lead",
				"name": "CRM-LEAD-2026-00001",
			},
			ignore_permissions=True,
		)

	def test_an_already_assigned_lead_is_never_reassigned(self):
		with (
			patch("crm.api.whatsapp.get_assigned_users", return_value=["priya@demo.crm"]),
			patch("crm.api.whatsapp.get_sales_user_candidates") as mock_candidates,
			patch("crm.api.whatsapp.assign") as mock_assign,
		):
			self.assertIsNone(assign_whatsapp_lead("CRM-LEAD-2026-00001"))

		mock_candidates.assert_not_called()
		mock_assign.assert_not_called()

	def test_no_sales_users_skips_assignment_gracefully(self):
		with (
			patch("crm.api.whatsapp.get_assigned_users", return_value=[]),
			patch("crm.api.whatsapp.get_sales_user_candidates", return_value=[]),
			patch("crm.api.whatsapp.assign") as mock_assign,
		):
			self.assertIsNone(assign_whatsapp_lead("CRM-LEAD-2026-00001"))

		mock_assign.assert_not_called()


class TestWhatsAppPriority(FrappeTestCase):
	"""compute_priority is deterministic: same inputs, same temperature."""

	NOW = datetime(2026, 8, 1, 12, 0, 0)

	def test_unanswered_incoming_within_a_day_is_hot(self):
		priority = compute_priority(
			datetime(2026, 8, 1, 6, 0, 0), datetime(2026, 7, 31, 20, 0, 0), 2, now=self.NOW
		)
		self.assertEqual(priority, "hot")

	def test_a_busy_week_is_hot_even_when_answered(self):
		priority = compute_priority(
			datetime(2026, 8, 1, 6, 0, 0), datetime(2026, 8, 1, 7, 0, 0), 5, now=self.NOW
		)
		self.assertEqual(priority, "hot")

	def test_an_answered_incoming_is_not_hot_on_its_own(self):
		priority = compute_priority(
			datetime(2026, 8, 1, 6, 0, 0), datetime(2026, 8, 1, 7, 0, 0), 2, now=self.NOW
		)
		self.assertEqual(priority, "warm")

	def test_an_old_unanswered_incoming_is_not_hot(self):
		priority = compute_priority(datetime(2026, 7, 30, 6, 0, 0), None, 1, now=self.NOW)
		self.assertEqual(priority, "warm")

	def test_activity_within_three_days_is_warm(self):
		priority = compute_priority(
			datetime(2026, 7, 29, 6, 0, 0), datetime(2026, 7, 29, 7, 0, 0), 2, now=self.NOW
		)
		self.assertEqual(priority, "warm")

	def test_older_activity_is_cold(self):
		priority = compute_priority(
			datetime(2026, 7, 20, 6, 0, 0), datetime(2026, 7, 20, 7, 0, 0), 0, now=self.NOW
		)
		self.assertEqual(priority, "cold")

	def test_a_conversation_without_timestamps_is_cold(self):
		self.assertEqual(compute_priority(None, None, 0, now=self.NOW), "cold")

	def test_string_timestamps_are_accepted(self):
		self.assertEqual(
			compute_priority("2026-08-01 06:00:00", "2026-07-31 20:00:00", 0, now=self.NOW),
			"hot",
		)

	def test_is_unanswered_only_when_the_reply_is_older(self):
		self.assertTrue(
			is_unanswered(
				{
					"last_incoming_at": datetime(2026, 8, 1, 10, 0, 0),
					"last_outgoing_at": datetime(2026, 8, 1, 9, 0, 0),
				}
			)
		)
		self.assertTrue(is_unanswered({"last_incoming_at": datetime(2026, 8, 1, 10, 0, 0)}))
		self.assertFalse(
			is_unanswered(
				{
					"last_incoming_at": datetime(2026, 8, 1, 8, 0, 0),
					"last_outgoing_at": datetime(2026, 8, 1, 9, 0, 0),
				}
			)
		)
		self.assertFalse(is_unanswered({"last_outgoing_at": datetime(2026, 8, 1, 9, 0, 0)}))


class TestWhatsAppFollowups(FrappeTestCase):
	"""Scheduler-side nudges and the manager digest."""

	NOW = datetime(2026, 8, 1, 12, 0, 0)

	def tearDown(self):
		frappe.db.rollback()

	def _aggregate(self, name, last_incoming_at, last_outgoing_at=None):
		return {
			"reference_doctype": "CRM Lead",
			"reference_name": name,
			"last_at": last_incoming_at,
			"message_count": 3,
			"last_incoming_at": last_incoming_at,
			"last_outgoing_at": last_outgoing_at,
			"message_count_7d": 3,
		}

	def test_only_conversations_waiting_longer_than_the_cutoff_are_pending(self):
		aggregates = [
			self._aggregate("LEAD-0001", datetime(2026, 8, 1, 8, 0, 0)),
			# Waiting for 30 minutes: inside the two hour grace period.
			self._aggregate("LEAD-0002", datetime(2026, 8, 1, 11, 30, 0)),
			# Answered: not waiting at all.
			self._aggregate("LEAD-0003", datetime(2026, 8, 1, 7, 0, 0), datetime(2026, 8, 1, 8, 0, 0)),
		]
		references = {
			("CRM Lead", "LEAD-0001"): {
				"display_name": "Ada Lovelace",
				"assigned_users": ["priya@demo.crm"],
				"owner_user": "priya@demo.crm",
			},
			("CRM Lead", "LEAD-0002"): {
				"display_name": "Grace Hopper",
				"assigned_users": ["rahul@demo.crm"],
				"owner_user": "rahul@demo.crm",
			},
		}
		unanswered_since = {
			("CRM Lead", "LEAD-0001"): datetime(2026, 8, 1, 8, 0, 0),
			("CRM Lead", "LEAD-0002"): datetime(2026, 8, 1, 11, 30, 0),
		}

		with (
			patch(
				"crm.api.whatsapp_followups.get_conversation_aggregates",
				return_value=aggregates,
			),
			patch(
				"crm.api.whatsapp_followups.get_unanswered_since",
				return_value=unanswered_since,
			),
			patch(
				"crm.api.whatsapp_followups.get_conversation_references",
				return_value=references,
			),
		):
			pending = get_pending_conversations(now=self.NOW)

		self.assertEqual([row["reference_name"] for row in pending], ["LEAD-0001"])
		self.assertEqual(pending[0]["assigned_users"], ["priya@demo.crm"])
		self.assertEqual(pending[0]["waiting_since"], datetime(2026, 8, 1, 8, 0, 0))

	def test_an_unassigned_conversation_falls_back_to_its_owner(self):
		aggregates = [self._aggregate("LEAD-0001", datetime(2026, 8, 1, 8, 0, 0))]
		references = {
			("CRM Lead", "LEAD-0001"): {
				"display_name": "Ada Lovelace",
				"assigned_users": [],
				"owner_user": "priya@demo.crm",
			}
		}

		with (
			patch(
				"crm.api.whatsapp_followups.get_conversation_aggregates",
				return_value=aggregates,
			),
			patch(
				"crm.api.whatsapp_followups.get_unanswered_since",
				return_value={("CRM Lead", "LEAD-0001"): datetime(2026, 8, 1, 8, 0, 0)},
			),
			patch(
				"crm.api.whatsapp_followups.get_conversation_references",
				return_value=references,
			),
		):
			pending = get_pending_conversations(now=self.NOW)

		self.assertEqual(pending[0]["assigned_users"], ["priya@demo.crm"])

	def test_a_conversation_nobody_owns_is_not_nudged(self):
		aggregates = [self._aggregate("LEAD-0001", datetime(2026, 8, 1, 8, 0, 0))]
		references = {
			("CRM Lead", "LEAD-0001"): {
				"display_name": "Ada Lovelace",
				"assigned_users": [],
				"owner_user": "",
			}
		}

		with (
			patch(
				"crm.api.whatsapp_followups.get_conversation_aggregates",
				return_value=aggregates,
			),
			patch(
				"crm.api.whatsapp_followups.get_unanswered_since",
				return_value={("CRM Lead", "LEAD-0001"): datetime(2026, 8, 1, 8, 0, 0)},
			),
			patch(
				"crm.api.whatsapp_followups.get_conversation_references",
				return_value=references,
			),
		):
			self.assertEqual(get_pending_conversations(now=self.NOW), [])

	def test_a_reminder_is_not_repeated_while_an_unread_one_exists(self):
		conversation = {
			"reference_doctype": "CRM Lead",
			"reference_name": "LEAD-0001",
			"display_name": "Ada Lovelace",
			"waiting_since": datetime(2026, 8, 1, 8, 0, 0),
		}

		with (
			patch("crm.api.whatsapp_followups.has_unread_followup", return_value=True),
			patch("crm.api.whatsapp_followups.notify_user") as mock_notify,
		):
			self.assertFalse(create_followup_notification(conversation, "priya@demo.crm"))

		mock_notify.assert_not_called()

		with (
			patch("crm.api.whatsapp_followups.has_unread_followup", return_value=False),
			patch("crm.api.whatsapp_followups.notify_user") as mock_notify,
		):
			self.assertTrue(create_followup_notification(conversation, "priya@demo.crm"))

		payload = mock_notify.call_args.args[0]
		self.assertEqual(payload["assigned_to"], "priya@demo.crm")
		self.assertEqual(payload["notification_type"], "WhatsApp")
		# Keeps reminders apart from per-message notifications, which point
		# notification_type_doctype at "WhatsApp Message".
		self.assertEqual(payload["reference_doctype"], "CRM Lead")
		self.assertEqual(payload["reference_docname"], "LEAD-0001")
		self.assertIn("Ada Lovelace", payload["notification_text"])

	def test_digest_counts_new_leads_replies_due_and_overdue_waits(self):
		aggregates = [
			self._aggregate("LEAD-0001", datetime(2026, 8, 1, 8, 0, 0)),
			self._aggregate("LEAD-0002", datetime(2026, 8, 1, 11, 30, 0)),
		]
		unanswered_since = {
			("CRM Lead", "LEAD-0001"): datetime(2026, 8, 1, 8, 0, 0),
			("CRM Lead", "LEAD-0002"): datetime(2026, 8, 1, 11, 30, 0),
		}

		with (
			patch(
				"crm.api.whatsapp_followups.frappe.get_all",
				return_value=["LEAD-0007", "LEAD-0008"],
			) as mock_leads,
			patch(
				"crm.api.whatsapp_followups.get_conversation_aggregates",
				return_value=aggregates,
			),
			patch(
				"crm.api.whatsapp_followups.get_unanswered_since",
				return_value=unanswered_since,
			),
		):
			summary = build_digest_summary(now=self.NOW)

		self.assertEqual(mock_leads.call_args.args[0], "CRM Lead")
		self.assertEqual(summary["new_leads"], 2)
		self.assertEqual(summary["needs_reply"], 2)
		# Only LEAD-0001 has been waiting for more than two hours.
		self.assertEqual(summary["overdue"], 1)
		# The newest WhatsApp lead is what the digest links to.
		self.assertEqual(summary["reference_doctype"], "CRM Lead")
		self.assertEqual(summary["reference_name"], "LEAD-0007")

	def test_digest_falls_back_to_the_longest_wait_when_no_lead_is_new(self):
		aggregates = [self._aggregate("LEAD-0001", datetime(2026, 8, 1, 8, 0, 0))]

		with (
			patch("crm.api.whatsapp_followups.frappe.get_all", return_value=[]),
			patch(
				"crm.api.whatsapp_followups.get_conversation_aggregates",
				return_value=aggregates,
			),
			patch(
				"crm.api.whatsapp_followups.get_unanswered_since",
				return_value={("CRM Lead", "LEAD-0001"): datetime(2026, 8, 1, 8, 0, 0)},
			),
		):
			summary = build_digest_summary(now=self.NOW)

		self.assertEqual(summary["new_leads"], 0)
		self.assertEqual(summary["reference_name"], "LEAD-0001")

	def test_an_empty_pipeline_produces_a_zero_summary(self):
		with (
			patch("crm.api.whatsapp_followups.frappe.get_all", return_value=[]),
			patch("crm.api.whatsapp_followups.get_conversation_aggregates", return_value=[]),
			patch("crm.api.whatsapp_followups.get_unanswered_since", return_value={}),
		):
			summary = build_digest_summary(now=self.NOW)

		self.assertEqual(summary["new_leads"], 0)
		self.assertEqual(summary["needs_reply"], 0)
		self.assertEqual(summary["overdue"], 0)
		self.assertIsNone(summary["reference_doctype"])
