# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for email sequences (master spec §5 item 21, design note 21).

Nothing here sends. Three seams stand between this suite and a real message and
every one of them is closed:

* `engine.commit` / `engine.rollback` and `outbound.commit` / `outbound.rollback`
  are neutralised, exactly as in `test_followup_engine.py` and
  `test_outbound.py`. A real commit would escape the test's rollback, and the
  ORDERING of those commits is the at-most-once guarantee, so the recorder is
  also how the ordering is asserted.
* The outbound Email adapter is replaced by a recorder, so "the sweep delivers
  the sequence email" is asserted by reading what the recorder was handed rather
  than by trusting that it would have been.
* No WhatsApp app is installed, so the WhatsApp half of the mixed-sequence test
  patches `engine.create_template_message` as the rest of the suite does.

The six acceptance criteria of `demo-package/specs/design-21-email-sequences.md`
each have at least one test named after them (`test_ac1_...` .. `test_ac6_...`).

Endpoint authorization (master spec §3), asserted below rather than described:

* `crm.api.followup_engine.get_email_template_options` -- any signed-in user may
  CALL it; only a Sales Manager or System Manager gets an answer, checked against
  `frappe.get_roles()` on the server. No client filter reaches the query, and an
  Email Template is site configuration rather than a customer record, so there is
  no row-level scope to derive. `TestPermissions` proves both halves.
* `/unsubscribe` -- a www route, reachable by Guest by design. The signed token
  IS the authorization: `TestToken` proves a token cannot be forged or replayed
  for another address, and `TestUnsubscribeRoute` proves an invalid token and an
  unknown address are answered identically.
