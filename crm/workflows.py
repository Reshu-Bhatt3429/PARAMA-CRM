"""Mini workflow rules (master spec §5 item 16), on the F4 automation guards.

A rule is three sentences a manager writes: **when** something happens to a lead
or a deal, **if** the record looks a certain way, **then** do these things. This
module is the engine that reads them. It is the first and only consumer of
`crm/automation_context.py`, and every guard in that file is here for a reason
this file makes concrete.

## The two switches

Nothing fires until BOTH are on: `workflow_rules_enabled` in FCRM Settings
(default OFF, master spec C5) and the rule's own `enabled` (default 0). One
switch is a foot-gun -- turning the feature on would immediately arm every draft
rule anyone had left lying around.

## Why the hot path is a Redis read and nothing else

`on_update` fires on EVERY save of a lead and of a deal, which are the two
hottest doctypes in the app. If the engine asked the database "are there any
rules?" on each of those saves, a feature that is switched off would still cost
the whole site a query per save forever. So the flag and the enabled rules live
in ONE cached blob, and a save on a site with no rules costs zero queries. The
blob is dropped whenever a rule is inserted, saved or deleted, and whenever FCRM
Settings is saved -- both are registered in `crm/hooks.py`, and neither happens
often enough to matter.

Cache staleness has exactly one hole, stated rather than hidden: a flag or a
rule changed by raw SQL (`frappe.db.set_value` on the Single, a direct UPDATE)
does not pass through a document hook and therefore does not drop the blob. The
fix is one call to `clear_cache()`; the tests do it, and so must any script that
flips the switch behind the doctype's back.

## Depth ceiling ONE

`crm/automation_context.py` defaults to three levels of synchronous nesting.
This engine uses one, deliberately: a field an automation writes NEVER triggers
another automation. A cascade is the single most damaging thing a rule engine
can do to a customer's data, and a travel agency's "when the stage becomes
Qualified, set the priority" rules do not need chains. The UI says so in the
help text on the Update-field action, so the behaviour is a promise and not a
surprise.

The ceiling is checked FIRST, before the cache read, because it is a
`frappe.local` attribute lookup and it is the only guard that has to hold even
when everything else is switched on.

## Actions run after the commit

`queue_after_commit` is not a nicety. A job enqueued inside the transaction can
be picked up by a free worker before the row it describes is visible, and a
transaction that later rolls back leaves the worker acting on a state that never
existed. Both failures put a real email in front of a real customer. So the hook
decides WHICH rules fire, synchronously and in memory, and the background job
does the acting.

Conditions are evaluated at hook time, on the in-memory document, because the
document is the thing that just changed; asking again in the background job
would answer a different question and would cost a read.

## At most once, and the key that guarantees it

Each action claims a `CRM Workflow Execution Log` row whose `execution_key`
carries a unique index, and the claim is committed BEFORE the action runs. A
retry, a double-fire and a second worker all build the same key and only one
gets past the index. A process that dies between the claim and the action leaves
a `Claimed` row that is never retried -- at most once, never twice, the same
direction every outbound path in this app takes.

## Who the actions run as

A background job runs as the user who saved the record, and that user may be a
sales agent with no right to email the customer or write the field the manager's
rule sets. So `execute_rule` switches to the RULE'S OWNER before it acts, after
re-checking AT EXECUTION TIME that the owner is still enabled and still a
manager (master spec §3). The rule is the manager's instruction; it carries the
manager's authority, not the agent's.

Endpoint authorization (master spec §3), stated here and in
`crm/tests/test_workflows.py`:

* `get_rules` / `get_rule` / `save_rule` / `delete_rule` / `set_rule_enabled` /
  `get_recent_runs` -- Sales Manager or System Manager ONLY, checked by
  `check_manager()` on every call, and again by the doctype's own role
  permissions (`CRM Workflow Rule` grants nothing to Sales User). Scope is the
  whole rule table on purpose: a rule is site configuration, not a customer
  record, and there is no row-level scope to derive. `get_recent_runs` returns
  execution-log rows, which name a lead or a deal but carry no field of it.
"""

import json

import frappe
from frappe import _

from crm import counters
from crm.automation_context import (
	current_depth,
	execution_depth,
	execution_key,
	has_changed,
	queue_after_commit,
	reserve_daily_slot,
)
from crm.fcrm.doctype.crm_notification.crm_notification import notify_user
from crm.suppression import CHANNEL_EMAIL, is_suppressed

