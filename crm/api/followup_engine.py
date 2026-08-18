"""Automated three-stage WhatsApp follow-up for leads who stop replying.

The agency sends a proposal. The customer goes quiet. After the configured
number of silent days the engine sends follow-up stage 1, then stage 2, then
stage 3, and stops. Any customer reply stops the sequence at once.

Platform constraint that shapes the whole design
------------------------------------------------
WhatsApp Cloud API opens a 24-hour customer-service window only when the
customer writes to us. Outside that window Meta accepts *pre-approved template
messages only*. A follow-up after two days of silence is therefore always a
template send, never free text. The AI may fill template variables -- short
fragments such as a first name or a destination -- and nothing else. Every
value is sanitised before it reaches Meta.

Sales follow-ups are MARKETING category messages. They cost money per delivered
message and they carry a spam risk: complaints lower the number's quality
rating and downgrade its messaging tier. That is why the engine ships disabled,
has a daily send cap, honours quiet hours, refuses to enrol stale threads, and
treats an opt-out as permanent until a manager reverses it.

Delivery guarantee: at most once
--------------------------------
`frappe_whatsapp` POSTs to Meta inside `WhatsApp Message.before_insert`, that
is, inside our open database transaction. If the job then rolled back -- an RQ
timeout, a crash, a later error -- the Send Log rows would disappear while the
paid messages had already left. The next hour would send them again.

So every due row runs in its **own** transaction with explicit commit points:

1. Lock the follow-up row (`SELECT ... FOR UPDATE`) and re-read the newest
   customer message in that fresh transaction.
2. Insert the Send Log row, which claims the unique `dedup_key`, and **commit**.
3. Only then create the WhatsApp Message, which calls Meta.
4. Record the outcome and commit again.

A crash between step 2 and step 4 leaves the key claimed, so the stage is never
re-sent. At-most-once is the correct trade for a paid marketing message.

Moving parts
------------
* `CRM Followup Settings` -- one Single the agency configures.
* `CRM WhatsApp Followup` -- one state-machine row per lead.
* `CRM Followup Send Log` -- the outbox, with a unique `dedup_key`.
* `process_followups()` -- hourly scheduler entry point.
* `handle_message_after_insert()` -- reacts to every WhatsApp Message insert.

Where the machine lives
-----------------------
The enrolment loop, the sweep, the stage transitions and the claim/commit/send
ordering described above are NOT in this file any more. They are in
`crm.sequences.core`, which is channel-agnostic, and this module drives them
through `crm.sequences.whatsapp.WhatsAppFollowupAdapter` -- the class that
carries every Meta-specific rule listed below.

Nothing about the behaviour changed with that move. The functions this module
exported before still exist, still take the same arguments and still do the same
thing; each one now states the decision and lets the core order it. The point of
the split is that the email sequences in a later stage get this module's
guarantees without a second copy of them, and that a bug fixed in the ordering is
fixed for both channels at once.

Consent
-------
CRM Lead carries no do-not-contact field, so the `Opted Out` state on the
follow-up row is the single source of truth. An opt-out is recorded even when
no follow-up row exists yet, is never overwritten by a later reply, and is
never re-enrolled. It is reversible only through `reopen_optout`, which needs a
manager and writes an audit comment.

SECURITY NOTE (upstream, not fixable here): `frappe_whatsapp` does not verify
Meta's `X-Hub-Signature-256` on the inbound webhook. Anyone who can reach the
endpoint can therefore forge a customer message. That is why an opt-out records
its source and stays reversible, and why inbound text only ever reaches the AI
as data. Raise the missing signature check with the webhook provider.

Error contract: both entry points run unattended, so neither is allowed to
raise. Failures go to `frappe.log_error` and the job reports the work it did
manage. This mirrors `crm.api.whatsapp_followups`.
"""

import json
import re
import sys
import unicodedata
from datetime import datetime

import frappe
from frappe import _
from frappe.permissions import add_permission, update_permission_property

from crm.api.whatsapp import (
	get_conversation_aggregates,
	get_counterpart_number,
	get_reference_whatsapp_numbers,
	normalize_whatsapp_number,
	validate_recipient,
)
from crm.fcrm.doctype.crm_notification.crm_notification import notify_user
from crm.permissions.org_hierarchy import (
	get_lead_permission_query_conditions,
	has_lead_permission,
)
from crm.sequences import core as sequence_core
from crm.sequences.whatsapp import WhatsAppFollowupAdapter
from crm.suppression import CHANNEL_WHATSAPP
from crm.suppression import STATE_OPTED_OUT as LEDGER_OPTED_OUT
from crm.suppression import suppress as suppress_address
from crm.suppression import unsuppress as unsuppress_address
from crm.utils import parse_phone_number

FOLLOWUP_DOCTYPE = "CRM WhatsApp Followup"
SEND_LOG_DOCTYPE = "CRM Followup Send Log"
SETTINGS_DOCTYPE = "CRM Followup Settings"
MESSAGE_DOCTYPE = "WhatsApp Message"
TEMPLATE_DOCTYPE = "WhatsApp Templates"
LEAD_DOCTYPE = "CRM Lead"
DEAL_DOCTYPE = "CRM Deal"

# Follow-up states. Only Active rows are ever swept.
STATE_ACTIVE = "Active"
STATE_REPLIED = "Replied"
STATE_OPTED_OUT = "Opted Out"
STATE_EXHAUSTED = "Exhausted"
STATE_STOPPED = "Stopped"
STATE_PENDING_APPROVAL = "Pending Approval"

# Only an opt-out is permanent. Everything else can be restarted by an agent
# writing in the thread, which is a human deciding to re-engage.
TERMINAL_STATES = (STATE_OPTED_OUT,)

# States an agent's own message re-arms into a fresh cycle.
RESTARTABLE_STATES = (STATE_REPLIED, STATE_EXHAUSTED, STATE_STOPPED)

SEND_MODE_DRAFT = "Draft for approval"

# Send Log lifecycle. A Claimed row means the key is spent, whatever happened
# next, which is what makes the send at-most-once.
SEND_CLAIMED = "Claimed"
SEND_SENT = "Sent"
SEND_FAILED = "Failed"

