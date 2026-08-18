# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the automation guards (F4) and the atomic counter they share.

Nothing consumes these yet -- the workflow rules that will are a Stage 5
feature. They are tested now, on their own, because each one exists to stop a
specific way a rule engine hurts a customer, and a guard that is only exercised
by the feature it guards is a guard nobody has checked.

`reserve_daily_slot` is field-agnostic: Stage 5's rule doctype supplies its own
counter and day columns. The tests drive it against `CRM Outbound Job`, whose
`sent_count` and `sender_timezone` columns stand in for them, because the point
under test is the SQL, not the schema.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm import automation_context as context
from crm import counters, outbound


class TestDepthCeiling(FrappeTestCase):
	def tearDown(self):
		# The depth lives on `frappe.local`, which outlives one test method.
		if hasattr(frappe.local, context.DEPTH_ATTRIBUTE):
			delattr(frappe.local, context.DEPTH_ATTRIBUTE)

	def test_depth_starts_at_zero_and_counts_up(self):
		self.assertEqual(context.current_depth(), 0)

		with context.execution_depth() as first:
			self.assertEqual(first, 1)
			with context.execution_depth() as second:
				self.assertEqual(second, 2)
				self.assertEqual(context.current_depth(), 2)

		self.assertEqual(context.current_depth(), 0)

	def test_the_ceiling_stops_a_runaway_chain(self):
		"""A rule that writes a field that triggers a rule that writes it back."""
		with context.execution_depth(limit=2), context.execution_depth(limit=2):
			self.assertRaises(context.DepthExceeded, context.execution_depth(limit=2).__enter__)

		self.assertEqual(context.current_depth(), 0)

	def test_an_action_that_raises_still_restores_the_depth(self):
		"""A leaked depth would refuse every later rule in the same request."""
		with self.assertRaises(RuntimeError), context.execution_depth():
			self.assertEqual(context.current_depth(), 1)
			raise RuntimeError("the rule failed")

		self.assertEqual(context.current_depth(), 0)

	def test_a_ceiling_of_zero_refuses_everything(self):
		with self.assertRaises(context.DepthExceeded):
			with context.execution_depth(limit=0):
				pass

		self.assertEqual(context.current_depth(), 0)


class TestTransitionDetection(FrappeTestCase):
	"""`doc.get_doc_before_save()` is the only honest answer to "did it change?"."""

	def make_deal_like(self, before, after):
		doc = frappe._dict({"status": after})
		doc.get_doc_before_save = lambda: frappe._dict({"status": before}) if before is not None else None
		return doc

	def test_a_field_that_did_not_change_is_not_a_transition(self):
		"""Regression shape: the customer gets the same email on every later save."""
		doc = self.make_deal_like("Won", "Won")

		self.assertFalse(context.has_changed(doc, "status"))
		self.assertFalse(context.is_transition(doc, "status", "Won"))

	def test_a_real_move_into_the_target_value_is_a_transition(self):
		doc = self.make_deal_like("Negotiation", "Won")

		self.assertTrue(context.has_changed(doc, "status"))
		self.assertTrue(context.is_transition(doc, "status", "Won"))
		self.assertEqual(context.previous_value(doc, "status"), "Negotiation")

	def test_a_move_to_another_value_is_not_the_transition_asked_about(self):
		doc = self.make_deal_like("Negotiation", "Lost")

		self.assertFalse(context.is_transition(doc, "status", "Won"))

	def test_an_insert_counts_as_a_transition_into_its_first_value(self):
		"""A deal created already won must still fire the won rule, once."""
		doc = self.make_deal_like(None, "Won")

		self.assertTrue(context.has_changed(doc, "status"))
		self.assertTrue(context.is_transition(doc, "status", "Won"))
		self.assertIsNone(context.previous_value(doc, "status"))

	def test_the_previous_value_can_be_required_too(self):
		doc = self.make_deal_like("Negotiation", "Won")

		self.assertTrue(context.is_transition(doc, "status", "Won", from_value="Negotiation"))
		self.assertFalse(context.is_transition(doc, "status", "Won", from_value="Qualification"))


