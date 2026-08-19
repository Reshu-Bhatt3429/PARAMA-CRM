# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the timeline Brief card (master spec items 13 + 28 + 15, merged).

`crm.api.ai_brief.complete` is patched in every generation test, so the suite
never reaches a provider and never spends a request. The one place the real
client is used is `TestBriefSchema`, which holds the schema this module sends
against the validator the client will hold the answer against -- a schema that
the validator refuses is a bug that would otherwise only appear against a live
provider, at the agency's expense.

Authorization asserted here (master spec §3): `generate` is callable by a user
who can READ the record; a user who cannot read it gets `PermissionError`, and
so does a name that does not exist, so the endpoint cannot be used to enumerate
records.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.ai import schema as json_schema
from crm.ai.client import AIConfigurationError, AIResponseError
from crm.api import ai_brief as api

LEAD_DOCTYPE = "CRM Lead"
OTHER_USER = "brief-outsider@example.com"


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


def good_answer(**overrides) -> dict:
	answer = {
		"bullets": [
			"Wants a five day Maldives trip in November.",
			"Asked twice about the transfer from the airport.",
			"Budget is 250000 and has not moved.",
		],
		"next_step": {"description": "Send the resort options.", "due_hint": "tomorrow"},
		"tone": "neutral",
	}
	answer.update(overrides)
	return answer


class BriefTestCase(FrappeTestCase):
	"""One travel lead with a two-sided email thread on it."""

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
				"destination": "Maldives",
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

	def add_note(self, content, title="Internal"):
		return frappe.get_doc(
			{
				"doctype": "FCRM Note",
				"title": title,
				"content": content,
				"reference_doctype": LEAD_DOCTYPE,
				"reference_docname": self.lead.name,
			}
		).insert(ignore_permissions=True)

	def generate(self, answer=None, **kwargs):
		"""Run `generate` with the model stubbed. Returns (brief, mock)."""
		with patch("crm.api.ai_brief.complete", return_value=answer or good_answer(), **kwargs) as mock:
			brief = api.generate(LEAD_DOCTYPE, self.lead.name)
		return brief, mock

	def prompt_for(self, answer=None) -> str:
		with patch("crm.api.ai_brief.complete", return_value=answer or good_answer()) as mock:
			api.generate(LEAD_DOCTYPE, self.lead.name)
		return mock.call_args.args[0]


# --- the schema ------------------------------------------------------------


class TestBriefSchema(FrappeTestCase):
	"""The schema the client will validate the answer against."""

	def check(self, instance):
		json_schema.validate(instance, api.BRIEF_SCHEMA)

	def test_the_validator_understands_every_keyword_the_schema_uses(self):
		# `UnsupportedSchema` means this app wrote a schema its own validator
		# cannot enforce, which would let any answer through unchecked.
		self.check(good_answer())

	def test_a_well_formed_brief_passes(self):
		self.check(good_answer())

	def test_a_null_next_step_and_a_null_tone_pass(self):
		self.check(good_answer(next_step=None, tone=None))

	def test_a_brief_with_five_bullets_passes(self):
		self.check(good_answer(bullets=[f"Line {i}." for i in range(5)]))

	def test_two_bullets_are_refused(self):
		with self.assertRaises(json_schema.SchemaViolation):
			self.check(good_answer(bullets=["One.", "Two."]))

	def test_six_bullets_are_refused(self):
		with self.assertRaises(json_schema.SchemaViolation):
			self.check(good_answer(bullets=[f"Line {i}." for i in range(6)]))

	def test_bullets_that_are_not_strings_are_refused(self):
		with self.assertRaises(json_schema.SchemaViolation):
			self.check(good_answer(bullets=[{"text": "One."}, "Two.", "Three."]))

	def test_a_tone_outside_the_list_is_refused(self):
		with self.assertRaises(json_schema.SchemaViolation):
			self.check(good_answer(tone="delighted"))

	def test_a_next_step_without_a_description_is_refused(self):
		with self.assertRaises(json_schema.SchemaViolation):
			self.check(good_answer(next_step={"due_hint": "today"}))

	def test_an_unknown_due_hint_is_refused(self):
		with self.assertRaises(json_schema.SchemaViolation):
			self.check(good_answer(next_step={"description": "Call them.", "due_hint": "eventually"}))

	def test_an_extra_member_is_refused(self):
		with self.assertRaises(json_schema.SchemaViolation):
			self.check(good_answer(sentiment_score=0.4))

	def test_a_missing_bullets_member_is_refused(self):
		answer = good_answer()
		answer.pop("bullets")
		with self.assertRaises(json_schema.SchemaViolation):
			self.check(answer)


# --- the call --------------------------------------------------------------