# Only an APPROVED template may be sent outside the 24-hour window.
APPROVED_STATUS = "APPROVED"

# Meta rejects template variables that hold newlines, tabs or long runs of
# spaces, and a long value breaks the rendered message. 120 characters is well
# inside what a nudge needs.
MAX_PARAM_LENGTH = 120

# How much conversation the AI is shown when it personalises a stage.
CONVERSATION_HISTORY_LIMIT = 10

# How far back the opt-out history scan reads before enrolling a conversation.
OPTOUT_SCAN_LIMIT = 200

# Used when the settings row predates the idle-bound field. Must match the
# doctype default and crm.patches.v1_0.backfill_followup_guardrail_defaults.
DEFAULT_IDLE_BOUND_DAYS = 14

# Lead fields the AI is allowed to see. Travel agency context, nothing else.
AI_LEAD_FIELDS = (
	"lead_name",
	"first_name",
	"destination",
	"travel_start_date",
	"travel_end_date",
	"group_size",
	"budget",
)

AI_SYSTEM_PROMPT = (
	"You write WhatsApp template variables for a travel agency's follow-up message. "
	"Each value is a short fragment that is pasted into an approved template, not a "
	"whole message. Keep every value under 120 characters, on one line, with no links, "
	"no emoji spam and no invented facts. Use only what the lead data and the "
	"conversation show."
)

# Anything that could act as a link. The bare-domain arm is deliberately greedy:
# a value that came from the lead's own record is kept by `sanitize_param`, so
# over-matching costs nothing while under-matching is a phishing surface.
URL_PATTERN = re.compile(
	r"(?:https?://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)*\.[a-z]{2,24}\b(?:/\S*)?",
	re.IGNORECASE,
)


# --- transaction seams -----------------------------------------------------
# Every commit and rollback in this module goes through these two functions.
# The scheduler needs real commit boundaries (see the module docstring); the
# tests need to neutralise them and to assert their ordering.


def commit():
	frappe.db.commit()


def rollback():
	frappe.db.rollback()


def lock_followup(name: str):
	"""Read one follow-up row with `SELECT ... FOR UPDATE`.

	Two things depend on this. The hourly sweep must not act on a row a webhook
	worker is changing, and two webhook workers must not both read `Active` and
	both write a different state back.
	"""
	return frappe.get_doc(FOLLOWUP_DOCTYPE, name, for_update=True)


# --- the channel adapter ---------------------------------------------------


_adapter = None


def get_channel_adapter():
	"""The WhatsApp adapter the sequence core drives this engine through.

	Built once and held, because it is stateless: it carries a reference to this
	module and nothing else, and every rule it applies is read off this module at
	call time.
	"""
	global _adapter

	if _adapter is None:
		_adapter = WhatsAppFollowupAdapter(sys.modules[__name__])
	return _adapter


# --- scheduler entry point -------------------------------------------------


def process_followups():
	"""Hourly: enrol new conversations, then send whatever stage is due.

	Returns the number of messages sent. Never raises: a scheduler job that
	throws takes the rest of its queue down with it.
	"""
	try:
		if not followups_installed():
			return 0

		settings = get_settings()
		if not settings.enabled:
			return 0

		stages = get_stages(settings)
		if not stages:
			return 0

		if settings.auto_enroll:
			enroll_conversations(settings, stages)

		return sweep_due_followups(settings, stages)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM follow-up engine: hourly run failed")
		return 0


def followups_installed() -> bool:
	"""False on a site where this app's doctypes or frappe_whatsapp are missing."""
	return bool(
		frappe.db.exists("DocType", FOLLOWUP_DOCTYPE) and frappe.db.exists("DocType", MESSAGE_DOCTYPE)
	)


def get_settings():
	"""The agency's configuration, cached.

	`handle_message_after_insert` reads this on every outbound message, so an
	uncached read would put one extra query on the agent's send path. Frappe
	clears the document cache when the Single is saved, so a manager who turns
	the sequence off is obeyed immediately.
	"""
	return frappe.get_cached_doc(SETTINGS_DOCTYPE)


def get_stages(settings) -> list:
	"""Configured stages in send order, as plain dicts."""
	rows = [
		frappe._dict(
			{
				"stage_number": frappe.utils.cint(row.stage_number),
				"silence_days": max(frappe.utils.cint(row.silence_days), 1),
				"template": (row.template or "").strip(),
				"use_ai": bool(row.use_ai),
				"ai_instruction": row.ai_instruction or "",
				"idx": row.idx,
			}
		)
		for row in (settings.stages or [])
	]
	return sorted(rows, key=lambda row: (row.stage_number or row.idx, row.idx))


# --- enrolment -------------------------------------------------------------


def enroll_conversations(settings, stages) -> int:
	"""Create a follow-up row for every WhatsApp conversation that has none.

	A row starts Active -- that is, waiting on the customer -- only when the
	newest message in the conversation is ours. If the customer spoke last, the
	agency owes a reply and the row starts Replied instead. A conversation whose
	history already holds an opt-out starts Opted Out and is never messaged.

	Threads with no recent activity are left alone. Without that bound, turning
	the sequence on would drain months of dormant conversations into the send
	queue, one stage-1 message at a time, for as many days as the daily cap
	needs to work through them.

	The loop itself -- the per-lead savepoint that stops one unenrollable lead
	from costing the whole hour its sweep -- is `crm.sequences.core.enroll`.
	"""
	return sequence_core.enroll(get_channel_adapter(), settings, stages)