"""

from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm import outbound
from crm.api import followup_engine as engine
from crm.sequences import core, router, unsubscribe
from crm.sequences import email as email_channel
from crm.suppression import CHANNEL_EMAIL, SUPPRESSION_DOCTYPE, get_suppression, suppress

SETTINGS = "FCRM Settings"
LEAD_DOCTYPE = "CRM Lead"
CUSTOMER_EMAIL = "ann.sequence@example.com"

APPROVED_TEMPLATE = frappe._dict(
	{
		"name": "trip_followup",
		"status": "APPROVED",
		"field_names": "first_name,destination",
		"sample_values": "Ann,Bali",
		"actual_name": "trip_followup",
		"template_name": "trip_followup",
	}
)


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


def stage_row(stage_number, channel="Email", **overrides):
	row = frappe._dict(
		{
			"stage_number": stage_number,
			"silence_days": 2,
			"channel": channel,
			"template": "trip_followup",
			"email_template": "",
			"email_subject_override": "",
			"use_ai": 0,
			"ai_instruction": "",
			"idx": stage_number,
		}
	)
	row.update(overrides)
	return row


def make_settings(stages, **overrides):
	settings = frappe._dict(
		{
			"enabled": 1,
			"auto_enroll": 0,
			"send_mode": "Auto-send",
			# Quiet hours off: a sweep test must not depend on the hour CI runs.
			"quiet_hours_start": "00:00:00",
			"quiet_hours_end": "00:00:00",
			"daily_send_cap": 50,
			"ignore_older_than_days": 14,
			"stop_keywords": "stop\nunsubscribe",
			"stages": stages,
		}
	)
	settings.update(overrides)
	return settings


class EmailSequenceTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.now = frappe.utils.now_datetime()
		self.journal = []
		self.delivered = []
		# The rate limiter counts in redis, which no test rollback undoes. A fresh
		# key per test is what keeps one test's clicks out of the next one's
		# window; the value is only ever a cache-key fragment.
		self.ip = f"test-{frappe.generate_hash(length=12)}"

		self.patches = [
			patch.object(engine, "commit", side_effect=lambda: self.journal.append("engine commit")),
			patch.object(engine, "rollback", side_effect=lambda: self.journal.append("engine rollback")),
			patch.object(outbound, "commit", side_effect=lambda: self.journal.append("outbound commit")),
			patch.object(outbound, "rollback", side_effect=lambda: self.journal.append("outbound rollback")),
		]
		for patcher in self.patches:
			patcher.start()

		self.set_flag(1)
		self.template = self.make_email_template()
		self.lead = self.make_lead()

	def tearDown(self):
		for patcher in self.patches:
			patcher.stop()
		outbound.unregister_adapter(outbound.CHANNEL_EMAIL)
		frappe.local.flags[outbound.UNSUBSCRIBE_FLAG] = None
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.clear_document_cache(SETTINGS, SETTINGS)

	# --- fixtures ---

	def set_flag(self, value: int):
		frappe.db.set_single_value(SETTINGS, email_channel.FLAG_EMAIL_SEQUENCES, value)
		frappe.clear_document_cache(SETTINGS, SETTINGS)

	def make_email_template(self, **overrides):
		values = {
			"doctype": "Email Template",
			"name": f"seq-template-{frappe.generate_hash(length=6)}",
			"subject": "Still thinking about {{ destination }}?",
			"use_html": 1,
			"response_html": "<p>Hi {{ first_name }}, shall we hold your dates?</p>",
			"enabled": 1,
		}
		values.update(overrides)
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def make_lead(self, email_address=CUSTOMER_EMAIL, **overrides):
		values = {
			"doctype": LEAD_DOCTYPE,
			"first_name": "Ann",
			"last_name": "Sequence",
			"email": email_address,
			"mobile_no": "+919876543210",
			"destination": "Bali",
			"status": lead_status(),
			"lead_owner": "Administrator",
		}
		values.update(overrides)
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def make_followup(self, **fields):
		values = {
			"doctype": engine.FOLLOWUP_DOCTYPE,
			"lead": self.lead.name,
			"phone": "+919876543210",
			"state": engine.STATE_ACTIVE,
			"current_stage": 0,
			"cycle": 1,
			"next_due": self.now - timedelta(minutes=1),
		}
		values.update(fields)
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def email_stages(self, count=2, **overrides):
		values = {"email_template": self.template.name}
		values.update(overrides)
		return [stage_row(number, **values) for number in range(1, count + 1)]

	def reload(self, row):
		return frappe.get_doc(engine.FOLLOWUP_DOCTYPE, row.name)

	def jobs(self):
		return frappe.get_all(
			outbound.JOB_DOCTYPE,
			filters={"job_type": email_channel.JOB_TYPE_SEQUENCE},
			fields=["name", "state", "idempotency_key", "subject", "payload", "owner_user"],
			order_by="creation asc",
		)

	def run_stage(self, followup, settings, stages):
		"""One due row through the real core, with the real router."""
		return engine.process_one(followup.name, settings, stages)

	def deliver(self, job_name: str):
		"""Run the outbound machine over one job with a recorder in place of email."""

		def recorder(job, recipient):
			self.delivered.append(
				{
					"job": job.name,
					"address": recipient.address,
					"payload": frappe.parse_json(job.payload or "{}"),
					"unsubscribe_flag": frappe.local.flags.get(outbound.UNSUBSCRIBE_FLAG),
				}
			)
			return {"email_queue": None, "message_id": f"<seq-{len(self.delivered)}@example.com>"}

		outbound.register_adapter(outbound.CHANNEL_EMAIL, recorder)
		outbound.claim_job(job_name)
		return outbound.execute_job(job_name)


class TestStageConfiguration(EmailSequenceTestCase):
	def test_a_stage_without_a_channel_is_a_whatsapp_stage(self):
		"""Every stage saved before Stage 5.1 has no channel stored."""
		settings = make_settings([frappe._dict({"stage_number": 1, "silence_days": 2, "idx": 1})])

		stages = engine.get_stages(settings)

		self.assertEqual(stages[0].channel, "WhatsApp")
		self.assertFalse(router.uses_email(stages))

	def test_the_email_fields_reach_the_stage_dicts(self):
		settings = make_settings(
			[stage_row(1, email_template="welcome-back", email_subject_override="A gentle nudge")]
		)

		stage = engine.get_stages(settings)[0]

		self.assertEqual(stage.channel, "Email")
		self.assertEqual(stage.email_template, "welcome-back")
		self.assertEqual(stage.email_subject_override, "A gentle nudge")

	def test_a_pure_whatsapp_sequence_gets_the_whatsapp_adapter_itself(self):
		"""No router, no new code path, for every site that has no email stage."""
		stages = engine.get_stages(make_settings([stage_row(1, channel="WhatsApp")]))

		adapter = engine.get_channel_adapter(stages)

		self.assertIs(adapter, engine.get_channel_adapter())
		self.assertNotIsInstance(adapter, router.ChannelRouter)

	def test_one_email_stage_switches_the_whole_row_to_the_router(self):
		stages = engine.get_stages(make_settings([stage_row(1, channel="WhatsApp"), stage_row(2)]))

		self.assertIsInstance(engine.get_channel_adapter(stages), router.ChannelRouter)


class TestAC1Sending(EmailSequenceTestCase):
	"""AC1: a 2-stage email sequence sends stage 1 via the outbound sweep."""

	def test_ac1_a_two_stage_email_sequence_sends_stage_one_via_the_outbound_sweep(self):
		followup = self.make_followup()
		stages = self.email_stages(2)
		settings = make_settings(stages)

		self.assertTrue(self.run_stage(followup, settings, stages))

		jobs = self.jobs()
		self.assertEqual(len(jobs), 1)
		self.assertEqual(jobs[0].state, outbound.JOB_SCHEDULED)
		self.assertEqual(jobs[0].idempotency_key, f"{self.lead.name}-cycle-1-stage-1-email")

		counts = self.deliver(jobs[0].name)

		self.assertEqual(counts["sent"], 1)
		self.assertEqual(len(self.delivered), 1)
		self.assertEqual(self.delivered[0]["address"], CUSTOMER_EMAIL)
		self.assertEqual(self.delivered[0]["payload"]["recipients"], [CUSTOMER_EMAIL])

	def test_the_row_advances_to_stage_one_and_waits_for_stage_two(self):
		followup = self.make_followup()
		stages = self.email_stages(2)

		self.run_stage(followup, make_settings(stages), stages)

		row = self.reload(followup)
		self.assertEqual(row.current_stage, 1)
		self.assertEqual(row.state, engine.STATE_ACTIVE)
		self.assertIsNotNone(row.next_due)

	def test_the_last_stage_exhausts_the_sequence(self):
		followup = self.make_followup(current_stage=1)
		stages = self.email_stages(2)

		self.run_stage(followup, make_settings(stages), stages)

		self.assertEqual(self.reload(followup).state, engine.STATE_EXHAUSTED)

	def test_the_message_is_the_template_rendered_with_the_lead(self):
		followup = self.make_followup()
		stages = self.email_stages(1)

		self.run_stage(followup, make_settings(stages), stages)
		payload = frappe.parse_json(self.jobs()[0].payload)

		self.assertEqual(payload["subject"], "Still thinking about Bali?")
		self.assertIn("Hi Ann", payload["content"])

	def test_a_subject_override_replaces_the_templates_own_subject(self):
		followup = self.make_followup()
		stages = self.email_stages(1, email_subject_override="One last thought on {{ destination }}")

		self.run_stage(followup, make_settings(stages), stages)

		self.assertEqual(self.jobs()[0].subject, "One last thought on Bali")

	def test_the_claim_is_committed_before_the_handover(self):
		"""The ordering that makes the send at-most-once."""
		followup = self.make_followup()
		stages = self.email_stages(1)

		self.run_stage(followup, make_settings(stages), stages)

		# The job exists (the claim) and it is Scheduled (the handover), with an
		# engine commit recorded between the two.
		self.assertIn("engine commit", self.journal)
		self.assertEqual(self.jobs()[0].state, outbound.JOB_SCHEDULED)

	def test_the_job_sends_as_the_leads_own_agent(self):
		followup = self.make_followup()
		stages = self.email_stages(1)

		self.run_stage(followup, make_settings(stages), stages)

		self.assertEqual(self.jobs()[0].owner_user, "Administrator")

	def test_a_lead_with_no_email_is_parked_with_a_stated_reason(self):
		self.lead.db_set("email", None, update_modified=False)
		frappe.db.set_value(LEAD_DOCTYPE, self.lead.name, "custom_parama_email_normalized", None)
		followup = self.make_followup()
		stages = self.email_stages(1)

		self.assertFalse(self.run_stage(followup, make_settings(stages), stages))

		row = self.reload(followup)
		self.assertEqual(row.state, engine.STATE_STOPPED)
		self.assertIn("no email address", row.blocked_reason)
		self.assertEqual(self.jobs(), [])

	def test_a_stage_with_no_template_is_parked_with_a_stated_reason(self):
		followup = self.make_followup()
		stages = self.email_stages(1, email_template="")

		self.assertFalse(self.run_stage(followup, make_settings(stages), stages))

		self.assertIn("No email template", self.reload(followup).blocked_reason)

	def test_a_disabled_template_is_parked_rather_than_sent(self):
		self.template.db_set("enabled", 0)
		followup = self.make_followup()
		stages = self.email_stages(1)

		self.assertFalse(self.run_stage(followup, make_settings(stages), stages))

		self.assertIn("disabled", self.reload(followup).blocked_reason)
		self.assertEqual(self.jobs(), [])

	def test_a_template_that_cannot_be_rendered_is_parked_not_retried(self):
		"""A broken template parks the row instead of throwing once an hour.

		The template cannot simply be SAVED broken -- `Email Template.validate`
		runs Jinja over it and refuses -- so the render itself is made to fail,
		which is the state a template that broke after it was saved leaves us in.
		"""
		followup = self.make_followup()
		stages = self.email_stages(1)

		with patch.object(frappe, "render_template", side_effect=RuntimeError("bad template")):
			self.assertFalse(self.run_stage(followup, make_settings(stages), stages))

		self.assertIn("could not be rendered", self.reload(followup).blocked_reason)


class TestAC1ReplyStop(EmailSequenceTestCase):
	"""AC1: an inbound reply stops the sequence, by header AND by address alone."""

	def received(self, **fields):
		values = {
			"doctype": "Communication",
			"communication_type": "Communication",
			"communication_medium": "Email",
			"sent_or_received": "Received",
			"subject": "Re: your trip",
			"content": "Sounds good",
			"sender": CUSTOMER_EMAIL,
			"reference_doctype": LEAD_DOCTYPE,
			"reference_name": self.lead.name,
		}
		values.update(fields)
		return frappe.get_doc(values)

	def test_ac1_a_reply_matched_by_header_stops_the_sequence(self):
		followup = self.make_followup()
		stages = self.email_stages(2)
		self.run_stage(followup, make_settings(stages), stages)

		recipient = frappe.get_all(
			outbound.RECIPIENT_DOCTYPE, filters={"job": self.jobs()[0].name}, pluck="name"
		)[0]
		frappe.db.set_value(
			outbound.RECIPIENT_DOCTYPE, recipient, "message_id", "<seq-1@example.com>", update_modified=False
		)

		# The reply comes from an address the CRM does NOT know, so only the header
		# can possibly match it.
		reply = self.received(sender="ann.personal@example.com")
		with patch.object(email_channel, "lead_from_address", return_value=None):
			with patch(
				"crm.api.email.reply_message_ids", return_value=["<seq-1@example.com>"]
			):
				stopped = email_channel.handle_inbound_reply(reply)

		self.assertEqual(stopped, followup.name)
		self.assertEqual(self.reload(followup).state, engine.STATE_REPLIED)
		self.assertIsNone(self.reload(followup).next_due)

	def test_ac1_a_reply_matched_by_address_only_stops_the_sequence(self):
		"""Mail clients strip headers, and a new thread is still an answer."""
		followup = self.make_followup()

		reply = self.received(reference_doctype=None, reference_name=None)
		stopped = email_channel.handle_inbound_reply(reply)

		self.assertEqual(stopped, followup.name)
		self.assertEqual(self.reload(followup).state, engine.STATE_REPLIED)

	def test_an_outgoing_email_stops_nothing(self):
		followup = self.make_followup()

		self.assertIsNone(email_channel.handle_inbound_reply(self.received(sent_or_received="Sent")))
		self.assertEqual(self.reload(followup).state, engine.STATE_ACTIVE)

	def test_an_unknown_sender_stops_nothing(self):
		followup = self.make_followup()

		self.assertIsNone(email_channel.handle_inbound_reply(self.received(sender="nobody@example.com")))
		self.assertEqual(self.reload(followup).state, engine.STATE_ACTIVE)

	def test_an_opted_out_row_is_never_downgraded_by_a_reply(self):
		followup = self.make_followup(state=engine.STATE_OPTED_OUT, next_due=None)

		email_channel.handle_inbound_reply(self.received())

		self.assertEqual(self.reload(followup).state, engine.STATE_OPTED_OUT)

	def test_the_reply_hook_never_raises_inside_an_inbound_insert(self):
		self.make_followup()

		with patch.object(email_channel, "lead_from_reply", side_effect=RuntimeError("boom")):
			with patch.object(frappe, "log_error") as log_mock:
				self.assertIsNone(email_channel.handle_inbound_reply(self.received()))

		log_mock.assert_called_once()

	def test_the_sweep_also_sees_an_email_reply_the_hook_missed(self):
		"""The router counts inbound email when it decides whether to send."""
		followup = self.make_followup()
		self.received().insert(ignore_permissions=True)
		stages = self.email_stages(2)

		self.assertFalse(self.run_stage(followup, make_settings(stages), stages))

		self.assertEqual(self.reload(followup).state, engine.STATE_REPLIED)
		self.assertEqual(self.jobs(), [])


class TestAC2Unsubscribe(EmailSequenceTestCase):
	"""AC2: the link suppresses the address, the next step is skipped, source is set."""

	def token(self):
		return unsubscribe.make_token(CUSTOMER_EMAIL, LEAD_DOCTYPE, self.lead.name)

	def test_ac2_the_unsubscribe_link_suppresses_the_address(self):
		outcome = unsubscribe.handle(self.token(), ip=self.ip)

		self.assertEqual(outcome["result"], unsubscribe.RESULT_DONE)
		self.assertEqual(outcome["address"], CUSTOMER_EMAIL)
		self.assertTrue(get_suppression(CHANNEL_EMAIL, CUSTOMER_EMAIL))

	def test_ac2_the_ledger_row_carries_the_unsubscribe_link_source(self):
		unsubscribe.handle(self.token(), ip=self.ip)

		row = frappe.db.get_value(
			SUPPRESSION_DOCTYPE,
			{"address": CUSTOMER_EMAIL, "channel": CHANNEL_EMAIL, "active": 1},
			["source", "state", "reference_doctype", "reference_name"],
			as_dict=True,
		)
		self.assertEqual(row.source, "unsubscribe_link")
		self.assertEqual(row.state, "Opted Out")
		self.assertEqual(row.reference_doctype, LEAD_DOCTYPE)
		self.assertEqual(row.reference_name, self.lead.name)

	def test_ac2_the_next_due_step_is_skipped_with_a_blocked_reason(self):
		followup = self.make_followup()
		stages = self.email_stages(2)
		unsubscribe.handle(self.token(), ip=self.ip)

		self.assertFalse(self.run_stage(followup, make_settings(stages), stages))

		row = self.reload(followup)
		self.assertEqual(row.state, engine.STATE_STOPPED)
		self.assertIn("opted out", row.blocked_reason)
		self.assertEqual(self.jobs(), [])

	def test_a_second_click_is_answered_and_writes_no_second_row(self):
		unsubscribe.handle(self.token(), ip=self.ip)
		outcome = unsubscribe.handle(self.token(), ip=self.ip)

		self.assertEqual(outcome["result"], unsubscribe.RESULT_ALREADY)
		self.assertEqual(
			frappe.db.count(SUPPRESSION_DOCTYPE, {"address": CUSTOMER_EMAIL, "channel": CHANNEL_EMAIL}), 1
		)

	def test_every_sequence_email_carries_the_footer_link(self):
		followup = self.make_followup()
		stages = self.email_stages(1)

		self.run_stage(followup, make_settings(stages), stages)
		payload = frappe.parse_json(self.jobs()[0].payload)

		self.assertIn("/unsubscribe?token=", payload["content"])
		self.assertIn("/unsubscribe?token=", payload["unsubscribe_url"])

	def test_the_suppression_is_checked_again_at_delivery(self):
		"""Consent can change in the hour between the claim and the sweep."""
		followup = self.make_followup()
		stages = self.email_stages(1)
		self.run_stage(followup, make_settings(stages), stages)

		suppress(CHANNEL_EMAIL, CUSTOMER_EMAIL, source="a later opt-out")
		counts = self.deliver(self.jobs()[0].name)

		self.assertEqual(counts["suppressed"], 1)
		self.assertEqual(self.delivered, [])


class TestUnsubscribeToken(EmailSequenceTestCase):
	def test_a_token_round_trips(self):
		payload = unsubscribe.read_token(unsubscribe.make_token(CUSTOMER_EMAIL, LEAD_DOCTYPE, "LEAD-1"))

		self.assertEqual(payload["address"], CUSTOMER_EMAIL)
		self.assertEqual(payload["reference_doctype"], LEAD_DOCTYPE)
		self.assertEqual(payload["reference_name"], "LEAD-1")

	def test_the_address_is_normalised_into_the_token(self):
		payload = unsubscribe.read_token(unsubscribe.make_token("  Ann.Sequence@Example.COM "))

		self.assertEqual(payload["address"], "ann.sequence@example.com")

	def test_an_unsigned_token_is_refused(self):
		body = unsubscribe.make_token(CUSTOMER_EMAIL).partition(".")[0]

		self.assertIsNone(unsubscribe.read_token(body))
		self.assertIsNone(unsubscribe.read_token(f"{body}.notasignature"))

	def test_a_token_cannot_be_edited_to_name_another_address(self):
		"""The forgery that would let anyone suppress a competitor's mailbox."""
		import base64
		import json

		forged_payload = json.dumps(
			{"v": unsubscribe.TOKEN_VERSION, "a": "victim@example.com", "dt": "", "dn": ""},
			separators=(",", ":"),
			sort_keys=True,
		)
		body = base64.urlsafe_b64encode(forged_payload.encode()).decode().rstrip("=")
		signature = unsubscribe.make_token(CUSTOMER_EMAIL).partition(".")[2]

		self.assertIsNone(unsubscribe.read_token(f"{body}.{signature}"))

	def test_garbage_is_refused_without_raising(self):
		for value in (None, "", "....", "not a token", "a.b.c", 12345):
			self.assertIsNone(unsubscribe.read_token(value))

	def test_an_address_that_cannot_be_normalised_gets_no_token(self):
		self.assertEqual(unsubscribe.make_token("not an address"), "")
		self.assertEqual(unsubscribe.link_for(""), "")

	def test_the_link_is_absolute_and_carries_the_token(self):
		link = unsubscribe.link_for(CUSTOMER_EMAIL)

		self.assertIn("/unsubscribe?token=", link)
		self.assertTrue(link.startswith("http"))


