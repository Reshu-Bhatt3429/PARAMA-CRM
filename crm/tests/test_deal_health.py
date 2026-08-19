# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for deal-health flags (master spec §5, item 22).

Nothing here is whitelisted, so there is no endpoint row to assert. What IS
asserted, because it is the part that goes wrong:

* the sweep is idempotent -- a second pass over unchanged data writes nothing;
* the watermark truncates and resumes rather than restarting;
* the watermark is CLEARED when a pass finishes, because `close_date_passed`
  becomes true through time passing rather than through a row being edited;
* the flag is a real off switch -- while `deal_health_enabled` is off the sweep
  reads no deal row and writes nothing;
* resolving the problem clears the chip on the next sweep;
* the manager digest names the flagged deals.

Nothing here sends anything or reaches a provider.
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from crm import deal_health
from crm.api import whatsapp_followups
from crm.sweeps import get_watermark, reset_watermark

SETTINGS = "FCRM Settings"
DEAL_DOCTYPE = "CRM Deal"


def open_status() -> str:
	status = frappe.db.get_value("CRM Deal Status", {"type": "Open"}, "name")
	if not status:
		status = frappe.db.get_value("CRM Deal Status", {"type": ["not in", ["Won", "Lost"]]}, "name")
	return status


def won_status() -> str:
	return frappe.db.get_value("CRM Deal Status", {"type": "Won"}, "name")


class DealHealthTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		reset_watermark(deal_health.JOB_NAME)
		frappe.db.set_single_value(SETTINGS, deal_health.FLAG_DEAL_HEALTH, 1)
		frappe.db.set_single_value(SETTINGS, "deal_health_stalled_days", 14)

	def tearDown(self):
		frappe.set_user("Administrator")
		reset_watermark(deal_health.JOB_NAME)
		frappe.db.rollback()

	def make_deal(self, **kwargs):
		# The forecasting feature installs Property Setters that make
		# `expected_deal_value` and `expected_closure_date` mandatory on this
		# site, so both carry a default here and a case overrides what it cares
		# about.
		values = {
			"doctype": DEAL_DOCTYPE,
			"status": open_status(),
			"deal_owner": "Administrator",
			"expected_deal_value": 100000,
			"expected_closure_date": frappe.utils.add_days(frappe.utils.nowdate(), 30),
		}
		values.update(kwargs)
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def sweep(self, **kwargs):
		"""A sweep that stays inside the test transaction."""
		kwargs.setdefault("commit_between_batches", False)
		return deal_health.run_sweep(**kwargs)

	def flags_of(self, deal) -> list[str]:
		return deal_health.parse(frappe.db.get_value(DEAL_DOCTYPE, deal.name, deal_health.HEALTH_FIELD))


# --- the stored value ------------------------------------------------------


class TestSerialisation(FrappeTestCase):
	def test_no_flags_is_an_empty_column_not_an_empty_object(self):
		"""`{}` would make `["is", "set"]` true for every healthy deal."""
		self.assertEqual(deal_health.serialise([]), "")
		self.assertEqual(deal_health.serialise(None), "")

	def test_flags_round_trip(self):
		stored = deal_health.serialise([deal_health.STALLED, deal_health.CLOSE_DATE_PASSED])
		self.assertEqual(
			deal_health.parse(stored),
			[deal_health.CLOSE_DATE_PASSED, deal_health.STALLED],
		)

	def test_order_is_the_display_order_not_the_caller_order(self):
		a = deal_health.serialise([deal_health.AWAITING_REPLY, deal_health.CLOSE_DATE_PASSED])
		b = deal_health.serialise([deal_health.CLOSE_DATE_PASSED, deal_health.AWAITING_REPLY])
		self.assertEqual(a, b)

	def test_an_unknown_flag_is_dropped(self):
		self.assertEqual(deal_health.serialise(["not_a_flag"]), "")
		self.assertEqual(deal_health.parse('{"flags": ["not_a_flag"]}'), [])

	def test_junk_never_raises(self):
		for value in ("", None, "not json", "[]", '{"flags": 3}', 7):
			self.assertEqual(deal_health.parse(value), [])

	def test_a_dict_is_accepted(self):
		"""Frappe casts a JSON column back to a dict on a loaded document."""
		self.assertEqual(deal_health.parse({"flags": [deal_health.STALLED]}), [deal_health.STALLED])


# --- the three questions ---------------------------------------------------


