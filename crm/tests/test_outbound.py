# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the outbound-job state machine (spec F2).

Nothing here sends. `crm.outbound` never talks to a provider by design -- it
calls a registered channel adapter -- and Stage 1 registers none, so a job run
without a test adapter fails with "no adapter" instead of reaching anything.
Every test that needs a delivery registers a recorder and unregisters it in
`tearDown`, so a leaked adapter cannot follow one test into the next.

`outbound.commit` and `outbound.rollback` are neutralised for the same reason
they are in `crm/tests/test_followup_engine.py`: a real commit would escape the
test's rollback. They are replaced with recorders, which is also how the commit
ORDERING that makes the send at-most-once is asserted -- the claim has to be
durable before the adapter is called, or a crash would re-send.

Endpoint authorization (master spec §3): this module adds NO whitelisted
endpoint. Every entry point is either the scheduler (`process_scheduled_jobs`,
gated on `outbound_engine_enabled`, default OFF) or a server-side caller inside
a later stage. `execute_job` re-reads the job's `owner_user` and refuses to run
for a disabled user AT EXECUTION TIME, not at enqueue time.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm import outbound, suppression


class OutboundTestCase(FrappeTestCase):
	def setUp(self):
		# `journal` records every commit, rollback and send, in order.
		self.journal = []
		self.sent = []

		self.patches = [
			patch.object(outbound, "commit", side_effect=lambda: self.journal.append("commit")),
			patch.object(outbound, "rollback", side_effect=lambda: self.journal.append("rollback")),
		]
		for patcher in self.patches:
			patcher.start()

	def tearDown(self):
		for patcher in self.patches:
			patcher.stop()
		outbound.unregister_adapter(outbound.CHANNEL_EMAIL)
		outbound.unregister_adapter(outbound.CHANNEL_WHATSAPP)
		frappe.db.rollback()

	# --- fixtures ---

	def recording_adapter(self, result=None, fail=False):
		def adapter(job, recipient):
			self.journal.append(f"send:{recipient.address}")
			self.sent.append(recipient.address)
			if fail:
				raise RuntimeError("provider refused")
			return result or {"email_queue": "EQ-0001", "message_id": "<m1@crm.test>"}

		return adapter

	def make_job(self, recipients=("ann@example.com",), key=None, **fields):
		return outbound.create_job(
			job_type="Test",
			channel=outbound.CHANNEL_EMAIL,
			idempotency_key=key or frappe.generate_hash(length=12),
			recipients=list(recipients),
			**fields,
		)

	def state_of(self, job_name):
		return frappe.db.get_value(outbound.JOB_DOCTYPE, job_name, "state")

	def recipients_of(self, job_name):
		return frappe.get_all(
			outbound.RECIPIENT_DOCTYPE,
			filters={"job": job_name},
			fields=["name", "address", "state"],
			order_by="address asc",
		)


class TestJobCreation(OutboundTestCase):
	def test_job_starts_in_draft(self):
		job = self.make_job()
		self.assertEqual(job.state, outbound.JOB_DRAFT)

	def test_recipients_are_normalised(self):
		job = self.make_job(recipients=["  Ann@Example.COM "])
		rows = self.recipients_of(job.name)
		self.assertEqual([r.address for r in rows], ["ann@example.com"])

	def test_duplicate_recipients_collapse_to_one_row(self):
		job = self.make_job(recipients=["ann@example.com", "ANN@example.com", "Ann Lee <ann@example.com>"])
		self.assertEqual(len(self.recipients_of(job.name)), 1)
		self.assertEqual(job.recipient_count, 1)

	def test_unparsable_recipients_are_dropped(self):
		job = self.make_job(recipients=["ann@example.com", "junk", ""])
		self.assertEqual([r.address for r in self.recipients_of(job.name)], ["ann@example.com"])

	def test_the_same_idempotency_key_returns_the_same_job(self):
		first = self.make_job(key="send-later:CRM-LEAD-1:1")
		second = self.make_job(key="send-later:CRM-LEAD-1:1", recipients=["other@example.com"])

		self.assertEqual(first.name, second.name)
		self.assertEqual(frappe.db.count(outbound.JOB_DOCTYPE, {"name": first.name}), 1)
		# The second call added nothing: the job it got back is the first one.
		self.assertEqual([r.address for r in self.recipients_of(first.name)], ["ann@example.com"])

	def test_unknown_channel_is_refused(self):
		self.assertRaises(
			frappe.ValidationError,
			outbound.create_job,
			job_type="Test",
			channel="Carrier Pigeon",
			idempotency_key="x",
			recipients=["ann@example.com"],
		)

	def test_recipient_key_is_built_from_the_normalised_address(self):
		job = self.make_job(recipients=["Ann@Example.com"])
		row = self.recipients_of(job.name)[0]
		key = frappe.db.get_value(outbound.RECIPIENT_DOCTYPE, row.name, "idempotency_key")
		self.assertEqual(key, f"{job.name}:Email:ann@example.com")