class TestUnsubscribeRoute(EmailSequenceTestCase):
	def test_an_invalid_token_and_an_unknown_address_are_answered_identically(self):
		unknown = unsubscribe.handle(unsubscribe.make_token("nobody@example.com"), ip=self.ip)
		invalid = unsubscribe.handle("rubbish", ip=self.ip)

		# The unknown address IS suppressed -- we cannot know it is unknown, and a
		# route that answered differently would confirm which addresses we hold.
		self.assertEqual(unknown["result"], unsubscribe.RESULT_DONE)
		self.assertEqual(invalid["result"], unsubscribe.RESULT_INVALID)
		self.assertEqual(invalid["address"], "")

	def test_the_rate_limit_refuses_a_scripted_caller(self):
		token = unsubscribe.make_token(CUSTOMER_EMAIL)

		results = [
			unsubscribe.handle(token, ip=self.ip)["result"]
			for _ in range(unsubscribe.RATE_LIMIT + 2)
		]

		self.assertEqual(results[-1], unsubscribe.RESULT_RATE_LIMITED)
		self.assertNotIn(unsubscribe.RESULT_RATE_LIMITED, results[: unsubscribe.RATE_LIMIT])

	def test_the_rate_limiter_fails_open_when_the_cache_is_down(self):
		"""Refusing a genuine unsubscribe is worse than serving an extra request."""
		with patch.object(frappe.cache, "incrby", side_effect=RuntimeError("no redis")):
			with patch.object(frappe, "log_error"):
				self.assertFalse(unsubscribe.over_rate_limit(self.ip))

	def test_the_page_says_something_for_every_outcome(self):
		for result in (
			unsubscribe.RESULT_DONE,
			unsubscribe.RESULT_ALREADY,
			unsubscribe.RESULT_INVALID,
			unsubscribe.RESULT_RATE_LIMITED,
		):
			text = unsubscribe.page_text(result, CUSTOMER_EMAIL)
			self.assertTrue(text["heading"])
			self.assertTrue(text["message"])

	def test_a_ledger_failure_is_logged_and_never_raised_at_the_visitor(self):
		with patch("crm.suppression.suppress", side_effect=RuntimeError("db down")):
			with patch.object(frappe, "log_error") as log_mock:
				outcome = unsubscribe.handle(unsubscribe.make_token(CUSTOMER_EMAIL), ip=self.ip)

		self.assertEqual(outcome["result"], unsubscribe.RESULT_INVALID)
		log_mock.assert_called_once()


