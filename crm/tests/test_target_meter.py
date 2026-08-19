# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the dashboard target meter (master spec §5, item 7).

Endpoint authorization (master spec §3), asserted below rather than described:

* `crm.api.dashboard.get_chart` with `name="target_meter"` -- the EXISTING
  dashboard endpoint, unchanged. `@sales_user_only` refuses anyone who is not an
  agent, and the endpoint pins a plain Sales User to `user = frappe.session.user`
  before it dispatches, so the chart's `deal_owner` filter is server-derived and
  cannot be widened from the request. `TestPermissions` proves it with a real
  Sales User and no patching.
* No new endpoint is added by item 7. The chart function itself is not
  whitelisted; it is reachable only through `get_chart` / `get_dashboard`.

The assertions are deltas rather than absolutes: this runs against a site that
already has deals, and a test that asserted a total would pass or fail on the
seed data rather than on the code.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.api import dashboard

SETTINGS = "FCRM Settings"
DEAL_DOCTYPE = "CRM Deal"
OTHER_USER = "target-meter-outsider@example.com"


def status_of_type(wanted: str) -> str | None:
	return frappe.db.get_value("CRM Deal Status", {"type": wanted}, "name")


class TargetMeterTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.won = status_of_type("Won")
		self.open = status_of_type("Open") or status_of_type("Ongoing")
		if not self.won:
			self.skipTest("this site has no Won deal status")
		frappe.db.set_single_value(SETTINGS, "monthly_revenue_target", 1000000)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def make_deal(self, **kwargs):
		values = {
			"doctype": DEAL_DOCTYPE,
			"status": self.open,
			"deal_owner": "Administrator",
			"expected_deal_value": 1,
			"expected_closure_date": frappe.utils.add_days(frappe.utils.nowdate(), 30),
		}
		values.update(kwargs)
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def win(self, deal, closed_date, value, exchange_rate=1, owner="Administrator"):
		"""Make a deal Won on a chosen date.

		Set through the database rather than through `save`, because
		`CRMDeal.validate` overwrites `closed_date` with today the moment the
		status becomes Won -- which is right in production and useless for a test
		about month boundaries.
		"""
		frappe.db.set_value(
			DEAL_DOCTYPE,
			deal.name,
			{
				"status": self.won,
				"closed_date": closed_date,
				"deal_value": value,
				"exchange_rate": exchange_rate,
				"deal_owner": owner,
			},
			update_modified=False,
		)

	def achieved(self, **kwargs) -> float:
		return frappe.utils.flt(dashboard.get_target_meter(**kwargs)["value"])


# --- the metric ------------------------------------------------------------


class TestMetric(TargetMeterTestCase):
	def test_a_deal_won_this_month_counts(self):
		before = self.achieved()
		deal = self.make_deal()
		self.win(deal, frappe.utils.get_first_day(frappe.utils.nowdate()), 250000)
		self.assertEqual(self.achieved() - before, 250000)

	def test_a_deal_won_last_month_does_not(self):
		"""The month boundary, from the wrong side."""
		before = self.achieved()
		last_month = frappe.utils.add_days(frappe.utils.get_first_day(frappe.utils.nowdate()), -1)
		deal = self.make_deal()
		self.win(deal, last_month, 250000)
		self.assertEqual(self.achieved(), before)

	def test_the_last_day_of_this_month_still_counts(self):
		"""The month boundary, from the right side: the upper bound is half-open."""
		before = self.achieved()
		deal = self.make_deal()
		self.win(deal, frappe.utils.get_last_day(frappe.utils.nowdate()), 300000)
		self.assertEqual(self.achieved() - before, 300000)

	def test_a_deal_won_next_month_does_not_count_yet(self):
		before = self.achieved()
		next_month = frappe.utils.add_days(frappe.utils.get_last_day(frappe.utils.nowdate()), 1)
		deal = self.make_deal()
		self.win(deal, next_month, 400000)
		self.assertEqual(self.achieved(), before)

	def test_an_open_deal_does_not_count_however_big(self):
		before = self.achieved()
		deal = self.make_deal()
		frappe.db.set_value(
			DEAL_DOCTYPE,
			deal.name,
			{"deal_value": 9000000, "closed_date": frappe.utils.nowdate()},
			update_modified=False,
		)
		self.assertEqual(self.achieved(), before)

	def test_value_is_converted_to_the_org_currency(self):
		"""Same exchange-rate path the other value charts use: value * rate."""
		before = self.achieved()
		deal = self.make_deal()
		self.win(deal, frappe.utils.nowdate(), 100000, exchange_rate=2.5)
		self.assertEqual(self.achieved() - before, 250000)

	def test_a_zero_value_deal_contributes_nothing(self):
		before = self.achieved()
		deal = self.make_deal()
		self.win(deal, frappe.utils.nowdate(), 0)
		self.assertEqual(self.achieved(), before)

	def test_the_conversion_matches_the_other_value_charts(self):
		"""Same `deal_value * IfNull(exchange_rate, 1)` the won-value chart uses.

		Named so a change to one of the two fails here rather than quietly making
		the meter disagree with the tile beside it. `exchange_rate` is NOT NULL
		with a default of 1 and `CRMDeal.update_exchange_rate` refills it whenever
		it is falsy, so the `IfNull` is belt and braces on both charts.
		"""
		before = self.achieved()
		deal = self.make_deal()
		self.win(deal, frappe.utils.nowdate(), 100000, exchange_rate=1)
		self.assertEqual(self.achieved() - before, 100000)

	def test_the_dashboard_date_filter_is_ignored(self):
		"""Item 7: the target period is the calendar month, not the filter."""
		deal = self.make_deal()
		self.win(deal, frappe.utils.nowdate(), 500000)

		wide = self.achieved(from_date="2020-01-01", to_date="2020-12-31")
		narrow = self.achieved(from_date="2099-01-01", to_date="2099-12-31")
		self.assertEqual(wide, narrow)
		self.assertEqual(wide, self.achieved())

	def test_the_widget_says_which_period_it_used(self):
		payload = dashboard.get_target_meter(from_date="2020-01-01", to_date="2020-12-31")
		self.assertIn("calendar month", payload["subtitle"])
		self.assertEqual(payload["periodStart"], str(frappe.utils.get_first_day(frappe.utils.nowdate())))
		self.assertEqual(payload["periodEnd"], str(frappe.utils.get_last_day(frappe.utils.nowdate())))


