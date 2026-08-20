# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the Today page endpoint (master spec §5, item 24).

Endpoint authorization (master spec §3), asserted below rather than described:

* `crm.api.today.get_today` -- any signed-in CRM user, enforced by
  `crm.utils.sales_user_only`. It is read-only. Row scope is derived SERVER-side
  per source and no argument is used as a filter:
  - CRM Task has NO row-level permission rule anywhere in this app, so the
    endpoint restricts to `assigned_to`/`owner` == `frappe.session.user`
    explicitly. `TestPermissions.test_a_sales_user_does_not_see_another_users_task`
    is what stops that regressing into "every task on the site".
  - CRM Deal goes through `frappe.get_list`, which puts
    `crm.permissions.org_hierarchy` into the SQL.
  - Replies delegate whole to `crm.api.whatsapp.get_whatsapp_conversations`,
    which validates access and drops unreadable references.
  - CRM WhatsApp Followup goes through `frappe.get_list`, scoped by
    `crm.api.followup_engine.get_followup_permission_query_conditions`.

Nothing here writes, sends or reaches a provider.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm import deal_health
from crm.api import today
from crm.sweeps import reset_watermark

SETTINGS = "FCRM Settings"
OTHER_USER = "today-outsider@example.com"


def open_deal_status() -> str:
	return frappe.db.get_value("CRM Deal Status", {"type": "Open"}, "name") or frappe.db.get_value(
		"CRM Deal Status", {"type": ["not in", ["Won", "Lost"]]}, "name"
	)


def lead_status() -> str:
	return frappe.db.get_value("CRM Lead Status", {"type": ["!=", "Lost"]}, "name")


class TodayTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		reset_watermark(deal_health.JOB_NAME)
		frappe.db.set_single_value(SETTINGS, deal_health.FLAG_DEAL_HEALTH, 1)
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

	def tearDown(self):
		frappe.set_user("Administrator")
		reset_watermark(deal_health.JOB_NAME)
		frappe.db.rollback()

	def make_task(self, **kwargs):
		values = {
			"doctype": "CRM Task",
			"title": "Call the customer back",
			"status": "Todo",
			"priority": "Medium",
			"due_date": frappe.utils.now_datetime(),
		}
		values.update(kwargs)
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def make_deal(self, **kwargs):
		values = {
			"doctype": "CRM Deal",
			"status": open_deal_status(),
			"deal_owner": "Administrator",
			"expected_deal_value": 1,
			"expected_closure_date": frappe.utils.add_days(frappe.utils.nowdate(), -5),
		}
		values.update(kwargs)
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def sweep(self):
		deal_health.run_sweep(commit_between_batches=False)

	def keys(self, payload) -> list[str]:
		return [item["key"] for item in payload["items"]]

	def of_type(self, payload, wanted) -> list[dict]:
		return [item for item in payload["items"] if item["type"] == wanted]


# --- pure helpers ----------------------------------------------------------


class TestHelpers(FrappeTestCase):
	def test_the_limit_is_clamped_not_trusted(self):
		self.assertEqual(today.clamp_limit(None), today.DEFAULT_LIMIT)
		self.assertEqual(today.clamp_limit(0), today.DEFAULT_LIMIT)
		self.assertEqual(today.clamp_limit(-4), 1)
		self.assertEqual(today.clamp_limit(10**6), today.MAX_LIMIT)
		self.assertEqual(today.clamp_limit("7"), 7)

	def test_overdue_sorts_before_merely_due(self):
		rows = [
			{"type": "task", "title": "b", "due": "2026-08-19 09:00:00", "overdue": False},
			{"type": "task", "title": "a", "due": "2026-08-19 18:00:00", "overdue": True},
		]
		rows.sort(key=today.sort_key)
		self.assertEqual([row["title"] for row in rows], ["a", "b"])

	def test_a_row_with_no_due_time_sorts_last(self):
		rows = [
			{"type": "deal", "title": "no clock", "due": None, "overdue": False},
			{"type": "task", "title": "has one", "due": "2026-08-19 18:00:00", "overdue": False},
		]
		rows.sort(key=today.sort_key)
		self.assertEqual([row["title"] for row in rows], ["has one", "no clock"])

	def test_earlier_due_sorts_first(self):
		rows = [
			{"type": "task", "title": "later", "due": "2026-08-19 18:00:00", "overdue": True},
			{"type": "task", "title": "earlier", "due": "2026-08-19 09:00:00", "overdue": True},
		]
		rows.sort(key=today.sort_key)
		self.assertEqual([row["title"] for row in rows], ["earlier", "later"])


