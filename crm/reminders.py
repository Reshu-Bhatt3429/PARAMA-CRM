"""Task due-date reminders (master spec item 1), on the F5 ledger.

Why this file is not `crm/api/event.py`
---------------------------------------
The event-reminder path is registered on BOTH the `all` and the `hourly`
scheduler events, so the same reminder is produced twice. The master spec calls
that out by name and forbids repeating it. Two things here make the number of
schedules irrelevant:

* one schedule. `send_task_reminders` is registered on exactly one cron entry
  (`*/15 * * * *`) in `crm/hooks.py`; and
* one ledger. Every delivery first claims a `CRM Reminder Log` row whose
  `dedup_key` carries a unique index. A second call -- a double-fire, a manual
  run, two workers racing -- collides on that index and stops there instead of
  notifying the user again.

The claim is committed BEFORE the notification is written. If the process dies
between the two, the reminder is lost rather than sent twice. That is the
deliberate direction: at-most-once, like every other outbound path in this app.

The window
----------
A reminder for a task is due at `due_date - offset`. At run time T the tasks
whose reminder time has arrived are exactly those with
`due_date <= T + offset`. The query is bounded below as well, at
`T + offset - LOOKBACK`, so a scheduler that was down for a weekend catches up
one day's worth of reminders and does not wake up the whole task table. Both
bounds are on `due_date`, with a `status` filter, which is the composite index
`due_date_status_index` that Stage 1A put on `tabCRM Task`.

Authorization
-------------
This module adds no whitelisted endpoint. It is reached from the scheduler only.
It reads tasks with `frappe.get_all` (no permission conditions) because no
result reaches a request: the only thing it does with a task is notify the user
that task is already assigned to. The recipient is re-checked AT EXECUTION TIME
-- a disabled user is never notified and never mailed -- and the email leg asks
`crm.suppression.is_suppressed` before it queues anything.

The offset is an ORG-WIDE default in FCRM Settings, not a per-user preference.
There is no per-user preference store in this app today (`crm/integrations/api.py`
reads its per-user default off a CRM Telephony Agent row, which is a doctype for
something else), and inventing one for a single integer is not a v1 decision.
`reminder_offset_minutes` is the only reader of the field, so a future per-user
override has exactly one place to hook into.
"""

import frappe
from frappe import _

from crm.fcrm.doctype.crm_notification.crm_notification import notify_user
from crm.fcrm.doctype.crm_reminder_log.crm_reminder_log import reminder_key
from crm.feature_flags import is_enabled
from crm.suppression import CHANNEL_EMAIL, is_suppressed

REMINDER_LOG = "CRM Reminder Log"
TASK_DOCTYPE = "CRM Task"
SETTINGS_DOCTYPE = "FCRM Settings"

FLAG_TASK_REMINDERS = "task_reminders_enabled"

CHANNEL_NOTIFICATION = "Notification"
CHANNEL_EMAIL_LOG = "Email"

STATUS_CLAIMED = "Claimed"
STATUS_SENT = "Sent"
STATUS_FAILED = "Failed"
STATUS_SUPPRESSED = "Suppressed"

# A finished task needs no reminder. Both spellings of the cancelled state are
# listed: `CRM Task` ships "Canceled", and a site that renamed the status must
# not start reminding about cancelled work.
CLOSED_STATUSES = ("Done", "Canceled", "Cancelled")

DEFAULT_OFFSET_MINUTES = 60
# A reminder more than four weeks ahead of its task is not a reminder.
MAX_OFFSET_MINUTES = 40320

# How far back the catch-up reaches. One day: enough to survive a scheduler
# outage, short enough that switching the feature on does not replay history.
LOOKBACK_MINUTES = 1440

# The most tasks one run will look at. Ordered by due date, so the most urgent
# are always the ones that fit.
SWEEP_LIMIT = 500

MAX_ERROR_LENGTH = 200


# --- transaction seams -----------------------------------------------------
# Same contract as `crm.outbound`: the scheduler needs real commit boundaries
# and the tests need to neutralise them and assert their ordering.


def commit():
	frappe.db.commit()


