"""Scheduled WhatsApp nudges: per-agent follow-up reminders and a manager digest.

Both entry points are wired into `scheduler_events` in `crm/hooks.py` (both
hourly). They run unattended, so neither is allowed to raise: a scheduler job
that throws takes the rest of its queue down with it. Every failure is logged
with `frappe.log_error` and the job reports zero work done.

Why the digest is on the HOURLY schedule
----------------------------------------
It used to be `daily`, which fires at the start of the day -- inside the
follow-up engine's default quiet window (21:00 to 09:00). Master spec §5 item 22
says the digest respects quiet hours and a per-user toggle, and Stage 3B recorded
both as missing.

Quiet hours are honoured by SHIFTING the digest, not by cancelling it: the job
now runs every hour, returns without reading anything while `now` is inside the
window the follow-up engine is configured with, and delivers at the first tick
after the window closes. `digest_is_due` makes that at-most-once per user per
day, so twenty-four ticks still produce one digest. It reads the CRM
Notification rows the digest itself writes, which means there is no new state to
keep in step and a manually cleared notification cannot cause a second send on
the same day.

The per-user toggle is `daily_digest` in `CRM User Preference`, default ON.

Nothing here talks to Meta. It only reads WhatsApp Message rows that already
exist and writes CRM Notifications.

The daily digest also carries the deal-health section (master spec §5, item 22).
It is appended to the EXISTING digest rather than given a second notification,
because UX §2.10 batches notifications and a manager who gets two scheduled
messages every morning reads neither. It rides the same notification-only
pattern: one CRM Notification, no email, no send. When `deal_health_enabled` is
off the section is empty and the digest is byte-for-byte what it was before.
"""

import frappe
from frappe import _

from crm.api.whatsapp import (
	WHATSAPP_LEAD_SOURCE,
	get_conversation_aggregates,
	get_conversation_references,
	get_unanswered_since,
	is_unanswered,
)
from crm.deal_health import FLAG_DEAL_HEALTH, flagged_deals
from crm.fcrm.doctype.crm_notification.crm_notification import notify_user
from crm.fcrm.doctype.crm_user_preference.crm_user_preference import is_on
from crm.feature_flags import is_enabled

# How long an unanswered incoming message may sit before the assignee is nudged.
FOLLOWUP_DUE_HOURS = 2

# How many flagged deals the digest names before it says "and N more".
DIGEST_DEAL_NAMES = 3

# Window the daily digest summarises.
DIGEST_HOURS = 24

# Roles that receive the daily digest. Administrator is included on purpose:
# `crm.api.session.get_users` counts it as a CRM user, and on a small site it is
# often the only account holding a manager role.
DIGEST_ROLES = ("Sales Manager", "System Manager")

DIGEST_EXCLUDED_USERS = ("Guest",)

# The nudge and the daily digest are both WhatsApp notifications pointed at the
# same Lead/Deal, so matching on the reference alone would let an unread digest
# silence every nudge for that conversation. The nudge message template lives
# here so `has_unread_followup` can match on its (translated) prefix instead.
FOLLOWUP_MESSAGE = "Pending WhatsApp follow-up: {0} has been waiting since {1}"

# The digest's own message template, kept here for the same reason: its leading
# words are how `digest_is_due` recognises a digest it already sent today among
# every other WhatsApp notification on the same user.
DIGEST_MESSAGE = "WhatsApp today: {0} new leads, {1} conversations need a reply, {2} waiting over {3}h"

# The per-user switch in `CRM User Preference`. Default ON: a manager who has
# never opened the setting keeps the digest they had before this existed.
DIGEST_PREFERENCE = "daily_digest"


def followup_message_prefix() -> str:
	"""The leading, conversation-independent part of a nudge message."""
	return _(FOLLOWUP_MESSAGE).split("{0}")[0]


def digest_message_prefix() -> str:
	"""The leading, count-independent part of a digest message."""
	return _(DIGEST_MESSAGE).split("{0}")[0]


def notify_pending_followups():
	"""Hourly: nudge whoever owns a conversation that has been waiting too long."""
	try:
		if not frappe.db.exists("DocType", "WhatsApp Message"):
			return 0

		created = 0
		for conversation in get_pending_conversations():
			for user in conversation["assigned_users"]:
				if create_followup_notification(conversation, user):
					created += 1

		return created
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			"CRM WhatsApp: pending follow-up notifications failed",
		)
		return 0