# --- the list --------------------------------------------------------------


class TestShape(TodayTestCase):
	def test_the_payload_is_one_list_plus_counts(self):
		payload = today.get_today()
		self.assertIsInstance(payload["items"], list)
		self.assertIn("all", payload["counts"])
		for name in today.TYPE_ORDER:
			self.assertIn(name, payload["counts"])
		self.assertIsInstance(payload["deal_health_enabled"], bool)

	def test_the_counts_agree_with_the_list(self):
		self.make_task(assigned_to="Administrator")
		payload = today.get_today()
		self.assertEqual(payload["counts"]["all"], len(payload["items"]))
		for name in today.TYPE_ORDER:
			self.assertEqual(payload["counts"][name], len(self.of_type(payload, name)))

	def test_every_row_carries_exactly_one_action(self):
		self.make_task(assigned_to="Administrator")
		for item in today.get_today()["items"]:
			self.assertIn(item["action"], ("open", "approve", "reply"))

	def test_the_list_is_sorted(self):
		self.make_task(assigned_to="Administrator")
		items = today.get_today()["items"]
		self.assertEqual(items, sorted(items, key=today.sort_key))

	def test_no_row_appears_twice(self):
		self.make_task(assigned_to="Administrator")
		keys = self.keys(today.get_today())
		self.assertEqual(len(keys), len(set(keys)))

	def test_the_endpoint_is_whitelisted(self):
		self.assertIn(today.get_today, frappe.whitelisted)


class TestTasks(TodayTestCase):
	def test_a_task_due_today_is_listed(self):
		task = self.make_task(assigned_to="Administrator")
		self.assertIn(f"task:{task.name}", self.keys(today.get_today()))

	def test_an_overdue_task_is_listed_and_marked_overdue(self):
		task = self.make_task(
			assigned_to="Administrator",
			due_date=frappe.utils.add_to_date(frappe.utils.now_datetime(), days=-2),
		)
		rows = [item for item in today.get_today()["items"] if item["key"] == f"task:{task.name}"]
		self.assertEqual(len(rows), 1)
		self.assertTrue(rows[0]["overdue"])

	def test_a_task_due_next_week_is_not_listed(self):
		task = self.make_task(
			assigned_to="Administrator",
			due_date=frappe.utils.add_to_date(frappe.utils.now_datetime(), days=7),
		)
		self.assertNotIn(f"task:{task.name}", self.keys(today.get_today()))

	def test_a_finished_task_is_not_listed(self):
		task = self.make_task(assigned_to="Administrator", status="Done")
		self.assertNotIn(f"task:{task.name}", self.keys(today.get_today()))

	def test_a_task_with_no_due_date_is_not_listed(self):
		"""Today is a due list, not the whole backlog."""
		task = self.make_task(assigned_to="Administrator", due_date=None)
		self.assertNotIn(f"task:{task.name}", self.keys(today.get_today()))

	def test_an_unassigned_task_belongs_to_whoever_made_it(self):
		task = self.make_task(assigned_to=None)
		self.assertIn(f"task:{task.name}", self.keys(today.get_today()))