class TestListUnsubscribeHeader(EmailSequenceTestCase):
	def test_the_header_is_added_while_a_sequence_job_is_delivering(self):
		doc = frappe._dict({"message": "Subject: hi\r\n\r\nbody"})
		frappe.local.flags[outbound.UNSUBSCRIBE_FLAG] = "https://crm.example.com/unsubscribe?token=abc"

		unsubscribe.add_list_unsubscribe_header(doc)

		self.assertTrue(
			doc.message.startswith("List-Unsubscribe: <https://crm.example.com/unsubscribe?token=abc>\r\n")
		)
		self.assertIn("Subject: hi", doc.message)

	def test_no_flag_means_no_header(self):
		doc = frappe._dict({"message": "Subject: hi\r\n\r\nbody"})
		frappe.local.flags[outbound.UNSUBSCRIBE_FLAG] = None

		unsubscribe.add_list_unsubscribe_header(doc)

		self.assertEqual(doc.message, "Subject: hi\r\n\r\nbody")

	def test_the_header_is_never_added_twice(self):
		doc = frappe._dict({"message": "List-Unsubscribe: <https://x/y>\r\nSubject: hi\r\n\r\nbody"})
		frappe.local.flags[outbound.UNSUBSCRIBE_FLAG] = "https://crm.example.com/unsubscribe?token=abc"

		unsubscribe.add_list_unsubscribe_header(doc)

		self.assertEqual(doc.message.count("List-Unsubscribe:"), 1)

	def test_the_hook_never_raises_inside_an_email_queue_insert(self):
		frappe.local.flags[outbound.UNSUBSCRIBE_FLAG] = "https://crm.example.com/unsubscribe?token=abc"

		class Exploding:
			def get(self, key):
				raise RuntimeError("boom")

		with patch.object(frappe, "log_error") as log_mock:
			unsubscribe.add_list_unsubscribe_header(Exploding())

		log_mock.assert_called_once()

	def test_the_outbound_engine_arms_the_flag_from_the_jobs_payload(self):
		followup = self.make_followup()
		stages = self.email_stages(1)
		self.run_stage(followup, make_settings(stages), stages)

		self.deliver(self.jobs()[0].name)

		self.assertIn("/unsubscribe?token=", self.delivered[0]["unsubscribe_flag"])

	def test_the_flag_is_cleared_after_the_adapter_returns(self):
		followup = self.make_followup()
		stages = self.email_stages(1)
		self.run_stage(followup, make_settings(stages), stages)

		self.deliver(self.jobs()[0].name)

		self.assertIsNone(frappe.local.flags.get(outbound.UNSUBSCRIBE_FLAG))