RULE_DOCTYPE = "CRM Workflow Rule"
ACTION_DOCTYPE = "CRM Workflow Action"
LOG_DOCTYPE = "CRM Workflow Execution Log"
TASK_DOCTYPE = "CRM Task"
SETTINGS_DOCTYPE = "FCRM Settings"

FLAG_WORKFLOW_RULES = "workflow_rules_enabled"

# The only two doctypes a rule may run on. Not a setting: every extra doctype is
# another `on_update` hook on another hot table.
APPLY_ON = ("CRM Lead", "CRM Deal")

EVENT_CREATED = "Record created"
EVENT_FIELD_CHANGED = "Field changed"
EVENT_STAGE_CHANGED = "Stage changed"
EVENTS = (EVENT_CREATED, EVENT_FIELD_CHANGED, EVENT_STAGE_CHANGED)

# "Stage" is `status` on both a lead and a deal. Named once so the two never
# drift apart.
STAGE_FIELD = "status"

# Who the record belongs to, per doctype. Falls back to `owner`.
OWNER_FIELD = {"CRM Lead": "lead_owner", "CRM Deal": "deal_owner"}

ACTION_EMAIL = "Send email template"
ACTION_TASK = "Create task"
ACTION_NOTIFY = "Notify user"
ACTION_UPDATE = "Update field"
ACTION_TYPES = (ACTION_EMAIL, ACTION_TASK, ACTION_NOTIFY, ACTION_UPDATE)

RECIPIENT_RECORD = "Record email"
RECIPIENT_ASSIGNED = "Assigned user"
RECIPIENT_SPECIFIC = "Specific address"

NOTIFY_ASSIGNED = "Assigned user"
NOTIFY_SPECIFIC = "Specific user"
NOTIFY_ROLE = "Everyone with a role"

STATUS_CLAIMED = "Claimed"
STATUS_EXECUTED = "Executed"
STATUS_SKIPPED_CAP = "Skipped-cap"
STATUS_SKIPPED_SUPPRESSED = "Skipped-suppressed"
STATUS_FAILED = "Failed"

# ONE. A field a workflow writes never triggers a workflow. See the module
# docstring.
DEPTH_CEILING = 1

CACHE_KEY = "crm_workflow_rules"

# The counter columns `reserve_daily_slot` spends on a rule.
COUNT_FIELD = "actions_today"
DAY_FIELD = "counter_day"

# What one queued job is told, and the reason the event is not called `event`.
#
# `frappe.enqueue` has parameters of its own -- `method`, `queue`, `timeout`,
# `event`, `is_async`, `job_name`, `now`, `enqueue_after_commit`, `at_front`,
# `job_id`, `deduplicate` -- and only what is left over reaches the job. A kwarg
# whose name collides is swallowed SILENTLY at the enqueue and then the worker
# raises `TypeError: missing 1 required positional argument` into a log nobody
# reads. That happened once with `event`; `test_no_job_argument_collides_with_enqueue`
# is what stops it happening again.
JOB_KWARGS = ("rule", "reference_doctype", "reference_docname", "workflow_event", "source")

LOG_RETENTION_DAYS = 90
CLEANUP_BATCH = 500
CLEANUP_MAX_BATCHES = 20

MAX_REASON_LENGTH = 500

MANAGER_ROLES = ("Sales Manager", "System Manager")

# Framework-owned columns. An automation that could set `owner` or `modified`
# would rewrite history; one that could set `name` would rename records.
PROTECTED_FIELDS = frozenset(
	{
		"name",
		"owner",
		"creation",
		"modified",
		"modified_by",
		"docstatus",
		"idx",
		"doctype",
		"parent",
		"parenttype",
		"parentfield",
		"naming_series",
		"_assign",
		"_comments",
		"_liked_by",
		"_user_tags",
	}
)

# Fields the condition builder offers that are not in the doctype's meta.
STANDARD_READABLE_FIELDS = frozenset(
	{"name", "owner", "creation", "modified", "modified_by", "_assign", "_user_tags", "_liked_by"}
)


def commit():
	"""Named so a test can watch it. See `crm/reminders.py` for the precedent."""
	frappe.db.commit()


# --- the cached rule table -------------------------------------------------


def clear_cache() -> None:
	"""Drop the blob. Called from the rule's hooks and from FCRM Settings."""
	frappe.cache().delete_value(CACHE_KEY)


