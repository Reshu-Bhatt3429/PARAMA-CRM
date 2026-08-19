"""Deal-health flags: the nightly sweep and the one field it writes.

Master spec §5, item 22. Three questions are asked of every deal once a night,
and the answers are stored so no list page ever has to compute them:

* `close_date_passed` -- the expected closure date is in the past and the deal
  is still open.
* `stalled` -- the status has not changed for N days (default 14), read from the
  `CRM Status Change Log` child table the Deal already writes on every status
  change.
* `awaiting_reply` -- the customer spoke last, and that was more than two days
  ago. The inbound/outbound pair is the same one the WhatsApp inbox computes
  (`crm.api.whatsapp.is_unanswered`), widened to cover email Communications.

## Why this is not `crm.sweeps.run_sweep`

It uses every primitive `crm/sweeps.py` provides -- the per-job lock, the
`(modified, name)` keyset cursor, the stored watermark, batch commits -- but it
drives the loop itself. `run_sweep` hands the handler one row at a time, and
each of the three questions above is answered by an aggregate over a *set* of
deals. Per-row it would be three queries per deal; per-batch it is three
queries per two hundred deals.

## Why the cursor is reset when a pass finishes

`close_date_passed` becomes true through the passage of time, not through a row
being modified. A watermark that only ever moved forward would therefore never
revisit the deal that went overdue last night. So the watermark does what it is
for -- resuming a crashed or truncated pass -- and is cleared once a pass
reaches the end, so tomorrow's run starts from the top again.

## What it writes

One JSON column, `custom_parama_health_flags`, created by
`crm.patches.v1_0.create_parama_deal_health_field`. A deal with nothing wrong
gets an EMPTY column, not `{}`: that is what makes `["is", "set"]` an exact
"needs attention" filter for the list view, with no LIKE over a JSON blob.
"Empty" on disk means NULL, because MariaDB guards a `json` column with
`CHECK (json_valid(col))` and the empty string does not pass it; `serialise`
still returns `""` as the in-Python "nothing", and `apply_batch` writes it as
NULL.

Writes use `update_modified=False` on purpose. Touching `modified` would move
the row to the end of the cursor's ordering and the sweep would read it a second
time in the same pass.

Everything is behind `deal_health_enabled`, which is OFF by default. While it is
off `sweep_deal_health` returns without reading a single deal row.
"""

import json

import frappe
from frappe.query_builder import Case
from frappe.query_builder.functions import Coalesce, Max

from crm.feature_flags import is_enabled
from crm.sweeps import get_watermark, read_batch, reset_watermark, set_watermark, sweep_lock

DEAL_DOCTYPE = "CRM Deal"
STATUS_DOCTYPE = "CRM Deal Status"
STATUS_LOG_DOCTYPE = "CRM Status Change Log"
WHATSAPP_DOCTYPE = "WhatsApp Message"

FLAG_DEAL_HEALTH = "deal_health_enabled"

# Namespaced, as master spec F9 requires for a field this fork adds to a doctype
# it does not own the schema of.
HEALTH_FIELD = "custom_parama_health_flags"

JOB_NAME = "deal_health"

CLOSE_DATE_PASSED = "close_date_passed"
STALLED = "stalled"
AWAITING_REPLY = "awaiting_reply"

# Order is display order: the chip expands to these in this sequence.
FLAG_ORDER = (CLOSE_DATE_PASSED, STALLED, AWAITING_REPLY)

DEFAULT_STALLED_DAYS = 14

# How long the customer's last word may stand before it counts as awaiting a
# reply. Two days, per the spec; not configurable in v1.
AWAITING_REPLY_HOURS = 48

BATCH_SIZE = 200

# A ceiling so one night's run cannot spin forever on a site that grew. What is
# left is picked up on the next tick from the stored watermark.
MAX_BATCHES = 200

# Statuses that end a deal. A closed deal is never "stalled".
CLOSED_STATUS_TYPES = ("Won", "Lost")


# --- settings --------------------------------------------------------------