def rollback():
	frappe.db.rollback()


# --- settings --------------------------------------------------------------


def reminder_offset_minutes() -> int:
	"""Minutes before the due date a reminder fires. Org-wide, default 60.

	Never raises and never returns something the window maths cannot use: an
	unreadable or nonsensical value falls back to the default.
	"""
	try:
		value = frappe.db.get_single_value(SETTINGS_DOCTYPE, "task_reminder_offset_minutes")
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM reminders: could not read the offset")
		return DEFAULT_OFFSET_MINUTES

	if value in (None, ""):
		return DEFAULT_OFFSET_MINUTES

	try:
		minutes = int(value)
	except (TypeError, ValueError):
		return DEFAULT_OFFSET_MINUTES

	if minutes < 0:
		return DEFAULT_OFFSET_MINUTES
	return min(minutes, MAX_OFFSET_MINUTES)


def email_enabled() -> bool:
	"""True when the reminder also goes out as an email. Off by default."""
	try:
		return bool(frappe.db.get_single_value(SETTINGS_DOCTYPE, "task_reminder_email"))
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM reminders: could not read the email switch")
		return False


# --- the window ------------------------------------------------------------


def due_window(now, offset_minutes: int) -> tuple[str, str]:
	"""The `due_date` range whose reminders are due at `now`, as strings."""
	end = frappe.utils.add_to_date(now, minutes=offset_minutes)
	start = frappe.utils.add_to_date(end, minutes=-LOOKBACK_MINUTES)
	return frappe.utils.get_datetime_str(start), frappe.utils.get_datetime_str(end)


def find_due_tasks(now=None, offset_minutes: int | None = None, limit: int = SWEEP_LIMIT) -> list[dict]:
	"""Open tasks whose reminder time has arrived, most urgent first."""
	now = now or frappe.utils.now_datetime()
	if offset_minutes is None:
		offset_minutes = reminder_offset_minutes()

	start, end = due_window(now, offset_minutes)

	return frappe.get_all(
		TASK_DOCTYPE,
		filters={
			"due_date": ["between", [start, end]],
			"status": ["not in", list(CLOSED_STATUSES)],
		},
		fields=[
			"name",
			"title",
			"status",
			"priority",
			"due_date",
			"assigned_to",
			"owner",
			"reference_doctype",
			"reference_docname",
		],
		order_by="due_date asc",
		limit_page_length=limit,
	)


def recipient_of(task: dict) -> str | None:
	"""Who the reminder is for: the assignee, or the creator when unassigned."""
	return task.get("assigned_to") or task.get("owner")


def recipient_is_reachable(user: str) -> bool:
	"""A disabled or deleted user is never notified. Checked at EXECUTION time.

	`Guest` is excluded because it is not a person. `Administrator` is NOT
	excluded: on a small self-hosted site it is somebody's real login, and
	dropping its reminders silently would be a bug nobody could see.
	"""
	if not user or user == "Guest":
		return False
	return bool(frappe.db.get_value("User", user, "enabled"))


# --- the ledger ------------------------------------------------------------


def claim(task: dict, recipient: str, offset_minutes: int, channel: str) -> str | None:
	"""Spend the key for one reminder. Returns the log name, or None if spent.

	The commit is the point of the whole function: it publishes the claim so a
	second worker's insert collides instead of duplicating the reminder.
	"""
	row = frappe.new_doc(REMINDER_LOG)
	row.update(
		{
			"task": task["name"],
			"recipient": recipient,
			"channel": channel,
			"status": STATUS_CLAIMED,
			"offset_minutes": offset_minutes,
			"due_date": task.get("due_date"),
			"dedup_key": reminder_key(task["name"], recipient, offset_minutes, task.get("due_date"), channel),
		}
	)

	try:
		row.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		# Already reminded. This is the ordinary outcome of a second run.
		return None

	commit()
	return row.name


def close(log_name: str, status: str, error: str = "") -> None:
	"""Record how the claimed reminder ended."""
	updates = {"status": status}
	if status == STATUS_SENT:
		updates["sent_at"] = frappe.utils.now_datetime()
	if error:
		updates["last_error"] = error[:MAX_ERROR_LENGTH]

	frappe.db.set_value(REMINDER_LOG, log_name, updates, update_modified=False)
	commit()


