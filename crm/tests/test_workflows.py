# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for mini workflow rules (master spec §5 item 16, design note 16).

The six classes named `TestAC1` .. `TestAC6` are the design note's six
acceptance criteria, one class each, so a reader can check the feature against
the note without reading the engine. Everything else in this file supports them.

Endpoint authorization (master spec §3), asserted in `TestAC6ManagersOnly`
rather than described:

* `crm.workflows.get_rules` / `get_rule` / `save_rule` / `set_rule_enabled` /
  `delete_rule` / `get_recent_runs` -- Sales Manager or System Manager only.
  `check_manager()` runs first on every one of them and raises
  `frappe.PermissionError` for anybody else; the `CRM Workflow Rule` doctype
  additionally grants Sales User nothing at all, which the same class asserts
  through `frappe.has_permission`. There is no row-level scope to derive: a rule
  is site configuration, not a customer record.

Nothing here reaches a provider. `crm.api.email.send_email` is replaced by a
recorder wherever the email action is exercised, and the feature's flag is put
back to OFF in `tearDown`.
"""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm import automation_context, workflows

LEAD_DOCTYPE = "CRM Lead"
DEAL_DOCTYPE = "CRM Deal"
RULE_DOCTYPE = workflows.RULE_DOCTYPE
LOG_DOCTYPE = workflows.LOG_DOCTYPE
TASK_DOCTYPE = workflows.TASK_DOCTYPE
SETTINGS_DOCTYPE = workflows.SETTINGS_DOCTYPE
FLAG = workflows.FLAG_WORKFLOW_RULES

LEAD_EMAIL = "workflow.customer@example.com"
MANAGER = "workflow-manager@example.com"
AGENT = "workflow-agent@example.com"


def lead_statuses() -> tuple[str, str]:
	"""Two different open lead statuses, so a transition has somewhere to go."""
	found = frappe.get_all("CRM Lead Status", pluck="name", order_by="position asc", limit=2)
	while len(found) < 2:
		name = f"Workflow Status {len(found)}"
		found.append(
			frappe.get_doc(
				{
					"doctype": "CRM Lead Status",
					"lead_status": name,
					"position": 90 + len(found),
					"type": "Open",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
	return found[0], found[1]


def ensure_user(email: str, role: str) -> str:
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"roles": [{"role": role}],
			}
		).insert(ignore_permissions=True)
	return email


class QueryCounter:
	"""Every statement the database sees while this is open.

	`frappe.db.sql` is the single funnel: the query builder's `run()` and every
	`get_value` / `get_all` / `exists` in the framework end up here, so counting
	it counts everything. The patch is on the CONNECTION INSTANCE, which is what
	`frappe.qb`'s `execute_query` looks up at call time.
	"""

	def __init__(self):
		self.statements: list[str] = []

	def __enter__(self):
		self.original = frappe.db.sql

		def counting(query, *args, **kwargs):
			self.statements.append(str(query)[:160])
			return self.original(query, *args, **kwargs)

		frappe.db.sql = counting
		return self

	def __exit__(self, *exc):
		frappe.db.sql = self.original
		return False

	def __len__(self):
		return len(self.statements)


class WorkflowTestCase(FrappeTestCase):
	"""One lead, the flag on, the queue captured, and nothing really enqueued."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.open_status, self.next_status = lead_statuses()

		frappe.db.set_single_value(SETTINGS_DOCTYPE, FLAG, 1)

		# A real site may already have rules, and a suite whose result depends on
		# what an operator configured last week is not a suite. Every existing
		# rule is switched off INSIDE this test's transaction, so the rollback in
		# `tearDown` puts each one back exactly as it was.
		self.pre_existing = frappe.get_all(RULE_DOCTYPE, filters={"enabled": 1}, pluck="name")
		for name in self.pre_existing:
			frappe.db.set_value(RULE_DOCTYPE, name, "enabled", 0, update_modified=False)

		workflows.clear_cache()

		# `commit` is named in the engine so a test can stop a real commit from
		# escaping its rollback, and still see the ORDER of the claim and the act.
		self.journal: list[str] = []
		self.commit_patch = patch.object(
			workflows, "commit", side_effect=lambda: self.journal.append("commit")
		)
		self.commit_patch.start()

		# The hook queues; it does not run. Capturing the enqueue is what lets a
		# test assert both halves separately.
		self.queued: list[dict] = []
		self.enqueue_patch = patch.object(frappe, "enqueue", side_effect=self.record_enqueue)
		self.enqueue_patch.start()

		self.sent: list[dict] = []
		self.send_patch = patch("crm.api.email.send_email", side_effect=self.record_send)
		self.send_patch.start()

		self.lead = self.make_lead()
		self.queued.clear()

	def tearDown(self):
		self.send_patch.stop()
		self.enqueue_patch.stop()
		self.commit_patch.stop()
		frappe.set_user("Administrator")
		frappe.db.rollback()
		# Redis is not rolled back. A blob that still says "enabled" would leak
		# this test's configuration into every module that runs after it.
		workflows.clear_cache()

	# --- fixtures ----------------------------------------------------------

	def record_enqueue(
		self,
		method,
		queue="default",
		timeout=None,
		event=None,
		is_async=True,
		job_name=None,
		now=False,
		enqueue_after_commit=False,
		**kwargs,
	):
		"""A double with `frappe.enqueue`'s OWN parameters spelled out.

		This is not decoration. `frappe.enqueue` consumes `event`, `queue`,
		`timeout` and the rest before `**kwargs` sees them, so a job argument
		named after one of them is swallowed silently at the enqueue and the
		worker then raises `TypeError` into a log nobody reads. A double that
		took `**kwargs` alone would pass every test in this file and hide it --
		which is exactly what it did until a live run found it.
		"""
		self.queued.append({"method": method, "enqueue_after_commit": enqueue_after_commit, **kwargs})

	def record_send(self, **kwargs):
		self.sent.append(kwargs)
		return {"name": "COMM-TEST"}

	def jobs(self) -> list[dict]:
		return [job for job in self.queued if job["method"] == "crm.workflows.execute_rule"]

	def run_jobs(self) -> int:
		"""Do what the worker would do, with the arguments the hook really gave."""
		jobs = self.jobs()
		self.queued.clear()
		for job in jobs:
			payload = {
				key: value for key, value in job.items() if key not in ("method", "enqueue_after_commit")
			}
			workflows.execute_rule(**payload)
		return len(jobs)

	def make_lead(self, **overrides):
		values = {
			"doctype": LEAD_DOCTYPE,
			"first_name": "Priya",
			"last_name": "Workflow",
			"status": self.open_status,
			"email": LEAD_EMAIL,
		}
		values.update(overrides)
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def make_rule(self, **overrides):
		values = {
			"doctype": RULE_DOCTYPE,
			"title": "Qualified leads get a call-back task",
			"apply_on": LEAD_DOCTYPE,
			"event": workflows.EVENT_STAGE_CHANGED,
			"enabled": 1,
			"daily_action_cap": 500,
			"actions": [
				{
					"action_type": workflows.ACTION_TASK,
					"task_title": "Call the customer back",
					"task_priority": "High",
					"task_due_offset_days": 1,
				}
			],
		}
		values.update(overrides)
		conditions = values.pop("conditions", None)
		if conditions is not None:
			values["condition_json"] = json.dumps(conditions)
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def logs(self, **filters) -> list[dict]:
		return frappe.get_all(
			LOG_DOCTYPE,
			filters=filters,
			fields=["name", "rule", "status", "action_type", "reason", "reference_docname"],
			order_by="creation asc",
		)

	def tasks_for(self, lead) -> list[dict]:
		return frappe.get_all(
			TASK_DOCTYPE,
			filters={"reference_doctype": LEAD_DOCTYPE, "reference_docname": lead.name},
			fields=["name", "title", "priority"],
		)

	def move_status(self, lead=None, status=None):
		lead = lead or self.lead
		lead.status = status or self.next_status
		lead.save(ignore_permissions=True)
		return lead


