# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the composer's AI email draft (master spec item 14).

`crm.api.ai_draft.complete` is patched throughout, so the suite never reaches a
provider and never spends a request.

The load-bearing assertion in this module is the link rule, and it is asserted
in BOTH directions: a URL that the record's own whitelisted fields do not
contain never reaches the prompt, and never comes back out of a draft either.
The first protects a customer's message from being forwarded verbatim to a model
vendor; the second stops a model putting an invented link in front of an agent
who is about to mail it to a customer.

Authorization asserted here (master spec §3): `generate` needs READ on the
record. A user without it gets `PermissionError` and spends no budget.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.ai.client import AIConfigurationError, AIResponseError
from crm.api import ai_draft as api

LEAD_DOCTYPE = "CRM Lead"
OTHER_USER = "draft-outsider@example.com"


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


class DraftTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.lead = frappe.get_doc(
			{
				"doctype": LEAD_DOCTYPE,
				"first_name": "Ravi",
				"last_name": "Kulkarni",
				"status": lead_status(),
				"email": "ravi.kulkarni@example.com",
				"mobile_no": "+919820000001",
				# A domain-shaped token inside a whitelisted field, so the "a link
				# the record itself holds survives" half of the rule has something
				# real to test against. `AI_LEAD_FIELDS` holds no URL field of its
				# own, which is exactly why it has to be put in one.
				"destination": "Maldives — goa-resorts.example",
				"travel_start_date": "2026-11-02",
				"travel_end_date": "2026-11-06",
				"group_size": 4,
				"budget": 250000,
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def add_email(self, content, sent_or_received="Received", subject="Maldives trip"):
		return frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_type": "Communication",
				"communication_medium": "Email",
				"sent_or_received": sent_or_received,
				"subject": subject,
				"content": content,
				"sender": "ravi.kulkarni@example.com",
				"recipients": "agency@example.invalid",
				"reference_doctype": LEAD_DOCTYPE,
				"reference_name": self.lead.name,
				"communication_date": frappe.utils.now(),
			}
		).insert(ignore_permissions=True)

	def draft(self, body="Hello Ravi,\n\nHere are the options.", instruction="Follow up."):
		with patch("crm.api.ai_draft.complete", return_value={"body": body}) as mock:
			answer = api.generate(LEAD_DOCTYPE, self.lead.name, instruction)
		return answer, mock

	def prompt_for(self, instruction="Follow up.") -> str:
		_answer, mock = self.draft(instruction=instruction)
		return mock.call_args.args[0]


# --- the prompt ------------------------------------------------------------


class TestPrompt(DraftTestCase):
	def test_the_whitelisted_lead_fields_are_sent(self):
		prompt = self.prompt_for()
		self.assertIn("Maldives", prompt)
		self.assertIn("250000", prompt)

	def test_the_lead_whitelist_is_the_follow_up_engines_own_list(self):
		from crm.api.followup_engine import AI_LEAD_FIELDS

		# Not a copy. A second list is a list that drifts, and the drift would be
		# a field reaching a provider that nobody decided could.
		self.assertIs(api.RECORD_FIELDS[LEAD_DOCTYPE], AI_LEAD_FIELDS)

	def test_the_customers_email_and_phone_never_leave(self):
		self.add_email("Any update?")
		prompt = self.prompt_for()
		self.assertNotIn("ravi.kulkarni@example.com", prompt)
		self.assertNotIn("+919820000001", prompt)

	def test_the_agents_instruction_is_sent(self):
		prompt = self.prompt_for(instruction="Apologise for the delay.")
		self.assertIn("Apologise for the delay.", prompt)

	def test_a_link_the_record_does_not_hold_never_reaches_the_prompt(self):
		self.add_email("Have you seen https://competitor-tours.example/offer ? It is cheaper.")

		prompt = self.prompt_for()

		self.assertNotIn("competitor-tours.example", prompt)
		self.assertNotIn("https://", prompt)
		# The rest of the sentence survives; only the link is taken out.
		self.assertIn("It is cheaper.", prompt)

	def test_a_link_the_record_itself_holds_survives_in_the_prompt(self):
		self.add_email("Is goa-resorts.example the one you meant?")
		prompt = self.prompt_for()
		self.assertIn("goa-resorts.example", prompt)

	def test_at_most_ten_messages_are_sent(self):
		for index in range(api.MESSAGE_HISTORY_LIMIT + 6):
			self.add_email(f"Message number {index}.", sent_or_received="Sent")

		history = api.message_history(LEAD_DOCTYPE, self.lead.name, "")
		self.assertEqual(len(history), api.MESSAGE_HISTORY_LIMIT)

	def test_the_message_bodies_are_sent_without_their_html(self):
		self.add_email("<p>Can you confirm the <b>transfer</b>?</p>")
		prompt = self.prompt_for()
		self.assertIn("Can you confirm the transfer?", prompt)
		self.assertNotIn("<b>", prompt)

	def test_it_asks_for_the_draft_schema_and_isolates_the_budget_claim(self):
		_answer, mock = self.draft()
		self.assertEqual(mock.call_args.kwargs["json_schema"], api.DRAFT_SCHEMA)
		self.assertEqual(mock.call_args.kwargs["system"], api.DRAFT_SYSTEM)
		self.assertTrue(mock.call_args.kwargs["isolate_budget_claim"])

	def test_a_long_instruction_is_cut_rather_than_sent_whole(self):
		prompt = self.prompt_for(instruction="x" * (api.MAX_INSTRUCTION_CHARS + 500))
		self.assertNotIn("x" * (api.MAX_INSTRUCTION_CHARS + 1), prompt)