def stalled_days() -> int:
	"""Days without a status change before a deal counts as stalled.

	Blank, zero or negative falls back to the documented default, the same way
	`crm.reminders.reminder_offset_minutes` treats its own knob.
	"""
	try:
		value = frappe.db.get_single_value("FCRM Settings", "deal_health_stalled_days")
	except Exception:
		return DEFAULT_STALLED_DAYS

	value = frappe.utils.cint(value)
	return value if value > 0 else DEFAULT_STALLED_DAYS


# --- the stored value ------------------------------------------------------


def serialise(flags) -> str:
	"""The column value for a set of flag names. No flags is an EMPTY column."""
	ordered = [flag for flag in FLAG_ORDER if flag in set(flags or ())]
	if not ordered:
		return ""
	return json.dumps({"flags": ordered}, separators=(",", ":"))


def parse(value) -> list[str]:
	"""Flag names out of a stored column value. Never raises on junk."""
	if not value:
		return []

	if isinstance(value, dict):
		payload = value
	else:
		try:
			payload = json.loads(value)
		except (TypeError, ValueError):
			return []

	flags = payload.get("flags") if isinstance(payload, dict) else None
	if not isinstance(flags, list):
		return []

	return [flag for flag in FLAG_ORDER if flag in flags]


# --- the three questions ---------------------------------------------------


def evaluate(row, context, now) -> list[str]:
	"""The flags one deal earns. Pure, given the batch context.

	`now` may be a string or a datetime, and is normalised here rather than at
	the call sites: `frappe.utils.add_to_date` returns whatever type it was
	given, so comparing its result with a datetime raises when a caller passed a
	string.
	"""
	now = frappe.utils.get_datetime(now)
	status_type = context["status_types"].get(row.get("status"))
	if status_type in CLOSED_STATUS_TYPES:
		# A won or lost deal has no health to report, and any flag it carried
		# from when it was open is cleared by returning nothing.
		return []

	flags = []
	today = frappe.utils.getdate(now)

	expected = row.get("expected_closure_date")
	if expected and frappe.utils.getdate(expected) < today:
		flags.append(CLOSE_DATE_PASSED)

	last_change = context["last_status_change"].get(row.get("name")) or row.get("creation")
	if last_change:
		stale_before = frappe.utils.add_to_date(now, days=-context["stalled_days"])
		if frappe.utils.get_datetime(last_change) < stale_before:
			flags.append(STALLED)

	if is_awaiting_reply(row.get("name"), context, now):
		flags.append(AWAITING_REPLY)

	return flags


def is_awaiting_reply(name, context, now) -> bool:
	"""The customer spoke last, and long enough ago to be worth surfacing.

	`crm.api.whatsapp.is_unanswered` owns the "who spoke last" rule; this adds
	the age test and the email leg. Importing it rather than restating it is
	deliberate -- two copies of that comparison would eventually disagree.
	"""
	from crm.api.whatsapp import is_unanswered

	now = frappe.utils.get_datetime(now)
	last_incoming = context["last_incoming"].get(name)
	last_outgoing = context["last_outgoing"].get(name)

	if not is_unanswered({"last_incoming_at": last_incoming, "last_outgoing_at": last_outgoing}):
		return False

	cutoff = frappe.utils.add_to_date(now, hours=-AWAITING_REPLY_HOURS)
	return frappe.utils.get_datetime(last_incoming) < frappe.utils.get_datetime(cutoff)


# --- batch context ---------------------------------------------------------


def build_context(rows, now) -> dict:
	"""Every aggregate the batch needs, in a fixed number of queries."""
	names = [row["name"] for row in rows]
	statuses = sorted({row.get("status") for row in rows if row.get("status")})

	incoming, outgoing = last_message_times(names)

	return {
		"stalled_days": stalled_days(),
		"status_types": status_types(statuses),
		"last_status_change": last_status_changes(names),
		"last_incoming": incoming,
		"last_outgoing": outgoing,
	}


def status_types(statuses) -> dict:
	if not statuses:
		return {}

	rows = frappe.get_all(
		STATUS_DOCTYPE,
		filters={"name": ["in", list(statuses)]},
		fields=["name", "type"],
	)
	return {row["name"]: row["type"] for row in rows}