# --- the target ------------------------------------------------------------


class TestTarget(TargetMeterTestCase):
	def test_the_percentage_is_achieved_over_target(self):
		frappe.db.set_single_value(SETTINGS, "monthly_revenue_target", 1000000)
		payload = dashboard.get_target_meter()
		expected = round(frappe.utils.flt(payload["value"]) / 1000000 * 100, 1)
		self.assertEqual(payload["percent"], expected)
		self.assertTrue(payload["hasTarget"])

	def test_no_target_is_not_zero_percent(self):
		frappe.db.set_single_value(SETTINGS, "monthly_revenue_target", 0)
		payload = dashboard.get_target_meter()
		self.assertFalse(payload["hasTarget"])
		self.assertEqual(payload["percent"], 0.0)

	def test_over_target_is_reported_as_over_a_hundred(self):
		frappe.db.set_single_value(SETTINGS, "monthly_revenue_target", 1)
		deal = self.make_deal()
		self.win(deal, frappe.utils.nowdate(), 100)
		self.assertGreater(dashboard.get_target_meter()["percent"], 100)

	def test_the_currency_symbol_comes_from_the_settings(self):
		payload = dashboard.get_target_meter()
		self.assertEqual(payload["prefix"], dashboard.get_base_currency_symbol())

	def test_the_setting_exists(self):
		self.assertTrue(frappe.get_meta(SETTINGS).get_field("monthly_revenue_target"))


# --- registration ----------------------------------------------------------


class TestRegistration(TargetMeterTestCase):
	def test_the_chart_is_allow_listed(self):
		self.assertIn("target_meter", dashboard.ALLOWED_CHARTS)

	def test_the_dispatcher_finds_it(self):
		self.assertIs(dashboard.get_chart_method("target_meter"), dashboard.get_target_meter)

	def test_get_chart_returns_it(self):
		payload = dashboard.get_chart(name="target_meter", type="progress_chart")
		self.assertNotIn("error", payload)
		self.assertIn("target", payload)

	def test_an_unknown_chart_name_is_still_refused(self):
		payload = dashboard.get_chart(name="target_meter_evil", type="progress_chart")
		self.assertIn("error", payload)


# --- authorization ---------------------------------------------------------


class TestPermissions(TargetMeterTestCase):
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

	def test_a_sales_user_sees_only_their_own_won_value(self):
		mine = self.make_deal()
		self.win(mine, frappe.utils.nowdate(), 700000, owner=OTHER_USER)
		theirs = self.make_deal()
		self.win(theirs, frappe.utils.nowdate(), 900000, owner="Administrator")

		frappe.set_user(OTHER_USER)
		payload = dashboard.get_chart(name="target_meter", type="progress_chart")
		self.assertEqual(frappe.utils.flt(payload["value"]), 700000)

	def test_a_sales_user_cannot_widen_the_scope_from_the_request(self):
		"""`user` is overwritten server-side before the chart is dispatched."""
		theirs = self.make_deal()
		self.win(theirs, frappe.utils.nowdate(), 900000, owner="Administrator")

		frappe.set_user(OTHER_USER)
		payload = dashboard.get_chart(name="target_meter", type="progress_chart", user="Administrator")
		self.assertEqual(frappe.utils.flt(payload["value"]), 0)

	def test_a_manager_sees_the_whole_site(self):
		deal = self.make_deal()
		self.win(deal, frappe.utils.nowdate(), 900000, owner=OTHER_USER)

		payload = dashboard.get_chart(name="target_meter", type="progress_chart")
		self.assertGreaterEqual(frappe.utils.flt(payload["value"]), 900000)

	def test_a_non_agent_is_refused(self):
		if not frappe.db.exists("User", "Guest"):
			self.skipTest("no Guest user on this site")

		frappe.set_user("Guest")
		self.assertRaises(
			frappe.PermissionError,
			dashboard.get_chart,
			name="target_meter",
			type="progress_chart",
		)