# --- the answer ------------------------------------------------------------


class TestBody(DraftTestCase):
	def test_it_returns_a_body_and_nothing_else(self):
		answer, _mock = self.draft()
		self.assertEqual(sorted(answer), ["body", "generated_at"])
		self.assertIn("Here are the options.", answer["body"])

	def test_the_body_is_cut_to_two_thousand_characters(self):
		answer, _mock = self.draft(body="y" * 5000)
		self.assertEqual(len(answer["body"]), api.MAX_BODY_CHARS)

	def test_paragraph_breaks_survive_the_cleaning(self):
		# The editor turns blank lines into paragraphs. Collapsing them would
		# hand the agent one run-on block to reformat by hand.
		answer, _mock = self.draft(body="First paragraph.\n\nSecond paragraph.")
		self.assertIn("\n\n", answer["body"])

	def test_a_link_the_record_does_not_hold_is_stripped_from_the_draft(self):
		answer, _mock = self.draft(body="Book here: https://not-our-site.example/pay")
		self.assertNotIn("not-our-site.example", answer["body"])

	def test_a_link_the_record_itself_holds_survives_in_the_draft(self):
		answer, _mock = self.draft(body="As on goa-resorts.example, the rate is unchanged.")
		self.assertIn("goa-resorts.example", answer["body"])

	def test_html_in_the_answer_is_stripped(self):
		answer, _mock = self.draft(body="<script>alert(1)</script>Hello Ravi.")
		self.assertNotIn("<script>", answer["body"])

	def test_an_empty_answer_is_an_error(self):
		with self.assertRaises(frappe.ValidationError):
			self.draft(body="   ")

	def test_an_instruction_is_required(self):
		with patch("crm.api.ai_draft.complete") as mock:
			with self.assertRaises(frappe.ValidationError):
				api.generate(LEAD_DOCTYPE, self.lead.name, "  ")
		mock.assert_not_called()

	def test_nothing_is_written_to_the_record(self):
		# C6: a draft is words in an editor. No Communication, no queue, no send.
		before = frappe.db.count("Communication", {"reference_name": self.lead.name})
		self.draft()
		self.assertEqual(frappe.db.count("Communication", {"reference_name": self.lead.name}), before)


# --- the disclosure line ---------------------------------------------------


class TestSentFields(DraftTestCase):
	def test_it_names_one_label_per_whitelisted_field(self):
		labels = api.sent_fields(LEAD_DOCTYPE)
		self.assertEqual(len(labels), len(api.RECORD_FIELDS[LEAD_DOCTYPE]))
		self.assertTrue(all(labels))

	def test_it_returns_nothing_for_a_doctype_with_no_draft(self):
		self.assertEqual(api.sent_fields("User"), [])


# --- failure paths ---------------------------------------------------------


class TestFailures(DraftTestCase):
	def test_a_provider_refusal_becomes_a_readable_error(self):
		with patch("crm.api.ai_draft.complete", side_effect=AIResponseError("declined")):
			with self.assertRaises(frappe.ValidationError) as caught:
				api.generate(LEAD_DOCTYPE, self.lead.name, "Follow up.")

		self.assertIn("could not draft this email", str(caught.exception))

	def test_ai_turned_off_raises_the_configuration_error_unchanged(self):
		with patch("crm.api.ai_draft.complete", side_effect=AIConfigurationError("AI is turned off.")):
			with self.assertRaises(AIConfigurationError):
				api.generate(LEAD_DOCTYPE, self.lead.name, "Follow up.")


# --- authorization ---------------------------------------------------------


class TestPermissions(DraftTestCase):
	def setUp(self):
		super().setUp()
		if not frappe.db.exists("User", OTHER_USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": OTHER_USER,
					"first_name": "Outsider",
					"send_welcome_email": 0,
					"roles": [{"role": "Sales User"}],
				}
			).insert(ignore_permissions=True)

	def test_a_user_without_read_access_is_refused_and_spends_no_budget(self):
		frappe.set_user(OTHER_USER)
		with patch("crm.api.ai_draft.complete") as mock:
			with self.assertRaises(frappe.PermissionError):
				api.generate(LEAD_DOCTYPE, self.lead.name, "Follow up.")

		mock.assert_not_called()

	def test_a_record_that_does_not_exist_fails_like_a_forbidden_one(self):
		with self.assertRaises(frappe.PermissionError):
			api.generate(LEAD_DOCTYPE, "CRM-LEAD-does-not-exist", "Follow up.")

	def test_an_unsupported_doctype_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api.generate("User", "Administrator", "Follow up.")