def on_settings_update(doc=None, method=None) -> None:
	"""FCRM Settings was saved: the master flag may have moved."""
	clear_cache()


def build_cache() -> dict:
	"""Read the flag and every enabled rule. Two queries, once per invalidation."""
	from crm.feature_flags import is_enabled

	blob = {"enabled": bool(is_enabled(FLAG_WORKFLOW_RULES)), "rules": {}}
	if not blob["enabled"]:
		# A site with the feature off does not need the rule list, and not reading
		# it keeps the OFF path to a single query.
		return blob

	rules = frappe.get_all(
		RULE_DOCTYPE,
		filters={"enabled": 1},
		fields=["name", "title", "apply_on", "event", "watched_field", "condition_json", "daily_action_cap"],
		order_by="creation asc",
	)

	for rule in rules:
		if rule.apply_on not in APPLY_ON or rule.event not in EVENTS:
			continue
		try:
			conditions = json.loads(rule.condition_json) if rule.condition_json else []
		except ValueError:
			# Stored JSON that no longer parses. Skipping is the safe direction:
			# a rule whose "if" cannot be read must not act on every record.
			frappe.log_error(f"Rule {rule.name} has an unreadable condition.", "CRM workflow rules")
			continue

		blob["rules"].setdefault(rule.apply_on, {}).setdefault(rule.event, []).append(
			{
				"name": rule.name,
				"title": rule.title,
				"watched_field": rule.watched_field,
				"conditions": conditions,
			}
		)

	return blob


def rule_cache() -> dict:
	"""The blob, from Redis when it is warm. Never raises, never returns None."""
	try:
		cached = frappe.cache().get_value(CACHE_KEY)
	except Exception:
		cached = None

	if isinstance(cached, dict):
		return cached

	blob = build_cache()
	try:
		frappe.cache().set_value(CACHE_KEY, blob)
	except Exception:
		# No cache is a slow engine, not a broken one.
		pass
	return blob


# --- the hot path ----------------------------------------------------------


def after_insert(doc, method=None) -> None:
	"""`after_insert` on CRM Lead and CRM Deal."""
	run_event(doc, EVENT_CREATED)


def on_update(doc, method=None) -> None:
	"""`on_update` on CRM Lead and CRM Deal.

	Both change events are answered here. An insert reaches this too -- the
	framework runs `on_update` after `after_insert` -- and `has_changed` reports
	True for every field of a new document, which is correct: a deal created
	already Won must fire the Won rule once.
	"""
	run_event(doc, EVENT_STAGE_CHANGED)
	run_event(doc, EVENT_FIELD_CHANGED)


def run_event(doc, event: str) -> None:
	"""Decide which rules fire, and queue them for after the commit.

	Cost on a site with the feature off, or with no rule for this doctype and
	event: one `frappe.local` attribute read and one Redis read. No query.
	"""
	# First, and cheapest: a workflow-caused save never re-enters the engine.
	if current_depth() >= DEPTH_CEILING:
		return

	if doc.doctype not in APPLY_ON:
		return

	blob = rule_cache()
	if not blob.get("enabled"):
		return

	rules = (blob.get("rules") or {}).get(doc.doctype, {}).get(event) or []
	if not rules:
		return

	source = source_for(doc, event)
	for rule in rules:
		if not fires(rule, doc, event):
			continue

		queue_after_commit(
			"crm.workflows.execute_rule",
			rule=rule["name"],
			reference_doctype=doc.doctype,
			reference_docname=doc.name,
			workflow_event=event,
			source=source,
		)


def source_for(doc, event: str) -> str:
	"""What separates a repeat from a retry in the execution key.

	The document's `modified` timestamp plus the event. A later save of the same
	record is a different source and may run again; the same save picked up twice
	is the same source and runs once.
	"""
	return f"{event}@{frappe.utils.cstr(doc.get('modified'))}"


def fires(rule: dict, doc, event: str) -> bool:
	"""True when this rule's when-and-if are both satisfied by this save.

	`has_changed` reads `doc.get_doc_before_save()`, which the framework already
	loaded for this save. No query is added here.
	"""
	if event == EVENT_STAGE_CHANGED and not has_changed(doc, STAGE_FIELD):
		return False

	if event == EVENT_FIELD_CHANGED:
		field = rule.get("watched_field")
		if not field or not has_changed(doc, field):
			return False

	return matches(rule.get("conditions"), doc)