# --- AC1 -------------------------------------------------------------------


class TestAC1FiresOncePerRealTransition(WorkflowTestCase):
	"""AC1: one real transition, one run, one log row. A no-op save does nothing."""

	def setUp(self):
		super().setUp()
		self.rule = self.make_rule()

	def test_ac1_a_real_stage_transition_fires_the_rule_exactly_once(self):
		self.move_status()

		self.assertEqual(len(self.jobs()), 1)
		self.assertEqual(self.run_jobs(), 1)

		rows = self.logs(rule=self.rule.name)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["status"], workflows.STATUS_EXECUTED)
		self.assertEqual(rows[0]["action_type"], workflows.ACTION_TASK)

		tasks = self.tasks_for(self.lead)
		self.assertEqual(len(tasks), 1)
		self.assertEqual(tasks[0]["title"], "Call the customer back")

	def test_ac1_re_saving_without_a_stage_change_fires_nothing(self):
		"""The regression this whole guard exists for: the daily congratulation."""
		self.move_status()
		self.run_jobs()

		self.lead.reload()
		self.lead.last_name = "Workflow Again"
		self.lead.save(ignore_permissions=True)

		self.assertEqual(self.jobs(), [])
		self.assertEqual(len(self.logs(rule=self.rule.name)), 1)
		self.assertEqual(len(self.tasks_for(self.lead)), 1)

	def test_ac1_the_same_job_run_twice_still_logs_one_row_and_makes_one_task(self):
		"""Idempotency across background jobs is the execution key's whole job."""
		self.move_status()
		jobs = self.jobs()
		self.queued.clear()

		for _attempt in range(2):
			for job in jobs:
				workflows.execute_rule(
					rule=job["rule"],
					reference_doctype=job["reference_doctype"],
					reference_docname=job["reference_docname"],
					workflow_event=job["workflow_event"],
					source=job["source"],
				)

		self.assertEqual(len(self.logs(rule=self.rule.name)), 1)
		self.assertEqual(len(self.tasks_for(self.lead)), 1)

	def test_ac1_a_later_save_is_a_new_source_and_may_run_again(self):
		"""The key must stop a RETRY without stopping the next real transition."""
		self.move_status()
		self.run_jobs()

		self.lead.reload()
		self.move_status(self.lead, self.open_status)
		self.run_jobs()

		self.assertEqual(len(self.logs(rule=self.rule.name)), 2)
		self.assertEqual(len(self.tasks_for(self.lead)), 2)

	def test_ac1_a_created_record_fires_a_record_created_rule(self):
		created = self.make_rule(
			title="Welcome task on creation",
			event=workflows.EVENT_CREATED,
		)
		self.queued.clear()

		lead = self.make_lead(first_name="Arun", email="arun.workflow@example.com")
		queued = [job for job in self.jobs() if job["rule"] == created.name]
		self.assertEqual(len(queued), 1)
		self.assertEqual(queued[0]["reference_docname"], lead.name)

	def test_ac1_the_claim_is_committed_before_the_action_runs(self):
		"""A crash between the two must lose the action, never repeat it."""
		self.move_status()
		self.journal.clear()
		self.run_jobs()

		# claim -> commit, then the action, then the outcome -> commit.
		self.assertGreaterEqual(self.journal.count("commit"), 2)

	def test_ac1_a_condition_that_does_not_match_stops_the_rule_at_the_hook(self):
		frappe.delete_doc(RULE_DOCTYPE, self.rule.name, force=True)
		self.make_rule(conditions=[["email", "==", "somebody.else@example.com"]])
		self.queued.clear()

		self.move_status()
		self.assertEqual(self.jobs(), [])

	def test_ac1_a_condition_that_matches_lets_the_rule_through(self):
		frappe.delete_doc(RULE_DOCTYPE, self.rule.name, force=True)
		self.make_rule(conditions=[["email", "==", LEAD_EMAIL]])
		self.queued.clear()

		self.move_status()
		self.assertEqual(len(self.jobs()), 1)


# --- AC2 -------------------------------------------------------------------