def enroll_one(lead: str, aggregate: dict, stages, cutoff, keywords) -> bool:
	"""Create one follow-up row. True when a row was written."""
	last_incoming = aggregate.get("last_incoming_at")
	last_outgoing = aggregate.get("last_outgoing_at")
	last_activity = max((stamp for stamp in (last_incoming, last_outgoing) if stamp), default=None)
	if not last_activity:
		return False

	if cutoff and frappe.utils.get_datetime(last_activity) < cutoff:
		return False

	phone = resolve_conversation_number(lead)
	if not phone:
		# Nothing valid to send to. Re-checked next run, in case the agency
		# fills the number in.
		return False

	followup = frappe.new_doc(FOLLOWUP_DOCTYPE)
	followup.update(
		{
			"lead": lead,
			"phone": phone,
			"cycle": 1,
			"current_stage": 0,
			"last_customer_message": last_incoming,
			"last_agency_message": last_outgoing,
		}
	)

	# An opt-out that predates this row still binds us. Without this scan a lead
	# who wrote STOP before the engine was installed would be enrolled and
	# messaged.
	historic_optout = find_historic_optout(lead, keywords)
	if historic_optout:
		followup.update(
			{
				"state": STATE_OPTED_OUT,
				"opted_out_at": historic_optout.get("creation"),
				"opted_out_source": optout_source(historic_optout),
			}
		)
		mirror_optout_to_ledger(phone, optout_source(historic_optout), lead)
	elif last_outgoing and (not last_incoming or last_outgoing > last_incoming):
		followup.state = STATE_ACTIVE
		followup.next_due = frappe.utils.add_to_date(last_activity, days=stages[0].silence_days)
	else:
		followup.state = STATE_REPLIED

	followup.insert(ignore_permissions=True)
	return True


def enrolment_cutoff(settings):
	"""The oldest last-activity an enrolment is allowed to have.

	`None` means the field was never stored -- a Single only applies its field
	defaults while nothing at all has been saved, so a field added after the
	first save reads back as `None`. Treating that as 0 would silently turn the
	backlog guard off, so the shipped default is used instead. An explicit 0 is
	still honoured and means "no bound".
	"""
	return sequence_core.enrolment_cutoff(settings, DEFAULT_IDLE_BOUND_DAYS)


def find_historic_optout(lead: str, keywords: list[str]) -> dict | None:
	"""The newest incoming message in this conversation that says stop."""
	if not keywords:
		return None

	for message in read_conversation(lead, "Incoming", OPTOUT_SCAN_LIMIT):
		if matches_stop_keyword(message.get("message"), keywords):
			return message
	return None


# --- conversation reads ----------------------------------------------------


def conversation_keys(lead: str) -> list[str]:
	"""Every reference name this conversation's messages can be filed under.

	When a lead converts, later WhatsApp messages are filed against the Deal.
	A reply-detection query that only looked at the Lead would go blind at
	exactly the moment the customer becomes valuable.
	"""
	keys = [lead]
	if frappe.db.exists("DocType", DEAL_DOCTYPE):
		keys.extend(frappe.get_all(DEAL_DOCTYPE, filters={"lead": lead}, pluck="name"))
	return keys


def read_conversation(lead: str, direction: str | None = None, limit: int = 10) -> list[dict]:
	"""Newest messages of one conversation, newest first."""
	filters = {
		"reference_doctype": ["in", [LEAD_DOCTYPE, DEAL_DOCTYPE]],
		"reference_name": ["in", conversation_keys(lead)],
	}
	if direction:
		filters["type"] = direction

	return frappe.get_all(
		MESSAGE_DOCTYPE,
		filters=filters,
		fields=["name", "type", "message", "creation", "from", "to"],
		order_by="creation desc",
		limit=limit,
	)


def get_latest_incoming(lead: str):
	"""Timestamp of the newest customer message on this lead's conversation."""
	rows = read_conversation(lead, "Incoming", 1)
	return rows[0]["creation"] if rows else None


def resolve_conversation_number(lead: str) -> str:
	"""The number this conversation actually runs on, in E.164.

	The lead's `mobile_no` is a guess; the thread is the fact. Fall back to the
	stored numbers when the conversation is empty.
	"""
	for message in read_conversation(lead, None, 1):
		number = e164(get_counterpart_number(message))
		if number:
			return number

	numbers = frappe.db.get_value(LEAD_DOCTYPE, lead, ["mobile_no", "phone"], as_dict=True) or {}
	for raw in (numbers.get("mobile_no"), numbers.get("phone")):
		if number := e164(raw):
			return number
	return ""


def e164(raw) -> str:
	"""The number in strict E.164 form, or an empty string.

	`normalize_whatsapp_number` hands back whatever it was given when parsing
	fails, so its result cannot be compared or stored without this guard.
	"""
	if not raw:
		return ""
	number = normalize_whatsapp_number(raw)
	if not number.startswith("+"):
		return ""
	return number if parse_phone_number(number).get("is_valid") else ""


# --- sweep -----------------------------------------------------------------


def sweep_due_followups(settings, stages) -> int:
	"""Send one stage for every Active row whose next_due has passed.

	Each row is processed in its own transaction, so one row's failure can never
	roll back another row's committed -- and already paid for -- send. The loop
	and that isolation are `crm.sequences.core.sweep`; the query that decides
	which rows are due is the adapter's.
	"""
	return sequence_core.sweep(get_channel_adapter(), settings, stages)


def process_one(name: str, settings, stages) -> bool:
	"""Decide what happens to one due follow-up. True when a message went out.

	The decision -- locked re-read, reply check, quiet-hours deferral, then the
	stage -- is `crm.sequences.core.process_row`. It runs in a transaction that
	began after the previous row committed, so the snapshot is current. That
	matters: MariaDB's REPEATABLE READ would otherwise hide a reply the webhook
	worker committed while this job was running, and we would message a customer
	who had already answered.
	"""
	return sequence_core.process_row(get_channel_adapter(), name, settings, stages)


def agency_spoke_last(last_agency_message, latest_incoming) -> bool:
	"""True when our last outbound message is newer than the customer's last one."""
	if not last_agency_message:
		return False
	return frappe.utils.get_datetime(last_agency_message) >= frappe.utils.get_datetime(latest_incoming)


def count_sent_today() -> int:
	"""Claimed keys today. A claim counts whether or not Meta accepted it."""
	start_of_day = frappe.utils.get_datetime_str(
		datetime.combine(frappe.utils.now_datetime().date(), datetime.min.time())
	)
	return frappe.db.count(SEND_LOG_DOCTYPE, {"sent_at": [">=", start_of_day]})


def daily_budget_left(settings) -> bool:
	"""Re-counted per row, so it holds across commits and across workers."""
	cap = frappe.utils.cint(settings.daily_send_cap)
	return True if cap <= 0 else count_sent_today() < cap