class TestAC3DraftMode(EmailSequenceTestCase):
	"""AC3: draft-for-approval parks email steps exactly like WhatsApp steps."""

	def test_ac3_draft_for_approval_parks_an_email_step(self):
		followup = self.make_followup()
		stages = self.email_stages(2)
		settings = make_settings(stages, send_mode=engine.SEND_MODE_DRAFT)

		self.assertFalse(self.run_stage(followup, settings, stages))

		row = self.reload(followup)
		self.assertEqual(row.state, engine.STATE_PENDING_APPROVAL)
		self.assertEqual(row.pending_stage, 1)
		self.assertEqual(self.jobs(), [])

	def test_ac3_the_parked_draft_holds_the_message_a_manager_will_approve(self):
		followup = self.make_followup()
		stages = self.email_stages(1)
		self.run_stage(followup, make_settings(stages, send_mode=engine.SEND_MODE_DRAFT), stages)

		params = frappe.parse_json(self.reload(followup).pending_params)

		self.assertEqual(params["subject"], "Still thinking about Bali?")
		self.assertIn("Hi Ann", params["content"])

	def test_approving_an_email_draft_schedules_exactly_one_job(self):
		followup = self.make_followup()
		stages = self.email_stages(2)
		settings = make_settings(stages, send_mode=engine.SEND_MODE_DRAFT)
		self.run_stage(followup, settings, stages)

		with patch.object(engine, "get_settings", return_value=settings):
			engine.approve_pending(followup.name)

		jobs = self.jobs()
		self.assertEqual(len(jobs), 1)
		self.assertEqual(jobs[0].state, outbound.JOB_SCHEDULED)
		self.assertEqual(self.reload(followup).current_stage, 1)

	def test_approving_refuses_when_the_template_went_away(self):
		followup = self.make_followup()
		stages = self.email_stages(1)
		settings = make_settings(stages, send_mode=engine.SEND_MODE_DRAFT)
		self.run_stage(followup, settings, stages)
		self.template.db_set("enabled", 0)

		with patch.object(engine, "get_settings", return_value=settings):
			with self.assertRaises(frappe.ValidationError):
				engine.approve_pending(followup.name)

		self.assertEqual(self.jobs(), [])


