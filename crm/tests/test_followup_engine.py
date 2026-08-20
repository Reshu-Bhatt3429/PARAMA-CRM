# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the automated WhatsApp follow-up engine.

`frappe_whatsapp` is not installed in CI, so nothing here reads or writes a
WhatsApp Message row. Every message-shaped input is a plain `frappe._dict`, and
the functions that talk to the WhatsApp app -- `create_template_message`,
`read_conversation` and `resolve_template` -- are patched out. That also
guarantees the suite can never send a real message.

`engine.commit` and `engine.rollback` are neutralised for the same reason a
test must not commit: the base class rolls the whole test back. They are
replaced with recorders, which is also how the commit *ordering* that makes the
send at-most-once is asserted.
"""

import json
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import ClassVar
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.ai import client as ai_client
from crm.api import followup_engine as engine

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


def stage_row(stage_number, silence_days=2, template="trip_followup", use_ai=0):
	return frappe._dict(
		{
			"stage_number": stage_number,
			"silence_days": silence_days,
			"template": template,
			"use_ai": use_ai,
			"ai_instruction": "",
			"idx": stage_number,
		}
	)


def make_settings(**overrides):
	settings = frappe._dict(
		{
			"enabled": 1,
			"auto_enroll": 1,
			"send_mode": "Auto-send",
			# Quiet hours are off by default here. A sweep test must not depend
			# on what time of day CI happens to run.
			"quiet_hours_start": "00:00:00",
			"quiet_hours_end": "00:00:00",
			"daily_send_cap": 50,
			"ignore_older_than_days": 14,
			"stop_keywords": "stop\nunsubscribe\noptout\nopt out",
			"stages": [stage_row(1), stage_row(2), stage_row(3)],
		}
	)
	settings.update(overrides)
	return settings


@contextmanager
def template_lookup(result):
	"""Answer only the WhatsApp Templates reads; every other database read is real.

	A blanket patch of `frappe.db.get_value` would also answer the reads that
	`frappe.log_error` makes while it loads its own metadata, which fails in a
	way that has nothing to do with the code under test.
	"""
	real_exists = frappe.db.exists
	real_get_value = frappe.db.get_value

	def exists(doctype, *args, **kwargs):
		if doctype == "DocType" and args and args[0] == engine.TEMPLATE_DOCTYPE:
			return engine.TEMPLATE_DOCTYPE
		return real_exists(doctype, *args, **kwargs)

	def get_value(doctype, *args, **kwargs):
		if doctype == engine.TEMPLATE_DOCTYPE:
			return result
		return real_get_value(doctype, *args, **kwargs)

	with (
		patch.object(frappe.db, "exists", side_effect=exists),
		patch.object(frappe.db, "get_value", side_effect=get_value),
	):
		yield


def fake_message(**fields):
	message = frappe._dict(
		{
			"name": "WM-TEST-0001",
			"type": "Incoming",
			"message": "",
			"reference_doctype": "CRM Lead",
			"reference_name": None,
			"from": "",
			"to": "",
			"creation": None,
			"flags": frappe._dict(),
		}
	)
	message.update(fields)
	return message


class FollowupEngineTestCase(FrappeTestCase):
	def setUp(self):
		self.settings = make_settings()
		self.stages = engine.get_stages(self.settings)
		self.now = frappe.utils.now_datetime()
		# `journal` records every commit, rollback and send, in order. The
		# at-most-once guarantee is an ordering property, so ordering is what
		# the B1 tests assert.
		self.journal = []

		self.patches = [
			patch.object(engine, "commit", side_effect=lambda: self.journal.append("commit")),
			patch.object(engine, "rollback", side_effect=lambda: self.journal.append("rollback")),
		]
		for patcher in self.patches:
			patcher.start()

		frappe.db.set_single_value(
			engine.SETTINGS_DOCTYPE, "stop_keywords", "stop\nunsubscribe\noptout\nopt out"
		)

	def tearDown(self):
		for patcher in self.patches:
			patcher.stop()
		frappe.db.rollback()
		# The rollback undoes the row but not the cached copy of the Single.
		frappe.clear_document_cache(engine.SETTINGS_DOCTYPE, engine.SETTINGS_DOCTYPE)

	# --- fixtures ---

	def make_lead(self, first_name="Ann", mobile_no="+919876543210", destination="Bali"):
		return frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": first_name,
				"mobile_no": mobile_no,
				"destination": destination,
				"lead_owner": "Administrator",
			}
		).insert(ignore_permissions=True)

	def make_followup(self, lead, **fields):
		followup = frappe.get_doc(
			{
				"doctype": engine.FOLLOWUP_DOCTYPE,
				"lead": lead.name,
				"phone": lead.mobile_no,
				"state": engine.STATE_ACTIVE,
				"current_stage": 0,
				"cycle": 1,
			}
		)
		followup.update(fields)
		return followup.insert(ignore_permissions=True)

	def reload(self, followup):
		return frappe.get_doc(engine.FOLLOWUP_DOCTYPE, followup.name)

	def sending_patches(self, latest_incoming=None, template=APPROVED_TEMPLATE, problem=None):
		"""Patch every boundary that reaches the WhatsApp app."""

		def send(*args, **kwargs):
			self.journal.append("send")
			return frappe._dict({"name": frappe.generate_hash(length=10)})

		return (
			patch.object(engine, "resolve_template", return_value=(template, problem)),
			patch.object(engine, "get_latest_incoming", return_value=latest_incoming),
			patch.object(engine, "resolve_recipient", return_value="+919876543210"),
			patch.object(engine, "create_template_message", side_effect=send),
		)

	@contextmanager
	def sending(self, **kwargs):
		template_patch, incoming_patch, recipient_patch, message_patch = self.sending_patches(**kwargs)
		with template_patch, incoming_patch, recipient_patch, message_patch as message_mock:
			yield message_mock


class TestEnrollment(FollowupEngineTestCase):
	def aggregate(self, lead, incoming=None, outgoing=None):
		return {
			"reference_doctype": "CRM Lead",
			"reference_name": lead.name,
			"last_incoming_at": incoming,
			"last_outgoing_at": outgoing,
		}

	def test_enrolls_active_only_when_the_agency_spoke_last(self):
		"""A conversation we answered last is Active; one the customer answered last is not."""
		waiting_on_customer = self.make_lead("Ann", "+919876543210")
		waiting_on_us = self.make_lead("Bob", "+919876543211")

		aggregates = [
			self.aggregate(waiting_on_customer, self.now - timedelta(days=3), self.now - timedelta(days=2)),
			self.aggregate(waiting_on_us, self.now - timedelta(days=1), self.now - timedelta(days=2)),
		]

		with (
			patch.object(engine, "get_conversation_aggregates", return_value=aggregates),
			patch.object(engine, "resolve_conversation_number", return_value="+919876543210"),
			patch.object(engine, "read_conversation", return_value=[]),
		):
			created = engine.enroll_conversations(self.settings, self.stages)

		self.assertEqual(created, 2)

		active = frappe.db.get_value(
			engine.FOLLOWUP_DOCTYPE,
			{"lead": waiting_on_customer.name},
			["state", "next_due", "current_stage", "cycle"],
			as_dict=True,
		)
		self.assertEqual(active.state, engine.STATE_ACTIVE)
		self.assertEqual(active.current_stage, 0)
		self.assertEqual(active.cycle, 1)
		self.assertAlmostEqual(
			frappe.utils.get_datetime(active.next_due),
			self.now - timedelta(days=2) + timedelta(days=2),
			delta=timedelta(seconds=5),
		)

		replied = frappe.db.get_value(
			engine.FOLLOWUP_DOCTYPE, {"lead": waiting_on_us.name}, ["state", "next_due"], as_dict=True
		)
		self.assertEqual(replied.state, engine.STATE_REPLIED)
		self.assertIsNone(replied.next_due)

	def test_enrollment_skips_leads_that_already_have_a_row(self):
		lead = self.make_lead()
		self.make_followup(lead, state=engine.STATE_OPTED_OUT)

		with (
			patch.object(
				engine,
				"get_conversation_aggregates",
				return_value=[self.aggregate(lead, None, self.now - timedelta(days=5))],
			),
			patch.object(engine, "resolve_conversation_number", return_value="+919876543210"),
			patch.object(engine, "read_conversation", return_value=[]),
		):
			created = engine.enroll_conversations(self.settings, self.stages)

		self.assertEqual(created, 0)
		self.assertEqual(frappe.db.count(engine.FOLLOWUP_DOCTYPE, {"lead": lead.name}), 1)
		self.assertEqual(
			frappe.db.get_value(engine.FOLLOWUP_DOCTYPE, {"lead": lead.name}, "state"),
			engine.STATE_OPTED_OUT,
		)

	def test_enrollment_skips_a_lead_without_a_valid_number(self):
		lead = self.make_lead(mobile_no="")
		with (
			patch.object(
				engine,
				"get_conversation_aggregates",
				return_value=[self.aggregate(lead, None, self.now - timedelta(days=5))],
			),
			patch.object(engine, "read_conversation", return_value=[]),
		):
			self.assertEqual(engine.enroll_conversations(self.settings, self.stages), 0)

	# --- M-enable-backlog ---

	def test_a_dormant_thread_is_not_enrolled(self):
		"""Regression: enabling the engine must not drain a months-old backlog."""
		fresh = self.make_lead("Ann", "+919876543210")
		dormant = self.make_lead("Bob", "+919876543211")

		aggregates = [
			self.aggregate(fresh, None, self.now - timedelta(days=3)),
			self.aggregate(dormant, None, self.now - timedelta(days=90)),
		]
		with (
			patch.object(engine, "get_conversation_aggregates", return_value=aggregates),
			patch.object(engine, "resolve_conversation_number", return_value="+919876543210"),
			patch.object(engine, "read_conversation", return_value=[]),
		):
			created = engine.enroll_conversations(self.settings, self.stages)

		self.assertEqual(created, 1)
		self.assertTrue(frappe.db.exists(engine.FOLLOWUP_DOCTYPE, {"lead": fresh.name}))
		self.assertFalse(frappe.db.exists(engine.FOLLOWUP_DOCTYPE, {"lead": dormant.name}))

	def test_a_zero_bound_disables_the_idle_check(self):
		lead = self.make_lead()
		settings = make_settings(ignore_older_than_days=0)
		with (
			patch.object(
				engine,
				"get_conversation_aggregates",
				return_value=[self.aggregate(lead, None, self.now - timedelta(days=400))],
			),
			patch.object(engine, "resolve_conversation_number", return_value="+919876543210"),
			patch.object(engine, "read_conversation", return_value=[]),
		):
			self.assertEqual(engine.enroll_conversations(settings, self.stages), 1)

	def test_an_unstored_idle_bound_falls_back_to_the_shipped_default(self):
		"""Regression: a Single field added after the first save reads back as None.

		Treating that None as 0 would turn the backlog guard off on exactly the
		sites that already migrated the version without the field.
		"""
		settings = make_settings()
		del settings["ignore_older_than_days"]
		cutoff = engine.enrolment_cutoff(settings)

		self.assertIsNotNone(cutoff)
		self.assertAlmostEqual(
			cutoff,
			self.now - timedelta(days=engine.DEFAULT_IDLE_BOUND_DAYS),
			delta=timedelta(seconds=30),
		)

	def test_an_explicit_zero_idle_bound_is_still_honoured(self):
		self.assertIsNone(engine.enrolment_cutoff(make_settings(ignore_older_than_days=0)))

	def test_a_dormant_thread_is_not_enrolled_when_the_bound_is_unstored(self):
		lead = self.make_lead()
		settings = make_settings()
		del settings["ignore_older_than_days"]

		with (
			patch.object(
				engine,
				"get_conversation_aggregates",
				return_value=[self.aggregate(lead, None, self.now - timedelta(days=90))],
			),
			patch.object(engine, "resolve_conversation_number", return_value="+919876543210"),
			patch.object(engine, "read_conversation", return_value=[]),
		):
			self.assertEqual(engine.enroll_conversations(settings, self.stages), 0)

	# --- B3b: opt-out that predates the row ---

	def test_history_opt_out_enrolls_as_opted_out_and_never_sends(self):
		"""Regression B3: a STOP written before the row existed still binds us."""
		lead = self.make_lead()
		history = [
			frappe._dict(
				{
					"name": "WM-OLD-1",
					"type": "Incoming",
					"message": "please STOP messaging me",
					"creation": self.now - timedelta(days=10),
					"from": "919876543210",
					"to": "",
				}
			)
		]

		with (
			patch.object(
				engine,
				"get_conversation_aggregates",
				return_value=[self.aggregate(lead, None, self.now - timedelta(days=3))],
			),
			patch.object(engine, "resolve_conversation_number", return_value="+919876543210"),
			patch.object(engine, "read_conversation", return_value=history),
		):
			self.assertEqual(engine.enroll_conversations(self.settings, self.stages), 1)

		row = frappe.db.get_value(
			engine.FOLLOWUP_DOCTYPE,
			{"lead": lead.name},
			["state", "next_due", "opted_out_source"],
			as_dict=True,
		)
		self.assertEqual(row.state, engine.STATE_OPTED_OUT)
		self.assertIsNone(row.next_due)
		self.assertIn("WM-OLD-1", row.opted_out_source)

	# --- M-enroll-isolation ---

	def test_one_bad_enrolment_does_not_abort_the_rest(self):
		"""Regression: a single failing insert must not cost the whole hour."""
		good = self.make_lead("Ann", "+919876543210")
		bad = self.make_lead("Bob", "+919876543211")

		aggregates = [
			self.aggregate(bad, None, self.now - timedelta(days=3)),
			self.aggregate(good, None, self.now - timedelta(days=3)),
		]
		real_enroll_one = engine.enroll_one

		def flaky(lead, *args, **kwargs):
			if lead == bad.name:
				raise RuntimeError("boom")
			return real_enroll_one(lead, *args, **kwargs)

		with (
			patch.object(engine, "get_conversation_aggregates", return_value=aggregates),
			patch.object(engine, "resolve_conversation_number", return_value="+919876543210"),
			patch.object(engine, "read_conversation", return_value=[]),
			patch.object(engine, "enroll_one", side_effect=flaky),
			patch.object(frappe, "log_error") as log_mock,
		):
			created = engine.enroll_conversations(self.settings, self.stages)

		self.assertEqual(created, 1)
		self.assertTrue(frappe.db.exists(engine.FOLLOWUP_DOCTYPE, {"lead": good.name}))
		self.assertFalse(frappe.db.exists(engine.FOLLOWUP_DOCTYPE, {"lead": bad.name}))
		log_mock.assert_called_once()


class TestStageSequence(FollowupEngineTestCase):
	def due_followup(self, lead, **fields):
		return self.make_followup(
			lead,
			next_due=self.now - timedelta(minutes=1),
			last_agency_message=self.now - timedelta(days=2),
			**fields,
		)

	def make_due_again(self, followup):
		frappe.db.set_value(
			engine.FOLLOWUP_DOCTYPE,
			followup.name,
			"next_due",
			self.now - timedelta(minutes=1),
			update_modified=False,
		)

	def test_three_stages_fire_in_order_and_then_exhaust(self):
		lead = self.make_lead()
		followup = self.due_followup(lead)

		with self.sending() as message_mock:
			for expected_stage in (1, 2, 3):
				sent = engine.sweep_due_followups(self.settings, self.stages)
				self.assertEqual(sent, 1)

				row = self.reload(followup)
				self.assertEqual(row.current_stage, expected_stage)
				if expected_stage < 3:
					self.assertEqual(row.state, engine.STATE_ACTIVE)
					self.assertGreater(frappe.utils.get_datetime(row.next_due), self.now)
					self.make_due_again(row)

		final = self.reload(followup)
		self.assertEqual(final.state, engine.STATE_EXHAUSTED)
		self.assertIsNone(final.next_due)
		self.assertEqual(message_mock.call_count, 3)
		self.assertEqual(frappe.db.count(engine.SEND_LOG_DOCTYPE, {"lead": lead.name}), 3)
		self.assertEqual(
			frappe.db.count(engine.SEND_LOG_DOCTYPE, {"lead": lead.name, "status": engine.SEND_SENT}),
			3,
		)

		self.assertEqual(engine.sweep_due_followups(self.settings, self.stages), 0)

	# --- B1: the commit boundary ---

	def test_the_dedup_key_is_committed_before_meta_is_called(self):
		"""Regression B1: a rollback after the send must not free the key.

		frappe_whatsapp POSTs to Meta inside the message insert. If the claim
		were still uncommitted at that point, an RQ timeout would roll the claim
		back while the paid message had already gone out, and the next hour
		would send it again.
		"""
		lead = self.make_lead()
		self.due_followup(lead)

		with self.sending():
			engine.sweep_due_followups(self.settings, self.stages)

		self.assertIn("send", self.journal)
		self.assertIn("commit", self.journal)
		self.assertLess(
			self.journal.index("commit"),
			self.journal.index("send"),
			msg=f"claim must commit before the Meta call; journal was {self.journal}",
		)

	def test_a_failed_meta_send_consumes_the_stage_and_records_the_failure(self):
		"""Regression B1/B-M2: a failed send must not retry the same stage for ever."""
		lead = self.make_lead()
		followup = self.due_followup(lead)

		with (
			patch.object(engine, "resolve_template", return_value=(APPROVED_TEMPLATE, None)),
			patch.object(engine, "get_latest_incoming", return_value=None),
			patch.object(engine, "resolve_recipient", return_value="+919876543210"),
			patch.object(engine, "create_template_message", side_effect=RuntimeError("meta down")),
			patch.object(frappe, "log_error") as log_mock,
		):
			self.assertEqual(engine.sweep_due_followups(self.settings, self.stages), 0)

		log = frappe.db.get_value(
			engine.SEND_LOG_DOCTYPE, {"lead": lead.name}, ["status", "stage"], as_dict=True
		)
		self.assertEqual(log.status, engine.SEND_FAILED)
		self.assertEqual(log.stage, 1)

		row = self.reload(followup)
		self.assertEqual(row.current_stage, 1)
		self.assertEqual(row.state, engine.STATE_ACTIVE)
		self.assertGreater(frappe.utils.get_datetime(row.next_due), self.now)
		log_mock.assert_called()

	def test_a_replied_followup_is_never_swept(self):
		lead = self.make_lead()
		self.make_followup(lead, state=engine.STATE_REPLIED, next_due=self.now - timedelta(days=1))

		with self.sending() as message_mock:
			self.assertEqual(engine.sweep_due_followups(self.settings, self.stages), 0)

		message_mock.assert_not_called()

	# --- B2: the race guard ---

	def test_a_reply_between_selection_and_send_cancels_the_send(self):
		"""Regression B2: the row was selected as due, then the customer wrote."""
		lead = self.make_lead()
		followup = self.due_followup(lead)
		reply_time = self.now - timedelta(minutes=5)

		with self.sending(latest_incoming=reply_time) as message_mock:
			self.assertEqual(engine.sweep_due_followups(self.settings, self.stages), 0)

		message_mock.assert_not_called()
		row = self.reload(followup)
		self.assertEqual(row.state, engine.STATE_REPLIED)
		self.assertEqual(row.current_stage, 0)
		self.assertIsNone(row.next_due)

	def test_the_row_is_locked_and_re_read_before_every_send(self):
		"""Regression B2: the decision must use a locked, current read of the row."""
		lead = self.make_lead()
		followup = self.due_followup(lead)
		real_lock = engine.lock_followup

		with (
			self.sending(),
			patch.object(engine, "lock_followup", side_effect=real_lock) as lock_mock,
		):
			engine.sweep_due_followups(self.settings, self.stages)

		self.assertIn(followup.name, [call.args[0] for call in lock_mock.call_args_list])

	def test_a_state_change_between_selection_and_lock_stops_the_send(self):
		"""The locked re-read wins over the list the sweep was built from."""
		lead = self.make_lead()
		followup = self.due_followup(lead)
		real_lock = engine.lock_followup

		def lock_and_mutate(name):
			frappe.db.set_value(
				engine.FOLLOWUP_DOCTYPE,
				name,
				"state",
				engine.STATE_OPTED_OUT,
				update_modified=False,
			)
			return real_lock(name)

		with (
			self.sending() as message_mock,
			patch.object(engine, "lock_followup", side_effect=lock_and_mutate),
		):
			self.assertEqual(engine.sweep_due_followups(self.settings, self.stages), 0)

		message_mock.assert_not_called()
		self.assertEqual(self.reload(followup).state, engine.STATE_OPTED_OUT)

	# --- M-dedup-deadlock ---

	def test_a_spent_dedup_key_advances_instead_of_retrying(self):
		"""Regression M-dedup: a replayed stage must not collide every hour."""
		lead = self.make_lead()
		followup = self.due_followup(lead)

		with self.sending() as message_mock:
			self.assertEqual(engine.sweep_due_followups(self.settings, self.stages), 1)

			# Rewind the state machine as a crashed or replayed run would.
			frappe.db.set_value(
				engine.FOLLOWUP_DOCTYPE,
				followup.name,
				{"current_stage": 0, "next_due": self.now - timedelta(minutes=1)},
				update_modified=False,
			)
			with patch.object(frappe, "log_error") as log_mock:
				self.assertEqual(engine.sweep_due_followups(self.settings, self.stages), 0)

		self.assertEqual(message_mock.call_count, 1)
		self.assertEqual(frappe.db.count(engine.SEND_LOG_DOCTYPE, {"lead": lead.name}), 1)
		# Advanced, not left due: the row must not come back next hour.
		row = self.reload(followup)
		self.assertEqual(row.current_stage, 1)
		self.assertGreater(frappe.utils.get_datetime(row.next_due), self.now)
		log_mock.assert_called_once()

	def test_a_new_cycle_gets_fresh_dedup_keys(self):
		"""Regression M-dedup: a re-armed sequence must be able to send stage 1 again."""
		lead = self.make_lead()
		followup = self.due_followup(lead)

		with self.sending() as message_mock:
			self.assertEqual(engine.sweep_due_followups(self.settings, self.stages), 1)

			# The agent writes again: new cycle, stage counter back to zero.
			frappe.db.set_value(
				engine.FOLLOWUP_DOCTYPE,
				followup.name,
				{"cycle": 2, "current_stage": 0, "next_due": self.now - timedelta(minutes=1)},
				update_modified=False,
			)
			self.assertEqual(engine.sweep_due_followups(self.settings, self.stages), 1)

		self.assertEqual(message_mock.call_count, 2)
		keys = frappe.get_all(engine.SEND_LOG_DOCTYPE, filters={"lead": lead.name}, pluck="dedup_key")
		self.assertEqual(sorted(keys), [f"{lead.name}-cycle-1-stage-1", f"{lead.name}-cycle-2-stage-1"])

	def test_dedup_key_format(self):
		self.assertEqual(engine.dedup_key("LEAD-1", 3, 2), "LEAD-1-cycle-3-stage-2")
		self.assertEqual(engine.dedup_key("LEAD-1", 0, 1), "LEAD-1-cycle-1-stage-1")

	def test_daily_cap_stops_the_sweep(self):
		leads = [self.make_lead(f"Lead{index}", f"+91987654{3210 + index}") for index in range(3)]
		for lead in leads:
			self.due_followup(lead)

		settings = make_settings(daily_send_cap=2)
		with self.sending():
			self.assertEqual(engine.sweep_due_followups(settings, self.stages), 2)


class TestGuardrails(FollowupEngineTestCase):
	QUIET: ClassVar[dict] = {"quiet_hours_start": "21:00:00", "quiet_hours_end": "09:00:00"}

	def test_quiet_hours_defer_instead_of_cancel(self):
		# The clock is injected, and the row's own timestamps are derived from the
		# injected moment rather than from the wall clock. Deriving them from
		# `self.now` made the test pass before 23:00 and fail after it: the row was
		# not yet due relative to the frozen 23:00, so the sweep returned before it
		# ever reached the quiet-hours branch this test is about.
		settings = make_settings(**self.QUIET)
		late_evening = datetime.combine(self.now.date(), datetime.min.time()) + timedelta(hours=23)

		lead = self.make_lead()
		followup = self.make_followup(
			lead,
			next_due=late_evening - timedelta(minutes=1),
			last_agency_message=late_evening - timedelta(days=2),
		)

		with (
			self.sending() as message_mock,
			patch.object(frappe.utils, "now_datetime", return_value=late_evening),
		):
			sent = engine.process_one(followup.name, settings, self.stages)

		self.assertFalse(sent)
		message_mock.assert_not_called()

		row = self.reload(followup)
		self.assertEqual(row.state, engine.STATE_ACTIVE)
		self.assertEqual(
			frappe.utils.get_datetime(row.next_due),
			datetime.combine(late_evening.date() + timedelta(days=1), datetime.min.time())
			+ timedelta(hours=9),
		)

	def test_quiet_window_wraps_midnight(self):
		settings = make_settings(**self.QUIET)
		day = self.now.date()
		inside = datetime.combine(day, datetime.min.time()) + timedelta(hours=2)
		outside = datetime.combine(day, datetime.min.time()) + timedelta(hours=12)

		self.assertTrue(engine.in_quiet_hours(inside, settings))
		self.assertFalse(engine.in_quiet_hours(outside, settings))

	def test_equal_quiet_hours_disable_the_window(self):
		settings = make_settings(quiet_hours_start="09:00:00", quiet_hours_end="09:00:00")
		self.assertFalse(engine.in_quiet_hours(self.now, settings))

	# --- m-template-params / park instead of retry ---

	def test_a_missing_template_parks_the_row_instead_of_retrying(self):
		"""Regression: an unusable stage must stop coming back every hour."""
		lead = self.make_lead()
		followup = self.make_followup(
			lead,
			next_due=self.now - timedelta(minutes=1),
			last_agency_message=self.now - timedelta(days=2),
		)

		with (
			self.sending(template=None, problem="Template missing") as message_mock,
			patch.object(frappe, "log_error") as log_mock,
		):
			self.assertEqual(engine.sweep_due_followups(self.settings, self.stages), 0)
			# Second hour: the row is parked, so it is not even selected.
			self.assertEqual(engine.sweep_due_followups(self.settings, self.stages), 0)

		message_mock.assert_not_called()
		row = self.reload(followup)
		self.assertEqual(row.state, engine.STATE_STOPPED)
		self.assertEqual(row.blocked_reason, "Template missing")
		self.assertIsNone(row.next_due)
		self.assertEqual(frappe.db.count(engine.SEND_LOG_DOCTYPE, {"lead": lead.name}), 0)
		self.assertEqual(log_mock.call_count, 1)

	def test_saving_the_settings_unparks_blocked_rows(self):
		lead = self.make_lead()
		followup = self.make_followup(
			lead, state=engine.STATE_STOPPED, blocked_reason="Template missing", next_due=None
		)

		self.assertEqual(engine.unpark_blocked_followups(), 1)
		row = self.reload(followup)
		self.assertEqual(row.state, engine.STATE_ACTIVE)
		self.assertIsNone(row.blocked_reason)
		self.assertIsNotNone(row.next_due)

	def test_unapproved_template_is_refused(self):
		pending = frappe._dict({"name": "trip_followup", "status": "PENDING"})
		with template_lookup(pending):
			template, problem = engine.resolve_template(stage_row(1))
		self.assertIsNone(template)
		self.assertIn("PENDING", problem)

	def test_approved_template_is_accepted(self):
		with template_lookup(APPROVED_TEMPLATE):
			template, problem = engine.resolve_template(stage_row(1))
		self.assertIsNone(problem)
		self.assertEqual(template.name, "trip_followup")

	def test_a_stage_without_a_template_name_is_refused(self):
		with template_lookup(None):
			template, problem = engine.resolve_template(stage_row(1, template=""))
		self.assertIsNone(template)
		self.assertTrue(problem)

	def test_field_names_without_sample_values_is_refused(self):
		"""Regression m-template-params: frappe_whatsapp would send zero parameters."""
		broken = frappe._dict(
			{
				"name": "trip_followup",
				"status": "APPROVED",
				"field_names": "first_name,destination",
				"sample_values": "",
			}
		)
		with template_lookup(broken):
			template, problem = engine.resolve_template(stage_row(1))
		self.assertIsNone(template)
		self.assertIn("sample values", problem)

	def test_a_template_without_variables_yields_no_names(self):
		bare = frappe._dict({"name": "t", "status": "APPROVED", "field_names": "", "sample_values": ""})
		self.assertEqual(engine.template_variable_names(bare), [])

	def test_process_followups_does_nothing_while_disabled(self):
		with (
			patch.object(engine, "followups_installed", return_value=True),
			patch.object(engine, "get_settings", return_value=make_settings(enabled=0)),
			patch.object(engine, "enroll_conversations") as enroll_mock,
		):
			self.assertEqual(engine.process_followups(), 0)

		enroll_mock.assert_not_called()

	def test_process_followups_swallows_failures(self):
		with (
			patch.object(engine, "followups_installed", return_value=True),
			patch.object(engine, "get_settings", side_effect=RuntimeError("boom")),
			patch.object(frappe, "log_error") as log_mock,
		):
			self.assertEqual(engine.process_followups(), 0)

		log_mock.assert_called_once()


class TestRecipient(FollowupEngineTestCase):
	"""M-recipient: the destination is re-derived from the lead at send time."""

	def test_a_tampered_phone_field_is_not_used_as_the_target(self):
		"""Regression M-recipient: the stored phone is a cache, not an instruction."""
		lead = self.make_lead(mobile_no="+919876543210")
		followup = self.make_followup(lead)
		frappe.db.set_value(
			engine.FOLLOWUP_DOCTYPE,
			followup.name,
			"phone",
			"+15551234567",
			update_modified=False,
		)

		with patch.object(engine, "read_conversation", return_value=[]):
			recipient = engine.resolve_recipient(self.reload(followup))

		self.assertEqual(recipient, "+919876543210")
		self.assertEqual(self.reload(followup).phone, "+919876543210")

	def test_the_conversation_number_wins_when_the_lead_holds_it(self):
		lead = self.make_lead(mobile_no="+919876543210")
		lead.db_set("phone", "+919876543211", update_modified=False)
		followup = self.make_followup(lead)

		history = [frappe._dict({"type": "Incoming", "from": "919876543211", "to": ""})]
		with patch.object(engine, "read_conversation", return_value=history):
			self.assertEqual(engine.resolve_recipient(self.reload(followup)), "+919876543211")

	def test_a_number_the_lead_does_not_hold_is_refused(self):
		lead = self.make_lead(mobile_no="+919876543210")
		followup = self.make_followup(lead)

		history = [frappe._dict({"type": "Incoming", "from": "15551234567", "to": ""})]
		with patch.object(engine, "read_conversation", return_value=history):
			# Falls back to the lead's own number rather than the stranger's.
			self.assertEqual(engine.resolve_recipient(self.reload(followup)), "+919876543210")

	def test_a_lead_without_a_number_parks_the_row(self):
		lead = self.make_lead(mobile_no="")
		followup = self.make_followup(
			lead,
			next_due=self.now - timedelta(minutes=1),
			last_agency_message=self.now - timedelta(days=2),
		)

		with (
			patch.object(engine, "resolve_template", return_value=(APPROVED_TEMPLATE, None)),
			patch.object(engine, "get_latest_incoming", return_value=None),
			patch.object(engine, "read_conversation", return_value=[]),
			patch.object(engine, "create_template_message") as message_mock,
			patch.object(frappe, "log_error"),
		):
			self.assertEqual(engine.sweep_due_followups(self.settings, self.stages), 0)

		message_mock.assert_not_called()
		self.assertEqual(self.reload(followup).state, engine.STATE_STOPPED)

	def test_e164_rejects_an_unparseable_number(self):
		"""Regression m-recipient-thread: the raw-string fallback must not leak through."""
		self.assertEqual(engine.e164("not a number"), "")
		self.assertEqual(engine.e164(""), "")
		self.assertEqual(engine.e164("919876543210"), "+919876543210")


class TestMessageHooks(FollowupEngineTestCase):
	def test_customer_reply_stops_the_sequence(self):
		lead = self.make_lead()
		followup = self.make_followup(lead, next_due=self.now + timedelta(days=1))

		with patch.object(engine, "followups_installed", return_value=True):
			engine.handle_message_after_insert(
				fake_message(
					type="Incoming",
					message="Sounds good, let me think",
					reference_name=lead.name,
					creation=self.now,
				)
			)

		row = self.reload(followup)
		self.assertEqual(row.state, engine.STATE_REPLIED)
		self.assertIsNone(row.next_due)

	def test_stop_keyword_opts_the_lead_out_for_good(self):
		lead = self.make_lead()
		followup = self.make_followup(lead, next_due=self.now + timedelta(days=1))

		with patch.object(engine, "followups_installed", return_value=True):
			engine.handle_message_after_insert(
				fake_message(type="Incoming", message="STOP", reference_name=lead.name, creation=self.now)
			)

		row = self.reload(followup)
		self.assertEqual(row.state, engine.STATE_OPTED_OUT)
		self.assertIsNotNone(row.opted_out_at)
		self.assertIn("WM-TEST-0001", row.opted_out_source)

		# An agent writing again must not re-arm an opted-out sequence.
		with (
			patch.object(engine, "followups_installed", return_value=True),
			patch.object(engine, "get_settings", return_value=self.settings),
		):
			engine.handle_message_after_insert(
				fake_message(
					type="Outgoing", message="Any update?", reference_name=lead.name, creation=self.now
				)
			)

		self.assertEqual(self.reload(followup).state, engine.STATE_OPTED_OUT)

		# And enrolment never gives them a second row.
		with (
			patch.object(
				engine,
				"get_conversation_aggregates",
				return_value=[
					{
						"reference_doctype": "CRM Lead",
						"reference_name": lead.name,
						"last_incoming_at": None,
						"last_outgoing_at": self.now,
					}
				],
			),
			patch.object(engine, "read_conversation", return_value=[]),
		):
			self.assertEqual(engine.enroll_conversations(self.settings, self.stages), 0)

	# --- M-optout-race ---

	def test_a_later_reply_never_downgrades_an_opt_out(self):
		"""Regression M-optout-race: Opted Out is terminal, whatever arrives next."""
		lead = self.make_lead()
		followup = self.make_followup(lead, state=engine.STATE_OPTED_OUT)

		with patch.object(engine, "followups_installed", return_value=True):
			engine.handle_message_after_insert(
				fake_message(
					type="Incoming",
					message="actually, send me the price",
					reference_name=lead.name,
					creation=self.now,
				)
			)

		row = self.reload(followup)
		self.assertEqual(row.state, engine.STATE_OPTED_OUT)
		self.assertIsNotNone(row.last_customer_message)

	def test_the_incoming_hook_locks_the_row(self):
		"""Regression M-optout-race: the read-modify-write must hold a row lock."""
		lead = self.make_lead()
		followup = self.make_followup(lead)
		real_lock = engine.lock_followup

		with (
			patch.object(engine, "followups_installed", return_value=True),
			patch.object(engine, "lock_followup", side_effect=real_lock) as lock_mock,
		):
			engine.handle_message_after_insert(
				fake_message(type="Incoming", message="hi", reference_name=lead.name, creation=self.now)
			)

		lock_mock.assert_called_once_with(followup.name)

	# --- B3a ---

	def test_an_opt_out_without_a_row_is_still_recorded(self):
		"""Regression B3: a STOP before enrolment must not be discarded."""
		lead = self.make_lead()
		self.assertFalse(frappe.db.exists(engine.FOLLOWUP_DOCTYPE, {"lead": lead.name}))

		with patch.object(engine, "followups_installed", return_value=True):
			engine.handle_message_after_insert(
				fake_message(
					type="Incoming",
					message="unsubscribe",
					reference_name=lead.name,
					creation=self.now,
					**{"from": "919876543210"},
				)
			)

		row = frappe.db.get_value(
			engine.FOLLOWUP_DOCTYPE,
			{"lead": lead.name},
			["state", "phone", "opted_out_source"],
			as_dict=True,
		)
		self.assertEqual(row.state, engine.STATE_OPTED_OUT)
		self.assertEqual(row.phone, "+919876543210")
		self.assertIn("WM-TEST-0001", row.opted_out_source)

	def test_a_normal_message_without_a_row_creates_nothing(self):
		lead = self.make_lead()
		with patch.object(engine, "followups_installed", return_value=True):
			engine.handle_message_after_insert(
				fake_message(
					type="Incoming", message="hello there", reference_name=lead.name, creation=self.now
				)
			)
		self.assertFalse(frappe.db.exists(engine.FOLLOWUP_DOCTYPE, {"lead": lead.name}))

	# --- stop keyword matching ---

	def test_stop_keyword_matching_is_whole_word_only(self):
		self.assertTrue(engine.matches_stop_keyword("  Stop  "))
		self.assertTrue(engine.matches_stop_keyword("please opt out of this"))
		self.assertTrue(engine.matches_stop_keyword("<b>UNSUBSCRIBE</b>"))
		self.assertFalse(engine.matches_stop_keyword("we want non-stopover flights"))
		self.assertFalse(engine.matches_stop_keyword("stopping by tomorrow"))
		self.assertFalse(engine.matches_stop_keyword(""))

	def test_stop_keyword_matching_survives_unicode_tricks(self):
		"""Regression m-nfkc: full-width text must not slip past the filter."""
		self.assertTrue(engine.matches_stop_keyword("ＳＴＯＰ"))
		self.assertTrue(engine.matches_stop_keyword("ｕｎｓｕｂｓｃｒｉｂｅ please"))

	def test_agent_message_re_arms_a_finished_sequence_with_a_new_cycle(self):
		lead = self.make_lead()
		followup = self.make_followup(lead, state=engine.STATE_EXHAUSTED, current_stage=3, cycle=1)

		with (
			patch.object(engine, "followups_installed", return_value=True),
			patch.object(engine, "get_settings", return_value=self.settings),
		):
			engine.handle_message_after_insert(
				fake_message(
					type="Outgoing", message="New offer for you", reference_name=lead.name, creation=self.now
				)
			)

		row = self.reload(followup)
		self.assertEqual(row.state, engine.STATE_ACTIVE)
		self.assertEqual(row.current_stage, 0)
		self.assertEqual(row.cycle, 2)
		self.assertAlmostEqual(
			frappe.utils.get_datetime(row.next_due),
			self.now + timedelta(days=2),
			delta=timedelta(seconds=5),
		)

	def test_agent_message_re_arms_a_parked_row(self):
		lead = self.make_lead()
		followup = self.make_followup(lead, state=engine.STATE_STOPPED, blocked_reason="Template missing")

		with (
			patch.object(engine, "followups_installed", return_value=True),
			patch.object(engine, "get_settings", return_value=self.settings),
		):
			engine.handle_message_after_insert(
				fake_message(type="Outgoing", reference_name=lead.name, creation=self.now)
			)

		row = self.reload(followup)
		self.assertEqual(row.state, engine.STATE_ACTIVE)
		self.assertIsNone(row.blocked_reason)

	def test_an_engine_send_does_not_reset_the_clock(self):
		lead = self.make_lead()
		followup = self.make_followup(lead, next_due=self.now + timedelta(hours=1))

		with (
			patch.object(engine, "followups_installed", return_value=True),
			patch.object(engine, "get_settings", return_value=self.settings),
		):
			engine.handle_message_after_insert(
				fake_message(
					type="Outgoing",
					reference_name=lead.name,
					creation=self.now,
					flags=frappe._dict({"crm_followup_send": True}),
				)
			)

		row = self.reload(followup)
		self.assertIsNone(row.last_agency_message)
		self.assertAlmostEqual(
			frappe.utils.get_datetime(row.next_due),
			self.now + timedelta(hours=1),
			delta=timedelta(seconds=5),
		)

	def test_message_hook_never_raises(self):
		with (
			patch.object(engine, "followups_installed", return_value=True),
			patch.object(engine, "handle_incoming", side_effect=RuntimeError("boom")),
			patch.object(frappe, "log_error") as log_mock,
		):
			engine.handle_message_after_insert(fake_message(type="Incoming"))

		log_mock.assert_called_once()


class TestDraftApproval(FollowupEngineTestCase):
	def pending_row(self, lead, params=None):
		return self.make_followup(
			lead,
			state=engine.STATE_PENDING_APPROVAL,
			pending_stage=1,
			pending_params=json.dumps(params or {"1": "Ann", "2": "Bali"}),
		)

	def test_draft_mode_parks_the_stage_and_approval_sends_it(self):
		lead = self.make_lead()
		followup = self.make_followup(
			lead,
			next_due=self.now - timedelta(minutes=1),
			last_agency_message=self.now - timedelta(days=2),
		)
		settings = make_settings(send_mode=engine.SEND_MODE_DRAFT)

		with self.sending() as message_mock:
			self.assertEqual(engine.sweep_due_followups(settings, self.stages), 0)
			message_mock.assert_not_called()

			row = self.reload(followup)
			self.assertEqual(row.state, engine.STATE_PENDING_APPROVAL)
			self.assertEqual(row.pending_stage, 1)
			self.assertEqual(json.loads(row.pending_params), {"1": "Ann", "2": "Bali"})

			with patch.object(engine, "get_settings", return_value=settings):
				engine.approve_pending(followup.name)

			message_mock.assert_called_once()

		approved = self.reload(followup)
		self.assertEqual(approved.state, engine.STATE_ACTIVE)
		self.assertEqual(approved.current_stage, 1)
		self.assertEqual(approved.pending_stage, 0)
		self.assertEqual(frappe.db.count(engine.SEND_LOG_DOCTYPE, {"lead": lead.name}), 1)

	# --- M-approve-bypass ---

	def test_approval_refuses_while_the_sequence_is_disabled(self):
		lead = self.make_lead()
		followup = self.pending_row(lead)
		with (
			self.sending() as message_mock,
			patch.object(engine, "get_settings", return_value=make_settings(enabled=0)),
		):
			self.assertRaises(frappe.ValidationError, engine.approve_pending, followup.name)
		message_mock.assert_not_called()

	def test_approval_refuses_inside_quiet_hours(self):
		lead = self.make_lead()
		followup = self.pending_row(lead)
		late_evening = datetime.combine(self.now.date(), datetime.min.time()) + timedelta(hours=23)
		settings = make_settings(quiet_hours_start="21:00:00", quiet_hours_end="09:00:00")

		with (
			self.sending() as message_mock,
			patch.object(engine, "get_settings", return_value=settings),
			patch.object(frappe.utils, "now_datetime", return_value=late_evening),
		):
			self.assertRaises(frappe.ValidationError, engine.approve_pending, followup.name)

		message_mock.assert_not_called()
		self.assertEqual(self.reload(followup).state, engine.STATE_PENDING_APPROVAL)

	def test_approval_refuses_over_the_daily_cap(self):
		lead = self.make_lead()
		followup = self.pending_row(lead)
		settings = make_settings(daily_send_cap=1)

		frappe.get_doc(
			{
				"doctype": engine.SEND_LOG_DOCTYPE,
				"lead": lead.name,
				"stage": 9,
				"dedup_key": "already-used-today",
				"status": engine.SEND_CLAIMED,
				"sent_at": self.now,
			}
		).insert(ignore_permissions=True)

		with (
			self.sending() as message_mock,
			patch.object(engine, "get_settings", return_value=settings),
		):
			self.assertRaises(frappe.ValidationError, engine.approve_pending, followup.name)
		message_mock.assert_not_called()

	# --- M-pending-params ---

	def test_approval_re_sanitises_the_stored_draft(self):
		"""Regression M-pending-params: a stored value must not reach Meta unchecked."""
		lead = self.make_lead()
		followup = self.pending_row(lead, params={"1": "Ann", "2": "Bali see evil.example/pay now"})

		captured = {}

		def capture(followup_doc, template, params, recipient):
			captured.update(params)
			return frappe._dict({"name": "WM-1"})

		with (
			patch.object(engine, "resolve_template", return_value=(APPROVED_TEMPLATE, None)),
			patch.object(engine, "resolve_recipient", return_value="+919876543210"),
			patch.object(engine, "create_template_message", side_effect=capture),
			patch.object(engine, "get_settings", return_value=self.settings),
		):
			engine.approve_pending(followup.name)

		self.assertNotIn("evil.example", captured["2"])
		self.assertEqual(captured["1"], "Ann")

	def test_dismiss_drops_the_draft_and_schedules_the_next_stage(self):
		lead = self.make_lead()
		followup = self.pending_row(lead)

		with patch.object(engine, "get_settings", return_value=self.settings):
			engine.dismiss_pending(followup.name)

		row = self.reload(followup)
		self.assertEqual(row.state, engine.STATE_ACTIVE)
		self.assertEqual(row.current_stage, 1)
		self.assertEqual(row.pending_stage, 0)
		self.assertEqual(frappe.db.count(engine.SEND_LOG_DOCTYPE, {"lead": lead.name}), 0)

	def test_approving_a_followup_without_a_draft_is_refused(self):
		lead = self.make_lead()
		followup = self.make_followup(lead)

		with patch.object(engine, "get_settings", return_value=self.settings):
			self.assertRaises(frappe.ValidationError, engine.approve_pending, followup.name)

	# --- M-csrf ---

	def test_state_changing_endpoints_are_post_only(self):
		"""Regression M-csrf: a GET-able paid endpoint is a CSRF target."""
		for method in (engine.approve_pending, engine.dismiss_pending, engine.reopen_optout):
			self.assertIn(method, frappe.whitelisted, msg=f"{method.__name__} must be whitelisted")
			self.assertEqual(
				frappe.allowed_http_methods_for_whitelisted_func[method],
				["POST"],
				msg=f"{method.__name__} must be POST only",
			)


class TestOptOutReversal(FollowupEngineTestCase):
	"""M-webhook-optout: an unauthenticated webhook must not create an irreversible state."""

	def test_a_manager_can_reopen_an_opt_out_with_an_audit_trail(self):
		lead = self.make_lead()
		followup = self.make_followup(
			lead,
			state=engine.STATE_OPTED_OUT,
			opted_out_at=self.now,
			opted_out_source="WhatsApp message WM-1: STOP",
		)

		engine.reopen_optout(followup.name, reason="Customer called and asked us to continue")

		row = self.reload(followup)
		self.assertEqual(row.state, engine.STATE_REPLIED)
		self.assertIsNone(row.opted_out_at)
		self.assertIsNone(row.opted_out_source)

		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": engine.FOLLOWUP_DOCTYPE, "reference_name": followup.name},
			pluck="content",
		)
		self.assertEqual(len(comments), 1)
		self.assertIn("Customer called", comments[0])
		self.assertIn("WM-1", comments[0])

	def test_reopening_needs_a_reason(self):
		lead = self.make_lead()
		followup = self.make_followup(lead, state=engine.STATE_OPTED_OUT)
		self.assertRaises(frappe.ValidationError, engine.reopen_optout, followup.name, "   ")

	def test_reopening_a_row_that_is_not_opted_out_is_refused(self):
		lead = self.make_lead()
		followup = self.make_followup(lead)
		self.assertRaises(frappe.ValidationError, engine.reopen_optout, followup.name, "why not")


class TestPermissions(FollowupEngineTestCase):
	"""M-followup-perms and m-org-hierarchy."""

	def test_query_conditions_restrict_rows_to_visible_leads(self):
		"""Regression M-followup-perms: a sales user must not list every customer number."""
		with patch.object(
			engine,
			"get_lead_permission_query_conditions",
			return_value="`tabCRM Lead`.`lead_owner` = 'agent@example.com'",
		):
			condition = engine.get_followup_permission_query_conditions("agent@example.com")

		self.assertIn("`tabCRM WhatsApp Followup`.`lead` in", condition)
		self.assertIn("`tabCRM Lead`.`lead_owner`", condition)

	def test_query_conditions_are_empty_for_an_unrestricted_user(self):
		with patch.object(engine, "get_lead_permission_query_conditions", return_value=""):
			self.assertEqual(engine.get_followup_permission_query_conditions("Administrator"), "")

	def test_row_permission_delegates_to_the_lead(self):
		lead = self.make_lead()
		followup = self.make_followup(lead)

		with patch.object(engine, "has_lead_permission", return_value=False) as lead_mock:
			self.assertFalse(engine.has_followup_permission(self.reload(followup), "read", "someone"))
		self.assertEqual(lead_mock.call_args[0][0].name, lead.name)

		with patch.object(engine, "has_lead_permission", return_value=True):
			self.assertTrue(engine.has_followup_permission(self.reload(followup), "read", "someone"))

	def test_acting_on_a_followup_respects_the_lead_rule(self):
		"""Regression m-org-hierarchy: a manager outside the subtree is refused."""
		lead = self.make_lead()
		followup = self.make_followup(lead, state=engine.STATE_PENDING_APPROVAL, pending_stage=1)

		with patch.object(engine, "has_lead_permission", return_value=False):
			self.assertRaises(frappe.PermissionError, engine.get_pending_followup, followup.name)

	def test_sales_user_grant_is_read_only(self):
		"""Regression m-sales-mgr-delete / M-followup-perms: the granted flags are checked."""
		granted = []
		with (
			patch.object(engine, "add_permission"),
			patch.object(
				engine,
				"update_permission_property",
				side_effect=lambda dt, role, lvl, prop, val: granted.append((dt, role, prop, val)),
			),
			patch.object(frappe.db, "exists", return_value=True),
		):
			engine.add_followup_roles()

		def flag(doctype, role, prop):
			return dict(((d, r, p), v) for d, r, p, v in granted)[(doctype, role, prop)]

		self.assertEqual(flag(engine.FOLLOWUP_DOCTYPE, "Sales User", "write"), 0)
		self.assertEqual(flag(engine.FOLLOWUP_DOCTYPE, "Sales User", "export"), 0)
		self.assertEqual(flag(engine.FOLLOWUP_DOCTYPE, "Sales User", "report"), 0)
		self.assertEqual(flag(engine.FOLLOWUP_DOCTYPE, "Sales User", "read"), 1)
		# Deleting an outbox row would free a spent key and reset the daily cap.
		self.assertEqual(flag(engine.SEND_LOG_DOCTYPE, "Sales Manager", "delete"), 0)
		self.assertEqual(flag(engine.SEND_LOG_DOCTYPE, "Sales Manager", "write"), 1)


class TestParameters(FollowupEngineTestCase):
	def test_deterministic_parameters_come_from_the_lead(self):
		lead = self.make_lead()
		followup = self.make_followup(lead)
		params = engine.build_params(followup, stage_row(1), APPROVED_TEMPLATE, ["first_name", "destination"])
		self.assertEqual(params, {"1": "Ann", "2": "Bali"})

	def test_ai_failure_falls_back_to_deterministic_parameters(self):
		lead = self.make_lead()
		followup = self.make_followup(lead)

		with (
			patch.object(engine, "ai_params", side_effect=RuntimeError("provider down")),
			patch.object(frappe, "log_error") as log_mock,
		):
			params = engine.build_params(
				followup, stage_row(1, use_ai=1), APPROVED_TEMPLATE, ["first_name", "destination"]
			)

		self.assertEqual(params, {"1": "Ann", "2": "Bali"})
		log_mock.assert_called_once()

	def test_ai_values_are_used_when_the_model_answers(self):
		lead = self.make_lead()
		followup = self.make_followup(lead)

		with (
			patch("crm.ai.client.complete", return_value={"1": "Ann", "2": "your Bali trip"}),
			patch.object(engine, "get_conversation_excerpt", return_value=[]),
		):
			params = engine.build_params(
				followup, stage_row(1, use_ai=1), APPROVED_TEMPLATE, ["first_name", "destination"]
			)

		self.assertEqual(params, {"1": "Ann", "2": "your Bali trip"})

	def test_an_empty_ai_value_falls_back(self):
		lead = self.make_lead()
		followup = self.make_followup(lead)

		with (
			patch("crm.ai.client.complete", return_value={"1": "Ann", "2": "   "}),
			patch.object(engine, "get_conversation_excerpt", return_value=[]),
		):
			params = engine.build_params(
				followup, stage_row(1, use_ai=1), APPROVED_TEMPLATE, ["first_name", "destination"]
			)

		self.assertEqual(params, {"1": "Ann", "2": "Bali"})

	def test_a_template_without_variables_sends_no_parameters(self):
		lead = self.make_lead()
		followup = self.make_followup(lead)
		self.assertEqual(engine.build_params(followup, stage_row(1), APPROVED_TEMPLATE, []), {})

	def test_sanitize_collapses_whitespace_and_caps_length(self):
		self.assertEqual(engine.sanitize_param("  Ann\nMarie\t"), "Ann Marie")
		self.assertEqual(len(engine.sanitize_param("x" * 200)), engine.MAX_PARAM_LENGTH)

	def test_sanitize_drops_a_link_the_lead_data_does_not_contain(self):
		self.assertEqual(
			engine.sanitize_param("Book here https://evil.example/pay", {"destination": "Bali"}),
			"Book here",
		)

	def test_sanitize_drops_a_bare_domain(self):
		"""Regression m-url-regex: a link needs no scheme to be a link."""
		self.assertEqual(
			engine.sanitize_param("Pay at evil.com/x today", {"destination": "Bali"}),
			"Pay at today",
		)
		self.assertEqual(engine.sanitize_param("Visit EVIL.COM now", {"destination": "Bali"}), "Visit now")
		self.assertEqual(engine.sanitize_param("go to bit.ly/abc", {}), "go to")

	def test_sanitize_keeps_a_link_that_comes_from_the_lead(self):
		lead = {"website": "https://agency.example/bali"}
		self.assertEqual(
			engine.sanitize_param("See https://agency.example/bali", lead),
			"See https://agency.example/bali",
		)

	def test_sanitize_keeps_ordinary_prose(self):
		self.assertEqual(
			engine.sanitize_param("Bali, Indonesia. 2 travellers", {}), "Bali, Indonesia. 2 travellers"
		)

	def test_template_variable_names_prefer_field_names(self):
		self.assertEqual(engine.template_variable_names(APPROVED_TEMPLATE), ["first_name", "destination"])
		self.assertEqual(
			engine.template_variable_names(frappe._dict({"sample_values": "Ann, Bali"})),
			["Ann", "Bali"],
		)
		self.assertEqual(engine.template_variable_names(frappe._dict({})), [])


class TestStageConfiguration(FollowupEngineTestCase):
	def test_stages_are_returned_in_send_order(self):
		settings = make_settings(stages=[stage_row(3), stage_row(1), stage_row(2)])
		self.assertEqual([stage.stage_number for stage in engine.get_stages(settings)], [1, 2, 3])

	def test_silence_days_below_one_are_clamped(self):
		settings = make_settings(stages=[stage_row(1, silence_days=0)])
		self.assertEqual(engine.get_stages(settings)[0].silence_days, 1)

	def test_settings_renumber_stages_on_save(self):
		settings = frappe.get_doc(engine.SETTINGS_DOCTYPE)
		settings.stages = []
		for silence_days in (2, 3, 4):
			settings.append("stages", {"stage_number": 9, "silence_days": silence_days})
		settings.save(ignore_permissions=True)

		self.assertEqual([row.stage_number for row in settings.stages], [1, 2, 3])


def http_response(status_code=200, payload=None, text=""):
	response = MagicMock()
	response.status_code = status_code
	response.text = text or json.dumps(payload or {})
	response.json.return_value = payload if payload is not None else {}
	return response


def ai_settings(provider="Anthropic", model="claude-sonnet-5", limit=0, used=0, month=None):
	return frappe._dict(
		{
			"provider": provider,
			"model": model,
			"api_key": "sk-secret-value",
			"max_monthly_requests": limit,
			"requests_this_month": used,
			"usage_month": month or ai_client.current_month(),
		}
	)


class TestAIClient(FrappeTestCase):
	"""The provider-agnostic AI client. No request ever leaves the process."""

	def tearDown(self):
		frappe.db.rollback()

	def test_client_refuses_to_run_while_ai_is_disabled(self):
		# CRM AI Settings ships disabled, and this asserts the real guard on that
		# state. The state is FORCED rather than assumed: on a site where the
		# agency has already configured a key -- the demo site does -- the guard
		# would not fire and the assertion below would send a real, paid request
		# to the provider. `requests.post` is stubbed with a failure for the same
		# reason: if the guard ever regresses, the test must fail loudly instead
		# of quietly reaching the network.
		frappe.db.set_single_value(ai_client.SETTINGS_DOCTYPE, "enabled", 0)
		frappe.clear_document_cache(ai_client.SETTINGS_DOCTYPE, ai_client.SETTINGS_DOCTYPE)

		with patch.object(
			ai_client.requests, "post", side_effect=AssertionError("the disabled guard let a request out")
		):
			self.assertRaises(ai_client.AIConfigurationError, ai_client.complete, "hello")
			self.assertFalse(ai_client.is_configured())

	def test_anthropic_request_shape_and_text_extraction(self):
		payload = {
			"content": [
				{"type": "thinking", "thinking": "ignored"},
				{"type": "text", "text": "Bali in November"},
			],
			"stop_reason": "end_turn",
		}
		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings()),
			patch.object(ai_client.requests, "post", return_value=http_response(payload=payload)) as post,
		):
			answer = ai_client.complete("nudge", system="be brief", max_tokens=256)

		self.assertEqual(answer, "Bali in November")
		url, kwargs = post.call_args[0][0], post.call_args[1]
		self.assertEqual(url, ai_client.ANTHROPIC_URL)
		self.assertEqual(kwargs["headers"]["x-api-key"], "sk-secret-value")
		self.assertEqual(kwargs["headers"]["anthropic-version"], ai_client.ANTHROPIC_VERSION)
		self.assertEqual(kwargs["json"]["model"], "claude-sonnet-5")
		self.assertEqual(kwargs["json"]["max_tokens"], 256)
		self.assertEqual(kwargs["json"]["system"], "be brief")
		self.assertEqual(kwargs["json"]["messages"], [{"role": "user", "content": "nudge"}])

	def test_a_refusal_is_reported_as_an_error(self):
		payload = {"content": [{"type": "text", "text": "no"}], "stop_reason": "refusal"}
		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings()),
			patch.object(ai_client.requests, "post", return_value=http_response(payload=payload)),
		):
			self.assertRaises(ai_client.AIResponseError, ai_client.complete, "nudge")

	def test_openrouter_uses_the_chat_completions_shape(self):
		payload = {"choices": [{"message": {"content": "hello"}}]}
		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings(provider="OpenRouter")),
			patch.object(ai_client.requests, "post", return_value=http_response(payload=payload)) as post,
		):
			self.assertEqual(ai_client.complete("hi"), "hello")

		self.assertEqual(post.call_args[0][0], ai_client.OPENROUTER_URL)
		self.assertEqual(post.call_args[1]["headers"]["authorization"], "Bearer sk-secret-value")

	def test_gemini_uses_googles_openai_compatible_endpoint(self):
		"""A free Google AI Studio key must work through the chat-completions path."""
		payload = {"choices": [{"message": {"content": "Bali in November"}}]}
		with (
			patch.object(
				ai_client,
				"load_settings",
				return_value=ai_settings(provider="Gemini", model="gemini-2.0-flash"),
			),
			patch.object(ai_client.requests, "post", return_value=http_response(payload=payload)) as post,
		):
			answer = ai_client.complete("nudge", system="be brief", max_tokens=256)

		self.assertEqual(answer, "Bali in November")
		url, kwargs = post.call_args[0][0], post.call_args[1]
		self.assertEqual(url, ai_client.GEMINI_URL)
		self.assertEqual(url, "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions")
		self.assertEqual(kwargs["headers"]["authorization"], "Bearer sk-secret-value")
		self.assertEqual(kwargs["json"]["model"], "gemini-2.0-flash")
		self.assertEqual(kwargs["json"]["max_tokens"], 256)
		self.assertEqual(
			kwargs["json"]["messages"],
			[{"role": "system", "content": "be brief"}, {"role": "user", "content": "nudge"}],
		)
		# The key rides in the header, never in the url, so it cannot be logged.
		self.assertNotIn("sk-secret-value", url)
		self.assertEqual(kwargs["timeout"], ai_client.REQUEST_TIMEOUT)

	def test_gemini_json_mode_retries_once_and_counts_both_requests(self):
		bad = http_response(payload={"choices": [{"message": {"content": "sure, here you go"}}]})
		good = http_response(payload={"choices": [{"message": {"content": '{"1": "Ann"}'}}]})
		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings(provider="Gemini")),
			patch.object(ai_client.requests, "post", side_effect=[bad, good]) as post,
			patch.object(ai_client, "record_usage") as usage_mock,
		):
			answer = ai_client.complete("fill", json_schema={"type": "object"})

		self.assertEqual(answer, {"1": "Ann"})
		self.assertEqual(post.call_count, 2)
		self.assertEqual(post.call_args_list[0][0][0], ai_client.GEMINI_URL)
		self.assertIn("JSON", post.call_args[1]["json"]["messages"][0]["content"])
		self.assertIn("not valid JSON", post.call_args[1]["json"]["messages"][1]["content"])
		# Both provider calls are charged against the monthly budget.
		self.assertEqual(usage_mock.call_count, 2)

	def test_gemini_budget_increment_is_recorded(self):
		payload = {"choices": [{"message": {"content": "ok"}}]}
		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings(provider="Gemini", used=7)),
			patch.object(ai_client.requests, "post", return_value=http_response(payload=payload)),
			patch.object(ai_client, "record_usage") as usage_mock,
		):
			ai_client.complete("hi")

		usage_mock.assert_called_once_with(ai_client.current_month(), 7)

	def test_gemini_over_budget_never_reaches_the_network(self):
		with (
			patch.object(
				ai_client,
				"load_settings",
				return_value=ai_settings(provider="Gemini", limit=3, used=3),
			),
			patch.object(ai_client.requests, "post") as post,
		):
			self.assertRaises(ai_client.AIConfigurationError, ai_client.complete, "hi")
		post.assert_not_called()

	def test_every_chat_completions_provider_has_an_endpoint(self):
		options = frappe.get_meta("CRM AI Settings").get_field("provider").options.split("\n")
		self.assertIn("Gemini", options)
		for provider in options:
			if provider == "Anthropic":
				continue
			self.assertIn(provider, ai_client.CHAT_COMPLETIONS_URLS)
			self.assertIn(provider, ai_client.SUGGESTED_MODELS)

	def test_openai_retries_once_with_max_completion_tokens(self):
		refusal = http_response(
			status_code=400,
			payload={"error": {"message": "Unsupported parameter: use 'max_completion_tokens'"}},
		)
		success = http_response(payload={"choices": [{"message": {"content": "ok"}}]})
		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings(provider="OpenAI")),
			patch.object(ai_client.requests, "post", side_effect=[refusal, success]) as post,
			patch.object(frappe, "log_error"),
		):
			self.assertEqual(ai_client.complete("hi", max_tokens=64), "ok")

		self.assertEqual(post.call_count, 2)
		self.assertNotIn("max_tokens", post.call_args[1]["json"])
		self.assertEqual(post.call_args[1]["json"]["max_completion_tokens"], 64)

	def test_json_mode_parses_a_fenced_object(self):
		payload = {"content": [{"type": "text", "text": '```json\n{"1": "Ann"}\n```'}]}
		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings()),
			patch.object(ai_client.requests, "post", return_value=http_response(payload=payload)) as post,
		):
			answer = ai_client.complete("fill", json_schema={"type": "object"})

		self.assertEqual(answer, {"1": "Ann"})
		self.assertIn("JSON", post.call_args[1]["json"]["system"])

	def test_json_mode_retries_once_then_raises(self):
		bad = http_response(payload={"content": [{"type": "text", "text": "not json"}]})
		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings()),
			patch.object(ai_client.requests, "post", side_effect=[bad, bad]) as post,
		):
			self.assertRaises(
				ai_client.AIResponseError, ai_client.complete, "fill", json_schema={"type": "object"}
			)

		self.assertEqual(post.call_count, 2)

	def test_json_mode_recovers_on_the_retry(self):
		bad = http_response(payload={"content": [{"type": "text", "text": "sorry"}]})
		good = http_response(payload={"content": [{"type": "text", "text": '{"1": "Ann"}'}]})
		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings()),
			patch.object(ai_client.requests, "post", side_effect=[bad, good]) as post,
		):
			self.assertEqual(ai_client.complete("fill", json_schema={"type": "object"}), {"1": "Ann"})

		self.assertIn("not valid JSON", post.call_args[1]["json"]["messages"][0]["content"])

	def test_the_monthly_budget_is_enforced(self):
		settings = ai_settings(limit=5, used=5)
		with patch.object(ai_client, "load_settings", return_value=settings):
			self.assertRaises(ai_client.AIConfigurationError, ai_client.complete, "hi")

	def test_a_new_month_resets_the_counter(self):
		settings = ai_settings(limit=5, used=5, month="1999-01")
		self.assertEqual(ai_client.check_quota(settings), (ai_client.current_month(), 0))

	def test_the_shipped_budget_is_not_unlimited(self):
		"""Regression m-ai-budget: an unlimited default is an unbounded bill."""
		default = frappe.get_meta("CRM AI Settings").get_field("max_monthly_requests").default
		self.assertTrue(frappe.utils.cint(default) > 0)

	def test_the_api_key_never_reaches_the_error_log(self):
		failure = http_response(status_code=401, text='{"error": "invalid key"}')
		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings()),
			patch.object(ai_client.requests, "post", return_value=failure),
			patch.object(frappe, "log_error") as log_mock,
		):
			self.assertRaises(ai_client.AIResponseError, ai_client.complete, "hi")

		logged = " ".join(str(part) for part in log_mock.call_args[0])
		self.assertNotIn("sk-secret-value", logged)
		self.assertIn("401", logged)

	def test_a_network_failure_is_reported_without_the_key(self):
		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings()),
			patch.object(ai_client.requests, "post", side_effect=ai_client.requests.ConnectionError("down")),
			patch.object(frappe, "log_error") as log_mock,
		):
			self.assertRaises(ai_client.AIResponseError, ai_client.complete, "hi")

		logged = " ".join(str(part) for part in log_mock.call_args[0])
		self.assertNotIn("sk-secret-value", logged)

	def test_parse_json_rejects_anything_that_is_not_an_object(self):
		self.assertRaises(ai_client.AIResponseError, ai_client.parse_json, "[1, 2]")
		self.assertRaises(ai_client.AIResponseError, ai_client.parse_json, "hello")
		self.assertEqual(ai_client.parse_json('  {"a": 1}  '), {"a": 1})