# --- quiet hours -----------------------------------------------------------


# Quiet hours are the same rule on every channel -- a customer asleep at 02:00 is
# asleep for email too -- so the window arithmetic lives in the core and these
# three names stay here as the engine's published surface.

in_quiet_hours = sequence_core.in_quiet_hours
quiet_hours_end_after = sequence_core.quiet_hours_end_after
to_time = sequence_core.to_time


# --- sending ---------------------------------------------------------------


def send_stage(followup, settings, stages, stage_number: int, now) -> bool:
	"""Send (or draft) one stage. True when a WhatsApp message was created.

	`crm.sequences.core.send_stage` runs the checks in order -- terminal state,
	sequence exhausted, an unsendable template, a missing number, draft mode --
	and the adapter answers each one with the WhatsApp rule for it.
	"""
	return sequence_core.send_stage(get_channel_adapter(), followup, settings, stages, stage_number, now)


def deliver(followup, stage_number: int, template, params: dict, stages, recipient: str, now) -> bool:
	"""Claim the key, commit, then call Meta. See the module docstring.

	Returns True only when the WhatsApp Message document was created, which is
	the point at which frappe_whatsapp has already POSTed to Meta. The ordering
	that makes that at-most-once is `crm.sequences.core.deliver`; the claim it
	commits is this channel's Send Log row.
	"""
	return sequence_core.deliver(
		get_channel_adapter(), followup, stage_number, template, params, stages, recipient, now
	)


def advance_after_stage(followup, stage_number: int, stages, now, sent_at=None):
	"""Move the state machine past a stage, sent or not."""
	sequence_core.advance(get_channel_adapter(), followup, stage_number, stages, now, sent_at=sent_at)


def park_followup(followup, reason: str):
	"""Stop sweeping a row the engine cannot serve, and say why.

	Parked is not permanent. Saving the settings un-parks every row, and an
	agent writing in the thread re-arms it. Parking is what stops a
	misconfigured stage from retrying and logging once an hour for ever.
	"""
	if followup.state == STATE_STOPPED and followup.blocked_reason == reason:
		return

	followup.db_set(
		{"state": STATE_STOPPED, "next_due": None, "blocked_reason": reason},
		update_modified=False,
	)
	frappe.log_error(
		f"Follow-up {followup.name} parked: {reason}",
		"CRM follow-up engine: stage cannot be sent",
	)


def unpark_blocked_followups() -> int:
	"""Give every parked row another chance. Called when the settings change."""
	if not frappe.db.exists("DocType", FOLLOWUP_DOCTYPE):
		return 0

	names = frappe.get_all(FOLLOWUP_DOCTYPE, filters={"state": STATE_STOPPED}, pluck="name")
	for name in names:
		frappe.db.set_value(
			FOLLOWUP_DOCTYPE,
			name,
			{
				"state": STATE_ACTIVE,
				"next_due": frappe.utils.now_datetime(),
				"blocked_reason": None,
			},
			update_modified=False,
		)
	return len(names)


def dedup_key(lead: str, cycle, stage_number: int) -> str:
	"""One key per lead, per run of the sequence, per stage.

	The cycle is what lets a re-armed sequence send stage 1 again. Without it
	the second run would collide with the first run's key on the unique index.
	"""
	return sequence_core.sequence_key(lead, cycle, stage_number)


def resolve_recipient(followup) -> str:
	"""Re-derive the destination number from the lead, at send time.

	`followup.phone` is writable by any sales manager and is only a cache. The
	number that actually gets messaged is derived from the lead's own record and
	then checked against it with the same anti-open-relay control the
	interactive endpoints use.
	"""
	if not frappe.db.exists(LEAD_DOCTYPE, followup.lead):
		return ""

	lead_doc = frappe.get_doc(LEAD_DOCTYPE, followup.lead)
	allowed = get_reference_whatsapp_numbers(lead_doc)

	number = resolve_conversation_number(followup.lead)
	if number not in allowed:
		number = e164(lead_doc.get("mobile_no")) or e164(lead_doc.get("phone"))

	if not number or number not in allowed:
		return ""

	# Belt and braces: this throws if the number is not one the lead holds.
	validate_recipient(lead_doc, number)

	if followup.phone != number:
		followup.db_set("phone", number, update_modified=False)
	return number


def create_template_message(followup, template, params: dict, recipient: str):
	"""Create the outgoing WhatsApp Message. frappe_whatsapp sends it on insert.

	`crm.api.whatsapp.send_whatsapp_template` cannot be reused here. It calls
	`validate_access`, which reads `frappe.get_roles()` of the session user, and
	the scheduler has no interactive session. So the document is written
	directly with permissions bypassed. The recipient is not taken on trust: it
	came through `resolve_recipient`, which re-derives it from the lead and runs
	`validate_recipient`.
	"""
	doc = frappe.new_doc(MESSAGE_DOCTYPE)
	doc.update(
		{
			"reference_doctype": LEAD_DOCTYPE,
			"reference_name": followup.lead,
			"type": "Outgoing",
			"message_type": "Template",
			"content_type": "text",
			"use_template": 1,
			"template": template.name,
			"to": recipient,
			"message": "Template message",
			"body_param": json.dumps(params) if params else None,
		}
	)
	# Read by handle_message_after_insert: an engine send must not be mistaken
	# for an agent typing a fresh message, which would reset the silence clock.
	doc.flags.crm_followup_send = True
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	return doc


def hold_for_approval(followup, stage_number: int, params: dict):
	"""Draft mode: park the stage and tell the lead's agent it is waiting."""
	followup.db_set(
		{
			"state": STATE_PENDING_APPROVAL,
			"pending_stage": stage_number,
			"pending_params": json.dumps(params),
		},
		update_modified=False,
	)
	notify_pending_draft(followup, stage_number)