# --- conditions ------------------------------------------------------------
# The stored shape is exactly what the assignment-rule builder produces:
# a flat list of [field, operator, value] rows separated by "and" / "or"
# strings, where a row may itself be a nested list of rows.
#
# It is evaluated STRUCTURALLY, never by building a Python expression and
# evaluating it. `safe_eval` is safe enough for a desk-only assignment rule; a
# condition an operator types into a web form and that then runs on every save
# of every lead is not the place to find out how safe "safe enough" is.


def is_group(item) -> bool:
	return isinstance(item, list) and bool(item) and isinstance(item[0], list)


def condition_fields(conditions) -> list[str]:
	"""Every fieldname a condition tree names. Raises on a malformed row."""
	found: list[str] = []
	for item in conditions or []:
		if isinstance(item, str):
			if item.strip().lower() not in ("and", "or"):
				frappe.throw(_("{0} is not a valid way to join two conditions.").format(item))
			continue

		if is_group(item):
			found.extend(condition_fields(item))
			continue

		if not isinstance(item, list) or len(item) != 3:
			frappe.throw(_("A condition needs a field, an operator and a value."))

		field, operator = item[0], item[1]
		if not isinstance(field, str) or not field.strip():
			frappe.throw(_("A condition is missing its field."))
		if not isinstance(operator, str) or not operator.strip():
			frappe.throw(_("The condition on {0} is missing its operator.").format(field))

		found.append(field.strip())

	return found


def matches(conditions, doc) -> bool:
	"""Fold a condition tree over one document. No conditions means every record.

	The rows are folded left to right, honouring each joining word as it is met.
	The builder writes ONE joining word per level, so left-to-right folding is
	exactly what the manager saw on screen.
	"""
	if not conditions:
		return True

	result = None
	joiner = "and"

	for item in conditions:
		if isinstance(item, str):
			word = item.strip().lower()
			joiner = word if word in ("and", "or") else "and"
			continue

		if not isinstance(item, list):
			continue

		value = matches(item, doc) if is_group(item) else evaluate(item, doc)

		if result is None:
			result = value
		elif joiner == "or":
			result = result or value
		else:
			result = result and value

	return True if result is None else bool(result)


def evaluate(row, doc) -> bool:
	"""One [field, operator, value] row against one document."""
	if not isinstance(row, list) or len(row) != 3:
		return False

	field, operator, expected = row
	if not isinstance(field, str) or not field.strip():
		return False

	actual = doc.get(field.strip())
	op = frappe.utils.cstr(operator).strip().lower()
	op = {"equals": "==", "=": "==", "not equals": "!="}.get(op, op)

	if op == "is":
		wanted = frappe.utils.cstr(expected).strip().lower()
		if wanted == "not set":
			return not truthy(actual)
		return truthy(actual)

	if op == "==":
		return same(actual, expected)
	if op == "!=":
		return not same(actual, expected)

	if op == "like":
		return truthy(actual) and frappe.utils.cstr(expected) in frappe.utils.cstr(actual)
	if op == "not like":
		return truthy(actual) and frappe.utils.cstr(expected) not in frappe.utils.cstr(actual)

	if op in ("in", "not in"):
		options = as_list(expected)
		present = frappe.utils.cstr(actual) in options
		return truthy(actual) and (present if op == "in" else not present)

	if op == "between":
		bounds = as_list(expected)
		if len(bounds) != 2:
			return False
		low, high = bounds
		return compare(actual, low) >= 0 and compare(actual, high) <= 0

	if op in ("<", "<=", ">", ">="):
		if actual is None:
			return False
		verdict = compare(actual, expected)
		return {
			"<": verdict < 0,
			"<=": verdict <= 0,
			">": verdict > 0,
			">=": verdict >= 0,
		}[op]

	return False


def truthy(value) -> bool:
	"""What the assignment-rule expression means by a bare field reference."""
	if value is None:
		return False
	if isinstance(value, str):
		return bool(value.strip())
	return bool(value)


def same(actual, expected) -> bool:
	"""Equality, with the two shapes the condition builder actually produces.

	A Check field is offered as Yes / No in the builder and stored as 1 / 0 on
	the record, so those are compared as truth rather than as text. Everything
	else is compared as a number when both sides are numbers and as text
	otherwise -- `2` from a form field and `2.0` on the record are the same
	value, and a manager who typed `2` meant that.
	"""
	wanted = frappe.utils.cstr(expected).strip()

	if wanted.lower() in ("yes", "no") and isinstance(actual, int | bool):
		return truthy(actual) is (wanted.lower() == "yes")

	if actual is None:
		return wanted == ""

	try:
		return float(actual) == float(wanted)
	except (TypeError, ValueError):
		return frappe.utils.cstr(actual) == wanted