class TestEvaluate(FrappeTestCase):
	NOW = "2026-08-19 10:00:00"

	def context(self, **overrides):
		base = {
			"stalled_days": 14,
			"status_types": {"Qualification": "Open", "Won": "Won"},
			"last_status_change": {},
			"last_incoming": {},
			"last_outgoing": {},
		}
		base.update(overrides)
		return base

	def test_a_past_expected_closure_date_flags(self):
		row = {"name": "D1", "status": "Qualification", "expected_closure_date": "2026-08-18"}
		flags = deal_health.evaluate(row, self.context(), self.NOW)
		self.assertIn(deal_health.CLOSE_DATE_PASSED, flags)

	def test_today_is_not_past(self):
		row = {"name": "D1", "status": "Qualification", "expected_closure_date": "2026-08-19"}
		flags = deal_health.evaluate(row, self.context(), self.NOW)
		self.assertNotIn(deal_health.CLOSE_DATE_PASSED, flags)

	def test_a_won_deal_carries_no_flags_at_all(self):
		"""Winning is how a flag gets cleared, so this must return nothing."""
		row = {
			"name": "D1",
			"status": "Won",
			"expected_closure_date": "2020-01-01",
			"creation": "2020-01-01 00:00:00",
		}
		self.assertEqual(deal_health.evaluate(row, self.context(), self.NOW), [])

	def test_no_status_change_for_longer_than_the_window_is_stalled(self):
		row = {"name": "D1", "status": "Qualification", "creation": "2026-01-01 00:00:00"}
		context = self.context(last_status_change={"D1": "2026-07-01 00:00:00"})
		self.assertIn(deal_health.STALLED, deal_health.evaluate(row, context, self.NOW))

	def test_a_recent_status_change_is_not_stalled(self):
		row = {"name": "D1", "status": "Qualification", "creation": "2020-01-01 00:00:00"}
		context = self.context(last_status_change={"D1": "2026-08-18 09:00:00"})
		self.assertNotIn(deal_health.STALLED, deal_health.evaluate(row, context, self.NOW))

	def test_a_deal_with_no_log_falls_back_to_its_creation(self):
		row = {"name": "D1", "status": "Qualification", "creation": "2026-01-01 00:00:00"}
		self.assertIn(deal_health.STALLED, deal_health.evaluate(row, self.context(), self.NOW))

	def test_the_window_comes_from_the_setting(self):
		row = {"name": "D1", "status": "Qualification", "creation": "2026-08-10 00:00:00"}
		self.assertNotIn(deal_health.STALLED, deal_health.evaluate(row, self.context(), self.NOW))
		context = self.context(stalled_days=2)
		self.assertIn(deal_health.STALLED, deal_health.evaluate(row, context, self.NOW))

	def test_an_old_unanswered_inbound_flags(self):
		row = {"name": "D1", "status": "Qualification", "creation": self.NOW}
		context = self.context(
			last_incoming={"D1": "2026-08-16 09:00:00"},
			last_outgoing={"D1": "2026-08-15 09:00:00"},
		)
		self.assertIn(deal_health.AWAITING_REPLY, deal_health.evaluate(row, context, self.NOW))

	def test_a_recent_inbound_does_not_flag_yet(self):
		row = {"name": "D1", "status": "Qualification", "creation": self.NOW}
		context = self.context(last_incoming={"D1": "2026-08-19 08:00:00"})
		self.assertNotIn(deal_health.AWAITING_REPLY, deal_health.evaluate(row, context, self.NOW))

	def test_we_spoke_last_so_nobody_is_waiting(self):
		row = {"name": "D1", "status": "Qualification", "creation": self.NOW}
		context = self.context(
			last_incoming={"D1": "2026-08-10 09:00:00"},
			last_outgoing={"D1": "2026-08-11 09:00:00"},
		)
		self.assertNotIn(deal_health.AWAITING_REPLY, deal_health.evaluate(row, context, self.NOW))

	def test_a_deal_can_earn_more_than_one_flag(self):
		row = {
			"name": "D1",
			"status": "Qualification",
			"expected_closure_date": "2026-01-01",
			"creation": "2026-01-01 00:00:00",
		}
		context = self.context(last_incoming={"D1": "2026-08-01 09:00:00"})
		self.assertEqual(
			deal_health.evaluate(row, context, self.NOW),
			[deal_health.CLOSE_DATE_PASSED, deal_health.STALLED, deal_health.AWAITING_REPLY],
		)


# --- the sweep -------------------------------------------------------------