class TestStateMachine(OutboundTestCase):
	def test_draft_to_scheduled(self):
		job = self.make_job()
		self.assertTrue(outbound.schedule_job(job.name, frappe.utils.now_datetime()))
		self.assertEqual(self.state_of(job.name), outbound.JOB_SCHEDULED)

	def test_repeating_a_transition_is_not_an_error(self):
		job = self.make_job()
		outbound.schedule_job(job.name)
		self.assertFalse(outbound.transition_job(job.name, outbound.JOB_SCHEDULED))
		self.assertEqual(self.state_of(job.name), outbound.JOB_SCHEDULED)

	def test_scheduled_cannot_jump_to_sent(self):
		job = self.make_job()
		outbound.schedule_job(job.name)
		self.assertRaises(outbound.InvalidTransition, outbound.transition_job, job.name, outbound.JOB_SENT)
		self.assertEqual(self.state_of(job.name), outbound.JOB_SCHEDULED)

	def test_cancel_before_claim_is_allowed(self):
		job = self.make_job()
		outbound.schedule_job(job.name)
		self.assertTrue(outbound.cancel_job(job.name, "user changed their mind"))
		self.assertEqual(self.state_of(job.name), outbound.JOB_CANCELLED)

	def test_cancel_closes_the_pending_recipients(self):
		job = self.make_job(recipients=["ann@example.com", "bob@example.com"])
		outbound.schedule_job(job.name)
		outbound.cancel_job(job.name, "stopped")

		states = {r.state for r in self.recipients_of(job.name)}
		self.assertEqual(states, {outbound.RECIPIENT_CANCELLED})

	def test_cancel_after_claim_is_refused(self):
		"""The cancellation cutoff. Past Claimed the message may already be gone."""
		job = self.make_job()
		outbound.schedule_job(job.name)
		self.assertTrue(outbound.claim_job(job.name))

		self.assertRaises(outbound.InvalidTransition, outbound.cancel_job, job.name, "too late")
		self.assertEqual(self.state_of(job.name), outbound.JOB_CLAIMED)

	def test_cancel_after_sent_is_refused(self):
		job = self.make_job()
		outbound.schedule_job(job.name)
		outbound.claim_job(job.name)
		outbound.transition_job(job.name, outbound.JOB_QUEUED)
		outbound.transition_job(job.name, outbound.JOB_SENT)

		self.assertRaises(outbound.InvalidTransition, outbound.cancel_job, job.name, "too late")

	def test_recipient_cannot_go_backwards(self):
		job = self.make_job()
		row = self.recipients_of(job.name)[0]
		outbound.transition_recipient(row.name, outbound.RECIPIENT_CLAIMED)
		self.assertRaises(
			outbound.InvalidTransition,
			outbound.transition_recipient,
			row.name,
			outbound.RECIPIENT_PENDING,
		)


class TestClaiming(OutboundTestCase):
	def test_a_second_claim_of_the_same_job_loses(self):
		"""Two workers reach one due job. Exactly one may own it."""
		job = self.make_job()
		outbound.schedule_job(job.name)

		self.assertTrue(outbound.claim_job(job.name))
		self.assertFalse(outbound.claim_job(job.name))
		self.assertEqual(self.state_of(job.name), outbound.JOB_CLAIMED)

	def test_a_cancelled_job_cannot_be_claimed(self):
		job = self.make_job()
		outbound.schedule_job(job.name)
		outbound.cancel_job(job.name, "stopped")

		self.assertFalse(outbound.claim_job(job.name))
		self.assertEqual(self.state_of(job.name), outbound.JOB_CANCELLED)

	def test_claim_commits_before_returning(self):
		"""The commit is what releases the row lock the loser is waiting on."""
		job = self.make_job()
		outbound.schedule_job(job.name)
		self.journal.clear()

		outbound.claim_job(job.name)
		self.assertEqual(self.journal, ["commit"])