def compare(actual, expected) -> int:
	"""-1, 0 or 1. Numeric when both sides are numbers, text otherwise."""
	try:
		left, right = float(actual), float(expected)
	except (TypeError, ValueError):
		left, right = frappe.utils.cstr(actual), frappe.utils.cstr(expected)

	if left < right:
		return -1
	return 1 if left > right else 0


def as_list(value) -> list[str]:
	"""The builder writes an In value as a list or as a comma-separated string."""
	if isinstance(value, list | tuple):
		items = value
	else:
		items = frappe.utils.cstr(value).split(",")
	return [frappe.utils.cstr(item).strip() for item in items if frappe.utils.cstr(item).strip()]


# --- execution, after the commit -------------------------------------------


def execute_rule(rule, reference_doctype, reference_docname, workflow_event, source, **kwargs) -> None:
	"""Run one rule's actions on one record. The background half of the engine.

	Everything is re-checked here rather than trusted from the enqueue: the flag,
	the rule, the record and the acting user. Master spec §3 requires a
	background job to check permissions at execution time, and the gap between
	the save and the worker is exactly where a manager disables a rule.

	The event argument is `workflow_event`, not `event`: see `JOB_KWARGS`.
	"""
	event = workflow_event
	if not rule_cache().get("enabled"):
		return

	if not frappe.db.exists(RULE_DOCTYPE, rule):
		return

	rule_doc = frappe.get_doc(RULE_DOCTYPE, rule)
	if not rule_doc.enabled:
		return

	if reference_doctype not in APPLY_ON or not frappe.db.exists(reference_doctype, reference_docname):
		return

	doc = frappe.get_doc(reference_doctype, reference_docname)

	actor = acting_user(rule_doc)
	original = frappe.session.user

	try:
		if actor:
			frappe.set_user(actor)

		for index, action in enumerate(rule_doc.actions):
			run_action(rule_doc, action, index, doc, event, source, actor)
	finally:
		if actor:
			frappe.set_user(original)


def acting_user(rule_doc) -> str | None:
	"""The rule's owner, if they may still act. None when they may not.

	A rule carries the authority of the manager who wrote it, not of the agent
	whose save happened to trigger it -- otherwise a rule would do more for a
	manager's own edits than for an agent's, which is not what "when a deal
	reaches Won" means. Re-checked here, at execution time.
	"""
	owner = rule_doc.owner
	if not owner or owner == "Administrator":
		return None

	if not frappe.db.exists("User", owner):
		return None

	if not frappe.db.get_value("User", owner, "enabled"):
		return None

	if not set(frappe.get_roles(owner)) & set(MANAGER_ROLES):
		return None

	return owner


def run_action(rule_doc, action, index: int, doc, event: str, source: str, actor: str | None) -> None:
	"""Claim, reserve, act, record. In that order, for a reason each.

	The claim comes first and is committed, so a crash cannot lose it and a
	retry cannot get past it. The cap is spent second, so a duplicate delivery
	that never happens does not burn a slot. The action is last, and whatever it
	returns is written on the row the claim created.
	"""
	key = execution_key(
		rule_doc.name,
		doc.doctype,
		doc.name,
		source,
		f"{index}:{action.action_type}",
	)

	log = claim(key, rule_doc, action, index, doc, event)
	if log is None:
		return

	cap = frappe.utils.cint(rule_doc.daily_action_cap)
	if not reserve_daily_slot(RULE_DOCTYPE, rule_doc.name, cap, COUNT_FIELD, DAY_FIELD):
		finish(log, STATUS_SKIPPED_CAP, _("The rule already ran {0} actions today.").format(cap))
		notify_owner_of_cap(rule_doc, cap)
		return

	# One savepoint per action, so an action that half-wrote something is undone
	# without touching the claim above it or the actions beside it. A blanket
	# rollback here would discard the whole worker's transaction, which is the
	# `crm/sequences/core.py` lesson: one bad row must cost one row.
	savepoint = f"crm_workflow_{frappe.generate_hash(length=8)}"
	frappe.db.savepoint(savepoint)

	try:
		status, reason = perform(rule_doc, action, doc, actor)
	except Exception as error:
		frappe.db.rollback(save_point=savepoint)
		# The ROW gets the message, because a manager reading Recent runs needs to
		# know what went wrong and the top of a traceback never says. The whole
		# traceback goes to the Error Log, where a developer will look for it.
		frappe.log_error(frappe.get_traceback(with_context=False), f"CRM workflow rule {rule_doc.name}")
		status = STATUS_FAILED
		reason = f"{type(error).__name__}: {error}"

	finish(log, status, reason)