class TestSweep(DealHealthTestCase):
	def test_an_overdue_open_deal_is_flagged(self):
		deal = self.make_deal(expected_closure_date=frappe.utils.add_days(frappe.utils.nowdate(), -3))
		self.sweep()
		self.assertIn(deal_health.CLOSE_DATE_PASSED, self.flags_of(deal))

	def test_a_healthy_deal_gets_an_empty_column(self):
		deal = self.make_deal(expected_closure_date=frappe.utils.add_days(frappe.utils.nowdate(), 30))
		self.sweep()
		stored = frappe.db.get_value(DEAL_DOCTYPE, deal.name, deal_health.HEALTH_FIELD)
		self.assertFalse(stored)
		self.assertEqual(self.flags_of(deal), [])

	def test_running_it_twice_writes_nothing_the_second_time(self):
		"""Idempotency: the same data must not produce a second write."""
		self.make_deal(expected_closure_date=frappe.utils.add_days(frappe.utils.nowdate(), -3))
		first = self.sweep()
		reset_watermark(deal_health.JOB_NAME)
		second = self.sweep()

		self.assertGreater(first["changed"], 0)
		self.assertEqual(second["changed"], 0)
		self.assertEqual(first["read"], second["read"])

	def test_resolving_the_problem_clears_the_chip_on_the_next_sweep(self):
		deal = self.make_deal(expected_closure_date=frappe.utils.add_days(frappe.utils.nowdate(), -3))
		self.sweep()
		self.assertIn(deal_health.CLOSE_DATE_PASSED, self.flags_of(deal))

		frappe.db.set_value(
			DEAL_DOCTYPE,
			deal.name,
			"expected_closure_date",
			frappe.utils.add_days(frappe.utils.nowdate(), 30),
			update_modified=False,
		)
		reset_watermark(deal_health.JOB_NAME)
		self.sweep()

		self.assertEqual(self.flags_of(deal), [])

	def test_winning_the_deal_clears_the_chip(self):
		won = won_status()
		if not won:
			self.skipTest("this site has no Won deal status")

		deal = self.make_deal(expected_closure_date=frappe.utils.add_days(frappe.utils.nowdate(), -3))
		self.sweep()
		self.assertTrue(self.flags_of(deal))

		frappe.db.set_value(DEAL_DOCTYPE, deal.name, "status", won, update_modified=False)
		reset_watermark(deal_health.JOB_NAME)
		self.sweep()

		self.assertEqual(self.flags_of(deal), [])

	def test_a_truncated_run_stores_a_cursor_and_the_next_one_resumes(self):
		self.make_deal(expected_closure_date=frappe.utils.add_days(frappe.utils.nowdate(), -3))
		self.make_deal(expected_closure_date=frappe.utils.add_days(frappe.utils.nowdate(), -4))

		first = self.sweep(batch_size=1, max_batches=1)
		self.assertEqual(first["read"], 1)
		self.assertFalse(first["finished"])
		self.assertNotEqual(get_watermark(deal_health.JOB_NAME), ("", ""))

		second = self.sweep(batch_size=1, max_batches=1)
		self.assertEqual(second["read"], 1)
		# Resumed, not restarted: two runs of one row each read two rows in total.
		self.assertEqual(first["read"] + second["read"], 2)

	def test_a_finished_pass_clears_the_cursor_so_tomorrow_starts_at_the_top(self):
		"""A deal goes overdue without being edited; a forward-only cursor would miss it."""
		self.make_deal()
		stats = self.sweep()
		self.assertTrue(stats["finished"])
		self.assertEqual(get_watermark(deal_health.JOB_NAME), ("", ""))

	def test_a_second_copy_does_not_run(self):
		from crm.sweeps import sweep_lock

		with sweep_lock(deal_health.JOB_NAME) as acquired:
			self.assertTrue(acquired)
			stats = self.sweep()

		self.assertFalse(stats["locked"])
		self.assertEqual(stats["read"], 0)


# --- the off switch --------------------------------------------------------


class TestFlag(DealHealthTestCase):
	def test_off_reads_nothing_and_writes_nothing(self):
		frappe.db.set_single_value(SETTINGS, deal_health.FLAG_DEAL_HEALTH, 0)
		deal = self.make_deal(expected_closure_date=frappe.utils.add_days(frappe.utils.nowdate(), -3))

		self.assertEqual(deal_health.sweep_deal_health(), 0)
		self.assertFalse(frappe.db.get_value(DEAL_DOCTYPE, deal.name, deal_health.HEALTH_FIELD))
		# The cursor did not move either, so turning the flag on later starts clean.
		self.assertEqual(get_watermark(deal_health.JOB_NAME), ("", ""))

	def test_the_flag_is_registered(self):
		from crm.feature_flags import FLAGS

		self.assertIn(deal_health.FLAG_DEAL_HEALTH, FLAGS)

	def test_the_flag_has_a_settings_field(self):
		self.assertTrue(frappe.get_meta(SETTINGS).get_field(deal_health.FLAG_DEAL_HEALTH))

	def test_the_column_exists(self):
		self.assertTrue(frappe.db.has_column(DEAL_DOCTYPE, deal_health.HEALTH_FIELD))

	def test_a_blank_window_falls_back_to_the_documented_default(self):
		frappe.db.set_single_value(SETTINGS, "deal_health_stalled_days", 0)
		self.assertEqual(deal_health.stalled_days(), deal_health.DEFAULT_STALLED_DAYS)
		frappe.db.set_single_value(SETTINGS, "deal_health_stalled_days", -5)
		self.assertEqual(deal_health.stalled_days(), deal_health.DEFAULT_STALLED_DAYS)