def notify_pending_draft(followup, stage_number: int):
	"""One CRM Notification per assignee. Never fatal to the send loop."""
	try:
		lead_name = frappe.db.get_value(LEAD_DOCTYPE, followup.lead, "lead_name") or followup.lead
		display_name = frappe.utils.escape_html(lead_name)
		message = _("Follow-up stage {0} is waiting for approval: {1}").format(stage_number, lead_name)
		notification_text = f"""
        <div class="mb-2 leading-5 text-ink-gray-5">
            <span>{_("Follow-up waiting for approval:")}</span>
            <span class="font-medium text-ink-gray-9">{display_name}</span>
        </div>
    """
		for user in get_lead_assignees(followup.lead):
			notify_user(
				{
					# No from_user: the draft comes from the scheduler.
					"owner": None,
					"assigned_to": user,
					"notification_type": "WhatsApp",
					"message": message,
					"notification_text": notification_text,
					"reference_doctype": LEAD_DOCTYPE,
					"reference_docname": followup.lead,
					"redirect_to_doctype": LEAD_DOCTYPE,
					"redirect_to_docname": followup.lead,
				}
			)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM follow-up engine: draft notification failed")


def get_lead_assignees(lead: str) -> list[str]:
	"""Users the lead is assigned to, falling back to its owner."""
	users = frappe.get_all(
		"ToDo",
		filters={
			"reference_type": LEAD_DOCTYPE,
			"reference_name": lead,
			"status": ["not in", ("Closed", "Cancelled")],
		},
		pluck="allocated_to",
		distinct=True,
	)
	users = [user for user in users if user]
	if users:
		return users

	owner = frappe.db.get_value(LEAD_DOCTYPE, lead, "lead_owner") or frappe.db.get_value(
		LEAD_DOCTYPE, lead, "owner"
	)
	return [owner] if owner else []


# --- templates and parameters ----------------------------------------------


def resolve_template(stage) -> tuple[dict | None, str | None]:
	"""The stage's template, or the reason it cannot be sent.

	Returns `(template, None)` when Meta would accept a send, otherwise
	`(None, reason)`. The reason is stored on the row rather than logged every
	hour.
	"""
	if not frappe.db.exists("DocType", TEMPLATE_DOCTYPE):
		return None, _("The WhatsApp app is not installed on this site.")

	if not stage.template:
		return None, _("No WhatsApp template is set for this stage.")

	template = frappe.db.get_value(
		TEMPLATE_DOCTYPE,
		stage.template,
		["name", "status", "field_names", "sample_values", "actual_name", "template_name"],
		as_dict=True,
	)
	if not template:
		return None, _("Template {0} does not exist.").format(stage.template)

	if (template.status or "").upper() != APPROVED_STATUS:
		return None, _("Template {0} is {1}, not {2}.").format(
			stage.template, template.status or _("without a status"), APPROVED_STATUS
		)

	# frappe_whatsapp only attaches body parameters when `sample_values` is set.
	# A template that declares field names without sample values would therefore
	# go to Meta with an empty body component and be rejected on every attempt.
	if (template.field_names or "").strip() and not (template.sample_values or "").strip():
		return None, _("Template {0} declares field names but no sample values.").format(stage.template)

	return template, None


def template_variable_names(template) -> list[str]:
	"""Names of the template's {{1}}..{{n}} variables, in order.

	Mirrors `frappe_whatsapp.WhatsAppMessage.send_template`: parameters are only
	attached when `sample_values` is set, and the names come from `field_names`
	when it is present.
	"""
	if not (template.get("sample_values") or "").strip():
		return []

	raw = template.get("field_names") or template.get("sample_values") or ""
	return [part.strip() for part in raw.split(",") if part.strip()]


def build_params(followup, stage, template, names: list[str]) -> dict:
	"""Values for the template variables, keyed "1".."n" in order.

	frappe_whatsapp reads `body_param` as `list(json.loads(...).values())`, so
	insertion order is what reaches Meta.
	"""
	if not names:
		return {}

	lead = get_lead_context(followup.lead)
	values = None

	if stage.use_ai:
		try:
			values = ai_params(lead, stage, template, names, followup)
		except Exception as error:
			# Never let a model failure stop a follow-up. Deterministic values
			# are always available.
			frappe.log_error(
				f"{type(error).__name__}: {error}",
				"CRM follow-up engine: AI parameters failed, using defaults",
			)
			values = None

	if not values:
		values = deterministic_params(lead, names)

	return {str(index): value for index, value in enumerate(values, start=1)}


def get_lead_context(lead: str) -> dict:
	fields = ["name", *AI_LEAD_FIELDS]
	row = frappe.db.get_value(LEAD_DOCTYPE, lead, fields, as_dict=True) or {}
	return {key: value for key, value in row.items() if value not in (None, "")}


def deterministic_params(lead: dict, names: list[str]) -> list[str]:
	"""Template values built from lead data alone. Always produces n non-empty values."""
	first_name = lead.get("first_name") or lead.get("lead_name") or _("there")
	destination = lead.get("destination") or _("your trip")
	fallbacks = [first_name, destination]

	values = []
	for index, field in enumerate(names):
		value = lead.get(field)
		if not value:
			value = fallbacks[index] if index < len(fallbacks) else first_name
		values.append(sanitize_param(value, lead) or first_name)
	return values


def ai_params(lead: dict, stage, template, names: list[str], followup) -> list[str] | None:
	"""Ask the configured model for the variable values. None when unusable."""
	from crm.ai import client

	schema = {
		"type": "object",
		"properties": {str(index): {"type": "string"} for index in range(1, len(names) + 1)},
		"required": [str(index) for index in range(1, len(names) + 1)],
		"additionalProperties": False,
	}

	answer = client.complete(
		build_ai_prompt(lead, stage, template, names, followup),
		system=AI_SYSTEM_PROMPT,
		max_tokens=1024,
		json_schema=schema,
	)

	values = []
	for index in range(1, len(names) + 1):
		value = sanitize_param(answer.get(str(index)), lead)
		if not value:
			return None
		values.append(value)
	return values