class TestAC4Idempotency(EmailSequenceTestCase):
	"""AC4: a double claim cannot double-send."""

	def test_ac4_a_second_claim_of_the_same_stage_collides_on_the_key(self):
		followup = self.make_followup()
		stages = self.email_stages(1)
		adapter = email_channel.EmailSequenceAdapter(engine)
		content, _problem = adapter.resolve_content(stages[0])
		key = adapter.claim_key(followup, 1)

		adapter.build_payload(followup, stages[0], content)
		adapter.claim(followup, 1, key, self.now)

		adapter.build_payload(followup, stages[0], content)
		with self.assertRaises(core.AlreadyClaimed):
			adapter.claim(followup, 1, key, self.now)

		self.assertEqual(len(self.jobs()), 1)

	def test_ac4_a_replayed_stage_advances_instead_of_sending_twice(self):
		"""The shape of a crashed run being repeated by the next sweep."""
		followup = self.make_followup()
		stages = self.email_stages(2)
		settings = make_settings(stages)
		self.run_stage(followup, settings, stages)

		# Put the row back exactly where it was before the first run.
		frappe.db.set_value(
			engine.FOLLOWUP_DOCTYPE,
			followup.name,
			{"current_stage": 0, "next_due": self.now - timedelta(minutes=1), "state": engine.STATE_ACTIVE},
			update_modified=False,
		)

		with patch.object(frappe, "log_error") as log_mock:
			self.assertFalse(self.run_stage(followup, settings, stages))

		self.assertEqual(len(self.jobs()), 1)
		self.assertEqual(self.reload(followup).current_stage, 1)
		log_mock.assert_called_once()

	def test_ac4_two_recipients_of_one_job_cannot_both_be_delivered(self):
		"""The outbound layer's own guard, on the row this adapter creates."""
		followup = self.make_followup()
		stages = self.email_stages(1)
		self.run_stage(followup, make_settings(stages), stages)
		job = self.jobs()[0]

		self.deliver(job.name)
		# A second sweep over the same job finds no pending recipient.
		second = outbound.execute_job(job.name)

		self.assertEqual(len(self.delivered), 1)
		self.assertTrue(second.get("skipped"))

	def test_a_new_cycle_gets_a_new_key(self):
		followup = self.make_followup()
		stages = self.email_stages(1)
		self.run_stage(followup, make_settings(stages), stages)

		frappe.db.set_value(
			engine.FOLLOWUP_DOCTYPE,
			followup.name,
			{
				"current_stage": 0,
				"cycle": 2,
				"state": engine.STATE_ACTIVE,
				"next_due": self.now - timedelta(minutes=1),
			},
			update_modified=False,
		)
		self.run_stage(followup, make_settings(stages), stages)

		self.assertEqual(
			sorted(job.idempotency_key for job in self.jobs()),
			[f"{self.lead.name}-cycle-1-stage-1-email", f"{self.lead.name}-cycle-2-stage-1-email"],
		)

	def test_a_failed_handover_cancels_its_job_rather_than_leaving_it_to_send(self):
		followup = self.make_followup()
		stages = self.email_stages(1)

		with patch.object(outbound, "schedule_job", side_effect=RuntimeError("boom")):
			with patch.object(frappe, "log_error"):
				self.assertFalse(self.run_stage(followup, make_settings(stages), stages))

		self.assertEqual(self.jobs()[0].state, outbound.JOB_CANCELLED)


