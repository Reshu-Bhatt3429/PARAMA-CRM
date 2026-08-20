# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the channel-agnostic sequence core (`crm.sequences.core`).

The core is driven here by a FAKE adapter that keeps its rows in a dictionary.
That is the point of the tests, not a shortcut: if the core needs a WhatsApp
doctype, a template or a phone number to run, then it is not channel-agnostic
and the email adapter in a later stage will not be able to use it.

The WhatsApp adapter's own behaviour is covered by `test_followup_engine`, which
still exercises every rule through the real engine. What is checked here about
that adapter is only that it leaves no method of the interface unimplemented.
"""

from datetime import datetime, timedelta
from typing import ClassVar
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.sequences import core
from crm.sequences.whatsapp import WhatsAppFollowupAdapter


def stage(silence_days=2):
	return frappe._dict({"silence_days": silence_days})


def settings(**overrides):
	base = frappe._dict(
		{
			"quiet_hours_start": "00:00:00",
			"quiet_hours_end": "00:00:00",
			"send_mode": "Auto-send",
			"ignore_older_than_days": 14,
		}
	)
	base.update(overrides)
	return base


class FakeAdapter(core.ChannelAdapter):
	"""One in-memory channel. Every call it receives is journalled, in order."""

	log_prefix = "CRM test channel"
	row_label = "Test row"
	provider_label = "the test provider"

	def __init__(self, rows=None, conversations=None):
		self.rows = rows or {}
		self.conversations = conversations or []
		self.journal = []
		self.claims = {}
		self.sent = []
		self.budget = 99
		self.draft_mode = False
		self.content = ("content", None)
		self.destination = "someone@example.com"
		self.inbound_at = None
		self.outbound_newer = True
		self.send_error = None
		self.enrol_error = None

	# --- helpers the tests read ---

	def add_row(self, name, **fields):
		row = frappe._dict(
			{
				"name": name,
				"state": "Active",
				"next_due": frappe.utils.now_datetime() - timedelta(minutes=1),
				"current_stage": 0,
				"cycle": 1,
			}
		)
		row.update(fields)
		self.rows[name] = row
		return row

	# --- transaction seam ---

	def commit(self):
		self.journal.append("commit")

	def rollback(self):
		self.journal.append("rollback")

	def lock(self, name):
		self.journal.append(f"lock:{name}")
		return self.rows[name]

	def row_name(self, row):
		return row.name

	# --- enrolment ---

	def known_keys(self):
		return set(self.rows)

	def list_conversations(self):
		return self.conversations

	def conversation_key(self, conversation):
		return conversation.get("key")

	def enrolment_cutoff(self, settings):
		return core.enrolment_cutoff(settings, 14)

	def stop_keywords(self, settings):
		return ["stop"]

	def enroll_one(self, key, conversation, stages, cutoff, keywords):
		if self.enrol_error and key == self.enrol_error:
			raise RuntimeError("boom")
		self.add_row(key)
		return True

	# --- sweep ---

	def due_rows(self):
		return [name for name, row in self.rows.items() if row.state == "Active"]

	def budget_left(self, settings):
		return self.budget > 0

	# --- decision ---

	def is_active(self, row):
		return row.state == "Active"

	def is_terminal(self, row):
		return row.state == "Opted Out"

	def due_at(self, row):
		return row.next_due

	def current_stage(self, row):
		return row.current_stage

	def latest_inbound_at(self, row):
		return self.inbound_at

	def outbound_is_newer(self, row, latest_inbound):
		return self.outbound_newer

	def mark_replied(self, row, latest_inbound):
		row.state = "Replied"
		row.next_due = None
		self.journal.append("replied")

	def defer(self, row, until):
		row.next_due = until
		self.journal.append("deferred")

	def mark_exhausted(self, row):
		row.state = "Exhausted"
		row.next_due = None
		self.journal.append("exhausted")

	def park(self, row, reason):
		row.state = "Stopped"
		row.blocked_reason = reason
		self.journal.append(f"parked:{reason}")

	# --- content ---

	def resolve_content(self, stage):
		return self.content

	def resolve_destination(self, row):
		return self.destination

	def no_destination_reason(self):
		return "no address"

	def build_payload(self, row, stage, content):
		return {"1": "value"}

	def is_draft_mode(self, settings):
		return self.draft_mode

	def hold_for_approval(self, row, stage_number, payload):
		row.state = "Pending Approval"
		row.pending_stage = stage_number
		self.journal.append("held")

	# --- send ---

	def claim_key(self, row, stage_number):
		return core.sequence_key(row.name, row.cycle, stage_number)

	def claim(self, row, stage_number, key, now):
		if key in self.claims:
			raise core.AlreadyClaimed(key)
		self.claims[key] = {"status": "Claimed", "stage": stage_number}
		self.journal.append(f"claim:{key}")
		return frappe._dict({"key": key})

	def send(self, row, content, payload, destination):
		self.journal.append("send")
		if self.send_error:
			raise self.send_error
		self.sent.append((row.name, destination, payload))
		return frappe._dict({"name": "EXT-1"})

	def record_success(self, claim, reference):
		self.claims[claim.key]["status"] = "Sent"
		self.claims[claim.key]["reference"] = reference.name

	def record_failure(self, claim):
		self.claims[claim.key]["status"] = "Failed"

	def apply_advance(self, row, stage_number, exhausted, next_due, sent_at):
		row.current_stage = stage_number
		row.state = "Exhausted" if exhausted else "Active"
		row.next_due = next_due
		if sent_at:
			row.last_outbound = sent_at
		self.journal.append(f"advance:{stage_number}:{row.state}")


class SequenceCoreTestCase(FrappeTestCase):
	def setUp(self):
		self.adapter = FakeAdapter()
		self.stages = [stage(2), stage(3), stage(4)]
		self.now = frappe.utils.now_datetime()

	def tearDown(self):
		frappe.db.rollback()


class TestEnrolment(SequenceCoreTestCase):
	def test_every_unknown_conversation_is_enrolled_once(self):
		self.adapter.conversations = [{"key": "A"}, {"key": "B"}, {"key": "A"}]

		created = core.enroll(self.adapter, settings(), self.stages)

		self.assertEqual(created, 2)
		self.assertEqual(sorted(self.adapter.rows), ["A", "B"])

	def test_a_conversation_that_already_has_a_row_is_skipped(self):
		self.adapter.add_row("A")
		self.adapter.conversations = [{"key": "A"}]

		self.assertEqual(core.enroll(self.adapter, settings(), self.stages), 0)

	def test_a_conversation_without_a_key_is_skipped(self):
		self.adapter.conversations = [{"key": None}, {"key": ""}]

		self.assertEqual(core.enroll(self.adapter, settings(), self.stages), 0)

	def test_one_failing_enrolment_does_not_cost_the_others_their_run(self):
		"""The savepoint is the guarantee: one bad row, not one lost hour."""
		self.adapter.conversations = [{"key": "bad"}, {"key": "good"}]
		self.adapter.enrol_error = "bad"

		with patch.object(frappe, "log_error") as log_mock:
			created = core.enroll(self.adapter, settings(), self.stages)

		self.assertEqual(created, 1)
		self.assertEqual(sorted(self.adapter.rows), ["good"])
		log_mock.assert_called_once()

	def test_enrolment_commits_once_at_the_end(self):
		self.adapter.conversations = [{"key": "A"}]
		core.enroll(self.adapter, settings(), self.stages)
		self.assertEqual(self.adapter.journal.count("commit"), 1)

	def test_an_unstored_idle_bound_falls_back_to_the_default(self):
		"""A Single field added after the first save reads back as None."""
		bare = settings()
		del bare["ignore_older_than_days"]

		cutoff = core.enrolment_cutoff(bare, 14)

		self.assertIsNotNone(cutoff)
		self.assertAlmostEqual(cutoff, self.now - timedelta(days=14), delta=timedelta(seconds=30))

	def test_an_explicit_zero_idle_bound_means_no_bound(self):
		self.assertIsNone(core.enrolment_cutoff(settings(ignore_older_than_days=0), 14))


class TestSweep(SequenceCoreTestCase):
	def test_every_due_row_is_served(self):
		self.adapter.add_row("A")
		self.adapter.add_row("B")

		self.assertEqual(core.sweep(self.adapter, settings(), self.stages), 2)
		self.assertEqual(len(self.adapter.sent), 2)

	def test_an_empty_sweep_touches_nothing(self):
		self.assertEqual(core.sweep(self.adapter, settings(), self.stages), 0)
		self.assertEqual(self.adapter.journal, [])

	def test_the_budget_stops_the_sweep_between_rows(self):
		for name in ("A", "B", "C"):
			self.adapter.add_row(name)
		self.adapter.budget = 0

		self.assertEqual(core.sweep(self.adapter, settings(), self.stages), 0)
		self.assertEqual(self.adapter.sent, [])

	def test_one_row_that_throws_is_rolled_back_and_logged(self):
		"""One row's failure must not roll back another row's committed send."""
		self.adapter.add_row("A")
		self.adapter.add_row("B")
		real_lock = self.adapter.lock

		def explode(name):
			if name == "A":
				raise RuntimeError("boom")
			return real_lock(name)

		with (
			patch.object(self.adapter, "lock", side_effect=explode),
			patch.object(frappe, "log_error") as log_mock,
		):
			sent = core.sweep(self.adapter, settings(), self.stages)

		self.assertEqual(sent, 1)
		self.assertIn("rollback", self.adapter.journal)
		log_mock.assert_called_once()


class TestOneRow(SequenceCoreTestCase):
	def test_a_row_that_is_not_active_is_left_alone(self):
		self.adapter.add_row("A", state="Replied")

		self.assertFalse(core.process_row(self.adapter, "A", settings(), self.stages))
		self.assertEqual(self.adapter.sent, [])

	def test_a_row_that_is_not_due_yet_is_left_alone(self):
		self.adapter.add_row("A", next_due=self.now + timedelta(hours=1))

		self.assertFalse(core.process_row(self.adapter, "A", settings(), self.stages))
		self.assertEqual(self.adapter.sent, [])

	def test_a_row_without_a_due_date_is_left_alone(self):
		self.adapter.add_row("A", next_due=None)

		self.assertFalse(core.process_row(self.adapter, "A", settings(), self.stages))
		self.assertEqual(self.adapter.sent, [])

	def test_an_inbound_message_since_the_last_send_stops_the_stage(self):
		row = self.adapter.add_row("A")
		self.adapter.inbound_at = self.now
		self.adapter.outbound_newer = False

		self.assertFalse(core.process_row(self.adapter, "A", settings(), self.stages))
		self.assertEqual(row.state, "Replied")
		self.assertIsNone(row.next_due)
		self.assertEqual(self.adapter.sent, [])

	def test_quiet_hours_defer_the_row_instead_of_cancelling_it(self):
		row = self.adapter.add_row("A")
		quiet = settings(quiet_hours_start="21:00:00", quiet_hours_end="09:00:00")
		late_evening = datetime.combine(self.now.date(), datetime.min.time()) + timedelta(hours=23)
		row.next_due = late_evening - timedelta(minutes=1)

		with patch.object(frappe.utils, "now_datetime", return_value=late_evening):
			self.assertFalse(core.process_row(self.adapter, "A", quiet, self.stages))

		self.assertEqual(row.state, "Active")
		self.assertEqual(
			row.next_due,
			datetime.combine(late_evening.date() + timedelta(days=1), datetime.min.time())
			+ timedelta(hours=9),
		)
		self.assertEqual(self.adapter.sent, [])


class TestQuietHours(SequenceCoreTestCase):
	QUIET: ClassVar[dict] = {"quiet_hours_start": "21:00:00", "quiet_hours_end": "09:00:00"}

	def moment(self, hours):
		return datetime.combine(self.now.date(), datetime.min.time()) + timedelta(hours=hours)

	def test_the_window_wraps_midnight(self):
		quiet = settings(**self.QUIET)
		self.assertTrue(core.in_quiet_hours(self.moment(23), quiet))
		self.assertTrue(core.in_quiet_hours(self.moment(2), quiet))
		self.assertFalse(core.in_quiet_hours(self.moment(12), quiet))

	def test_a_window_that_does_not_wrap_is_read_the_plain_way(self):
		quiet = settings(quiet_hours_start="09:00:00", quiet_hours_end="17:00:00")
		self.assertTrue(core.in_quiet_hours(self.moment(12), quiet))
		self.assertFalse(core.in_quiet_hours(self.moment(20), quiet))

	def test_equal_bounds_disable_the_window(self):
		self.assertFalse(
			core.in_quiet_hours(self.moment(3), settings(quiet_hours_start="09:00", quiet_hours_end="09:00"))
		)

	def test_the_window_end_is_always_in_the_future(self):
		quiet = settings(**self.QUIET)
		self.assertEqual(core.quiet_hours_end_after(self.moment(23), quiet), self.moment(24 + 9))
		self.assertEqual(core.quiet_hours_end_after(self.moment(2), quiet), self.moment(9))

	def test_an_unreadable_time_disables_the_window(self):
		self.assertIsNone(core.to_time("not a time"))
		self.assertIsNone(core.to_time(""))
		self.assertFalse(core.in_quiet_hours(self.moment(3), settings(quiet_hours_start="nonsense")))


class TestStageSelection(SequenceCoreTestCase):
	def test_a_terminal_row_never_sends(self):
		row = self.adapter.add_row("A", state="Opted Out")

		self.assertFalse(core.send_stage(self.adapter, row, settings(), self.stages, 1, self.now))
		self.assertEqual(self.adapter.sent, [])

	def test_a_stage_past_the_last_one_exhausts_the_sequence(self):
		row = self.adapter.add_row("A")

		self.assertFalse(core.send_stage(self.adapter, row, settings(), self.stages, 4, self.now))
		self.assertEqual(row.state, "Exhausted")
		self.assertEqual(self.adapter.sent, [])

	def test_unsendable_content_parks_the_row(self):
		row = self.adapter.add_row("A")
		self.adapter.content = (None, "template is not approved")

		self.assertFalse(core.send_stage(self.adapter, row, settings(), self.stages, 1, self.now))
		self.assertEqual(row.state, "Stopped")
		self.assertEqual(row.blocked_reason, "template is not approved")
		self.assertEqual(self.adapter.sent, [])

	def test_a_missing_destination_parks_the_row(self):
		row = self.adapter.add_row("A")
		self.adapter.destination = ""

		self.assertFalse(core.send_stage(self.adapter, row, settings(), self.stages, 1, self.now))
		self.assertEqual(row.state, "Stopped")
		self.assertEqual(row.blocked_reason, "no address")

	def test_draft_mode_holds_the_stage_and_sends_nothing(self):
		row = self.adapter.add_row("A")
		self.adapter.draft_mode = True

		self.assertFalse(core.send_stage(self.adapter, row, settings(), self.stages, 1, self.now))
		self.assertEqual(row.state, "Pending Approval")
		self.assertEqual(row.pending_stage, 1)
		self.assertEqual(self.adapter.sent, [])
		self.assertEqual(self.adapter.claims, {})


class TestAtMostOnce(SequenceCoreTestCase):
	"""The ordering contract. These are the tests the whole module exists for."""

	def deliver(self, row, stage_number=1):
		return core.deliver(
			self.adapter,
			row,
			stage_number,
			"content",
			{"1": "v"},
			self.stages,
			"someone@example.com",
			self.now,
		)

	def test_the_key_is_committed_before_the_external_call(self):
		row = self.adapter.add_row("A")

		self.assertTrue(self.deliver(row))

		journal = self.adapter.journal
		self.assertLess(
			journal.index("commit"),
			journal.index("send"),
			msg=f"the claim must commit before the send; journal was {journal}",
		)
		self.assertLess(journal.index("claim:A-cycle-1-stage-1"), journal.index("commit"))

	def test_a_successful_send_is_recorded_and_the_row_advances(self):
		row = self.adapter.add_row("A")

		self.assertTrue(self.deliver(row))

		self.assertEqual(self.adapter.claims["A-cycle-1-stage-1"]["status"], "Sent")
		self.assertEqual(self.adapter.claims["A-cycle-1-stage-1"]["reference"], "EXT-1")
		self.assertEqual(row.current_stage, 1)
		self.assertEqual(row.state, "Active")
		self.assertEqual(row.last_outbound, self.now)

	def test_a_failed_send_consumes_the_stage_and_records_the_failure(self):
		"""A failure must not retry the same stage for ever: we cannot know."""
		row = self.adapter.add_row("A")
		self.adapter.send_error = RuntimeError("provider down")

		with patch.object(frappe, "log_error") as log_mock:
			self.assertFalse(self.deliver(row))

		self.assertEqual(self.adapter.claims["A-cycle-1-stage-1"]["status"], "Failed")
		self.assertEqual(row.current_stage, 1)
		self.assertEqual(row.state, "Active")
		self.assertIn("rollback", self.adapter.journal)
		log_mock.assert_called_once()

	def test_a_spent_key_advances_instead_of_retrying(self):
		"""Regression shape: a replayed stage must not collide every run."""
		row = self.adapter.add_row("A")
		self.adapter.claims["A-cycle-1-stage-1"] = {"status": "Claimed", "stage": 1}

		with patch.object(frappe, "log_error") as log_mock:
			self.assertFalse(self.deliver(row))

		self.assertEqual(self.adapter.sent, [])
		self.assertNotIn("send", self.adapter.journal)
		self.assertEqual(row.current_stage, 1)
		log_mock.assert_called_once()

	def test_a_new_cycle_produces_a_new_key(self):
		row = self.adapter.add_row("A")
		self.assertTrue(self.deliver(row))

		row.cycle = 2
		row.current_stage = 0
		self.assertTrue(self.deliver(row))

		self.assertEqual(
			sorted(self.adapter.claims),
			["A-cycle-1-stage-1", "A-cycle-2-stage-1"],
		)

	def test_the_key_format(self):
		self.assertEqual(core.sequence_key("ROW-1", 3, 2), "ROW-1-cycle-3-stage-2")
		self.assertEqual(core.sequence_key("ROW-1", 0, 1), "ROW-1-cycle-1-stage-1")
		self.assertEqual(core.sequence_key("ROW-1", None, 1), "ROW-1-cycle-1-stage-1")


class TestAdvance(SequenceCoreTestCase):
	def test_a_middle_stage_schedules_the_next_one(self):
		row = self.adapter.add_row("A")

		core.advance(self.adapter, row, 1, self.stages, self.now)

		self.assertEqual(row.state, "Active")
		self.assertEqual(row.current_stage, 1)
		# The wait before stage 2 is stage 2's own silence period, not stage 1's.
		self.assertAlmostEqual(
			frappe.utils.get_datetime(row.next_due),
			self.now + timedelta(days=3),
			delta=timedelta(seconds=5),
		)

	def test_the_last_stage_exhausts_the_sequence(self):
		row = self.adapter.add_row("A")

		core.advance(self.adapter, row, 3, self.stages, self.now)

		self.assertEqual(row.state, "Exhausted")
		self.assertIsNone(row.next_due)

	def test_a_send_stamps_the_row_and_a_failure_does_not(self):
		sent_row = self.adapter.add_row("A")
		failed_row = self.adapter.add_row("B")

		core.advance(self.adapter, sent_row, 1, self.stages, self.now, sent_at=self.now)
		core.advance(self.adapter, failed_row, 1, self.stages, self.now)

		self.assertEqual(sent_row.last_outbound, self.now)
		self.assertIsNone(failed_row.get("last_outbound"))


class TestAdapterContract(FrappeTestCase):
	"""What a channel must implement, and what the WhatsApp channel does."""

	def required_methods(self):
		return [
			name
			for name, member in vars(core.ChannelAdapter).items()
			if callable(member) and not name.startswith("_")
		]

	def test_the_interface_refuses_to_run_half_implemented(self):
		"""An adapter that forgot a method must fail loudly, not silently."""

		class Empty(core.ChannelAdapter):
			pass

		empty = Empty()
		for name in self.required_methods():
			with self.assertRaises(NotImplementedError, msg=f"{name} should be required"):
				getattr(empty, name)(*[None] * argument_count(getattr(core.ChannelAdapter, name)))

	def test_the_whatsapp_adapter_implements_every_method(self):
		"""The migration is complete only when nothing falls back to the base."""
		missing = [
			name
			for name in self.required_methods()
			if getattr(WhatsAppFollowupAdapter, name) is getattr(core.ChannelAdapter, name)
		]
		self.assertEqual(missing, [], msg=f"WhatsApp adapter does not implement {missing}")

	def test_the_whatsapp_adapter_reads_the_engine_at_call_time(self):
		"""Late binding is what keeps the engine's seams patchable and testable."""
		from crm.api import followup_engine as engine

		adapter = engine.get_channel_adapter()
		self.assertIs(adapter.engine, engine)

		with patch.object(engine, "commit") as commit_mock:
			adapter.commit()
		commit_mock.assert_called_once()

	def test_the_engine_holds_one_adapter(self):
		from crm.api import followup_engine as engine

		self.assertIs(engine.get_channel_adapter(), engine.get_channel_adapter())


def argument_count(method) -> int:
	"""How many arguments after `self` a bound call of this method needs."""
	return method.__code__.co_argcount - 1