def last_status_changes(names) -> dict:
	"""When each deal last entered its current status.

	`CRM Status Change Log` is a child table with no link field: the deal is the
	implicit `parent`. `from_date` is when the row's status began, so the newest
	`from_date` is the last stage change.
	"""
	if not names:
		return {}

	Log = frappe.qb.DocType(STATUS_LOG_DOCTYPE)
	rows = (
		frappe.qb.from_(Log)
		.select(Log.parent, Max(Log.from_date).as_("last_change"))
		.where((Log.parenttype == DEAL_DOCTYPE) & Log.parent.isin(list(names)))
		.groupby(Log.parent)
		.run(as_dict=True)
	)
	return {row["parent"]: row["last_change"] for row in rows if row["last_change"]}


def last_message_times(names) -> tuple[dict, dict]:
	"""Newest inbound and newest outbound moment per deal, over both channels."""
	incoming: dict = {}
	outgoing: dict = {}
	if not names:
		return incoming, outgoing

	def keep(target, key, value):
		if not value:
			return
		current = target.get(key)
		if not current or frappe.utils.get_datetime(value) > frappe.utils.get_datetime(current):
			target[key] = value

	Communication = frappe.qb.DocType("Communication")
	stamp = Coalesce(Communication.communication_date, Communication.creation)
	rows = (
		frappe.qb.from_(Communication)
		.select(
			Communication.reference_name,
			Max(Case().when(Communication.sent_or_received == "Received", stamp)).as_("last_in"),
			Max(Case().when(Communication.sent_or_received == "Sent", stamp)).as_("last_out"),
		)
		.where(
			(Communication.reference_doctype == DEAL_DOCTYPE) & Communication.reference_name.isin(list(names))
		)
		.groupby(Communication.reference_name)
		.run(as_dict=True)
	)
	for row in rows:
		keep(incoming, row["reference_name"], row.get("last_in"))
		keep(outgoing, row["reference_name"], row.get("last_out"))

	if frappe.db.exists("DocType", WHATSAPP_DOCTYPE):
		Message = frappe.qb.DocType(WHATSAPP_DOCTYPE)
		rows = (
			frappe.qb.from_(Message)
			.select(
				Message.reference_name,
				Max(Case().when(Message.type == "Incoming", Message.creation)).as_("last_in"),
				Max(Case().when(Message.type == "Outgoing", Message.creation)).as_("last_out"),
			)
			.where(
				(Message.reference_doctype == DEAL_DOCTYPE)
				& Message.reference_name.isin(list(names))
				# The inbox hides reactions, so counting one as the customer's
				# last word would disagree with the thread it opens.
				& (Coalesce(Message.content_type, "text") != "reaction")
			)
			.groupby(Message.reference_name)
			.run(as_dict=True)
		)
		for row in rows:
			keep(incoming, row["reference_name"], row.get("last_in"))
			keep(outgoing, row["reference_name"], row.get("last_out"))

	return incoming, outgoing


# --- the sweep -------------------------------------------------------------


def apply_batch(rows, now) -> int:
	"""Write the flags for one batch. Returns how many columns actually changed."""
	context = build_context(rows, now)
	changed = 0

	for row in rows:
		wanted = serialise(evaluate(row, context, now))
		# `or ""` so a NULL column and an empty one compare equal: without it the
		# first pass over a freshly created field would rewrite every deal.
		if wanted == (row.get(HEALTH_FIELD) or ""):
			continue

		# NULL, not "", for a healthy deal. Frappe maps the JSON fieldtype to
		# MariaDB `json`, and MariaDB puts a `CHECK (json_valid(col))` on such a
		# column: the empty string is not valid JSON and the write is refused.
		# NULL passes the check, and `["is", "set"]` reads it the same way.
		frappe.db.set_value(DEAL_DOCTYPE, row["name"], HEALTH_FIELD, wanted or None, update_modified=False)
		changed += 1

	return changed