def claim(key: str, rule_doc, action, index: int, doc, event: str) -> str | None:
	"""Take the execution key. Returns the log name, or None when it was taken."""
	row = frappe.new_doc(LOG_DOCTYPE)
	row.update(
		{
			"execution_key": key,
			"rule": rule_doc.name,
			"rule_title": rule_doc.title,
			"reference_doctype": doc.doctype,
			"reference_docname": doc.name,
			"event": event,
			"action_index": index,
			"action_type": action.action_type,
			"status": STATUS_CLAIMED,
		}
	)

	try:
		row.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		# The ordinary outcome of a retry or a second worker.
		return None

	commit()
	return row.name


def finish(log_name: str, status: str, reason: str = "") -> None:
	"""Write how the action ended, and commit it."""
	updates = {"status": status, "executed_at": frappe.utils.now_datetime()}
	if reason:
		updates["reason"] = frappe.utils.cstr(reason)[:MAX_REASON_LENGTH]

	frappe.db.set_value(LOG_DOCTYPE, log_name, updates, update_modified=False)
	commit()


def notify_owner_of_cap(rule_doc, cap: int) -> None:
	"""Tell the rule's owner once a day that the rule stopped acting.

	Claimed with ONE update. Read-then-write would let two workers that both hit
	the cap in the same second each send the notification.
	"""
	today = frappe.utils.nowdate()

	frappe.db.sql(
		f"""
		update `tab{RULE_DOCTYPE}`
		set `cap_notified_on` = %(today)s
		where name = %(name)s
			and (`cap_notified_on` is null or `cap_notified_on` != %(today)s)
		""",
		{"today": today, "name": rule_doc.name},
	)

	if counters.rows_affected() != 1:
		return

	if not rule_doc.owner or not frappe.db.exists("User", rule_doc.owner):
		return

	text = _("The workflow rule {0} reached its daily cap of {1} actions and stopped for today.").format(
		rule_doc.title, cap
	)
	notify_user(
		{
			"owner": None,
			"assigned_to": rule_doc.owner,
			"notification_type": "Task",
			"message": text,
			"notification_text": text,
			"reference_doctype": RULE_DOCTYPE,
			"reference_docname": rule_doc.name,
			"redirect_to_doctype": RULE_DOCTYPE,
			"redirect_to_docname": rule_doc.name,
		}
	)
	commit()


# --- the four actions ------------------------------------------------------


def perform(rule_doc, action, doc, actor: str | None) -> tuple[str, str]:
	"""Dispatch one action. Returns (status, reason)."""
	if action.action_type == ACTION_EMAIL:
		return send_template_email(action, doc)
	if action.action_type == ACTION_TASK:
		return create_task(action, doc, actor)
	if action.action_type == ACTION_NOTIFY:
		return notify(action, doc, actor)
	if action.action_type == ACTION_UPDATE:
		return update_field(action, doc)

	return STATUS_FAILED, _("{0} is not a workflow action.").format(action.action_type)


def record_owner(doc) -> str | None:
	"""Who the lead or deal belongs to."""
	return doc.get(OWNER_FIELD.get(doc.doctype, "")) or doc.get("owner")


def email_address(action, doc) -> str | None:
	if action.recipient_mode == RECIPIENT_SPECIFIC:
		return action.recipient_address

	if action.recipient_mode == RECIPIENT_ASSIGNED:
		owner = record_owner(doc)
		return frappe.db.get_value("User", owner, "email") if owner else None

	return doc.get("email")


def send_template_email(action, doc) -> tuple[str, str]:
	"""Render the template and send it through the composer's own send path.

	`crm.api.email.send_email` is the ONE send path in this app that checks the
	suppression ledger and the `email` permission on the record. The address is
	checked here as well, before anything is rendered, so a suppressed customer
	produces a Skipped-suppressed row rather than a raised exception from a path
	that is meant for a human at a keyboard.
	"""
	from crm.api.email import send_email

	address = email_address(action, doc)
	if not address:
		return STATUS_FAILED, _("The record has no email address to send to.")

	if is_suppressed(CHANNEL_EMAIL, address):
		return STATUS_SKIPPED_SUPPRESSED, _("{0} has opted out of email.").format(address)

	subject, content = render_template(action.email_template, doc)

	send_email(
		doctype=doc.doctype,
		name=doc.name,
		recipients=address,
		subject=subject,
		content=content,
	)
	return STATUS_EXECUTED, ""