class TestDeals(TodayTestCase):
	def test_a_flagged_deal_is_listed_with_its_flags(self):
		deal = self.make_deal()
		self.sweep()

		rows = [item for item in today.get_today()["items"] if item["key"] == f"deal:{deal.name}"]
		self.assertEqual(len(rows), 1)
		self.assertIn(deal_health.CLOSE_DATE_PASSED, rows[0]["flags"])
		self.assertTrue(rows[0]["context"])

	def test_an_unflagged_deal_is_not_listed(self):
		deal = self.make_deal(expected_closure_date=frappe.utils.add_days(frappe.utils.nowdate(), 30))
		self.sweep()
		self.assertNotIn(f"deal:{deal.name}", self.keys(today.get_today()))

	def test_the_flag_being_off_removes_every_deal_row(self):
		"""AC: flag OFF means no chips anywhere, even over stale stored values."""
		deal = self.make_deal()
		self.sweep()
		self.assertIn(f"deal:{deal.name}", self.keys(today.get_today()))

		frappe.db.set_single_value(SETTINGS, deal_health.FLAG_DEAL_HEALTH, 0)
		payload = today.get_today()
		self.assertNotIn(f"deal:{deal.name}", self.keys(payload))
		self.assertEqual(payload["counts"]["deal"], 0)
		self.assertFalse(payload["deal_health_enabled"])


class TestApprovals(TodayTestCase):
	def make_followup(self, state="Pending Approval"):
		lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Approval",
				"last_name": "Waiting",
				"status": lead_status(),
				"mobile_no": "+919820000099",
			}
		).insert(ignore_permissions=True)

		return (
			frappe.get_doc(
				{
					"doctype": "CRM WhatsApp Followup",
					"lead": lead.name,
					"phone": "+919820000099",
					"state": state,
					"pending_stage": 2,
				}
			).insert(ignore_permissions=True),
			lead,
		)

	def test_a_pending_draft_is_listed_with_an_approve_action(self):
		followup, lead = self.make_followup()
		rows = [item for item in today.get_today()["items"] if item["key"] == f"approval:{followup.name}"]
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["action"], "approve")
		self.assertEqual(rows[0]["reference_name"], lead.name)

	def test_an_active_followup_is_not_listed(self):
		followup, _ = self.make_followup(state="Active")
		self.assertNotIn(f"approval:{followup.name}", self.keys(today.get_today()))


# --- authorization ---------------------------------------------------------


class TestPermissions(TodayTestCase):
	def test_a_sales_user_does_not_see_another_users_task(self):
		"""CRM Task has no row-level rule: this filter is the only boundary."""
		task = self.make_task(assigned_to="Administrator")
		frappe.set_user(OTHER_USER)
		self.assertNotIn(f"task:{task.name}", self.keys(today.get_today()))

	def test_a_sales_user_does_see_their_own_task(self):
		task = self.make_task(assigned_to=OTHER_USER)
		frappe.set_user(OTHER_USER)
		self.assertIn(f"task:{task.name}", self.keys(today.get_today()))

	def test_a_sales_user_does_not_see_another_teams_flagged_deal(self):
		deal = self.make_deal(deal_owner="Administrator")
		self.sweep()
		frappe.set_user(OTHER_USER)
		self.assertNotIn(f"deal:{deal.name}", self.keys(today.get_today()))

	def test_a_sales_user_does_see_their_own_flagged_deal(self):
		deal = self.make_deal(deal_owner=OTHER_USER)
		self.sweep()
		frappe.set_user(OTHER_USER)
		self.assertIn(f"deal:{deal.name}", self.keys(today.get_today()))

	def test_the_deal_scope_comes_from_the_hierarchy_conditions(self):
		"""Named explicitly so a rewrite away from `get_list` fails here."""
		from crm.permissions.org_hierarchy import get_deal_permission_query_conditions

		condition = get_deal_permission_query_conditions(OTHER_USER)
		self.assertIn("deal_owner", condition)
		self.assertIn(OTHER_USER, condition)

	def test_a_non_agent_is_refused(self):
		frappe.set_user("Guest")
		self.assertRaises(frappe.PermissionError, today.get_today)

	def test_the_limit_argument_cannot_widen_anything(self):
		task = self.make_task(assigned_to="Administrator")
		frappe.set_user(OTHER_USER)
		self.assertNotIn(f"task:{task.name}", self.keys(today.get_today(limit=999999)))