def build_ai_prompt(lead: dict, stage, template, names: list[str], followup) -> str:
	lead_lines = "\n".join(f"- {key}: {value}" for key, value in lead.items() if key != "name")
	history = "\n".join(get_conversation_excerpt(followup.lead)) or _("(no messages on record)")
	variables = "\n".join(
		f'- "{index}" is the template field named {name}' for index, name in enumerate(names, start=1)
	)

	return (
		f"Approved WhatsApp template body:\n{template.get('template_name') or template.get('name')}\n\n"
		f"Lead data:\n{lead_lines or '- none on record'}\n\n"
		f"Last messages (oldest first):\n{history}\n\n"
		f"Fill these template variables:\n{variables}\n\n"
		f"Tone instruction: {stage.ai_instruction or 'friendly, brief, no pressure'}\n\n"
		"Answer with a JSON object whose keys are the variable numbers as strings."
	)


def get_conversation_excerpt(lead: str) -> list[str]:
	excerpt = []
	for row in reversed(read_conversation(lead, None, CONVERSATION_HISTORY_LIMIT)):
		speaker = _("Customer") if row["type"] == "Incoming" else _("Agency")
		text = " ".join(frappe.utils.strip_html(frappe.utils.cstr(row["message"])).split())
		if text:
			excerpt.append(f"{speaker}: {text[:200]}")
	return excerpt


def sanitize_param(value, lead: dict | None = None) -> str:
	"""Make one value safe to paste into a WhatsApp template variable.

	Meta rejects newlines, tabs and long space runs inside a parameter. A link
	the model invented is a phishing surface, so anything that looks like a
	link -- scheme, `www.`, or a bare domain -- is dropped unless the lead's own
	data already contains it.
	"""
	text = " ".join(frappe.utils.strip_html(frappe.utils.cstr(value)).split())
	if not text:
		return ""

	allowed = " ".join(frappe.utils.cstr(item) for item in (lead or {}).values())

	def keep_known_url(match):
		return match.group(0) if match.group(0) in allowed else ""

	text = " ".join(URL_PATTERN.sub(keep_known_url, text).split())
	if len(text) > MAX_PARAM_LENGTH:
		text = text[: MAX_PARAM_LENGTH - 1].rstrip() + "…"
	return text


# --- reaction to inbound and outbound messages -----------------------------


def handle_message_after_insert(doc, method=None):
	"""Hooked on WhatsApp Message `after_insert`. Fast, and never raises.

	This runs inside the Meta webhook's insert. An exception here would fail the
	webhook and make Meta retry the delivery, so every failure is logged and
	swallowed. Nothing here commits: the request's own transaction does that.
	"""
	try:
		if not followups_installed():
			return

		if doc.type == "Incoming":
			handle_incoming(doc)
		elif doc.type == "Outgoing":
			handle_outgoing(doc)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM follow-up engine: message hook failed")


def handle_incoming(doc):
	"""A customer wrote. Stop the sequence, permanently if they asked to opt out."""
	is_optout = matches_stop_keyword(doc.get("message"))
	name = find_followup_name(doc)

	if not name:
		# An opt-out that arrives before the lead is enrolled still binds us, so
		# it is written down now rather than discarded.
		if is_optout:
			record_optout_without_row(doc)
		return

	# Locked: two webhook workers must not both read Active and both write.
	followup = lock_followup(name)
	stamp = doc.get("creation") or frappe.utils.now_datetime()

	if is_optout:
		followup.db_set(
			{
				"state": STATE_OPTED_OUT,
				"last_customer_message": stamp,
				"next_due": None,
				"pending_stage": 0,
				"pending_params": None,
				"opted_out_at": stamp,
				"opted_out_source": optout_source(doc),
			},
			update_modified=False,
		)
		mirror_optout_to_ledger(
			followup.phone or e164(get_counterpart_number(doc)), optout_source(doc), followup.lead
		)
		return

	if followup.state in TERMINAL_STATES:
		# An opt-out is never downgraded by a later message.
		followup.db_set("last_customer_message", stamp, update_modified=False)
		return

	followup.db_set(
		{
			"state": STATE_REPLIED,
			"last_customer_message": stamp,
			"next_due": None,
			"pending_stage": 0,
			"pending_params": None,
		},
		update_modified=False,
	)


def record_optout_without_row(doc):
	"""Persist an opt-out for a lead that has no follow-up row yet."""
	lead = doc.get("reference_name") if doc.get("reference_doctype") == LEAD_DOCTYPE else None
	if not lead and doc.get("reference_doctype") == DEAL_DOCTYPE and doc.get("reference_name"):
		lead = frappe.db.get_value(DEAL_DOCTYPE, doc.reference_name, "lead")

	if not lead or not frappe.db.exists(LEAD_DOCTYPE, lead):
		frappe.log_error(
			f"Opt-out message {doc.get('name')} could not be tied to a lead.",
			"CRM follow-up engine: unattributable opt-out",
		)
		return

	stamp = doc.get("creation") or frappe.utils.now_datetime()
	phone = e164(get_counterpart_number(doc))
	mirror_optout_to_ledger(phone, optout_source(doc), lead)

	followup = frappe.new_doc(FOLLOWUP_DOCTYPE)
	followup.update(
		{
			"lead": lead,
			"phone": phone,
			"state": STATE_OPTED_OUT,
			"cycle": 1,
			"current_stage": 0,
			"last_customer_message": stamp,
			"opted_out_at": stamp,
			"opted_out_source": optout_source(doc),
		}
	)
	try:
		followup.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		# Another worker created the row first; re-apply the opt-out to it.
		existing = frappe.db.get_value(FOLLOWUP_DOCTYPE, {"lead": lead}, "name")
		if existing:
			lock_followup(existing).db_set(
				{
					"state": STATE_OPTED_OUT,
					"next_due": None,
					"opted_out_at": stamp,
					"opted_out_source": optout_source(doc),
				},
				update_modified=False,
			)


def optout_source(message) -> str:
	"""A short, auditable record of what triggered an opt-out."""
	text = " ".join(frappe.utils.strip_html(frappe.utils.cstr(message.get("message"))).split())
	return f"WhatsApp message {message.get('name') or '?'}: {text[:120]}"


# --- consent write-through -------------------------------------------------
# The `Opted Out` state on the follow-up row remains this engine's own source of
# truth; nothing below changes how it behaves. These two helpers ALSO write the
# same fact into `crm.suppression`, which is the ledger every other outbound
# path in the app reads. Without them a customer who says STOP on WhatsApp is
# still reachable by the composer, by mass email and by the web-form
# auto-response, because none of those can see a follow-up row.
#
# Neither helper is allowed to raise. An opt-out that failed to record because
# its mirror failed would be a far worse bug than a mirror that lags.