class TestExecutionKey(FrappeTestCase):
	def test_the_same_execution_produces_the_same_key(self):
		first = context.execution_key("RULE-1", "CRM Deal", "DEAL-1", "2026-08-18 10:00:00", "send_email")
		second = context.execution_key("RULE-1", "CRM Deal", "DEAL-1", "2026-08-18 10:00:00", "send_email")

		self.assertEqual(first, second)

	def test_every_part_changes_the_key(self):
		base = ("RULE-1", "CRM Deal", "DEAL-1", "v1", "send_email")
		keys = {context.execution_key(*base)}

		for index in range(len(base)):
			changed = list(base)
			changed[index] = "other"
			keys.add(context.execution_key(*changed))

		self.assertEqual(len(keys), len(base) + 1)

	def test_a_later_version_of_the_same_document_may_run_again(self):
		"""The source version is what separates a repeat from a retry."""
		first = context.execution_key("RULE-1", "CRM Deal", "DEAL-1", "v1", "notify")
		second = context.execution_key("RULE-1", "CRM Deal", "DEAL-1", "v2", "notify")

		self.assertNotEqual(first, second)

	def test_a_long_key_is_digested_rather_than_truncated(self):
		"""Truncation would make two different executions collide silently."""
		long_name = "D" * 200
		key = context.execution_key("RULE-1", "CRM Deal", long_name, "v1", "notify")
		neighbour = context.execution_key("RULE-1", "CRM Deal", long_name + "X", "v1", "notify")

		self.assertLessEqual(len(key), context.MAX_KEY_LENGTH)
		self.assertTrue(key.startswith("sha256:"))
		self.assertNotEqual(key, neighbour)


class TestAfterCommit(FrappeTestCase):
	def test_a_side_effect_is_queued_for_after_the_commit(self):
		"""A job enqueued inside the transaction races the transaction."""
		with patch.object(frappe, "enqueue") as enqueue_mock:
			context.queue_after_commit("crm.tests.test_automation_context.noop", rule="RULE-1")

		kwargs = enqueue_mock.call_args[1]
		self.assertTrue(kwargs["enqueue_after_commit"])
		self.assertEqual(kwargs["rule"], "RULE-1")
		self.assertEqual(enqueue_mock.call_args[0][0], "crm.tests.test_automation_context.noop")


class TestDailyCapReservation(FrappeTestCase):
	"""The atomic reservation, on a real row, through the real statement."""

	COUNT_FIELD = "sent_count"
	DAY_FIELD = "sender_timezone"

	def setUp(self):
		self.job = outbound.create_job(
			job_type="test",
			channel=outbound.CHANNEL_EMAIL,
			idempotency_key=f"cap-{frappe.generate_hash(length=8)}",
			recipients=[],
		)
		self.today = frappe.utils.nowdate()

	def tearDown(self):
		frappe.db.rollback()

	def reserve(self, cap, day=None):
		return counters.reserve_daily_slot(
			outbound.JOB_DOCTYPE,
			self.job.name,
			cap,
			self.COUNT_FIELD,
			self.DAY_FIELD,
			day=day or self.today,
		)

	def counted(self):
		return frappe.db.get_value(outbound.JOB_DOCTYPE, self.job.name, self.COUNT_FIELD)

	def test_the_cap_is_spent_exactly_once_per_slot(self):
		self.assertTrue(self.reserve(2))
		self.assertEqual(self.counted(), 1)

		self.assertTrue(self.reserve(2))
		self.assertEqual(self.counted(), 2)

		# The third caller is refused, and refused without changing the count.
		self.assertFalse(self.reserve(2))
		self.assertEqual(self.counted(), 2)

	def test_a_new_day_resets_the_counter_in_the_same_statement(self):
		"""A separate reset is a window in which two workers each reset it."""
		self.assertTrue(self.reserve(1))
		self.assertFalse(self.reserve(1))

		tomorrow = frappe.utils.add_days(self.today, 1)
		self.assertTrue(self.reserve(1, day=tomorrow))
		self.assertEqual(self.counted(), 1)
		self.assertEqual(frappe.db.get_value(outbound.JOB_DOCTYPE, self.job.name, self.DAY_FIELD), tomorrow)

	def test_a_cap_of_zero_means_unlimited(self):
		for _index in range(5):
			self.assertTrue(self.reserve(0))

		self.assertEqual(self.counted(), 5)

	def test_a_row_that_does_not_exist_reserves_nothing(self):
		self.assertFalse(
			counters.reserve_daily_slot(
				outbound.JOB_DOCTYPE, "no-such-job", 5, self.COUNT_FIELD, self.DAY_FIELD
			)
		)

	def test_a_field_name_that_is_not_a_field_is_refused(self):
		"""A field name reaches SQL as an identifier, where binding cannot help."""
		for bad in ("sent_count; drop table x", "`sent_count`", "not_a_field", ""):
			with self.assertRaises(frappe.ValidationError, msg=bad):
				counters.reserve_daily_slot(outbound.JOB_DOCTYPE, self.job.name, 5, bad, self.DAY_FIELD)

	def test_rows_affected_reports_the_last_statement(self):
		self.assertTrue(self.reserve(5))
		self.assertEqual(counters.rows_affected(), 1)

		self.assertFalse(self.reserve(1))
		self.assertEqual(counters.rows_affected(), 0)


def noop(**kwargs):
	"""A queue target the after-commit test can name without importing anything."""
	return kwargs