class TestAC5MixedSequence(EmailSequenceTestCase):
	"""AC5: WhatsApp stage 1 then Email stage 2, in order."""

	def test_ac5_a_mixed_sequence_runs_both_channels_in_order(self):
		followup = self.make_followup()
		stages = [
			stage_row(1, channel="WhatsApp"),
			stage_row(2, channel="Email", email_template=self.template.name),
		]
		settings = make_settings(stages)
		sent_whatsapp = []

		def fake_send(*args, **kwargs):
			sent_whatsapp.append(args)
			return frappe._dict({"name": frappe.generate_hash(length=10)})

		with (
			patch.object(engine, "resolve_template", return_value=(APPROVED_TEMPLATE, None)),
			patch.object(engine, "get_latest_incoming", return_value=None),
			patch.object(engine, "resolve_recipient", return_value="+919876543210"),
			patch.object(engine, "create_template_message", side_effect=fake_send),
		):
			self.assertTrue(self.run_stage(followup, settings, stages))

		# Stage 1 went to Meta and created no outbound job.
		self.assertEqual(len(sent_whatsapp), 1)
		self.assertEqual(self.jobs(), [])
		self.assertEqual(self.reload(followup).current_stage, 1)

		frappe.db.set_value(
			engine.FOLLOWUP_DOCTYPE,
			followup.name,
			"next_due",
			self.now - timedelta(minutes=1),
			update_modified=False,
		)
		self.assertTrue(self.run_stage(followup, settings, stages))

		# Stage 2 went to the outbound engine and to nothing else.
		self.assertEqual(len(sent_whatsapp), 1)
		jobs = self.jobs()
		self.assertEqual(len(jobs), 1)
		self.assertEqual(jobs[0].idempotency_key, f"{self.lead.name}-cycle-1-stage-2-email")
		self.assertEqual(self.reload(followup).state, engine.STATE_EXHAUSTED)

	def test_the_router_sends_each_stage_on_its_own_channel(self):
		stages = [
			stage_row(1, channel="WhatsApp"),
			stage_row(2, channel="Email", email_template=self.template.name),
		]
		route = engine.get_channel_adapter(stages)
		row = frappe._dict({"current_stage": 0})

		self.assertIs(route.for_row(row), route.whatsapp)
		row.current_stage = 1
		self.assertIs(route.for_row(row), route.email)

	def test_the_daily_cap_counts_both_channels(self):
		stages = self.email_stages(1)
		route = engine.get_channel_adapter(stages)

		with patch.object(engine, "count_sent_today", return_value=3):
			with patch.object(route.email, "count_sent_today", return_value=2):
				self.assertTrue(route.budget_left(frappe._dict({"daily_send_cap": 6})))
				self.assertFalse(route.budget_left(frappe._dict({"daily_send_cap": 5})))
				self.assertTrue(route.budget_left(frappe._dict({"daily_send_cap": 0})))

	def test_quiet_hours_defer_an_email_stage_like_any_other(self):
		followup = self.make_followup()
		stages = self.email_stages(1)
		late = frappe.utils.get_datetime(f"{frappe.utils.today()} 23:00:00")
		frappe.db.set_value(
			engine.FOLLOWUP_DOCTYPE, followup.name, "next_due", late - timedelta(minutes=5), update_modified=False
		)
		settings = make_settings(stages, quiet_hours_start="21:00:00", quiet_hours_end="09:00:00")

		with patch.object(frappe.utils, "now_datetime", return_value=late):
			self.assertFalse(self.run_stage(followup, settings, stages))

		self.assertEqual(self.reload(followup).state, engine.STATE_ACTIVE)
		self.assertEqual(self.jobs(), [])