def get_pending_conversations(now=None) -> list[dict]:
	"""Conversations whose newest message is an incoming one older than the cutoff.

	Reuses the inbox aggregation so a nudge and the inbox badge can never
	disagree about what "waiting" means.
	"""
	now = now or frappe.utils.now_datetime()
	cutoff = frappe.utils.add_to_date(now, hours=-FOLLOWUP_DUE_HOURS)

	aggregates = [row for row in get_conversation_aggregates() if is_unanswered(row)]
	if not aggregates:
		return []

	unanswered_since = get_unanswered_since(aggregates)
	references = get_conversation_references(aggregates)

	pending = []
	for row in aggregates:
		key = (row["reference_doctype"], row["reference_name"])
		reference = references.get(key)
		waiting_since = unanswered_since.get(key)
		if not reference or not waiting_since or waiting_since > cutoff:
			continue

		assigned_users = reference.get("assigned_users") or []
		if not assigned_users and reference.get("owner_user"):
			assigned_users = [reference["owner_user"]]
		if not assigned_users:
			# Nobody to nudge. The lead still shows up in the manager digest.
			continue

		pending.append(
			{
				"reference_doctype": row["reference_doctype"],
				"reference_name": row["reference_name"],
				"display_name": reference["display_name"],
				"waiting_since": waiting_since,
				"assigned_users": assigned_users,
			}
		)

	return pending


def create_followup_notification(conversation: dict, user: str) -> bool:
	"""Raise one reminder, unless an unread one for the same conversation exists."""
	if has_unread_followup(conversation, user):
		return False

	waiting_since = frappe.utils.format_datetime(conversation["waiting_since"])
	display_name = frappe.utils.escape_html(conversation["display_name"])
	notification_text = f"""
        <div class="mb-2 leading-5 text-ink-gray-5">
            <span>{_("Pending WhatsApp follow-up:")}</span>
            <span class="font-medium text-ink-gray-9">{display_name}</span>
            <span>{_("has been waiting since {0}").format(waiting_since)}</span>
        </div>
    """

	notify_user(
		{
			# No from_user: the reminder comes from the scheduler, and leaving it
			# empty is also what lets a user be nudged about their own lead
			# (notify_user drops notifications a user would send to themselves).
			"owner": None,
			"assigned_to": user,
			"notification_type": "WhatsApp",
			"message": _(FOLLOWUP_MESSAGE).format(conversation["display_name"], waiting_since),
			"notification_text": notification_text,
			# The reminder is about a conversation, not about one message. Pointing
			# notification_type_doctype at the Lead/Deal (per-message notifications
			# point at "WhatsApp Message") is what keeps the two apart when we look
			# for an unread copy.
			"reference_doctype": conversation["reference_doctype"],
			"reference_docname": conversation["reference_name"],
			"redirect_to_doctype": conversation["reference_doctype"],
			"redirect_to_docname": conversation["reference_name"],
		}
	)
	return True


def has_unread_followup(conversation: dict, user: str) -> bool:
	return bool(
		frappe.db.exists(
			"CRM Notification",
			{
				"to_user": user,
				"type": "WhatsApp",
				"read": 0,
				"notification_type_doctype": conversation["reference_doctype"],
				"notification_type_doc": conversation["reference_name"],
				# Nudges only. Without this an unread daily digest -- which is also a
				# WhatsApp notification on the same Lead/Deal -- would suppress the
				# hourly nudge for that conversation.
				"message": ["like", f"{followup_message_prefix()}%"],
			},
		)
	)


def empty_digest_summary() -> dict:
	"""The shape `build_digest_summary` returns, with nothing in it.

	A site without the WhatsApp app still gets a digest, because the deal-health
	half of it (master spec §5, item 22) does not go through Meta.
	"""
	return {
		"new_leads": 0,
		"needs_reply": 0,
		"overdue": 0,
		"reference_doctype": None,
		"reference_name": None,
		"flagged_deals": [],
	}