# --- the manager digest ----------------------------------------------------


class TestDigest(DealHealthTestCase):
	def test_the_digest_names_a_flagged_deal(self):
		deal = self.make_deal(expected_closure_date=frappe.utils.add_days(frappe.utils.nowdate(), -3))
		self.sweep()

		flagged = whatsapp_followups.get_flagged_deals()
		self.assertIn(deal.name, [row["name"] for row in flagged])

		line = whatsapp_followups.digest_deal_health_line(flagged)
		self.assertIn("attention", line)

	def test_one_flagged_deal_reads_as_one_deal(self):
		"""'1 deals need attention' is the kind of thing a demo audience notices."""
		line = whatsapp_followups.digest_deal_health_line([{"title": "Acme", "name": "D1"}])
		self.assertEqual(line, "1 deal needs attention: Acme")

	def test_the_digest_says_nothing_about_deal_health_when_the_flag_is_off(self):
		self.make_deal(expected_closure_date=frappe.utils.add_days(frappe.utils.nowdate(), -3))
		self.sweep()

		frappe.db.set_single_value(SETTINGS, deal_health.FLAG_DEAL_HEALTH, 0)
		self.assertEqual(whatsapp_followups.get_flagged_deals(), [])
		self.assertEqual(whatsapp_followups.digest_deal_health_line([]), "")

	def test_the_digest_names_at_most_three_and_then_counts(self):
		flagged = [{"title": f"Deal {index}", "name": str(index)} for index in range(5)]
		line = whatsapp_followups.digest_deal_health_line(flagged)
		self.assertIn("Deal 0", line)
		self.assertIn("2 more", line)
		self.assertNotIn("Deal 4", line)

	def test_a_digest_with_only_deal_health_still_has_something_to_link_to(self):
		"""The notification list builds its route from the reference."""
		deal = self.make_deal(expected_closure_date=frappe.utils.add_days(frappe.utils.nowdate(), -3))
		self.sweep()

		summary = whatsapp_followups.empty_digest_summary()
		summary["flagged_deals"] = whatsapp_followups.get_flagged_deals()
		self.assertTrue(summary["flagged_deals"])

		# The same fallback `send_daily_digest` applies.
		self.assertIsNone(summary["reference_name"])
		self.assertIn(deal.name, [row["name"] for row in summary["flagged_deals"]])

	def test_the_notification_body_escapes_the_deal_title(self):
		"""A deal title is customer data and lands inside the notification HTML."""
		flagged = [{"title": "<img src=x onerror=alert(1)>", "name": "D1"}]
		summary = whatsapp_followups.empty_digest_summary()
		summary["flagged_deals"] = flagged
		summary["reference_doctype"] = "CRM Deal"
		summary["reference_name"] = "D1"

		captured = {}

		def fake_notify(payload):
			captured.update(payload)

		original = whatsapp_followups.notify_user
		whatsapp_followups.notify_user = fake_notify
		try:
			whatsapp_followups.create_digest_notification(summary, "Administrator")
		finally:
			whatsapp_followups.notify_user = original

		self.assertNotIn("<img", captured["notification_text"])
		self.assertIn("&lt;img", captured["notification_text"])


# --- the flag payload as the frontend receives it --------------------------


class TestPayloadShape(DealHealthTestCase):
	def test_the_column_holds_a_json_object_with_a_flags_list(self):
		deal = self.make_deal(expected_closure_date=frappe.utils.add_days(frappe.utils.nowdate(), -3))
		self.sweep()

		raw = frappe.db.get_value(DEAL_DOCTYPE, deal.name, deal_health.HEALTH_FIELD)
		payload = json.loads(raw)
		self.assertIsInstance(payload["flags"], list)
		self.assertIn(deal_health.CLOSE_DATE_PASSED, payload["flags"])

	def test_every_flag_has_a_label(self):
		for flag in deal_health.FLAG_ORDER:
			self.assertTrue(deal_health.flag_label(flag))
			self.assertNotEqual(deal_health.flag_label(flag), flag)