def run_sweep(
	now=None,
	batch_size: int = BATCH_SIZE,
	max_batches: int | None = MAX_BATCHES,
	commit_between_batches: bool = True,
) -> dict:
	"""One pass over CRM Deal from the stored cursor. Not the scheduler entry point.

	Closed deals are read too, and deliberately: a deal that was flagged while it
	was open must have the flag cleared when it is won, and a sweep that skipped
	closed rows would leave that stale chip on the card forever.

	`commit_between_batches` matches `crm.sweeps.run_sweep`'s parameter of the
	same name and exists for the same reason: in production a batch must be
	durable before the next one starts, and in a test the whole run has to stay
	inside the transaction the test rolls back.
	"""
	now = now or frappe.utils.now_datetime()
	stats = {"read": 0, "changed": 0, "batches": 0, "locked": False, "finished": False}

	with sweep_lock(JOB_NAME) as acquired:
		if not acquired:
			return stats

		stats["locked"] = True
		cursor = get_watermark(JOB_NAME)

		while max_batches is None or stats["batches"] < max_batches:
			rows = read_batch(
				DEAL_DOCTYPE,
				["status", "expected_closure_date", "creation", HEALTH_FIELD],
				cursor,
				batch_size,
			)
			if not rows:
				stats["finished"] = True
				break

			stats["read"] += len(rows)
			stats["changed"] += apply_batch(rows, now)

			last = rows[-1]
			cursor = (frappe.utils.cstr(last.get("modified")), last.get("name"))
			set_watermark(JOB_NAME, cursor[0], cursor[1])
			stats["batches"] += 1
			if commit_between_batches:
				frappe.db.commit()

			if len(rows) < batch_size:
				stats["finished"] = True
				break

		if stats["finished"]:
			# Time alone can make a deal overdue, so tomorrow starts from the top.
			reset_watermark(JOB_NAME)

	return stats


def sweep_deal_health() -> int:
	"""Scheduler entry point, daily. Never raises; returns rows changed.

	Behind `deal_health_enabled`, default OFF. While the flag is off this reads
	no deal row and writes nothing at all.
	"""
	try:
		if not is_enabled(FLAG_DEAL_HEALTH):
			return 0

		if not frappe.db.has_column(DEAL_DOCTYPE, HEALTH_FIELD):
			# The patch has not run on this site yet. Turning the flag on before
			# `bench migrate` is a configuration mistake, not a crash.
			return 0

		return run_sweep()["changed"]
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM deal health: sweep failed")
		return 0


# --- readers ---------------------------------------------------------------


def flag_label(flag: str) -> str:
	"""One short human sentence per flag. Used by the chip and the digest."""
	from frappe import _

	return {
		CLOSE_DATE_PASSED: _("Expected close date has passed"),
		STALLED: _("No stage change in {0} days").format(stalled_days()),
		AWAITING_REPLY: _("Customer is waiting for a reply"),
	}.get(flag, flag)


def flagged_deals(limit: int = 20) -> list[dict]:
	"""Flagged deals for the manager digest. NOT permission-scoped, on purpose.

	The digest already reports site-wide WhatsApp counts to every manager, and
	this rides the same notification. It returns no record to a request: the only
	thing done with the rows is to count them and name the worst few in one
	CRM Notification. An endpoint over this data must go through
	`frappe.get_list` instead -- `crm.api.today` does.
	"""
	if not frappe.db.has_column(DEAL_DOCTYPE, HEALTH_FIELD):
		return []

	rows = frappe.get_all(
		DEAL_DOCTYPE,
		filters={HEALTH_FIELD: ["is", "set"]},
		fields=["name", "organization", "lead_name", "deal_owner", HEALTH_FIELD],
		order_by="modified desc",
		limit_page_length=limit,
	)

	found = []
	for row in rows:
		flags = parse(row.get(HEALTH_FIELD))
		if not flags:
			continue
		found.append(
			{
				"name": row["name"],
				"title": row.get("organization") or row.get("lead_name") or row["name"],
				"deal_owner": row.get("deal_owner"),
				"flags": flags,
			}
		)

	return found
