"""The "Today" page: one prioritised list, four existing queries (spec §5, item 24).

UX §2.17 is the whole design constraint. Today is NOT four stacked panels; it is
one list, sorted by urgency, with filter chips that narrow it. So this endpoint
returns ONE array of rows in one shape, already sorted, plus the counts the chips
and the sidebar badge need. The frontend does no merging and no sorting.

Nothing here is new work. Each of the four sources is an existing query, and each
keeps the authorisation its own surface already has:

## Endpoint authorization (master spec §3)

`crm.api.today.get_today` -- any signed-in CRM user (`sales_user_only`, the same
gate the dashboard uses). It is read-only: it sends nothing, writes nothing, and
reaches no provider. Row scope is derived SERVER-side, per source, and no
argument the caller sends is ever used as a filter:

* **Tasks** -- `frappe.get_list("CRM Task")` for the doctype-level check, plus an
  explicit `assigned_to`/`owner` restriction to `frappe.session.user`. That
  restriction is load-bearing, not decoration: `crm/hooks.py` registers row-level
  conditions for CRM Lead, CRM Deal, CRM Notification, CRM WhatsApp Followup,
  CRM Itinerary and CRM Snippet -- and NOT for CRM Task. A Sales User's Tasks
  page already lists every task on the site; this page must not, and the fix is
  the explicit filter below rather than a promise.
* **Deals** -- `frappe.get_list("CRM Deal")`, which puts
  `crm.permissions.org_hierarchy.get_deal_permission_query_conditions` into the
  SQL.
* **Replies** -- delegated whole to `crm.api.whatsapp.get_whatsapp_conversations`
  with `scope="mine"`. That function runs `validate_access()` and then drops
  every conversation whose Lead/Deal fails `frappe.get_list`. Reimplementing the
  unanswered computation here would have meant reimplementing its scoping too.
* **Approvals** -- `frappe.get_list("CRM WhatsApp Followup")`, scoped by
  `crm.api.followup_engine.get_followup_permission_query_conditions`, which
  resolves to the leads the caller may already see.

The one action that writes -- Approve -- is the EXISTING
`crm.api.followup_engine.approve_pending`, with its own manager and lock checks.
This module adds no write path.
"""

import frappe
from frappe import _

from crm.deal_health import FLAG_DEAL_HEALTH, HEALTH_FIELD
from crm.deal_health import flag_label as deal_flag_label
from crm.deal_health import parse as parse_health_flags
from crm.feature_flags import is_enabled
from crm.utils import sales_user_only

TASK_DOCTYPE = "CRM Task"
DEAL_DOCTYPE = "CRM Deal"
FOLLOWUP_DOCTYPE = "CRM WhatsApp Followup"

# Task statuses that mean "no longer on anybody's plate". Same tuple
# `crm.reminders` uses, including the British spelling the doctype does not have,
# so a renamed option cannot quietly resurrect closed tasks here.
CLOSED_TASK_STATUSES = ("Done", "Canceled", "Cancelled")

STATE_PENDING_APPROVAL = "Pending Approval"

TYPE_TASK = "task"
TYPE_REPLY = "reply"
TYPE_DEAL = "deal"
TYPE_APPROVAL = "approval"

# Filter-chip order, and the tie-break order for two rows that are equally due.
TYPE_ORDER = (TYPE_TASK, TYPE_REPLY, TYPE_DEAL, TYPE_APPROVAL)

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


def clamp_limit(limit) -> int:
	"""A caller-supplied page size is a size, never a filter."""
	limit = frappe.utils.cint(limit) or DEFAULT_LIMIT
	return max(1, min(limit, MAX_LIMIT))


# --- sorting ---------------------------------------------------------------


def sort_key(item):
	"""Overdue first, then by due time, then by a stable type and title order.

	A row with no due time is not urgent-by-absence: it sorts after everything
	that has one, which is why the missing case gets the maximum date rather than
	the minimum.
	"""
	due = item.get("due")
	return (
		0 if item.get("overdue") else 1,
		frappe.utils.get_datetime(due) if due else frappe.utils.get_datetime("9999-12-31 23:59:59"),
		TYPE_ORDER.index(item["type"]) if item["type"] in TYPE_ORDER else len(TYPE_ORDER),
		frappe.utils.cstr(item.get("title")),
	)


# --- tasks -----------------------------------------------------------------