class TestAC6FlagOff(EmailSequenceTestCase):
	"""AC6: flag OFF = the adapter never sends; the foundation stays send-free."""

	def test_ac6_with_the_flag_off_an_email_stage_is_parked_and_nothing_is_claimed(self):
		self.set_flag(0)
		followup = self.make_followup()
		stages = self.email_stages(2)

		self.assertFalse(self.run_stage(followup, make_settings(stages), stages))

		row = self.reload(followup)
		self.assertEqual(row.state, engine.STATE_STOPPED)
		self.assertEqual(row.blocked_reason, "Email sequences are turned off.")
		self.assertEqual(self.jobs(), [])

	def test_ac6_with_the_flag_off_an_inbound_email_stops_nothing(self):
		"""A site that has not switched sequences on behaves exactly as before."""
		self.set_flag(0)
		followup = self.make_followup()

		reply = frappe._dict(
			{
				"sent_or_received": "Received",
				"sender": CUSTOMER_EMAIL,
				"reference_doctype": LEAD_DOCTYPE,
				"reference_name": self.lead.name,
			}
		)
		self.assertIsNone(email_channel.handle_inbound_reply(reply))
		self.assertEqual(self.reload(followup).state, engine.STATE_ACTIVE)

	def test_ac6_the_outbound_foundation_sends_nothing_with_no_adapter_registered(self):
		"""Stage 1's proof, re-run on a job this stage created."""
		followup = self.make_followup()
		stages = self.email_stages(1)
		self.run_stage(followup, make_settings(stages), stages)
		job = self.jobs()[0].name

		outbound.claim_job(job)
		result = outbound.execute_job(job)

		self.assertTrue(result["no_adapter"])
		self.assertEqual(result["sent"], 0)
		self.assertEqual(frappe.db.get_value(outbound.JOB_DOCTYPE, job, "state"), outbound.JOB_FAILED)

	def test_ac6_the_scheduled_sweep_reads_nothing_while_the_outbound_flag_is_off(self):
		frappe.db.set_single_value(SETTINGS, outbound.FLAG_OUTBOUND_ENGINE, 0)
		frappe.clear_document_cache(SETTINGS, SETTINGS)

		with patch.object(outbound, "load_adapter_modules") as loader:
			self.assertEqual(outbound.process_scheduled_jobs(), 0)

		loader.assert_not_called()

	def test_ac6_the_flag_is_off_by_default_in_the_registry(self):
		from crm.feature_flags import FLAGS

		self.assertIn(email_channel.FLAG_EMAIL_SEQUENCES, FLAGS)
		field = frappe.get_meta(SETTINGS).get_field(email_channel.FLAG_EMAIL_SEQUENCES)
		self.assertEqual(field.fieldtype, "Check")
		self.assertEqual(frappe.utils.cint(field.default), 0)


class TestPermissions(EmailSequenceTestCase):
	def test_a_sales_user_cannot_read_the_email_template_list(self):
		user = "sequence-sales-user@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": user,
					"first_name": "Sales",
					"send_welcome_email": 0,
					"roles": [{"role": "Sales User"}],
				}
			).insert(ignore_permissions=True)

		frappe.set_user(user)
		with self.assertRaises(frappe.PermissionError):
			engine.get_email_template_options()

	def test_a_manager_gets_only_enabled_templates(self):
		self.make_email_template(enabled=0, name="seq-template-disabled")

		names = [row["name"] for row in engine.get_email_template_options()]

		self.assertIn(self.template.name, names)
		self.assertNotIn("seq-template-disabled", names)


class TestTimelineChip(EmailSequenceTestCase):
	def test_a_sequence_email_is_labelled_with_its_stage(self):
		from crm.api.activities import sequence_stages

		followup = self.make_followup()
		stages = self.email_stages(1)
		self.run_stage(followup, make_settings(stages), stages)

		recipient = frappe.get_all(
			outbound.RECIPIENT_DOCTYPE, filters={"job": self.jobs()[0].name}, pluck="name"
		)[0]
		frappe.db.set_value(
			outbound.RECIPIENT_DOCTYPE, recipient, "communication", "COMM-SEQ-1", update_modified=False
		)

		found = sequence_stages([frappe._dict({"name": "COMM-SEQ-1"})])

		self.assertEqual(found, {"COMM-SEQ-1": 1})

	def test_an_ordinary_email_carries_no_stage(self):
		from crm.api.activities import sequence_stages

		self.assertEqual(sequence_stages([frappe._dict({"name": "COMM-PLAIN-1"})]), {})

	def test_the_lookup_costs_nothing_while_the_flag_is_off(self):
		from crm.api.activities import sequence_stages

		self.set_flag(0)
		with patch.object(frappe, "get_all") as get_all:
			self.assertEqual(sequence_stages([frappe._dict({"name": "COMM-SEQ-1"})]), {})

		get_all.assert_not_called()