class TestDelivery(OutboundTestCase):
	def test_a_job_with_no_adapter_fails_and_sends_nothing(self):
		"""Stage 1 registers no adapter, so this is the shipped behaviour."""
		job = self.make_job()
		outbound.schedule_job(job.name)
		outbound.claim_job(job.name)

		result = outbound.execute_job(job.name)

		self.assertTrue(result["no_adapter"])
		self.assertEqual(self.state_of(job.name), outbound.JOB_FAILED)
		self.assertEqual(self.sent, [])
		self.assertIn("No send adapter", frappe.db.get_value(outbound.JOB_DOCTYPE, job.name, "last_error"))

	def test_a_delivered_recipient_is_queued_not_sent(self):
		"""'Sent' belongs to the Email Queue, not to us handing the message over."""
		outbound.register_adapter(outbound.CHANNEL_EMAIL, self.recording_adapter())
		job = self.make_job()
		outbound.schedule_job(job.name)
		outbound.claim_job(job.name)

		outbound.execute_job(job.name)

		row = self.recipients_of(job.name)[0]
		self.assertEqual(row.state, outbound.RECIPIENT_QUEUED)
		stored = frappe.db.get_value(
			outbound.RECIPIENT_DOCTYPE, row.name, ["email_queue", "message_id"], as_dict=True
		)
		self.assertEqual(stored.email_queue, "EQ-0001")
		self.assertEqual(stored.message_id, "<m1@crm.test>")

	def test_the_claim_commits_before_the_send(self):
		"""The at-most-once ordering. A crash after the claim must not re-send."""
		outbound.register_adapter(outbound.CHANNEL_EMAIL, self.recording_adapter())
		job = self.make_job()
		outbound.schedule_job(job.name)
		outbound.claim_job(job.name)
		self.journal.clear()

		outbound.execute_job(job.name)

		send_at = self.journal.index("send:ann@example.com")
		self.assertIn("commit", self.journal[:send_at])

	def test_running_a_job_twice_sends_once(self):
		"""Double-claim cannot double-send: the second run finds no Pending row."""
		outbound.register_adapter(outbound.CHANNEL_EMAIL, self.recording_adapter())
		job = self.make_job(recipients=["ann@example.com", "bob@example.com"])
		outbound.schedule_job(job.name)
		outbound.claim_job(job.name)

		outbound.execute_job(job.name)
		first = list(self.sent)

		# A retried worker re-runs the same job. The job is no longer Claimed and
		# every recipient key is spent.
		outbound.execute_job(job.name)

		self.assertEqual(sorted(first), ["ann@example.com", "bob@example.com"])
		self.assertEqual(self.sent, first)

	def test_a_recipient_already_claimed_is_never_sent_twice(self):
		"""Simulates the second of two concurrent workers reaching one recipient."""
		outbound.register_adapter(outbound.CHANNEL_EMAIL, self.recording_adapter())
		job = self.make_job()
		row = self.recipients_of(job.name)[0]

		# Worker A got here first and claimed the row.
		outbound.transition_recipient(row.name, outbound.RECIPIENT_CLAIMED)

		outcome = outbound.deliver_recipient(job, row.name, self.recording_adapter())
		self.assertEqual(outcome, "skipped")
		self.assertEqual(self.sent, [])

	def test_a_suppressed_recipient_is_held_back(self):
		outbound.register_adapter(outbound.CHANNEL_EMAIL, self.recording_adapter())
		suppression.suppress("Email", "ann@example.com", source="test")

		job = self.make_job(recipients=["ann@example.com", "bob@example.com"])
		outbound.schedule_job(job.name)
		outbound.claim_job(job.name)
		counts = outbound.execute_job(job.name)

		self.assertEqual(self.sent, ["bob@example.com"])
		self.assertEqual(counts["suppressed"], 1)
		states = {r.address: r.state for r in self.recipients_of(job.name)}
		self.assertEqual(states["ann@example.com"], outbound.RECIPIENT_SUPPRESSED)

	def test_suppression_recorded_after_scheduling_still_stops_the_send(self):
		"""The check runs at send time, inside the lock -- not at enqueue time."""
		outbound.register_adapter(outbound.CHANNEL_EMAIL, self.recording_adapter())
		job = self.make_job()
		outbound.schedule_job(job.name)
		outbound.claim_job(job.name)

		suppression.suppress("Email", "ann@example.com", source="opted out while queued")
		outbound.execute_job(job.name)

		self.assertEqual(self.sent, [])

	def test_an_adapter_failure_marks_the_recipient_failed_and_spends_the_key(self):
		outbound.register_adapter(outbound.CHANNEL_EMAIL, self.recording_adapter(fail=True))
		job = self.make_job()
		outbound.schedule_job(job.name)
		outbound.claim_job(job.name)

		counts = outbound.execute_job(job.name)

		self.assertEqual(counts["failed"], 1)
		row = self.recipients_of(job.name)[0]
		self.assertEqual(row.state, outbound.RECIPIENT_FAILED)
		self.assertEqual(self.state_of(job.name), outbound.JOB_FAILED)

	def test_a_disabled_sender_stops_the_job_at_execution_time(self):
		outbound.register_adapter(outbound.CHANNEL_EMAIL, self.recording_adapter())
		job = self.make_job()
		outbound.schedule_job(job.name)
		outbound.claim_job(job.name)

		with patch.object(outbound, "sender_is_active", return_value=False):
			result = outbound.execute_job(job.name)

		self.assertTrue(result["sender_disabled"])
		self.assertEqual(self.sent, [])
		self.assertEqual(self.state_of(job.name), outbound.JOB_FAILED)

	def test_an_unclaimed_job_is_not_executed(self):
		outbound.register_adapter(outbound.CHANNEL_EMAIL, self.recording_adapter())
		job = self.make_job()
		outbound.schedule_job(job.name)

		result = outbound.execute_job(job.name)

		self.assertTrue(result["skipped"])
		self.assertEqual(self.sent, [])