def get_flagged_deals() -> list[dict]:
	"""Deals the health sweep flagged, for the digest. Empty when the flag is off.

	Never raises: the digest is a scheduler job, and a deal-health problem must
	not cost the manager their WhatsApp summary.
	"""
	try:
		if not is_enabled(FLAG_DEAL_HEALTH):
			return []
		return flagged_deals()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM WhatsApp: digest deal-health section failed")
		return []


def send_daily_digest(now=None):
	"""Hourly: one summary of the WhatsApp pipeline and deal health, per manager.

	At most one per manager per day. Silent inside the follow-up engine's quiet
	hours, and silent for a manager who switched the digest off. See the module
	docstring for why this is an hourly job rather than a daily one.
	"""
	try:
		now = now or frappe.utils.now_datetime()

		if in_digest_quiet_hours(now):
			return 0

		# Work out WHO before working out WHAT. On twenty-three of the day's
		# twenty-four ticks this list is empty, and building the summary first
		# would mean twenty-three pointless aggregate queries a day.
		recipients = [user for user in get_digest_recipients() if digest_is_due(user, now)]
		if not recipients:
			return 0

		if frappe.db.exists("DocType", "WhatsApp Message"):
			summary = build_digest_summary(now)
		else:
			summary = empty_digest_summary()

		summary["flagged_deals"] = get_flagged_deals()

		if not any(summary[key] for key in ("new_leads", "needs_reply", "overdue", "flagged_deals")):
			return 0

		# A digest whose only content is deal health still needs somewhere to
		# point, or the notification list renders an unclickable row.
		if not summary.get("reference_name") and summary["flagged_deals"]:
			summary["reference_doctype"] = "CRM Deal"
			summary["reference_name"] = summary["flagged_deals"][0]["name"]

		created = 0
		for user in recipients:
			create_digest_notification(summary, user, now)
			# `notify_user` drops a notification identical to one that is already
			# there, so "I called it" is not the same as "it exists". Counting
			# what is actually on the record keeps the job's return value true.
			if has_digest_today(user, now):
				created += 1

		return created
	except Exception:
		frappe.log_error(
			frappe.get_traceback(),
			"CRM WhatsApp: daily digest failed",
		)
		return 0


def in_digest_quiet_hours(now) -> bool:
	"""True while the follow-up engine's configured quiet window is open.

	The SAME window the sequence engine defers sends into, read from the same
	Single, so a manager who moves quiet hours moves both at once and cannot end
	up with a digest arriving at 03:00 because it kept its own copy of the times.

	Fails OPEN, with a log entry. A settings row this job cannot read is a
	configuration problem; a digest that then never arrives again is a silent
	one, and this notification costs nothing to deliver -- it sends no message,
	spends no budget and reaches no customer.
	"""
	try:
		from crm.api.followup_engine import get_settings
		from crm.sequences import in_quiet_hours

		return bool(in_quiet_hours(now, get_settings()))
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM WhatsApp: digest quiet hours unreadable")
		return False


def digest_is_due(user: str, now) -> bool:
	"""True when this user wants a digest and has not had today's yet."""
	if not is_on(user, DIGEST_PREFERENCE):
		return False

	return not has_digest_today(user, now)


def has_digest_today(user: str, now) -> bool:
	"""Whether this user already has a digest notification dated today.

	The digest's own notification is the record of the digest having been sent,
	so there is no second table to keep in step and nothing to clean up. Matching
	is on the message's leading words, for the same reason `has_unread_followup`
	matches on the nudge's: an unread digest and an unread nudge are both
	WhatsApp notifications on the same document.
	"""
	day_start = frappe.utils.get_datetime(frappe.utils.getdate(now))

	return bool(
		frappe.db.exists(
			"CRM Notification",
			{
				"to_user": user,
				"type": "WhatsApp",
				"creation": [">=", frappe.utils.get_datetime_str(day_start)],
				"message": ["like", f"{digest_message_prefix()}%"],
			},
		)
	)