# --- delivery --------------------------------------------------------------


def reminder_text(task: dict) -> str:
	"""The one-line body both channels use."""
	title = task.get("title") or task["name"]
	due = frappe.utils.format_datetime(task.get("due_date")) if task.get("due_date") else ""
	if due:
		return _("Task {0} is due at {1}").format(title, due)
	return _("Task {0} is due").format(title)


def deliver_notification(task: dict, recipient: str) -> None:
	"""Write the in-app notification.

	`owner` is left unset on purpose. `notify_user` returns early when the owner
	equals the recipient, and a reminder for a task somebody made for themselves
	is exactly that case -- the commonest one. A reminder has no human sender.
	"""
	text = reminder_text(task)
	notify_user(
		{
			"owner": None,
			"assigned_to": recipient,
			"notification_type": "Task",
			"message": text,
			"notification_text": text,
			"reference_doctype": TASK_DOCTYPE,
			"reference_docname": task["name"],
			"redirect_to_doctype": task.get("reference_doctype") or TASK_DOCTYPE,
			"redirect_to_docname": task.get("reference_docname") or task["name"],
		}
	)


def deliver_email(task: dict, recipient: str) -> str:
	"""Queue the reminder email. Returns the log status it earned.

	Suppression is checked here, not by the caller: this is a send path, and the
	master spec's must-not-do list has no send path without a suppression check.
	"""
	address = frappe.db.get_value("User", recipient, "email") or recipient
	if is_suppressed(CHANNEL_EMAIL, address):
		return STATUS_SUPPRESSED

	frappe.sendmail(
		recipients=[address],
		subject=_("Reminder: {0}").format(task.get("title") or task["name"]),
		message=reminder_text(task),
		reference_doctype=TASK_DOCTYPE,
		reference_name=task["name"],
	)
	return STATUS_SENT


# --- the sweep -------------------------------------------------------------


def remind_about(task: dict, offset_minutes: int, with_email: bool) -> int:
	"""Deliver every channel's reminder for one task. Returns how many were sent."""
	recipient = recipient_of(task)
	if not recipient_is_reachable(recipient):
		return 0

	sent = 0

	log_name = claim(task, recipient, offset_minutes, CHANNEL_NOTIFICATION)
	if log_name:
		try:
			deliver_notification(task, recipient)
			close(log_name, STATUS_SENT)
			sent += 1
		except Exception:
			rollback()
			close(log_name, STATUS_FAILED, frappe.get_traceback())
			frappe.log_error(frappe.get_traceback(), f"CRM reminders: notification for {task['name']} failed")

	if with_email:
		log_name = claim(task, recipient, offset_minutes, CHANNEL_EMAIL_LOG)
		if log_name:
			try:
				close(log_name, deliver_email(task, recipient))
				sent += 1
			except Exception:
				rollback()
				close(log_name, STATUS_FAILED, frappe.get_traceback())
				frappe.log_error(frappe.get_traceback(), f"CRM reminders: email for {task['name']} failed")

	return sent


def send_task_reminders() -> int:
	"""Scheduler entry point. Registered on ONE cron entry, `*/15 * * * *`.

	Returns the number of reminders delivered. Never raises: a scheduler job that
	throws takes the rest of its queue down with it. Gated on
	`task_reminders_enabled`, which is OFF by default -- while it is off this
	function does not read a single task row.
	"""
	try:
		if not is_enabled(FLAG_TASK_REMINDERS):
			return 0

		offset_minutes = reminder_offset_minutes()
		with_email = email_enabled()
		delivered = 0

		for task in find_due_tasks(offset_minutes=offset_minutes):
			try:
				delivered += remind_about(task, offset_minutes, with_email)
			except Exception:
				rollback()
				frappe.log_error(frappe.get_traceback(), f"CRM reminders: task {task.get('name')} failed")

		return delivered
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM reminders: sweep failed")
		return 0