def due_tasks(user: str, limit: int) -> list[dict]:
	"""Tasks due today or already overdue, for this user only."""
	end_of_today = frappe.utils.get_datetime_str(
		frappe.utils.add_to_date(frappe.utils.get_datetime(frappe.utils.nowdate()), days=1, seconds=-1)
	)
	now = frappe.utils.now_datetime()

	base = [
		["due_date", "is", "set"],
		["due_date", "<=", end_of_today],
		["status", "not in", list(CLOSED_TASK_STATUSES)],
	]
	fields = [
		"name",
		"title",
		"status",
		"priority",
		"due_date",
		"assigned_to",
		"owner",
		"reference_doctype",
		"reference_docname",
	]

	rows = frappe.get_list(
		TASK_DOCTYPE,
		filters=[*base, ["assigned_to", "=", user]],
		fields=fields,
		order_by="due_date asc",
		limit_page_length=limit,
	)

	# A task nobody was assigned belongs to whoever made it -- the same rule
	# `crm.reminders.recipient_of` applies when it decides who to remind.
	rows += frappe.get_list(
		TASK_DOCTYPE,
		filters=[*base, ["assigned_to", "is", "not set"], ["owner", "=", user]],
		fields=fields,
		order_by="due_date asc",
		limit_page_length=limit,
	)

	items = []
	for row in rows:
		due = row.get("due_date")
		items.append(
			{
				"key": f"{TYPE_TASK}:{row['name']}",
				"type": TYPE_TASK,
				"title": row.get("title") or _("Untitled task"),
				"context": task_context(row),
				"due": frappe.utils.get_datetime_str(due) if due else None,
				"overdue": bool(due) and frappe.utils.get_datetime(due) < now,
				"action": "open",
				"doctype": TASK_DOCTYPE,
				"name": frappe.utils.cstr(row["name"]),
				"reference_doctype": row.get("reference_doctype"),
				"reference_name": row.get("reference_docname"),
			}
		)

	return items


def task_context(row) -> str:
	parts = [_(row.get("status"))] if row.get("status") else []
	if row.get("priority"):
		parts.append(_(row["priority"]))
	if row.get("due_date"):
		parts.append(_("due {0}").format(frappe.utils.format_datetime(row["due_date"], "d MMM, HH:mm")))
	return " · ".join(parts)


# --- flagged deals ---------------------------------------------------------


def flagged_deals(limit: int) -> list[dict]:
	"""Deals the health sweep flagged, inside the caller's own row scope.

	Returns nothing at all when `deal_health_enabled` is off, even if the column
	still holds values a previous run wrote. A switched-off feature must leave no
	trace in the UI (spec §5, item 22 acceptance criteria).
	"""
	if not is_enabled(FLAG_DEAL_HEALTH):
		return []

	if not frappe.db.has_column(DEAL_DOCTYPE, HEALTH_FIELD):
		return []

	rows = frappe.get_list(
		DEAL_DOCTYPE,
		filters=[[HEALTH_FIELD, "is", "set"]],
		fields=["name", "organization", "lead_name", "status", "expected_closure_date", HEALTH_FIELD],
		order_by="modified desc",
		limit_page_length=limit,
	)

	items = []
	for row in rows:
		flags = parse_health_flags(row.get(HEALTH_FIELD))
		if not flags:
			continue

		items.append(
			{
				"key": f"{TYPE_DEAL}:{row['name']}",
				"type": TYPE_DEAL,
				"title": row.get("organization") or row.get("lead_name") or row["name"],
				"context": ", ".join(deal_flag_label(flag) for flag in flags),
				# A deal has no clock of its own; it earns its place by being
				# flagged, and sorts among the not-overdue rows.
				"due": None,
				"overdue": False,
				"action": "open",
				"doctype": DEAL_DOCTYPE,
				"name": row["name"],
				"reference_doctype": DEAL_DOCTYPE,
				"reference_name": row["name"],
				"flags": flags,
			}
		)

	return items


# --- conversations awaiting a reply ---------------------------------------