def mirror_optout_to_ledger(phone, source: str, lead: str | None = None) -> None:
	"""Copy a WhatsApp opt-out into the suppression ledger. Never raises."""
	if not phone:
		return

	try:
		suppress_address(
			CHANNEL_WHATSAPP,
			phone,
			state=LEDGER_OPTED_OUT,
			source=source,
			reference_doctype=LEAD_DOCTYPE if lead else None,
			reference_name=lead,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM follow-up engine: suppression write-through failed")


def mirror_reopen_to_ledger(phone, reason: str) -> None:
	"""Lift the matching ledger row when a manager reopens an opt-out."""
	if not phone:
		return

	try:
		unsuppress_address(CHANNEL_WHATSAPP, phone, reason)
	except Exception:
		frappe.log_error(
			frappe.get_traceback(), "CRM follow-up engine: suppression release write-through failed"
		)


def handle_outgoing(doc):
	"""An agent wrote. Restart the silence clock; re-arm a finished sequence."""
	if doc.flags.get("crm_followup_send"):
		return

	name = find_followup_name(doc)
	if not name:
		return

	followup = lock_followup(name)
	if followup.state in TERMINAL_STATES:
		return

	stamp = doc.get("creation") or frappe.utils.now_datetime()
	update = {"last_agency_message": stamp}

	stages = get_stages(get_settings())
	if not stages:
		followup.db_set(update, update_modified=False)
		return

	if followup.state in RESTARTABLE_STATES:
		# The conversation restarted, so the sequence restarts with it. A new
		# cycle gives the stages fresh dedup keys.
		update["state"] = STATE_ACTIVE
		update["current_stage"] = 0
		update["cycle"] = (frappe.utils.cint(followup.cycle) or 1) + 1
		update["blocked_reason"] = None
		update["next_due"] = frappe.utils.add_to_date(stamp, days=stages[0].silence_days)
	elif followup.state == STATE_ACTIVE:
		# A manual message resets the clock for the stage that is still pending.
		pending = min(frappe.utils.cint(followup.current_stage), len(stages) - 1)
		update["next_due"] = frappe.utils.add_to_date(stamp, days=stages[pending].silence_days)

	followup.db_set(update, update_modified=False)


def find_followup_name(doc) -> str | None:
	"""The follow-up row for a message, by lead reference first, then by number."""
	reference_doctype = doc.get("reference_doctype")
	reference_name = doc.get("reference_name")

	if reference_doctype == LEAD_DOCTYPE and reference_name:
		if name := frappe.db.get_value(FOLLOWUP_DOCTYPE, {"lead": reference_name}, "name"):
			return name
	elif reference_doctype == DEAL_DOCTYPE and reference_name:
		lead = frappe.db.get_value(DEAL_DOCTYPE, reference_name, "lead")
		if lead and (name := frappe.db.get_value(FOLLOWUP_DOCTYPE, {"lead": lead}, "name")):
			return name

	number = e164(get_counterpart_number(doc))
	if not number:
		return None

	return frappe.db.get_value(FOLLOWUP_DOCTYPE, {"phone": number}, "name")


def matches_stop_keyword(message, keywords: list[str] | None = None) -> bool:
	"""True when the reply carries an opt-out word. Whole words only.

	The text is NFKC-normalised first, so full-width and other decorated forms
	of the same letters fold down to plain ASCII before matching. "stop" matches
	"STOP" and "Stop please". It does not match "non-stopover flights", which is
	a sentence a travel customer really does write.
	"""
	raw = frappe.utils.strip_html(frappe.utils.cstr(message))
	text = " ".join(unicodedata.normalize("NFKC", raw).casefold().split())
	if not text:
		return False

	for keyword in keywords if keywords is not None else get_stop_keywords():
		if re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text):
			return True
	return False


def get_stop_keywords(settings=None) -> list[str]:
	raw = (
		settings.stop_keywords
		if settings is not None
		else frappe.db.get_single_value(SETTINGS_DOCTYPE, "stop_keywords")
	) or ""
	return [
		unicodedata.normalize("NFKC", line).strip().casefold() for line in raw.splitlines() if line.strip()
	]


# --- approval and manager actions ------------------------------------------


@frappe.whitelist(methods=["POST"])
def approve_pending(followup: str):
	"""Send the stage a manager or the assigned agent parked in draft mode.

	Approval is a paid send, so it obeys the same three guards the scheduler
	obeys: the sequence must be on, the moment must be outside quiet hours, and
	the daily cap must have room. The draft stays parked when a guard refuses,
	so nothing is lost.
	"""
	doc = get_pending_followup(followup)
	settings = get_settings()
	stages = get_stages(settings)
	stage_number = frappe.utils.cint(doc.pending_stage)
	now = frappe.utils.now_datetime()

	if not settings.enabled:
		frappe.throw(_("Follow-ups are turned off."))

	if in_quiet_hours(now, settings):
		frappe.throw(
			_("Quiet hours are in force until {0}. Approve again after that.").format(
				frappe.utils.format_datetime(quiet_hours_end_after(now, settings))
			)
		)

	if not daily_budget_left(settings):
		frappe.throw(_("The daily follow-up send cap is reached. Approve again tomorrow."))

	if not stages or stage_number < 1 or stage_number > len(stages):
		frappe.throw(_("This follow-up stage is no longer configured."))

	template, problem = resolve_template(stages[stage_number - 1])
	if problem:
		frappe.throw(problem)

	recipient = resolve_recipient(doc)
	if not recipient:
		frappe.throw(_("The lead has no valid WhatsApp number."))

	# The stored draft is re-sanitised: it was written by an earlier run, and a
	# stored value must never reach Meta unchecked.
	lead = get_lead_context(doc.lead)
	stored = json.loads(doc.pending_params) if doc.pending_params else {}
	params = {key: sanitize_param(value, lead) for key, value in stored.items()}
	names = template_variable_names(template)
	if len(params) != len(names) or not all(params.values()):
		params = {str(index): value for index, value in enumerate(deterministic_params(lead, names), start=1)}

	deliver(doc, stage_number, template, params, stages, recipient, now)
	return doc.name