def render_template(template_name: str, doc) -> tuple[str, str]:
	"""Subject and body of an Email Template, with the record as `doc`."""
	template = frappe.get_doc("Email Template", template_name)
	body = template.response_html if template.use_html else template.response
	context = {"doc": doc, "user": frappe.session.user}

	subject = frappe.render_template(template.subject or "", context)
	content = frappe.render_template(body or "", context)
	return subject, content


def create_task(action, doc, actor: str | None) -> tuple[str, str]:
	"""One CRM Task, linked back to the record that triggered the rule."""
	offset = max(frappe.utils.cint(action.task_due_offset_days), 0)

	task = frappe.new_doc(TASK_DOCTYPE)
	task.update(
		{
			"title": action.task_title,
			"priority": action.task_priority or "Medium",
			"status": "Todo",
			"due_date": frappe.utils.add_to_date(frappe.utils.now_datetime(), days=offset),
			"assigned_to": record_owner(doc) or actor,
			"reference_doctype": doc.doctype,
			"reference_docname": doc.name,
		}
	)
	task.insert()
	return STATUS_EXECUTED, ""


def notify_recipients(action, doc, actor: str | None) -> list[str]:
	if action.notify_mode == NOTIFY_SPECIFIC:
		return [action.notify_user] if action.notify_user else []

	if action.notify_mode == NOTIFY_ROLE:
		if not action.notify_role:
			return []
		return [
			user
			for user in frappe.get_all(
				"Has Role",
				filters={"role": action.notify_role, "parenttype": "User"},
				pluck="parent",
			)
			if frappe.db.get_value("User", user, "enabled")
		]

	owner = record_owner(doc) or actor
	return [owner] if owner else []


def notify(action, doc, actor: str | None) -> tuple[str, str]:
	"""In-app notifications only. A rule may not mail a colleague behind a flag."""
	recipients = notify_recipients(action, doc, actor)
	if not recipients:
		return STATUS_FAILED, _("There was nobody to notify.")

	text = _("{0}: {1}").format(_(doc.doctype), doc.get("title") or doc.name)
	for recipient in recipients:
		notify_user(
			{
				"owner": None,
				"assigned_to": recipient,
				"notification_type": "Task",
				"message": text,
				"notification_text": text,
				"reference_doctype": doc.doctype,
				"reference_docname": doc.name,
				"redirect_to_doctype": doc.doctype,
				"redirect_to_docname": doc.name,
			}
		)

	return STATUS_EXECUTED, ""


def update_field(action, doc) -> tuple[str, str]:
	"""Set one field on the record, one level deep and no deeper.

	The save happens inside `execution_depth(limit=1)`, so the `on_update` hook
	it triggers finds the depth already at the ceiling and returns before it
	reads anything. That is the whole cascade guard: one rule may write a field,
	and the write is the end of the chain.
	"""
	field = action.update_field
	if field in PROTECTED_FIELDS or not frappe.get_meta(doc.doctype).get_field(field):
		return STATUS_FAILED, _("{0} is not a field this automation may set.").format(field)

	if frappe.utils.cstr(doc.get(field)) == frappe.utils.cstr(action.update_value):
		return STATUS_EXECUTED, _("The field already held that value.")

	with execution_depth(limit=DEPTH_CEILING):
		doc.set(field, action.update_value)
		doc.save()

	return STATUS_EXECUTED, ""


# --- housekeeping ----------------------------------------------------------


def cleanup_execution_log() -> None:
	"""Keep 90 days of execution log. Daily, bounded, and never fatal.

	Bounded on purpose: a site that has not been cleaned for a year deletes
	`CLEANUP_BATCH * CLEANUP_MAX_BATCHES` rows tonight and the rest tomorrow,
	rather than holding one enormous transaction open.
	"""
	cutoff = frappe.utils.add_days(frappe.utils.nowdate(), -LOG_RETENTION_DAYS)

	for _batch in range(CLEANUP_MAX_BATCHES):
		names = frappe.get_all(
			LOG_DOCTYPE,
			filters={"creation": ("<", cutoff)},
			pluck="name",
			order_by="creation asc",
			limit=CLEANUP_BATCH,
		)
		if not names:
			return

		frappe.db.delete(LOG_DOCTYPE, {"name": ("in", names)})
		commit()