class TestScheduler(OutboundTestCase):
	def test_the_sweep_does_nothing_while_the_flag_is_off(self):
		outbound.register_adapter(outbound.CHANNEL_EMAIL, self.recording_adapter())
		job = self.make_job()
		outbound.schedule_job(job.name, frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-1))

		with patch.object(outbound, "is_enabled", return_value=False) as flag:
			self.assertEqual(outbound.process_scheduled_jobs(), 0)

		flag.assert_called_once_with(outbound.FLAG_OUTBOUND_ENGINE)
		self.assertEqual(self.state_of(job.name), outbound.JOB_SCHEDULED)
		self.assertEqual(self.sent, [])

	def test_the_shipped_flag_default_is_off(self):
		self.assertFalse(frappe.db.get_single_value("FCRM Settings", "outbound_engine_enabled"))

	def test_a_future_job_is_not_claimed(self):
		outbound.register_adapter(outbound.CHANNEL_EMAIL, self.recording_adapter())
		job = self.make_job()
		outbound.schedule_job(job.name, frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=2))

		with patch.object(outbound, "is_enabled", return_value=True):
			outbound.process_scheduled_jobs()

		self.assertEqual(self.state_of(job.name), outbound.JOB_SCHEDULED)
		self.assertEqual(self.sent, [])

	def test_a_due_job_is_claimed_and_run(self):
		outbound.register_adapter(outbound.CHANNEL_EMAIL, self.recording_adapter())
		job = self.make_job()
		outbound.schedule_job(job.name, frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-1))

		with patch.object(outbound, "is_enabled", return_value=True):
			claimed = outbound.process_scheduled_jobs()

		self.assertEqual(claimed, 1)
		self.assertEqual(self.sent, ["ann@example.com"])
		self.assertEqual(self.state_of(job.name), outbound.JOB_SENT)

	def test_the_sweep_never_raises(self):
		with patch.object(outbound, "is_enabled", side_effect=RuntimeError("boom")):
			self.assertEqual(outbound.process_scheduled_jobs(), 0)