@frappe.whitelist(methods=["POST"])
def dismiss_pending(followup: str):
	"""Drop a parked stage without sending it, and schedule the next one.

	Dismissing means "not this message", not "stop the sequence" -- the agency
	stops a sequence by opting the lead out or by replying in the thread.
	"""
	doc = get_pending_followup(followup)
	stages = get_stages(get_settings())
	stage_number = frappe.utils.cint(doc.pending_stage)
	now = frappe.utils.now_datetime()

	if not stages:
		doc.db_set(
			{"state": STATE_EXHAUSTED, "next_due": None, "pending_stage": 0, "pending_params": None},
			update_modified=False,
		)
		return doc.name

	advance_after_stage(doc, stage_number, stages, now)
	return doc.name


@frappe.whitelist(methods=["POST"])
def reopen_optout(followup: str, reason: str):
	"""Reverse an opt-out. Manager action, audited.

	The inbound webhook is unauthenticated upstream (see the module docstring),
	so an opt-out must not be an irreversible state that anyone on the internet
	can force. Reopening does not resume sending by itself: the row goes back to
	Replied, and only an agent writing in the thread re-arms the sequence.
	"""
	doc = frappe.get_doc(FOLLOWUP_DOCTYPE, followup)
	check_followup_permission(doc)

	if doc.state != STATE_OPTED_OUT:
		frappe.throw(_("This follow-up is not opted out."))

	if not (reason or "").strip():
		frappe.throw(_("Give a reason for reopening the opt-out."))

	previous = doc.opted_out_source
	doc.db_set(
		{
			"state": STATE_REPLIED,
			"next_due": None,
			"opted_out_at": None,
			"opted_out_source": None,
			"blocked_reason": None,
		},
		update_modified=False,
	)
	mirror_reopen_to_ledger(doc.phone, reason)

	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": FOLLOWUP_DOCTYPE,
			"reference_name": doc.name,
			"content": _("Opt-out reopened by {0}. Reason: {1}. Previous source: {2}").format(
				frappe.session.user,
				frappe.utils.strip_html(frappe.utils.cstr(reason))[:200],
				previous or _("unknown"),
			),
		}
	).insert(ignore_permissions=True)
	return doc.name


def get_pending_followup(followup: str):
	# Locked: two agents may hit Approve on the same draft at the same moment.
	doc = lock_followup(followup)
	if doc.state != STATE_PENDING_APPROVAL:
		frappe.throw(_("This follow-up has no draft waiting for approval."))

	check_followup_permission(doc)
	return doc


def check_followup_permission(doc, ptype: str = "write"):
	"""Whoever may write the lead may act on its follow-up.

	This delegates to `crm.permissions.org_hierarchy`, so a sales manager inside
	the hierarchy only reaches their own subtree, exactly as they do on the lead
	itself.
	"""
	if has_lead_permission(frappe._dict({"name": doc.lead}), ptype, frappe.session.user):
		return

	frappe.throw(
		_("You are not allowed to act on this lead's follow-up."),
		frappe.PermissionError,
	)


@frappe.whitelist()
def get_template_options():
	"""Approved WhatsApp templates, for the settings screen's stage picker."""
	roles = frappe.get_roles()
	if "System Manager" not in roles and "Sales Manager" not in roles:
		frappe.throw(_("Only a sales manager can read the template list."), frappe.PermissionError)

	if not frappe.db.exists("DocType", TEMPLATE_DOCTYPE):
		return []

	return frappe.get_all(
		TEMPLATE_DOCTYPE,
		filters={"status": APPROVED_STATUS},
		fields=["name", "template_name", "category"],
		order_by="template_name asc",
	)


# --- row-level permissions -------------------------------------------------


def get_followup_permission_query_conditions(user=None):
	"""Restrict follow-up rows to the leads the user may already see.

	Without this a sales user could list every follow-up in the company and read
	off the customer phone number attached to each one.
	"""
	lead_conditions = get_lead_permission_query_conditions(user)
	if not lead_conditions:
		return ""

	return (
		f"`tab{FOLLOWUP_DOCTYPE}`.`lead` in "
		f"(select `tab{LEAD_DOCTYPE}`.`name` from `tab{LEAD_DOCTYPE}` where {lead_conditions})"
	)


def has_followup_permission(doc, ptype, user=None):
	if ptype == "create" or not doc.get("lead"):
		return True
	return has_lead_permission(frappe._dict({"name": doc.get("lead")}), ptype, user)


# --- installation ----------------------------------------------------------


def add_followup_roles():
	"""after_migrate: let sales roles use the follow-up doctypes.

	Managers configure and inspect the sequence. Sales users only read the
	follow-up rows their own leads produce, and cannot report on or export them.
	"""
	for doctype in ("CRM AI Settings", SETTINGS_DOCTYPE, FOLLOWUP_DOCTYPE):
		grant(doctype, "Sales Manager", write=True, delete=True, report=True, export=True)

	# No delete on the outbox: deleting a Send Log row would free a spent dedup
	# key and reset the daily cap.
	grant(SEND_LOG_DOCTYPE, "Sales Manager", write=True, delete=False, report=True, export=True)

	grant(FOLLOWUP_DOCTYPE, "Sales User", write=False, delete=False, report=False, export=False)


def grant(doctype: str, role: str, write: bool, delete: bool, report: bool, export: bool):
	"""Set one role's permissions on one doctype. Idempotent, and corrective.

	The properties are written on every migrate, not only when the permission
	row is new, so tightening a grant in code actually tightens it on a site
	that already ran the looser version.
	"""
	if not frappe.db.exists("DocType", doctype):
		return

	add_permission(doctype, role, 0, "write" if write else "read")
	for property_name, value in (
		("read", 1),
		("write", int(write)),
		("create", int(write)),
		("delete", int(delete)),
		("report", int(report)),
		("export", int(export)),
	):
		update_permission_property(doctype, role, 0, property_name, value)