# --- endpoints -------------------------------------------------------------


def check_manager() -> None:
	"""Managers only (design note, master spec §3). Every endpoint calls this."""
	if not set(frappe.get_roles()) & set(MANAGER_ROLES):
		frappe.throw(_("Only a sales manager can manage workflow rules."), frappe.PermissionError)


@frappe.whitelist()
def get_rules() -> list[dict]:
	"""Every rule, with how many actions it has run today."""
	check_manager()

	today = frappe.utils.nowdate()
	rules = frappe.get_all(
		RULE_DOCTYPE,
		fields=[
			"name",
			"title",
			"apply_on",
			"event",
			"watched_field",
			"enabled",
			"daily_action_cap",
			"actions_today",
			"counter_day",
			"modified",
		],
		order_by="modified desc",
	)

	for rule in rules:
		rule["runs_today"] = rule["actions_today"] if frappe.utils.cstr(rule["counter_day"]) == today else 0

	return rules


@frappe.whitelist()
def get_rule(name: str) -> dict:
	"""One rule, with its actions and its condition parsed for the builder."""
	check_manager()

	doc = frappe.get_doc(RULE_DOCTYPE, name)
	data = doc.as_dict()
	data["condition_json"] = json.loads(doc.condition_json) if doc.condition_json else []
	return data


@frappe.whitelist(methods=["POST"])
def save_rule(rule) -> str:
	"""Create or update one rule. Returns its name.

	The doctype's own `validate` is the authority on what a rule may say; this
	only decides which submitted keys are allowed to reach it. `name` is taken
	from the payload solely to choose between insert and update.
	"""
	check_manager()

	rule = frappe.parse_json(rule) if isinstance(rule, str) else rule
	rule = frappe._dict(rule or {})

	name = rule.get("name")
	if name and frappe.db.exists(RULE_DOCTYPE, name):
		doc = frappe.get_doc(RULE_DOCTYPE, name)
	else:
		doc = frappe.new_doc(RULE_DOCTYPE)

	for field in ("title", "apply_on", "event", "watched_field", "enabled", "daily_action_cap"):
		if field in rule:
			doc.set(field, rule.get(field))

	if "condition_json" in rule:
		conditions = rule.get("condition_json")
		if isinstance(conditions, str):
			conditions = frappe.parse_json(conditions or "[]")
		doc.condition_json = json.dumps(conditions or [])

	if "actions" in rule:
		doc.set("actions", [])
		for row in rule.get("actions") or []:
			doc.append("actions", {key: row.get(key) for key in ALLOWED_ACTION_FIELDS if key in row})

	doc.save()
	return doc.name


ALLOWED_ACTION_FIELDS = (
	"action_type",
	"email_template",
	"recipient_mode",
	"recipient_address",
	"task_title",
	"task_priority",
	"task_due_offset_days",
	"notify_mode",
	"notify_user",
	"notify_role",
	"update_field",
	"update_value",
)


@frappe.whitelist(methods=["POST"])
def set_rule_enabled(name: str, enabled: int) -> int:
	"""The list's toggle. One field, so it needs no round trip through the form."""
	check_manager()

	doc = frappe.get_doc(RULE_DOCTYPE, name)
	doc.enabled = 1 if frappe.utils.cint(enabled) else 0
	doc.save()
	return doc.enabled


@frappe.whitelist(methods=["POST"])
def delete_rule(name: str) -> None:
	"""Delete a rule. Its execution log rows stay: they are the history."""
	check_manager()
	frappe.delete_doc(RULE_DOCTYPE, name)


@frappe.whitelist()
def get_recent_runs(rule: str | None = None, limit: int = 20) -> list[dict]:
	"""The read-only Recent runs panel."""
	check_manager()

	filters = {"rule": rule} if rule else {}
	return frappe.get_all(
		LOG_DOCTYPE,
		filters=filters,
		fields=[
			"name",
			"rule",
			"rule_title",
			"reference_doctype",
			"reference_docname",
			"event",
			"action_type",
			"status",
			"reason",
			"executed_at",
			"creation",
		],
		order_by="creation desc",
		limit=min(max(frappe.utils.cint(limit) or 20, 1), 100),
	)