class TestAC2NoCascade(WorkflowTestCase):
	"""AC2: a field one rule writes never triggers the rule that watches it."""

	def setUp(self):
		super().setUp()
		self.writer = self.make_rule(
			title="Stage change renames the lead",
			event=workflows.EVENT_STAGE_CHANGED,
			actions=[
				{
					"action_type": workflows.ACTION_UPDATE,
					"update_field": "last_name",
					"update_value": "Renamed By Rule",
				}
			],
		)
		self.watcher = self.make_rule(
			title="A changed last name makes a task",
			event=workflows.EVENT_FIELD_CHANGED,
			watched_field="last_name",
		)
		self.queued.clear()

	def test_ac2_a_rule_that_writes_a_watched_field_does_not_cascade(self):
		self.move_status()

		# Only the writer is queued by the transition itself.
		self.assertEqual([job["rule"] for job in self.jobs()], [self.writer.name])

		self.run_jobs()

		# The write happened...
		self.assertEqual(frappe.db.get_value(LEAD_DOCTYPE, self.lead.name, "last_name"), "Renamed By Rule")
		# ...and it queued nothing. That is the ceiling.
		self.assertEqual(self.jobs(), [])
		self.assertEqual(self.logs(rule=self.watcher.name), [])
		self.assertEqual(self.tasks_for(self.lead), [])

	def test_ac2_the_watcher_still_fires_when_a_HUMAN_changes_the_field(self):
		"""The ceiling must stop automation, not the feature."""
		self.lead.reload()
		self.lead.last_name = "Changed By Hand"
		self.lead.save(ignore_permissions=True)

		self.assertEqual([job["rule"] for job in self.jobs()], [self.watcher.name])
		self.run_jobs()
		self.assertEqual(len(self.tasks_for(self.lead)), 1)

	def test_ac2_the_engine_returns_at_the_ceiling_without_reading_anything(self):
		"""Depth is checked before the cache, so a nested save costs nothing."""
		with automation_context.execution_depth(limit=5):
			with QueryCounter() as counter:
				workflows.on_update(self.lead)
				workflows.after_insert(self.lead)

		self.assertEqual(len(counter), 0, counter.statements)
		self.assertEqual(self.jobs(), [])

	def test_ac2_the_ceiling_is_one(self):
		"""Named so a change to the constant has to change this line too."""
		self.assertEqual(workflows.DEPTH_CEILING, 1)


# --- AC3 -------------------------------------------------------------------


class TestAC3DailyCap(WorkflowTestCase):
	"""AC3: cap 2, third record skipped and logged, owner told once."""

	def setUp(self):
		super().setUp()
		self.rule = self.make_rule(daily_action_cap=2)
		self.queued.clear()

	def cap_notifications(self) -> list[dict]:
		return frappe.get_all(
			"CRM Notification",
			filters={"notification_type_doctype": RULE_DOCTYPE, "notification_type_doc": self.rule.name},
			fields=["name", "to_user", "notification_text"],
		)

	def fire_on_a_new_lead(self, index: int):
		lead = self.make_lead(first_name=f"Capped{index}", email=f"capped{index}@example.com")
		self.queued.clear()
		self.move_status(lead)
		self.run_jobs()
		return lead

	def test_ac3_the_third_matching_record_is_skipped_and_logged(self):
		leads = [self.fire_on_a_new_lead(index) for index in range(3)]

		statuses = [row["status"] for row in self.logs(rule=self.rule.name)]
		self.assertEqual(
			statuses,
			[workflows.STATUS_EXECUTED, workflows.STATUS_EXECUTED, workflows.STATUS_SKIPPED_CAP],
		)
		self.assertEqual(self.tasks_for(leads[2]), [])

	def test_ac3_the_owner_is_notified_once_however_many_records_are_refused(self):
		for index in range(4):
			self.fire_on_a_new_lead(index)

		skipped = [
			row for row in self.logs(rule=self.rule.name) if row["status"] == workflows.STATUS_SKIPPED_CAP
		]
		self.assertEqual(len(skipped), 2)
		self.assertEqual(len(self.cap_notifications()), 1)

	def test_ac3_the_notification_claim_is_one_atomic_statement(self):
		"""Read-then-write would let two workers both notify in the same second."""
		self.fire_on_a_new_lead(0)
		self.fire_on_a_new_lead(1)
		self.fire_on_a_new_lead(2)

		self.assertEqual(
			frappe.utils.cstr(frappe.db.get_value(RULE_DOCTYPE, self.rule.name, "cap_notified_on")),
			frappe.utils.nowdate(),
		)

		# A second call on the same day claims nothing and sends nothing.
		workflows.notify_owner_of_cap(frappe.get_doc(RULE_DOCTYPE, self.rule.name), 2)
		self.assertEqual(len(self.cap_notifications()), 1)

	def test_ac3_the_reason_says_what_the_cap_was(self):
		for index in range(3):
			self.fire_on_a_new_lead(index)

		skipped = [
			row for row in self.logs(rule=self.rule.name) if row["status"] == workflows.STATUS_SKIPPED_CAP
		]
		self.assertIn("2", skipped[0]["reason"])

	def test_ac3_a_cap_of_zero_never_refuses(self):
		frappe.db.set_value(RULE_DOCTYPE, self.rule.name, "daily_action_cap", 0)
		for index in range(3):
			self.fire_on_a_new_lead(index)

		statuses = {row["status"] for row in self.logs(rule=self.rule.name)}
		self.assertEqual(statuses, {workflows.STATUS_EXECUTED})


# --- AC4 -------------------------------------------------------------------