class TestGenerate(BriefTestCase):
	def test_one_click_costs_exactly_one_model_call(self):
		# Items 13, 28 and 15 were three features. Merging them was only worth
		# doing if they share the call, so this is the assertion that keeps them
		# merged.
		_brief, mock = self.generate()
		self.assertEqual(mock.call_count, 1)

	def test_it_returns_the_bullets_the_next_step_and_the_tone(self):
		self.add_email("Can you confirm the airport transfer?")
		brief, _mock = self.generate()

		self.assertEqual(len(brief["bullets"]), 3)
		self.assertEqual(brief["next_step"]["description"], "Send the resort options.")
		self.assertEqual(brief["next_step"]["due_hint"], "tomorrow")
		self.assertEqual(brief["tone"], "neutral")
		self.assertTrue(brief["generated_at"])

	def test_it_asks_for_the_brief_schema_and_isolates_the_budget_claim(self):
		_brief, mock = self.generate()

		self.assertEqual(mock.call_args.kwargs["json_schema"], api.BRIEF_SCHEMA)
		self.assertEqual(mock.call_args.kwargs["system"], api.BRIEF_SYSTEM)
		# Stage 1B flag 1: an interactive call must not hold the budget row lock
		# for the length of the provider call.
		self.assertTrue(mock.call_args.kwargs["isolate_budget_claim"])

	def test_at_most_five_bullets_survive_however_many_come_back(self):
		brief, _mock = self.generate(good_answer(bullets=[f"Line {i}." for i in range(9)]))
		self.assertEqual(len(brief["bullets"]), api.MAX_BULLETS)

	def test_a_next_step_that_is_not_an_object_becomes_none(self):
		brief, _mock = self.generate(good_answer(next_step="call them"))
		self.assertIsNone(brief["next_step"])

	def test_an_unknown_due_hint_is_dropped_but_the_step_survives(self):
		brief, _mock = self.generate(
			good_answer(next_step={"description": "Call them.", "due_hint": "someday"})
		)
		self.assertEqual(brief["next_step"]["description"], "Call them.")
		self.assertIsNone(brief["next_step"]["due_hint"])

	def test_a_link_the_record_does_not_contain_is_stripped_from_a_bullet(self):
		brief, _mock = self.generate(
			good_answer(bullets=["Book at https://not-our-site.example/deal now.", "Two.", "Three."])
		)
		self.assertNotIn("not-our-site.example", brief["bullets"][0])

	def test_an_empty_brief_is_an_error_not_an_empty_card(self):
		with self.assertRaises(frappe.ValidationError):
			self.generate(good_answer(bullets=["   ", ""]))


# --- tone is evidence-bound ------------------------------------------------


class TestTone(BriefTestCase):
	def test_tone_is_null_when_the_customer_has_written_nothing(self):
		# Item 15 as amended: on-demand tone, and only about the customer's own
		# words. A record with no inbound message has no tone to report, whatever
		# the model felt like saying.
		self.add_email("Here are the options.", sent_or_received="Sent")
		brief, _mock = self.generate(good_answer(tone="frustrated"))
		self.assertIsNone(brief["tone"])

	def test_tone_survives_when_the_customer_has_written(self):
		self.add_email("This is the third time I am asking.")
		brief, _mock = self.generate(good_answer(tone="frustrated"))
		self.assertEqual(brief["tone"], "frustrated")

	def test_the_prompt_says_tone_must_be_null_when_nobody_wrote_in(self):
		prompt = self.prompt_for()
		self.assertIn("tone MUST be null", prompt)

	def test_a_tone_outside_the_list_becomes_null(self):
		self.add_email("Any update?")
		brief, _mock = self.generate(good_answer(tone="ecstatic"))
		self.assertIsNone(brief["tone"])


# --- what leaves the site --------------------------------------------------


