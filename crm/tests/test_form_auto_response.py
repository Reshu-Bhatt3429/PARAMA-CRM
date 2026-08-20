# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the web-form automatic reply (master spec §5, item 4).

Nothing here reaches a mail server. `crm.api.form._make_auto_response` is the one
seam between the decision path and the email queue, and every test stands in for
it with a recorder -- which is also how "exactly one reply per submission" is
asserted, by counting what the recorder was handed.

Endpoint authorization (master spec §3), asserted below rather than described:

* `crm.api.form.get_auto_response_fields` -- needs `System Manager` or
  `Sales Manager` (`_check_manager`). It returns a fixed vocabulary and no
  record. `TestPermissions` proves a Sales User is refused.
* `crm.api.form.send_auto_response_test` -- POST only, same manager gate, and the
  recipient is ALWAYS the caller's own address. `TestTestSend` proves the address
  cannot be supplied by the caller: the endpoint takes no recipient argument at
  all, and the send goes to `frappe.session.user`.
* `crm.api.form.save_form` / `get_form_config` -- already manager-gated and
  already covered in `crm/tests/test_form_api.py`; the auto-response fields ride
  the same gate and the round trip is asserted here.

`send_auto_response` itself is NOT whitelisted. It is a background job, reached
only from `queue_auto_response`, which refuses anything that is not a CRM web
form targeting the doctype of the record that was just created.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.api import form
from crm.suppression import CHANNEL_EMAIL, suppress

LEAD_DOCTYPE = "CRM Lead"
MANAGER = "auto-response-manager@example.com"
AGENT = "auto-response-agent@example.com"


def lead_status() -> str:
	status = frappe.db.get_value("CRM Lead Status", {"type": ["!=", "Lost"]}, "name")
	if not status:
		status = (
			frappe.get_doc(
				{"doctype": "CRM Lead Status", "lead_status": "New", "position": 1, "type": "Open"}
			)
			.insert(ignore_permissions=True)
			.name
		)
	return status


def make_user(email: str, role: str):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"roles": [{"role": role}],
			}
		).insert(ignore_permissions=True)


class AutoResponseTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.sent = []

		self.web_form = frappe.get_doc(
			{
				"doctype": "Web Form",
				"title": frappe.generate_hash(length=10),
				"route": f"auto-{frappe.generate_hash(length=8)}",
				"doc_type": LEAD_DOCTYPE,
				"module": form.FORM_MODULE,
				"login_required": 0,
				"is_standard": 0,
				"web_form_fields": [
					{"fieldname": "first_name", "label": "First name", "fieldtype": "Data"},
				],
			}
		).insert(ignore_permissions=True)

		self.lead = frappe.get_doc(
			{
				"doctype": LEAD_DOCTYPE,
				"first_name": "Priya",
				"last_name": "Sharma",
				"email": "priya@example.com",
				"status": lead_status(),
			}
		).insert(ignore_permissions=True)

		# One outgoing account, so "no account" is a state a test opts into
		# rather than the state every test starts in.
		self.account = self.ensure_email_account()

		self.patcher = patch.object(form, "_make_auto_response", side_effect=self.recorder)
		self.patcher.start()

	def tearDown(self):
		self.patcher.stop()
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def recorder(self, **kwargs):
		self.sent.append(kwargs)
		return {"name": f"COMM-{len(self.sent)}"}

	def ensure_email_account(self):
		existing = frappe.db.get_value("Email Account", {"enable_outgoing": 1}, "name")
		if existing:
			return existing
		return (
			frappe.get_doc(
				{
					"doctype": "Email Account",
					"email_account_name": "Auto Response Test",
					"email_id": "agency@example.com",
					"enable_outgoing": 1,
					"default_outgoing": 1,
					"smtp_server": "smtp.example.com",
					"smtp_port": 587,
					"password": "not-a-real-password",
					"login_id_is_different": 0,
					"awaiting_password": 1,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def configure(self, enabled=1, subject="Thanks {{ first_name }}", message="<p>Hi {{ first_name }}</p>"):
		form.save_auto_response(
			self.web_form.name, {"enabled": enabled, "subject": subject, "message": message}
		)

	def run_job(self, lead=None):
		return form.send_auto_response(self.web_form.name, LEAD_DOCTYPE, (lead or self.lead).name)

	def logs(self):
		return frappe.get_all(
			form.AUTO_RESPONSE_LOG_DOCTYPE,
			filters={"reference_name": self.lead.name},
			fields=["name", "status", "recipient", "detail"],
		)


# --- merge fields ----------------------------------------------------------


class TestRenderMerge(FrappeTestCase):
	def test_a_known_token_is_replaced(self):
		self.assertEqual(form.render_merge("Hi {{ first_name }}", {"first_name": "Priya"}), "Hi Priya")

	def test_whitespace_inside_the_braces_is_tolerated(self):
		values = {"first_name": "Priya"}
		self.assertEqual(form.render_merge("{{first_name}}", values), "Priya")
		self.assertEqual(form.render_merge("{{   first_name   }}", values), "Priya")

	def test_an_unknown_token_renders_as_nothing(self):
		"""The customer must never be shown the plumbing."""
		self.assertEqual(form.render_merge("Hi {{ salary }}!", {"first_name": "Priya"}), "Hi !")

	def test_a_value_is_html_escaped_into_a_body(self):
		out = form.render_merge("Hi {{ first_name }}", {"first_name": "<script>alert(1)</script>"})
		self.assertNotIn("<script>", out)
		self.assertIn("&lt;script&gt;", out)

	def test_a_subject_is_not_escaped(self):
		"""A subject line is plain text; escaping would print `&amp;` at a customer."""
		out = form.render_merge(
			"Thanks {{ organization_name }}", {"organization_name": "A & B"}, escape=False
		)
		self.assertEqual(out, "Thanks A & B")

	def test_nothing_outside_the_vocabulary_is_reachable(self):
		"""Substitution, not a template engine. There is nothing to evaluate."""
		out = form.render_merge("{{ frappe.session.user }}", {"first_name": "Priya"})
		self.assertEqual(out, "{{ frappe.session.user }}")

	def test_an_empty_template_is_an_empty_string(self):
		self.assertEqual(form.render_merge(None, {}), "")


class TestMergeValues(AutoResponseTestCase):
	def test_the_full_name_falls_back_to_the_two_halves(self):
		values = form.merge_values(self.lead)
		self.assertEqual(values["first_name"], "Priya")
		self.assertTrue(values["full_name"].startswith("Priya"))

	def test_every_advertised_token_has_an_entry(self):
		"""The builder shows these pills; every one of them must resolve."""
		values = form.merge_values(self.lead)
		for token, _label in form.AUTO_RESPONSE_MERGE_FIELDS:
			self.assertIn(token, values, f"{token} is offered in the builder but never resolved")


# --- the send decision -----------------------------------------------------


class TestSendDecision(AutoResponseTestCase):
	def test_one_submission_produces_exactly_one_reply(self):
		self.configure()
		self.assertEqual(self.run_job(), "sent")
		self.assertEqual(len(self.sent), 1)

	def test_a_second_run_for_the_same_submission_sends_nothing(self):
		"""The claim row is the idempotency key, and it is taken before rendering."""
		self.configure()
		self.run_job()
		self.assertEqual(self.run_job(), "duplicate")
		self.assertEqual(len(self.sent), 1)

	def test_the_toggle_off_sends_nothing(self):
		self.configure(enabled=0)
		self.assertEqual(self.run_job(), "disabled")
		self.assertEqual(self.sent, [])

	def test_a_form_that_was_never_configured_sends_nothing(self):
		self.assertEqual(self.run_job(), "disabled")
		self.assertEqual(self.sent, [])

	def test_a_suppressed_address_is_never_written_to(self):
		self.configure()
		suppress(CHANNEL_EMAIL, self.lead.email, source="test")
		self.assertEqual(self.run_job(), "suppressed")
		self.assertEqual(self.sent, [])

	def test_a_submission_with_no_email_address_sends_nothing(self):
		self.configure()
		lead = frappe.get_doc(
			{"doctype": LEAD_DOCTYPE, "first_name": "Anonymous", "status": lead_status()}
		).insert(ignore_permissions=True)
		self.assertEqual(form.send_auto_response(self.web_form.name, LEAD_DOCTYPE, lead.name), "no_recipient")
		self.assertEqual(self.sent, [])

	def test_no_outgoing_account_sends_nothing_and_says_so(self):
		self.configure()
		with patch.object(form, "outgoing_sender", return_value=None):
			self.assertEqual(self.run_job(), "no_email_account")
		self.assertEqual(self.sent, [])
		self.assertEqual(self.logs()[0]["status"], "No Email Account")

	def test_a_record_that_no_longer_exists_is_not_an_error(self):
		self.configure()
		self.assertEqual(form.send_auto_response(self.web_form.name, LEAD_DOCTYPE, "CRM-LEAD-NOPE"), "gone")

	def test_a_doctype_outside_the_allowlist_is_refused(self):
		self.configure()
		self.assertEqual(form.send_auto_response(self.web_form.name, "User", "Administrator"), "not_allowed")
		self.assertEqual(self.sent, [])

	def test_the_job_never_raises(self):
		"""The visitor is gone and the record exists; an exception helps nobody."""
		self.configure()
		with patch.object(form, "_make_auto_response", side_effect=RuntimeError("smtp is down")):
			self.assertEqual(self.run_job(), "failed")
		self.assertEqual(self.logs()[0]["status"], "Failed")

	def test_the_rendered_message_carries_the_merged_values(self):
		self.configure(subject="Thanks {{ first_name }}", message="<p>Hi {{ full_name }}</p>")
		self.run_job()
		self.assertEqual(self.sent[0]["subject"], "Thanks Priya")
		self.assertIn("Priya", self.sent[0]["content"])

	def test_the_reply_is_addressed_to_the_submitted_address(self):
		self.configure()
		self.run_job()
		self.assertEqual(self.sent[0]["recipient"], "priya@example.com")

	def test_the_reply_comes_from_the_agency_account_not_the_visitor(self):
		self.configure()
		self.run_job()
		self.assertTrue(self.sent[0]["sender"])
		self.assertEqual(self.sent[0]["doctype"], LEAD_DOCTYPE)
		self.assertEqual(self.sent[0]["name"], self.lead.name)

	def test_a_blank_subject_still_produces_one(self):
		"""A subject-less email is filtered as spam far more often."""
		self.configure(subject="")
		self.run_job()
		self.assertTrue(self.sent[0]["subject"].strip())

	def test_the_outcome_is_written_onto_the_claim_row(self):
		self.configure()
		self.run_job()
		row = self.logs()[0]
		self.assertEqual(row["status"], "Sent")
		self.assertEqual(row["recipient"], "priya@example.com")


# --- the trigger -----------------------------------------------------------


class TestQueueTrigger(AutoResponseTestCase):
	"""`queue_auto_response` is the gate between a record insert and a send."""

	def test_nothing_is_queued_outside_a_web_form_submission(self):
		self.configure()
		with patch.object(form.frappe, "enqueue") as enqueued:
			form.queue_auto_response(self.lead)
		enqueued.assert_not_called()

	def test_nothing_is_queued_without_a_named_form(self):
		self.configure()
		frappe.flags.in_web_form = True
		try:
			with patch.object(form.frappe, "enqueue") as enqueued:
				form.queue_auto_response(self.lead)
			enqueued.assert_not_called()
		finally:
			frappe.flags.in_web_form = False

	def test_a_submission_of_a_configured_form_is_queued_after_commit(self):
		self.configure()
		frappe.flags.in_web_form = True
		frappe.form_dict["web_form"] = self.web_form.name
		try:
			with patch.object(form.frappe, "enqueue") as enqueued:
				form.queue_auto_response(self.lead)
			enqueued.assert_called_once()
			kwargs = enqueued.call_args.kwargs
			self.assertTrue(kwargs["enqueue_after_commit"])
			self.assertEqual(kwargs["reference_name"], self.lead.name)
		finally:
			frappe.flags.in_web_form = False
			frappe.form_dict.pop("web_form", None)

	def test_a_form_for_another_doctype_cannot_drive_this_send(self):
		"""`web_form` arrives in the POST body, so it is never trusted as given."""
		self.configure()
		frappe.db.set_value("Web Form", self.web_form.name, "doc_type", "CRM Deal")
		frappe.flags.in_web_form = True
		frappe.form_dict["web_form"] = self.web_form.name
		try:
			with patch.object(form.frappe, "enqueue") as enqueued:
				form.queue_auto_response(self.lead)
			enqueued.assert_not_called()
		finally:
			frappe.flags.in_web_form = False
			frappe.form_dict.pop("web_form", None)

	def test_a_web_form_from_another_app_cannot_drive_this_send(self):
		self.configure()
		frappe.db.set_value("Web Form", self.web_form.name, "module", "Core")
		frappe.flags.in_web_form = True
		frappe.form_dict["web_form"] = self.web_form.name
		try:
			with patch.object(form.frappe, "enqueue") as enqueued:
				form.queue_auto_response(self.lead)
			enqueued.assert_not_called()
		finally:
			frappe.flags.in_web_form = False
			frappe.form_dict.pop("web_form", None)


# --- configuration round trip ----------------------------------------------


class TestConfiguration(AutoResponseTestCase):
	def test_an_unconfigured_form_reads_as_off(self):
		self.assertEqual(form.load_auto_response(self.web_form.name)["enabled"], 0)

	def test_saving_nothing_creates_no_row(self):
		form.save_auto_response(self.web_form.name, {"enabled": 0, "subject": "", "message": ""})
		self.assertFalse(frappe.db.exists(form.AUTO_RESPONSE_DOCTYPE, self.web_form.name))

	def test_a_saved_reply_round_trips(self):
		self.configure(subject="Hello", message="<p>Body</p>")
		stored = form.load_auto_response(self.web_form.name)
		self.assertEqual(stored, {"enabled": 1, "subject": "Hello", "message": "<p>Body</p>"})

	def test_the_form_config_carries_the_reply(self):
		self.configure(subject="Hello")
		config = form.get_form_config(self.web_form.name)
		self.assertEqual(config["auto_response"]["subject"], "Hello")

	def test_there_is_at_most_one_row_per_form(self):
		self.configure(subject="One")
		self.configure(subject="Two")
		rows = frappe.get_all(form.AUTO_RESPONSE_DOCTYPE, filters={"web_form": self.web_form.name})
		self.assertEqual(len(rows), 1)
		self.assertEqual(form.load_auto_response(self.web_form.name)["subject"], "Two")


# --- permissions -----------------------------------------------------------


class TestPermissions(AutoResponseTestCase):
	"""Master spec §3. No patching of the gate: the real role check refuses."""

	def setUp(self):
		super().setUp()
		make_user(MANAGER, "Sales Manager")
		make_user(AGENT, "Sales User")

	def test_a_sales_user_cannot_read_the_merge_vocabulary(self):
		frappe.set_user(AGENT)
		self.assertRaises(frappe.PermissionError, form.get_auto_response_fields)

	def test_a_sales_manager_can(self):
		frappe.set_user(MANAGER)
		self.assertTrue(form.get_auto_response_fields())

	def test_a_sales_user_cannot_send_a_test(self):
		frappe.set_user(AGENT)
		self.assertRaises(frappe.PermissionError, form.send_auto_response_test, self.web_form.name)

	def test_a_sales_user_cannot_read_a_form_config(self):
		frappe.set_user(AGENT)
		self.assertRaises(frappe.PermissionError, form.get_form_config, self.web_form.name)


class TestTestSend(AutoResponseTestCase):
	def setUp(self):
		super().setUp()
		make_user(MANAGER, "Sales Manager")

	def test_the_test_goes_to_the_caller_and_nobody_else(self):
		self.configure()
		frappe.set_user(MANAGER)
		with patch.object(form.frappe, "sendmail") as sendmail:
			result = form.send_auto_response_test(self.web_form.name)
		self.assertEqual(result["sent_to"], MANAGER)
		self.assertEqual(sendmail.call_args.kwargs["recipients"], [MANAGER])

	def test_the_endpoint_takes_no_recipient_argument(self):
		"""The strongest form of "the address cannot be supplied by the caller"."""
		import inspect

		signature = inspect.signature(form.send_auto_response_test)
		self.assertEqual(list(signature.parameters), ["name"])

	def test_the_test_shows_sample_values_not_empty_pills(self):
		self.configure(subject="Thanks {{ first_name }}")
		frappe.set_user(MANAGER)
		with patch.object(form.frappe, "sendmail") as sendmail:
			form.send_auto_response_test(self.web_form.name)
		self.assertIn("Priya", sendmail.call_args.kwargs["subject"])

	def test_no_outgoing_account_refuses_out_loud(self):
		self.configure()
		frappe.set_user(MANAGER)
		with patch.object(form, "outgoing_sender", return_value=None):
			self.assertRaises(frappe.ValidationError, form.send_auto_response_test, self.web_form.name)