class TestEmailQueueCorrelation(OutboundTestCase):
	"""The queue is stood in for. No Email Queue row is created, so nothing sends."""

	def make_queued_recipient(self):
		job = self.make_job()
		row = self.recipients_of(job.name)[0]
		outbound.transition_recipient(row.name, outbound.RECIPIENT_CLAIMED)
		outbound.transition_recipient(
			row.name, outbound.RECIPIENT_QUEUED, email_queue=frappe.generate_hash(length=10)
		)
		return job, row

	def test_a_sent_queue_row_moves_the_recipient_to_sent(self):
		job, row = self.make_queued_recipient()
		with patch.object(outbound, "read_queue_status", return_value="Sent"):
			updated = outbound.refresh_delivery_states(job.name)

		self.assertEqual(updated, 1)
		self.assertEqual(
			frappe.db.get_value(outbound.RECIPIENT_DOCTYPE, row.name, "state"), outbound.RECIPIENT_SENT
		)

	def test_an_errored_queue_row_moves_the_recipient_to_failed(self):
		job, row = self.make_queued_recipient()
		with patch.object(outbound, "read_queue_status", return_value="Error"):
			outbound.refresh_delivery_states(job.name)

		self.assertEqual(
			frappe.db.get_value(outbound.RECIPIENT_DOCTYPE, row.name, "state"), outbound.RECIPIENT_FAILED
		)

	def test_a_still_queued_row_is_left_alone(self):
		job, row = self.make_queued_recipient()
		with patch.object(outbound, "read_queue_status", return_value="Not Sent"):
			self.assertEqual(outbound.refresh_delivery_states(job.name), 0)

		self.assertEqual(
			frappe.db.get_value(outbound.RECIPIENT_DOCTYPE, row.name, "state"), outbound.RECIPIENT_QUEUED
		)


class TestReplyMatching(OutboundTestCase):
	def test_message_ids_are_extracted_from_a_header(self):
		self.assertEqual(outbound.extract_message_ids("<a@crm.test>"), ["a@crm.test"])
		self.assertEqual(
			outbound.extract_message_ids("<a@crm.test> <b@crm.test>"), ["a@crm.test", "b@crm.test"]
		)
		self.assertEqual(outbound.extract_message_ids(""), [])
		self.assertEqual(outbound.extract_message_ids(None), [])

	def test_a_reply_is_matched_whichever_spelling_was_stored(self):
		"""Adapters store the bare id or the bracketed one. Both must match."""
		for stored in ("a@crm.test", "<a@crm.test>"):
			with self.subTest(stored=stored):
				job = self.make_job(key=frappe.generate_hash(length=12))
				row = self.recipients_of(job.name)[0]
				frappe.db.set_value(
					outbound.RECIPIENT_DOCTYPE, row.name, "message_id", stored, update_modified=False
				)

				match = outbound.match_reply(in_reply_to="<a@crm.test>")
				self.assertIsNotNone(match)
				self.assertEqual(match["name"], row.name)
				frappe.db.set_value(
					outbound.RECIPIENT_DOCTYPE, row.name, "message_id", None, update_modified=False
				)

	def test_a_reply_is_matched_from_the_references_chain(self):
		job = self.make_job()
		row = self.recipients_of(job.name)[0]
		frappe.db.set_value(
			outbound.RECIPIENT_DOCTYPE, row.name, "message_id", "<a@crm.test>", update_modified=False
		)

		match = outbound.match_reply(references="<z@other.test> <a@crm.test>")
		self.assertEqual(match["name"], row.name)

	def test_an_unrelated_reply_matches_nothing(self):
		self.make_job()
		self.assertIsNone(outbound.match_reply(in_reply_to="<nothing@crm.test>"))

	def test_a_subject_line_is_not_a_match(self):
		"""Subject matching is not a fallback and must never become one."""
		job = self.make_job(subject="Re: your Bali quote")
		row = self.recipients_of(job.name)[0]
		frappe.db.set_value(
			outbound.RECIPIENT_DOCTYPE, row.name, "message_id", "<a@crm.test>", update_modified=False
		)

		self.assertIsNone(outbound.match_reply(in_reply_to="Re: your Bali quote"))

	def test_record_reply_stamps_the_row(self):
		job = self.make_job()
		row = self.recipients_of(job.name)[0]

		outbound.record_reply(row.name, "<a@crm.test>")

		stored = frappe.db.get_value(
			outbound.RECIPIENT_DOCTYPE, row.name, ["in_reply_to", "replied_at"], as_dict=True
		)
		self.assertEqual(stored.in_reply_to, "<a@crm.test>")
		self.assertIsNotNone(stored.replied_at)