def awaiting_reply(limit: int) -> list[dict]:
	"""The inbox's own unanswered list, reused rather than recomputed.

	Never raises. A site without the WhatsApp app, or a user without the inbox
	roles, gets an empty section instead of a broken page.
	"""
	try:
		from crm.api.whatsapp import get_whatsapp_conversations

		conversations = get_whatsapp_conversations(limit=limit, scope="mine")
	except Exception:
		return []

	now = frappe.utils.now_datetime()
	items = []
	for row in conversations or []:
		if not row.get("needs_reply"):
			continue

		waiting_since = row.get("waiting_since")
		items.append(
			{
				"key": f"{TYPE_REPLY}:{row.get('reference_doctype')}:{row.get('reference_name')}",
				"type": TYPE_REPLY,
				"title": row.get("display_name") or row.get("reference_name"),
				"context": reply_context(row),
				# The customer's message is the clock: it was due the moment it
				# arrived, so an unanswered thread is overdue by definition.
				"due": frappe.utils.get_datetime_str(waiting_since) if waiting_since else None,
				"overdue": bool(waiting_since) and frappe.utils.get_datetime(waiting_since) < now,
				"action": "reply",
				"doctype": row.get("reference_doctype"),
				"name": row.get("reference_name"),
				"reference_doctype": row.get("reference_doctype"),
				"reference_name": row.get("reference_name"),
			}
		)

	return items


def reply_context(row) -> str:
	waiting_since = row.get("waiting_since")
	if waiting_since:
		return _("Waiting since {0}").format(frappe.utils.format_datetime(waiting_since, "d MMM, HH:mm"))
	return _("Waiting for a reply")


# --- follow-up drafts waiting for approval ---------------------------------


def lead_names(leads) -> dict:
	"""Display names for the leads behind a page of drafts. One bounded query.

	`frappe.get_list`, so a lead the caller cannot read simply has no name here
	and the row falls back to the docname it already carries.
	"""
	wanted = sorted({lead for lead in leads if lead})
	if not wanted:
		return {}

	rows = frappe.get_list(
		"CRM Lead",
		filters={"name": ["in", wanted]},
		fields=["name", "lead_name", "first_name", "last_name", "organization"],
		limit_page_length=len(wanted),
	)
	return {
		row["name"]: (
			row.get("lead_name")
			or " ".join(part for part in (row.get("first_name"), row.get("last_name")) if part)
			or row.get("organization")
		)
		for row in rows
	}


def pending_approvals(limit: int) -> list[dict]:
	"""Follow-up drafts parked by `hold_for_approval`, in the caller's scope."""
	if not frappe.db.exists("DocType", FOLLOWUP_DOCTYPE):
		return []

	try:
		rows = frappe.get_list(
			FOLLOWUP_DOCTYPE,
			filters={"state": STATE_PENDING_APPROVAL},
			fields=["name", "lead", "pending_stage", "modified"],
			order_by="modified asc",
			limit_page_length=limit,
		)
	except frappe.PermissionError:
		return []

	names = lead_names([row.get("lead") for row in rows])

	items = []
	for row in rows:
		items.append(
			{
				"key": f"{TYPE_APPROVAL}:{row['name']}",
				"type": TYPE_APPROVAL,
				"title": names.get(row.get("lead")) or row.get("lead") or row["name"],
				"context": _("Follow-up draft waiting for approval (stage {0})").format(
					frappe.utils.cint(row.get("pending_stage"))
				),
				# A draft has been waiting since it was parked, and a draft that
				# is never approved is never sent -- so it is overdue on sight.
				"due": frappe.utils.get_datetime_str(row["modified"]) if row.get("modified") else None,
				"overdue": True,
				"action": "approve",
				"doctype": FOLLOWUP_DOCTYPE,
				"name": row["name"],
				"reference_doctype": "CRM Lead" if row.get("lead") else None,
				"reference_name": row.get("lead"),
			}
		)

	return items


# --- the endpoint ----------------------------------------------------------


@frappe.whitelist()
@sales_user_only
def get_today(limit: int | str | None = None) -> dict:
	"""One prioritised list plus the counts the chips and the badge need.

	`limit` bounds EACH source, not the merged list: a hundred overdue tasks must
	not push every awaiting reply off the page.
	"""
	limit = clamp_limit(limit)
	user = frappe.session.user

	items = []
	items += due_tasks(user, limit)
	items += awaiting_reply(limit)
	items += flagged_deals(limit)
	items += pending_approvals(limit)

	# Two sources can name the same row -- a task about a deal, and the deal --
	# and the list must not show it twice.
	seen = set()
	unique = []
	for item in items:
		if item["key"] in seen:
			continue
		seen.add(item["key"])
		unique.append(item)

	unique.sort(key=sort_key)

	counts = {"all": len(unique)}
	for type_name in TYPE_ORDER:
		counts[type_name] = sum(1 for item in unique if item["type"] == type_name)

	return {
		"items": unique,
		"counts": counts,
		# The Deals list reads this to decide whether a Needs attention chip may
		# render at all, so a switched-off feature leaves nothing behind.
		"deal_health_enabled": is_enabled(FLAG_DEAL_HEALTH),
	}