class TestPayload(BriefTestCase):
	def test_the_whitelisted_travel_fields_are_sent(self):
		prompt = self.prompt_for()
		self.assertIn("Maldives", prompt)
		self.assertIn("250000", prompt)

	def test_the_customers_email_and_phone_never_leave(self):
		self.add_email("Any update?")
		prompt = self.prompt_for()
		self.assertNotIn("ravi.kulkarni@example.com", prompt)
		self.assertNotIn("+919820000001", prompt)

	def test_a_field_change_history_row_never_reaches_the_prompt(self):
		# A version row reads "email changed to ...". Including version rows in
		# the excerpt would mail the address out through the back door, which is
		# why `timeline_items` does not read them at all.
		self.lead.email = "ravi.new-address@example.com"
		self.lead.save(ignore_permissions=True)

		prompt = self.prompt_for()
		self.assertNotIn("ravi.new-address@example.com", prompt)

	def test_the_message_bodies_are_sent_without_their_html(self):
		self.add_email("<p>Can you confirm the <b>transfer</b>?</p>")
		prompt = self.prompt_for()
		self.assertIn("Can you confirm the transfer?", prompt)
		self.assertNotIn("<b>", prompt)

	def test_the_timeline_itself_hands_over_at_most_twenty_one_emails(self):
		# Not our cap: `frappe.desk.form.load.get_docinfo` reads the newest 21
		# communications (`load.py:108`), so that is the window the timeline
		# shows an agent and therefore the window the brief summarises. Asserted
		# rather than assumed, because our own caps are sized on top of it and
		# because a Frappe upgrade that changes the number should say so here.
		for index in range(30):
			self.add_email(f"Message number {index}.", sent_or_received="Sent")

		items = api.timeline_items(LEAD_DOCTYPE, self.lead.name)
		emails = [row for row in items if row["kind"] == "email"]
		self.assertEqual(len(emails), 21)

	def test_the_excerpt_is_capped_at_the_activity_limit(self):
		for index in range(20):
			self.add_email(f"Message number {index}.", sent_or_received="Sent")
		for index in range(15):
			self.add_note(f"Internal note {index}.")

		items = api.timeline_items(LEAD_DOCTYPE, self.lead.name)
		# The cap, plus at most the inbound floor on top of it. Nothing here is
		# inbound, so the cap is exact.
		self.assertEqual(len(items), api.ACTIVITY_LIMIT)

	def test_the_customers_own_words_survive_a_flood_of_internal_activity(self):
		self.add_email("The one thing the customer said.")
		for index in range(api.ACTIVITY_LIMIT + 8):
			self.add_note(f"Internal note {index}.")

		items = api.timeline_items(LEAD_DOCTYPE, self.lead.name)
		self.assertTrue(any("The one thing the customer said." in row["text"] for row in items))

	def test_the_excerpt_stays_under_the_byte_cap(self):
		# In Devanagari, which is why the second cap exists: the item cap counts
		# CHARACTERS and the provider bills BYTES, and a character here is three
		# of them. An agency writing in Hindi would otherwise send three times
		# the payload an English one does for the same-looking timeline.
		for index in range(30):
			self.add_note("यह एक लंबा आंतरिक नोट है। " * 40, title=f"नोट {index}")

		items = api.timeline_items(LEAD_DOCTYPE, self.lead.name)

		self.assertLess(len(items), api.ACTIVITY_LIMIT)
		self.assertLessEqual(api.payload_size(items), api.PAYLOAD_BYTES)

	def test_the_whole_request_stays_inside_the_clients_own_ceiling(self):
		from crm.ai import client

		for index in range(30):
			self.add_note("यह एक लंबा आंतरिक नोट है। " * 40, title=f"नोट {index}")
		for index in range(25):
			self.add_email("x" * 4000, sent_or_received="Sent", subject=f"Long {index}")

		prompt = self.prompt_for()
		# `check_request_size` raises above this; a caller that could trip it
		# would burn a budgeted request to find out.
		self.assertLess(len(prompt.encode("utf-8")), client.MAX_REQUEST_BYTES)


# --- failure paths ---------------------------------------------------------


class TestFailures(BriefTestCase):
	def test_a_provider_refusal_becomes_a_readable_error(self):
		with patch("crm.api.ai_brief.complete", side_effect=AIResponseError("declined")):
			with self.assertRaises(frappe.ValidationError) as caught:
				api.generate(LEAD_DOCTYPE, self.lead.name)

		self.assertIn("could not write this brief", str(caught.exception))

	def test_ai_turned_off_raises_the_configuration_error_unchanged(self):
		# The card branches on this: an unconfigured provider opens the "set it
		# up in Settings" popover, and turning it into a generic failure would
		# put an error toast in front of somebody who has done nothing wrong.
		with patch("crm.api.ai_brief.complete", side_effect=AIConfigurationError("AI is turned off.")):
			with self.assertRaises(AIConfigurationError):
				api.generate(LEAD_DOCTYPE, self.lead.name)

	def test_an_answer_that_is_not_an_object_is_refused(self):
		with patch("crm.api.ai_brief.complete", return_value="a brief, honestly"):
			with self.assertRaises(frappe.ValidationError):
				api.generate(LEAD_DOCTYPE, self.lead.name)

	def test_nothing_is_written_to_the_record(self):
		# C6. The brief is text handed back to the browser; the task and the note
		# are separate, later, explicit acts by the agent.
		modified = frappe.db.get_value(LEAD_DOCTYPE, self.lead.name, "modified")
		self.generate()
		self.assertEqual(frappe.db.get_value(LEAD_DOCTYPE, self.lead.name, "modified"), modified)


# --- authorization ---------------------------------------------------------


class TestPermissions(BriefTestCase):
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

	def test_a_user_without_read_access_is_refused(self):
		# The real CRM Lead rule, not a patched one: the lead belongs to
		# Administrator and this sales user is neither its owner nor an assignee.
		frappe.set_user(OTHER_USER)
		with patch("crm.api.ai_brief.complete", return_value=good_answer()) as mock:
			with self.assertRaises(frappe.PermissionError):
				api.generate(LEAD_DOCTYPE, self.lead.name)

		# And the refusal costs no budget.
		mock.assert_not_called()

	def test_a_record_that_does_not_exist_fails_like_a_forbidden_one(self):
		with self.assertRaises(frappe.PermissionError):
			api.generate(LEAD_DOCTYPE, "CRM-LEAD-does-not-exist")

	def test_an_unsupported_doctype_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api.generate("User", "Administrator")

	def test_an_empty_name_is_refused(self):
		with self.assertRaises(frappe.PermissionError):
			api.generate(LEAD_DOCTYPE, "")