def build_digest_summary(now=None) -> dict:
	"""Count what the manager needs to know, plus a document to link the digest to."""
	now = now or frappe.utils.now_datetime()
	digest_cutoff = frappe.utils.add_to_date(now, hours=-DIGEST_HOURS)
	overdue_cutoff = frappe.utils.add_to_date(now, hours=-FOLLOWUP_DUE_HOURS)

	new_leads = frappe.get_all(
		"CRM Lead",
		filters={
			"source": WHATSAPP_LEAD_SOURCE,
			"creation": [">=", frappe.utils.get_datetime_str(digest_cutoff)],
		},
		pluck="name",
		order_by="creation desc",
	)

	aggregates = [row for row in get_conversation_aggregates() if is_unanswered(row)]
	unanswered_since = get_unanswered_since(aggregates) if aggregates else {}
	overdue = sorted(
		(waiting_since, key)
		for key, waiting_since in unanswered_since.items()
		if waiting_since <= overdue_cutoff
	)

	# The notification list builds its route from the reference, so a digest with
	# no reference would render an unclickable row. Any non-zero summary has at
	# least one document to point at: the newest lead, else the longest wait.
	if new_leads:
		reference = ("CRM Lead", new_leads[0])
	elif overdue:
		reference = overdue[0][1]
	elif unanswered_since:
		reference = sorted(unanswered_since.items())[0][0]
	else:
		reference = (None, None)

	return {
		"new_leads": len(new_leads),
		"needs_reply": len(unanswered_since),
		"overdue": len(overdue),
		"reference_doctype": reference[0],
		"reference_name": reference[1],
	}


def get_digest_recipients() -> list[str]:
	"""Enabled system users holding a manager role, minus Guest."""
	role_holders = set(
		frappe.get_all(
			"Has Role",
			filters={"parenttype": "User", "role": ["in", list(DIGEST_ROLES)]},
			pluck="parent",
			distinct=True,
		)
	)
	# Administrator holds every role implicitly, but its Has Role table is not
	# guaranteed to list System Manager -- crm.api.session.get_users special-cases
	# it the same way.
	role_holders.add("Administrator")
	role_holders.difference_update(DIGEST_EXCLUDED_USERS)
	if not role_holders:
		return []

	return sorted(
		frappe.get_all(
			"User",
			filters={"name": ["in", sorted(role_holders)], "enabled": 1, "user_type": "System User"},
			pluck="name",
		)
	)


def digest_deal_health_line(flagged: list[dict]) -> str:
	"""One sentence naming the flagged deals, or "" when there are none.

	At most three names, because this is a notification row and not a report;
	the Today page is where the whole list lives.
	"""
	if not flagged:
		return ""

	names = ", ".join(deal["title"] for deal in flagged[:DIGEST_DEAL_NAMES])

	if len(flagged) == 1:
		return _("1 deal needs attention: {0}").format(names)

	if len(flagged) > DIGEST_DEAL_NAMES:
		return _("{0} deals need attention: {1} and {2} more").format(
			len(flagged), names, len(flagged) - DIGEST_DEAL_NAMES
		)

	return _("{0} deals need attention: {1}").format(len(flagged), names)


def create_digest_notification(summary: dict, user: str, now=None):
	now = now or frappe.utils.now_datetime()

	message = _(DIGEST_MESSAGE).format(
		summary["new_leads"], summary["needs_reply"], summary["overdue"], FOLLOWUP_DUE_HOURS
	)

	health_line = digest_deal_health_line(summary.get("flagged_deals") or [])
	if health_line:
		message = f"{message}. {health_line}"

	# The date is in the title for two reasons. It tells a manager scrolling
	# yesterday's notifications which morning they are looking at, and it makes
	# two days' digests different documents: `notify_user` silently drops a
	# notification whose every field matches one that already exists, so two
	# quiet days running would otherwise produce one digest between them.
	title = _("WhatsApp daily digest · {0}").format(frappe.utils.format_date(now, "d MMM"))

	notification_text = f"""
        <div class="mb-2 leading-5 text-ink-gray-5">
            <span class="font-medium text-ink-gray-9">{frappe.utils.escape_html(title)}</span>
            <span>{frappe.utils.escape_html(message)}</span>
        </div>
    """

	notify_user(
		{
			"owner": None,
			"assigned_to": user,
			"notification_type": "WhatsApp",
			"message": message,
			"notification_text": notification_text,
			"reference_doctype": summary["reference_doctype"],
			"reference_docname": summary["reference_name"],
			"redirect_to_doctype": summary["reference_doctype"],
			"redirect_to_docname": summary["reference_name"],
		}
	)