class TestAC4SuppressedEmail(WorkflowTestCase):
	"""AC4: a suppressed address logs Skipped-suppressed and sends nothing."""

	def setUp(self):
		super().setUp()
		self.template = self.make_template()
		self.rule = self.make_rule(
			title="Qualified leads get the welcome mail",
			actions=[
				{
					"action_type": workflows.ACTION_EMAIL,
					"email_template": self.template,
					"recipient_mode": workflows.RECIPIENT_RECORD,
				}
			],
		)
		self.queued.clear()

	def make_template(self) -> str:
		name = "workflow-welcome-template"
		if frappe.db.exists("Email Template", name):
			frappe.delete_doc("Email Template", name, force=True)
		return (
			frappe.get_doc(
				{
					"doctype": "Email Template",
					"name": name,
					"subject": "Welcome, {{ doc.first_name }}",
					"response": "<p>Thank you for your interest, {{ doc.first_name }}.</p>",
					"enabled": 1,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def suppress(self, address: str):
		from crm.suppression import CHANNEL_EMAIL, suppress

		return suppress(CHANNEL_EMAIL, address, state="Opted Out", source="test")

	def test_ac4_a_suppressed_address_logs_skipped_suppressed_and_sends_nothing(self):
		self.suppress(LEAD_EMAIL)

		self.move_status()
		self.run_jobs()

		rows = self.logs(rule=self.rule.name)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["status"], workflows.STATUS_SKIPPED_SUPPRESSED)
		self.assertEqual(self.sent, [])

	def test_ac4_an_address_that_is_not_suppressed_is_sent_the_rendered_template(self):
		self.move_status()
		self.run_jobs()

		rows = self.logs(rule=self.rule.name)
		self.assertEqual(rows[0]["status"], workflows.STATUS_EXECUTED)
		self.assertEqual(len(self.sent), 1)
		self.assertEqual(self.sent[0]["recipients"], LEAD_EMAIL)
		self.assertEqual(self.sent[0]["subject"], "Welcome, Priya")
		self.assertIn("Thank you for your interest, Priya.", self.sent[0]["content"])
		self.assertEqual(self.sent[0]["doctype"], LEAD_DOCTYPE)

	def test_ac4_the_send_goes_through_the_suppression_checked_endpoint(self):
		"""Named so a rewrite onto `frappe.sendmail` fails here rather than live."""
		import crm.api.email as email_module

		self.assertTrue(hasattr(email_module, "send_email"))
		self.move_status()
		self.run_jobs()
		self.assertEqual(len(self.sent), 1)

	def test_ac4_a_record_with_no_address_fails_rather_than_guessing(self):
		frappe.db.set_value(LEAD_DOCTYPE, self.lead.name, "email", "")
		self.lead.reload()

		self.move_status()
		self.run_jobs()

		rows = self.logs(rule=self.rule.name)
		self.assertEqual(rows[0]["status"], workflows.STATUS_FAILED)
		self.assertEqual(self.sent, [])


# --- AC5 -------------------------------------------------------------------


class TestAC5NoEngineWorkWhenOff(WorkflowTestCase):
	"""AC5: flag off or rule disabled costs the save nothing.

	What is measured: the engine entry points ONLY -- `after_insert` and
	`on_update` called directly on a loaded document, with `frappe.db.sql`
	counted. Measuring a whole `doc.save()` would count the framework's own
	statements and prove nothing about this feature.
	"""

	def warm(self):
		"""Fill the Redis blob, so the next call is the steady-state one."""
		workflows.rule_cache()

	def test_ac5_with_the_flag_off_the_engine_runs_no_query_at_all(self):
		frappe.db.set_single_value(SETTINGS_DOCTYPE, FLAG, 0)
		workflows.clear_cache()
		self.warm()

		with QueryCounter() as counter:
			workflows.after_insert(self.lead)
			workflows.on_update(self.lead)

		self.assertEqual(len(counter), 0, counter.statements)
		self.assertEqual(self.jobs(), [])

	def test_ac5_a_cold_cache_costs_exactly_one_flag_read_while_the_flag_is_off(self):
		"""The bound the AC allows: one cached flag read, and it is cached after."""
		frappe.db.set_single_value(SETTINGS_DOCTYPE, FLAG, 0)
		workflows.clear_cache()

		with QueryCounter() as cold:
			workflows.on_update(self.lead)
		self.assertEqual(len(cold), 1, cold.statements)
		self.assertIn("Singles", cold.statements[0])

		with QueryCounter() as warm:
			workflows.on_update(self.lead)
		self.assertEqual(len(warm), 0, warm.statements)

	def test_ac5_with_the_flag_on_and_no_rule_the_engine_runs_no_query(self):
		self.assertEqual(frappe.get_all(RULE_DOCTYPE, filters={"enabled": 1}), [])
		workflows.clear_cache()
		self.warm()

		with QueryCounter() as counter:
			workflows.after_insert(self.lead)
			workflows.on_update(self.lead)

		self.assertEqual(len(counter), 0, counter.statements)

	def test_ac5_a_disabled_rule_is_not_in_the_cache_and_costs_nothing(self):
		self.make_rule(enabled=0)
		workflows.clear_cache()
		self.warm()

		with QueryCounter() as counter:
			workflows.on_update(self.lead)

		self.assertEqual(len(counter), 0, counter.statements)
		self.assertEqual(self.jobs(), [])

	def test_ac5_a_rule_for_the_other_doctype_costs_a_lead_save_nothing(self):
		self.make_rule(apply_on=DEAL_DOCTYPE)
		workflows.clear_cache()
		self.warm()

		with QueryCounter() as counter:
			workflows.on_update(self.lead)

		self.assertEqual(len(counter), 0, counter.statements)

	def test_ac5_an_enabled_matching_rule_still_costs_no_query_at_the_hook(self):
		"""Even the firing path is query-free: the decision is made in memory."""
		self.make_rule()
		workflows.clear_cache()
		self.warm()
		self.lead.reload()
		self.lead.status = self.next_status

		with QueryCounter() as counter:
			workflows.on_update(self.lead)

		self.assertEqual(len(counter), 0, counter.statements)
		self.assertEqual(len(self.jobs()), 1)

	def test_ac5_saving_a_rule_drops_the_cache(self):
		self.warm()
		rule = self.make_rule()
		self.assertIsNone(frappe.cache().get_value(workflows.CACHE_KEY))

		self.assertEqual(len(workflows.rule_cache()["rules"][LEAD_DOCTYPE][workflows.EVENT_STAGE_CHANGED]), 1)

		rule.enabled = 0
		rule.save(ignore_permissions=True)
		self.assertIsNone(frappe.cache().get_value(workflows.CACHE_KEY))
		self.assertEqual(workflows.rule_cache()["rules"], {})

	def test_ac5_deleting_a_rule_drops_the_cache(self):
		rule = self.make_rule()
		self.warm()
		frappe.delete_doc(RULE_DOCTYPE, rule.name, force=True)
		self.assertIsNone(frappe.cache().get_value(workflows.CACHE_KEY))

	def test_ac5_saving_the_settings_drops_the_cache(self):
		"""The flag lives in FCRM Settings, so its save has to invalidate too."""
		self.warm()
		settings = frappe.get_single(SETTINGS_DOCTYPE)
		settings.save(ignore_permissions=True)
		self.assertIsNone(frappe.cache().get_value(workflows.CACHE_KEY))


# --- AC6 -------------------------------------------------------------------


class TestAC6ManagersOnly(WorkflowTestCase):
	"""AC6: a Sales User cannot create, edit or list rules. A manager can."""

	def setUp(self):
		super().setUp()
		self.manager = ensure_user(MANAGER, "Sales Manager")
		self.agent = ensure_user(AGENT, "Sales User")
		self.rule = self.make_rule()
		self.queued.clear()

	def test_ac6_a_sales_user_cannot_list_rules(self):
		frappe.set_user(self.agent)
		self.assertRaises(frappe.PermissionError, workflows.get_rules)

	def test_ac6_a_sales_user_cannot_read_one_rule(self):
		frappe.set_user(self.agent)
		self.assertRaises(frappe.PermissionError, workflows.get_rule, self.rule.name)

	def test_ac6_a_sales_user_cannot_create_or_edit_a_rule(self):
		frappe.set_user(self.agent)
		self.assertRaises(
			frappe.PermissionError,
			workflows.save_rule,
			{"title": "Mine now", "apply_on": LEAD_DOCTYPE, "event": workflows.EVENT_CREATED},
		)
		self.assertRaises(frappe.PermissionError, workflows.set_rule_enabled, self.rule.name, 0)
		self.assertRaises(frappe.PermissionError, workflows.delete_rule, self.rule.name)

	def test_ac6_a_sales_user_cannot_read_the_execution_log_endpoint(self):
		frappe.set_user(self.agent)
		self.assertRaises(frappe.PermissionError, workflows.get_recent_runs)

	def test_ac6_the_doctype_itself_grants_a_sales_user_nothing(self):
		"""The endpoint check is the first door; this is the second."""
		for ptype in ("read", "create", "write", "delete"):
			self.assertFalse(
				frappe.has_permission(RULE_DOCTYPE, ptype, user=self.agent),
				msg=f"Sales User should not have {ptype}",
			)

	def test_ac6_a_manager_can_list_read_create_and_delete(self):
		frappe.set_user(self.manager)

		self.assertIn(self.rule.name, [row["name"] for row in workflows.get_rules()])
		self.assertEqual(workflows.get_rule(self.rule.name)["title"], self.rule.title)

		created = workflows.save_rule(
			{
				"title": "Manager's own rule",
				"apply_on": LEAD_DOCTYPE,
				"event": workflows.EVENT_CREATED,
				"enabled": 0,
				"actions": [{"action_type": workflows.ACTION_TASK, "task_title": "Say hello"}],
			}
		)
		self.assertTrue(frappe.db.exists(RULE_DOCTYPE, created))

		workflows.delete_rule(created)
		self.assertFalse(frappe.db.exists(RULE_DOCTYPE, created))

	def test_ac6_a_manager_may_not_delete_an_execution_log_row(self):
		"""The send-log precedent: a log an operator can delete is not a guard."""
		self.assertTrue(frappe.has_permission(LOG_DOCTYPE, "read", user=self.manager))
		self.assertFalse(frappe.has_permission(LOG_DOCTYPE, "delete", user=self.manager))

	def test_ac6_the_recent_runs_panel_is_readable_by_a_manager(self):
		self.move_status()
		self.run_jobs()

		frappe.set_user(self.manager)
		runs = workflows.get_recent_runs(rule=self.rule.name)
		self.assertEqual(len(runs), 1)
		self.assertEqual(runs[0]["status"], workflows.STATUS_EXECUTED)


# --- the other doctype -----------------------------------------------------


class TestDealRules(WorkflowTestCase):
	"""The engine serves two doctypes, so the second one is exercised for real."""

	def deal_statuses(self) -> tuple[str, str]:
		found = frappe.get_all("CRM Deal Status", pluck="name", order_by="position asc", limit=2)
		self.assertGreaterEqual(len(found), 2, "the site needs two deal statuses")
		return found[0], found[1]

	def setUp(self):
		super().setUp()
		self.deal_open, self.deal_next = self.deal_statuses()
		self.deal = frappe.get_doc(
			{
				"doctype": DEAL_DOCTYPE,
				"status": self.deal_open,
				"email": "deal.workflow@example.com",
				"expected_deal_value": 100000,
				"expected_closure_date": frappe.utils.add_days(frappe.utils.nowdate(), 30),
			}
		).insert(ignore_permissions=True)
		self.rule = self.make_rule(
			title="A moved deal gets a follow-up task",
			apply_on=DEAL_DOCTYPE,
			actions=[{"action_type": workflows.ACTION_TASK, "task_title": "Follow the deal up"}],
		)
		self.queued.clear()

	def test_a_deal_stage_transition_fires_the_rule_once(self):
		self.deal.status = self.deal_next
		self.deal.save(ignore_permissions=True)

		jobs = self.jobs()
		self.assertEqual(len(jobs), 1)
		self.assertEqual(jobs[0]["reference_doctype"], DEAL_DOCTYPE)
		self.assertEqual(jobs[0]["reference_docname"], self.deal.name)

		self.run_jobs()
		rows = self.logs(rule=self.rule.name)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["status"], workflows.STATUS_EXECUTED)

		tasks = frappe.get_all(
			TASK_DOCTYPE,
			filters={"reference_doctype": DEAL_DOCTYPE, "reference_docname": self.deal.name},
			pluck="title",
		)
		self.assertEqual(tasks, ["Follow the deal up"])

	def test_a_deal_re_saved_without_a_stage_change_fires_nothing(self):
		self.deal.reload()
		self.deal.expected_deal_value = 120000
		self.deal.save(ignore_permissions=True)

		self.assertEqual(self.jobs(), [])

	def test_a_lead_rule_does_not_fire_on_a_deal(self):
		self.make_rule(title="Lead only", apply_on=LEAD_DOCTYPE)
		self.queued.clear()

		self.deal.status = self.deal_next
		self.deal.save(ignore_permissions=True)

		self.assertEqual([job["rule"] for job in self.jobs()], [self.rule.name])


# --- conditions ------------------------------------------------------------


class TestConditions(FrappeTestCase):
	"""The condition tree, evaluated structurally rather than by `eval`."""

	def doc(self, **values):
		return frappe._dict(values)

	def test_no_condition_means_every_record(self):
		self.assertTrue(workflows.matches([], self.doc(status="Open")))
		self.assertTrue(workflows.matches(None, self.doc(status="Open")))

	def test_equals_and_not_equals(self):
		doc = self.doc(status="Qualified")
		self.assertTrue(workflows.matches([["status", "==", "Qualified"]], doc))
		self.assertFalse(workflows.matches([["status", "==", "Open"]], doc))
		self.assertTrue(workflows.matches([["status", "!=", "Open"]], doc))

	def test_equals_spelled_out_is_the_same_operator(self):
		doc = self.doc(status="Qualified")
		self.assertTrue(workflows.matches([["status", "equals", "Qualified"]], doc))
		self.assertTrue(workflows.matches([["status", "not equals", "Open"]], doc))

	def test_and_needs_both_rows(self):
		doc = self.doc(status="Qualified", email="a@b.com")
		self.assertTrue(
			workflows.matches([["status", "==", "Qualified"], "and", ["email", "==", "a@b.com"]], doc)
		)
		self.assertFalse(
			workflows.matches([["status", "==", "Qualified"], "and", ["email", "==", "x@y.com"]], doc)
		)

	def test_or_needs_only_one(self):
		doc = self.doc(status="Qualified", email="a@b.com")
		self.assertTrue(workflows.matches([["status", "==", "Nope"], "or", ["email", "==", "a@b.com"]], doc))
		self.assertFalse(workflows.matches([["status", "==", "Nope"], "or", ["email", "==", "x@y.com"]], doc))

	def test_a_nested_group_is_evaluated_as_one_row(self):
		doc = self.doc(status="Qualified", email="a@b.com", mobile_no="")
		tree = [
			["status", "==", "Qualified"],
			"and",
			[["email", "==", "x@y.com"], "or", ["email", "==", "a@b.com"]],
		]
		self.assertTrue(workflows.matches(tree, doc))

	def test_is_set_and_is_not_set(self):
		self.assertTrue(workflows.matches([["email", "is", "set"]], self.doc(email="a@b.com")))
		self.assertFalse(workflows.matches([["email", "is", "set"]], self.doc(email="")))
		self.assertTrue(workflows.matches([["email", "is", "not set"]], self.doc(email=None)))

	def test_like_is_a_substring_and_an_empty_field_never_matches(self):
		self.assertTrue(workflows.matches([["email", "like", "@b.com"]], self.doc(email="a@b.com")))
		self.assertFalse(workflows.matches([["email", "like", "@c.com"]], self.doc(email="a@b.com")))
		self.assertFalse(workflows.matches([["email", "like", "x"]], self.doc(email="")))
		self.assertFalse(workflows.matches([["email", "not like", "x"]], self.doc(email="")))
		self.assertTrue(workflows.matches([["email", "not like", "x"]], self.doc(email="a@b.com")))

	def test_in_accepts_a_list_and_a_comma_string(self):
		doc = self.doc(status="Qualified")
		self.assertTrue(workflows.matches([["status", "in", ["Open", "Qualified"]]], doc))
		self.assertTrue(workflows.matches([["status", "in", "Open, Qualified"]], doc))
		self.assertFalse(workflows.matches([["status", "in", "Open, Lost"]], doc))
		self.assertTrue(workflows.matches([["status", "not in", "Open, Lost"]], doc))

	def test_numbers_compare_as_numbers(self):
		doc = self.doc(annual_revenue=1500)
		self.assertTrue(workflows.matches([["annual_revenue", ">", "1000"]], doc))
		self.assertFalse(workflows.matches([["annual_revenue", "<", "1000"]], doc))
		self.assertTrue(workflows.matches([["annual_revenue", ">=", "1500"]], doc))
		self.assertTrue(workflows.matches([["annual_revenue", "==", "1500"]], doc))

	def test_between_takes_two_bounds(self):
		doc = self.doc(annual_revenue=1500)
		self.assertTrue(workflows.matches([["annual_revenue", "between", "1000,2000"]], doc))
		self.assertFalse(workflows.matches([["annual_revenue", "between", "2000,3000"]], doc))

	def test_a_check_field_is_compared_as_yes_or_no(self):
		self.assertTrue(workflows.matches([["converted", "==", "Yes"]], self.doc(converted=1)))
		self.assertFalse(workflows.matches([["converted", "==", "Yes"]], self.doc(converted=0)))
		self.assertTrue(workflows.matches([["converted", "==", "No"]], self.doc(converted=0)))

	def test_a_missing_field_never_matches_a_value(self):
		self.assertFalse(workflows.matches([["nothing_here", "==", "x"]], self.doc()))

	def test_an_unknown_operator_refuses_rather_than_matching_everything(self):
		self.assertFalse(workflows.matches([["status", "sounds like", "x"]], self.doc(status="x")))

	def test_condition_fields_lists_every_field_including_nested_ones(self):
		tree = [["status", "==", "a"], "and", [["email", "==", "b"], "or", ["mobile_no", "==", "c"]]]
		self.assertEqual(workflows.condition_fields(tree), ["status", "email", "mobile_no"])

	def test_condition_fields_refuses_a_malformed_row(self):
		self.assertRaises(frappe.ValidationError, workflows.condition_fields, [["status", "=="]])
		self.assertRaises(frappe.ValidationError, workflows.condition_fields, [["", "==", "x"]])
		self.assertRaises(frappe.ValidationError, workflows.condition_fields, [["status", "", "x"]])
		self.assertRaises(frappe.ValidationError, workflows.condition_fields, [["a", "==", "b"], "maybe"])


# --- validation ------------------------------------------------------------


class TestRuleValidation(WorkflowTestCase):
	"""A rule that cannot work is refused by Save, not discovered at 2 a.m."""

	def test_a_rule_with_no_action_is_refused(self):
		self.assertRaises(frappe.ValidationError, self.make_rule, actions=[])

	def test_a_field_changed_rule_needs_a_field(self):
		self.assertRaises(
			frappe.ValidationError,
			self.make_rule,
			event=workflows.EVENT_FIELD_CHANGED,
			watched_field=None,
		)

	def test_a_watched_field_that_does_not_exist_is_refused(self):
		self.assertRaises(
			frappe.ValidationError,
			self.make_rule,
			event=workflows.EVENT_FIELD_CHANGED,
			watched_field="not_a_real_field",
		)

	def test_a_condition_on_a_field_that_does_not_exist_is_refused(self):
		self.assertRaises(
			frappe.ValidationError, self.make_rule, conditions=[["not_a_real_field", "==", "x"]]
		)

	def test_a_condition_that_is_not_json_is_refused(self):
		self.assertRaises(frappe.ValidationError, self.make_rule, condition_json="{not json")

	def test_an_update_action_may_not_write_a_framework_field(self):
		for field in ("owner", "modified", "name", "_assign"):
			self.assertRaises(
				frappe.ValidationError,
				self.make_rule,
				actions=[
					{"action_type": workflows.ACTION_UPDATE, "update_field": field, "update_value": "x"}
				],
			)

	def test_an_update_action_may_not_write_a_field_that_does_not_exist(self):
		self.assertRaises(
			frappe.ValidationError,
			self.make_rule,
			actions=[
				{"action_type": workflows.ACTION_UPDATE, "update_field": "nope_not_here", "update_value": "x"}
			],
		)

	def test_an_email_action_needs_a_template(self):
		self.assertRaises(
			frappe.ValidationError,
			self.make_rule,
			actions=[{"action_type": workflows.ACTION_EMAIL, "recipient_mode": workflows.RECIPIENT_RECORD}],
		)

	def test_a_task_action_needs_a_title(self):
		self.assertRaises(
			frappe.ValidationError, self.make_rule, actions=[{"action_type": workflows.ACTION_TASK}]
		)

	def test_a_notify_action_that_names_nobody_is_refused(self):
		self.assertRaises(
			frappe.ValidationError,
			self.make_rule,
			actions=[{"action_type": workflows.ACTION_NOTIFY, "notify_mode": workflows.NOTIFY_SPECIFIC}],
		)

	def test_the_watched_field_is_cleared_on_a_rule_that_does_not_use_it(self):
		rule = self.make_rule(event=workflows.EVENT_STAGE_CHANGED, watched_field="email")
		self.assertIsNone(rule.watched_field)

	def test_a_condition_is_stored_re_serialised(self):
		rule = self.make_rule(condition_json='[["email",  "==",   "a@b.com"]]')
		self.assertEqual(json.loads(rule.condition_json), [["email", "==", "a@b.com"]])


# --- the acting user -------------------------------------------------------


class TestActingUser(WorkflowTestCase):
	"""A rule carries its owner's authority, re-checked at execution time."""

	def test_a_manager_owner_is_the_acting_user(self):
		manager = ensure_user(MANAGER, "Sales Manager")
		rule = self.make_rule()
		frappe.db.set_value(RULE_DOCTYPE, rule.name, "owner", manager)
		rule.reload()

		self.assertEqual(workflows.acting_user(rule), manager)

	def test_a_disabled_owner_may_not_act(self):
		manager = ensure_user(MANAGER, "Sales Manager")
		rule = self.make_rule()
		frappe.db.set_value(RULE_DOCTYPE, rule.name, "owner", manager)
		frappe.db.set_value("User", manager, "enabled", 0)
		rule.reload()

		self.assertIsNone(workflows.acting_user(rule))

	def test_an_owner_who_lost_the_manager_role_may_not_act(self):
		agent = ensure_user(AGENT, "Sales User")
		rule = self.make_rule()
		frappe.db.set_value(RULE_DOCTYPE, rule.name, "owner", agent)
		rule.reload()

		self.assertIsNone(workflows.acting_user(rule))

	def test_administrator_needs_no_switch(self):
		rule = self.make_rule()
		self.assertIsNone(workflows.acting_user(rule))


# --- the actions -----------------------------------------------------------


class TestActions(WorkflowTestCase):
	def test_an_update_field_action_writes_the_field(self):
		self.make_rule(
			actions=[
				{
					"action_type": workflows.ACTION_UPDATE,
					"update_field": "last_name",
					"update_value": "Set By Rule",
				}
			]
		)
		self.queued.clear()

		self.move_status()
		self.run_jobs()

		self.assertEqual(frappe.db.get_value(LEAD_DOCTYPE, self.lead.name, "last_name"), "Set By Rule")

	def test_an_update_field_action_that_changes_nothing_says_so(self):
		self.make_rule(
			actions=[
				{
					"action_type": workflows.ACTION_UPDATE,
					"update_field": "last_name",
					"update_value": "Workflow",
				}
			]
		)
		self.queued.clear()

		self.move_status()
		self.run_jobs()

		rows = self.logs(reference_docname=self.lead.name)
		self.assertEqual(rows[0]["status"], workflows.STATUS_EXECUTED)
		self.assertIn("already held", rows[0]["reason"])

	def test_a_notify_action_writes_one_crm_notification(self):
		recipient = ensure_user(MANAGER, "Sales Manager")
		self.make_rule(
			actions=[
				{
					"action_type": workflows.ACTION_NOTIFY,
					"notify_mode": workflows.NOTIFY_SPECIFIC,
					"notify_user": recipient,
				}
			]
		)
		self.queued.clear()

		self.move_status()
		self.run_jobs()

		found = frappe.get_all(
			"CRM Notification",
			filters={"notification_type_doctype": LEAD_DOCTYPE, "notification_type_doc": self.lead.name},
			fields=["to_user"],
		)
		self.assertIn(recipient, [row["to_user"] for row in found])

	def test_a_notify_action_with_nobody_to_notify_fails_rather_than_passing(self):
		self.make_rule(
			actions=[
				{
					"action_type": workflows.ACTION_NOTIFY,
					"notify_mode": workflows.NOTIFY_ROLE,
					"notify_role": "Sales Manager",
				}
			]
		)
		self.queued.clear()

		with patch.object(workflows, "notify_recipients", return_value=[]):
			self.move_status()
			self.run_jobs()

		rows = self.logs(reference_docname=self.lead.name)
		self.assertEqual(rows[0]["status"], workflows.STATUS_FAILED)

	def test_a_task_action_links_the_task_back_to_the_record(self):
		self.make_rule()
		self.queued.clear()

		self.move_status()
		self.run_jobs()

		task = frappe.get_doc(TASK_DOCTYPE, self.tasks_for(self.lead)[0]["name"])
		self.assertEqual(task.reference_doctype, LEAD_DOCTYPE)
		self.assertEqual(task.reference_docname, self.lead.name)
		self.assertEqual(task.priority, "High")

	def test_an_action_that_raises_is_logged_as_failed_and_stops_there(self):
		self.make_rule()
		self.queued.clear()

		with patch.object(workflows, "create_task", side_effect=RuntimeError("the action broke")):
			self.move_status()
			self.run_jobs()

		rows = self.logs(reference_docname=self.lead.name)
		self.assertEqual(rows[0]["status"], workflows.STATUS_FAILED)
		self.assertIn("the action broke", rows[0]["reason"])

	def test_every_action_of_a_rule_gets_its_own_log_row(self):
		self.make_rule(
			actions=[
				{"action_type": workflows.ACTION_TASK, "task_title": "First"},
				{"action_type": workflows.ACTION_TASK, "task_title": "Second"},
			]
		)
		self.queued.clear()

		self.move_status()
		self.run_jobs()

		rows = self.logs(reference_docname=self.lead.name)
		self.assertEqual(len(rows), 2)
		self.assertEqual({row["status"] for row in rows}, {workflows.STATUS_EXECUTED})
		self.assertEqual(len(self.tasks_for(self.lead)), 2)


# --- the background half ---------------------------------------------------


class TestExecuteRuleRechecks(WorkflowTestCase):
	"""Everything is re-checked at execution time, not trusted from the enqueue."""

	def setUp(self):
		super().setUp()
		self.rule = self.make_rule()
		self.queued.clear()
		self.move_status()
		self.job = self.jobs()[0]
		self.queued.clear()

	def run_job(self):
		workflows.execute_rule(
			rule=self.job["rule"],
			reference_doctype=self.job["reference_doctype"],
			reference_docname=self.job["reference_docname"],
			workflow_event=self.job["workflow_event"],
			source=self.job["source"],
		)

	def test_the_flag_turned_off_between_the_save_and_the_worker_stops_the_job(self):
		frappe.db.set_single_value(SETTINGS_DOCTYPE, FLAG, 0)
		workflows.clear_cache()

		self.run_job()
		self.assertEqual(self.logs(rule=self.rule.name), [])

	def test_the_rule_disabled_between_the_save_and_the_worker_stops_the_job(self):
		frappe.db.set_value(RULE_DOCTYPE, self.rule.name, "enabled", 0)

		self.run_job()
		self.assertEqual(self.logs(rule=self.rule.name), [])

	def test_the_rule_deleted_between_the_save_and_the_worker_stops_the_job(self):
		frappe.delete_doc(RULE_DOCTYPE, self.rule.name, force=True)

		self.run_job()
		self.assertEqual(self.logs(rule=self.rule.name), [])

	def test_the_record_deleted_between_the_save_and_the_worker_stops_the_job(self):
		frappe.delete_doc(LEAD_DOCTYPE, self.lead.name, force=True, ignore_permissions=True)

		self.run_job()
		self.assertEqual(self.logs(rule=self.rule.name), [])

	def test_a_doctype_the_engine_does_not_serve_is_refused(self):
		self.job["reference_doctype"] = "User"
		self.run_job()
		self.assertEqual(self.logs(rule=self.rule.name), [])


# --- housekeeping ----------------------------------------------------------


class TestCleanup(WorkflowTestCase):
	def make_log(self, key: str, age_days: int) -> str:
		row = frappe.get_doc(
			{
				"doctype": LOG_DOCTYPE,
				"execution_key": key,
				"rule_title": "Old rule",
				"reference_doctype": LEAD_DOCTYPE,
				"reference_docname": self.lead.name,
				"status": workflows.STATUS_EXECUTED,
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			LOG_DOCTYPE,
			row.name,
			"creation",
			frappe.utils.add_days(frappe.utils.nowdate(), -age_days),
			update_modified=False,
		)
		return row.name

	def test_rows_older_than_ninety_days_go_and_newer_ones_stay(self):
		old = self.make_log("cleanup-old", 120)
		edge = self.make_log("cleanup-edge", 89)

		workflows.cleanup_execution_log()

		self.assertFalse(frappe.db.exists(LOG_DOCTYPE, old))
		self.assertTrue(frappe.db.exists(LOG_DOCTYPE, edge))

	def test_the_retention_window_is_ninety_days(self):
		self.assertEqual(workflows.LOG_RETENTION_DAYS, 90)

	def test_the_cleanup_is_bounded(self):
		"""An unbounded delete on a year of rows holds one huge transaction open."""
		self.assertLessEqual(workflows.CLEANUP_BATCH * workflows.CLEANUP_MAX_BATCHES, 20000)

	def test_an_empty_table_costs_one_read_and_stops(self):
		frappe.db.delete(LOG_DOCTYPE)
		workflows.cleanup_execution_log()  # must not raise


# --- registration ----------------------------------------------------------


class TestRegistration(FrappeTestCase):
	"""The hooks and the flag, asserted rather than assumed."""

	def hooks(self) -> dict:
		import crm.hooks as crm_hooks

		return crm_hooks.doc_events

	def test_the_engine_is_registered_on_both_doctypes(self):
		for doctype in workflows.APPLY_ON:
			events = self.hooks()[doctype]
			self.assertIn("crm.workflows.after_insert", events["after_insert"])
			self.assertIn("crm.workflows.on_update", events["on_update"])

	def test_the_settings_invalidation_is_registered(self):
		self.assertIn("crm.workflows.on_settings_update", self.hooks()["FCRM Settings"]["on_update"])

	def test_the_existing_deal_hook_was_not_replaced(self):
		"""Single-owner rule on hooks.py: adding must not remove."""
		self.assertIn(
			"crm.fcrm.doctype.erpnext_crm_settings.erpnext_crm_settings.create_customer_in_erpnext",
			self.hooks()["CRM Deal"]["on_update"],
		)

	def test_the_cleanup_runs_daily_and_only_daily(self):
		import crm.hooks as crm_hooks

		target = "crm.workflows.cleanup_execution_log"
		self.assertIn(target, crm_hooks.scheduler_events["daily"])
		for key, entries in crm_hooks.scheduler_events.items():
			if key == "daily":
				continue
			flat = entries if isinstance(entries, list) else [x for v in entries.values() for x in v]
			self.assertNotIn(target, flat)

	def test_the_flag_is_in_the_registry_and_in_the_settings_doctype(self):
		from crm.feature_flags import FLAGS

		self.assertIn(FLAG, FLAGS)
		self.assertTrue(frappe.get_meta(SETTINGS_DOCTYPE).get_field(FLAG))

	def test_the_flag_is_off_by_default(self):
		field = frappe.get_meta(SETTINGS_DOCTYPE).get_field(FLAG)
		self.assertEqual(frappe.utils.cint(field.default), 0)

	def test_a_new_rule_is_disabled_by_default(self):
		field = frappe.get_meta(RULE_DOCTYPE).get_field("enabled")
		self.assertEqual(frappe.utils.cint(field.default), 0)

	def test_the_execution_key_column_is_unique(self):
		field = frappe.get_meta(LOG_DOCTYPE).get_field("execution_key")
		self.assertTrue(field.unique)

	def test_no_job_argument_collides_with_enqueue(self):
		"""A real bug, found on a live site, and this is what stops it returning.

		`frappe.enqueue` takes `event` for itself. The engine used to send the
		workflow event under that name; the enqueue swallowed it, the worker
		raised `TypeError: execute_rule() missing 1 required positional
		argument: 'event'`, and NOTHING in the app said so -- no log row, no
		notification, just a rule that quietly never fired.

		The reserved names come from `enqueue_parameters()`, not from
		`inspect.signature(frappe.enqueue)`: that attribute is a lazy re-export
		whose signature is `(*args, **kwargs)`, so reading it here made this
		test pass vacuously against an empty reserved set.
		"""
		from crm.automation_context import enqueue_parameters

		reserved = enqueue_parameters()
		# Prove the guard sees the real parameter list, not the shim's.
		self.assertIn("event", reserved)
		collisions = reserved & set(workflows.JOB_KWARGS)
		self.assertEqual(collisions, set(), f"these job arguments would be eaten: {collisions}")

	def test_execute_rule_accepts_exactly_the_arguments_the_hook_sends(self):
		"""The other half: the names must also be the ones the job expects."""
		import inspect

		accepted = set(inspect.signature(workflows.execute_rule).parameters) - {"kwargs"}
		self.assertEqual(set(workflows.JOB_KWARGS) - accepted, set())
