"""Items 13 + 28 + 15, merged: the timeline's one "Brief" card.

Master spec §5 folds three v1 features -- record summary, suggested next step,
message tone -- into ONE card fed by ONE model call. Not three cards and not
three calls: UX §2.15 allows the timeline a single dismissible Brief, and the
budget in `crm.ai.client` counts requests, so three calls for one click would
cost the agency three times as much for a worse screen.

Two rules the whole module is built around
------------------------------------------
* **On demand only.** Nothing here runs when a record is opened. `generate` is
  reached from a button and from nothing else -- no doc event, no scheduler, no
  prefetch. Opening fifty leads must not cost fifty model calls.
* **The AI never acts (master spec C6).** This endpoint returns text. It writes
  nothing to the record. The suggested next step arrives as words; a task exists
  only after the agent opens the task modal and presses Create, and the brief
  becomes a Note only if the agent asks for that. Both of those happen in the
  browser, through the ordinary CRM APIs, not here.

What leaves the site
--------------------
The same table is kept in `crm/ai/client.py` and in
`demo-package/specs/permission-matrix.md`. For one call, exactly:

* The whitelisted record fields in `RECORD_FIELDS` -- for a CRM Lead:
  `lead_name`, `first_name`, `status`, `source`, `destination`,
  `travel_start_date`, `travel_end_date`, `group_size`, `budget`; for a CRM
  Deal: `lead_name`, `first_name`, `organization`, `status`, `currency`,
  `deal_value`, `expected_deal_value`, `expected_closure_date`, `next_step`.
  Empty fields are dropped rather than sent as blanks.
* An excerpt of that record's own timeline: email subjects and bodies, comments,
  call direction/status/duration/note, task titles and due dates, note titles
  and bodies, and the newest `WHATSAPP_LIMIT` WhatsApp messages of that record's
  conversation. HTML is stripped, each item is cut to `ITEM_CHARS` characters, at
  most `ACTIVITY_LIMIT` items are sent, and the whole excerpt is cut to
  `PAYLOAD_BYTES` bytes from the oldest end.

WhatsApp is in that list as of Stage 5.1, and it is not an afterthought: this
agency is WhatsApp-first, so a brief that read only Communications summarised a
record whose entire conversation it could not see and judged the tone of a
customer whose words it had never read. The message TEXT is sent; the number is
not, and neither is anything else about the WhatsApp row.

What NEVER leaves the site: the customer's email address, phone number, mobile
number; the record's owner, assignees and every other user; attachments and file
names; and field-change history (a "email changed to ..." version row carries an
address, so version rows are not in the excerpt at all).

Authorization (master spec §3)
------------------------------
`generate` is callable by any authenticated user who can READ the record, which
is the same bar the timeline it summarises already clears
(`crm.api.activities.get_activities`). Row-level scope is derived server-side:
the caller supplies a doctype and a name, both are checked with
`frappe.has_permission(..., doc=name)`, and the activity data is read by
`crm.api.activities`, which repeats that check itself. A missing record and a
forbidden one fail identically, so the endpoint cannot be used to find out which
record ids exist.
"""

import re

import frappe
from frappe import _

from crm.ai.client import (
	AIConfigurationError,
	AIResponseError,
	complete,
)

LEAD_DOCTYPE = "CRM Lead"
DEAL_DOCTYPE = "CRM Deal"

# The Brief exists on the two records that have a timeline. Anything else is a
# caller mistake, not a feature request to be silently absorbed.
SUPPORTED_DOCTYPES = (LEAD_DOCTYPE, DEAL_DOCTYPE)

# Record fields the model is allowed to see, per doctype. Travel-agency context
# and nothing else -- no address, no phone, no owner. Same discipline as
# `crm.api.followup_engine.AI_LEAD_FIELDS`.
RECORD_FIELDS = {
	LEAD_DOCTYPE: (
		"lead_name",
		"first_name",
		"status",
		"source",
		"destination",
		"travel_start_date",
		"travel_end_date",
		"group_size",
		"budget",
	),
	DEAL_DOCTYPE: (
		"lead_name",
		"first_name",
		"organization",
		"status",
		"currency",
		"deal_value",
		"expected_deal_value",
		"expected_closure_date",
		"next_step",
	),
}

# How many timeline items reach the prompt, newest first.
ACTIVITY_LIMIT = 25

# How much of one item's text survives. An email thread quotes itself; the top
# of a message is the part that says anything.
ITEM_CHARS = 400

# The ceiling on the whole excerpt. `crm.ai.client.MAX_REQUEST_BYTES` is 100_000
# and refuses anything larger; this sits an order of magnitude under it, because
# the request cap is the last line of defence and not the working limit.
PAYLOAD_BYTES = 12_000

# Inbound messages are what the tone line is about, so the newest few are kept
# even when they fall outside the newest `ACTIVITY_LIMIT` items.
INBOUND_FLOOR = 3

# How many WhatsApp messages of the record's conversation are read before the
# shared caps above trim them. Deliberately close to `ACTIVITY_LIMIT`: on a
# WhatsApp-first record the conversation IS the timeline, and reading fewer would
# hand the model an excerpt that starts in the middle of a sentence.
WHATSAPP_LIMIT = 20

WHATSAPP_DOCTYPE = "WhatsApp Message"

BRIEF_TOKENS = 900

TONES = ("positive", "neutral", "negative", "frustrated")

DUE_HINTS = ("today", "tomorrow", "this_week", "next_week")

MIN_BULLETS = 3
MAX_BULLETS = 5
BULLET_CHARS = 240
NEXT_STEP_CHARS = 200

BRIEF_SCHEMA = {
	"type": "object",
	"properties": {
		"bullets": {
			"type": "array",
			"minItems": MIN_BULLETS,
			"maxItems": MAX_BULLETS,
			"items": {"type": "string", "minLength": 1},
			"description": "Three to five short factual lines about this customer.",
		},
		"next_step": {
			"type": ["object", "null"],
			"nullable": True,
			"properties": {
				"description": {"type": "string", "minLength": 1},
				"due_hint": {
					"type": ["string", "null"],
					"nullable": True,
					"enum": [*DUE_HINTS, None],
				},
			},
			"required": ["description"],
			"additionalProperties": False,
			"description": "One action the agent should take next, or null if none is warranted.",
		},
		"tone": {
			"type": ["string", "null"],
			"nullable": True,
			"enum": [*TONES, None],
			"description": "The customer's tone in their own messages, or null if they sent none.",
		},
	},
	# Only `bullets` is required. A model that expresses "no next step" by leaving
	# the member out rather than by writing null is answering correctly enough,
	# and failing it would spend a second budgeted request to be told the same
	# thing. `clean_brief` reads a missing member as null.
	"required": ["bullets"],
	"additionalProperties": False,
}

BRIEF_SYSTEM = (
	"You brief a travel agency's sales agent on one customer record before they "
	"pick it up again. Write only what the record and its timeline show. Never "
	"invent a fact, a price, a date or a booking. Bullets are short, factual and "
	"in the past tense; do not greet the agent and do not write a preamble. The "
	"suggested next step is one concrete action the agent can take, or null when "
	"the record does not call for one. Judge tone from the CUSTOMER's own "
	"messages only; if the customer has sent none, tone is null. Write no links."
)

# Anything that could act as a link. Copied in spirit from
# `crm.api.followup_engine.URL_PATTERN`: over-matching costs a stripped word,
# under-matching lets the model put a URL in front of an agent.
URL_PATTERN = re.compile(
	r"(?:https?://|www\.)\S+|\b[\w-]+(?:\.[\w-]+)*\.[a-z]{2,24}\b(?:/\S*)?",
	re.IGNORECASE,
)


# --- endpoint --------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def generate(doctype: str, name: str) -> dict:
	"""Summarise one record in one model call. Reached from a button only.

	Authorization: the caller must be able to READ `name`. See the module
	docstring; the check is `require_record` below and it runs before anything
	is read, built or spent.
	"""
	require_record(doctype, name)

	context = record_context(doctype, name)
	items = timeline_items(doctype, name)
	has_inbound = any(item["inbound"] for item in items)

	answer = ask_model(build_prompt(doctype, context, items, has_inbound))

	return clean_brief(answer, context, has_inbound)


def require_record(doctype: str, name: str) -> None:
	"""Refuse anything the caller may not read, without saying which it was."""
	if doctype not in SUPPORTED_DOCTYPES:
		frappe.throw(_("A brief can only be written for a lead or a deal."))

	refused = _("Not permitted to access {0} {1}.").format(doctype, name)

	if not name or not frappe.db.exists(doctype, name):
		frappe.throw(refused, frappe.PermissionError)

	if not frappe.has_permission(doctype, "read", doc=name):
		frappe.throw(refused, frappe.PermissionError)


def ask_model(prompt: str) -> dict:
	"""One `complete()` call, with the AI failures turned into clear errors.

	`isolate_budget_claim=True`: this endpoint reads and writes nothing, and a
	person is waiting on the answer. See `crm.ai.client.reserve_request`.
	"""
	try:
		answer = complete(
			prompt,
			system=BRIEF_SYSTEM,
			max_tokens=BRIEF_TOKENS,
			json_schema=BRIEF_SCHEMA,
			isolate_budget_claim=True,
		)
	except AIConfigurationError:
		# The message already names what to fix ("AI is turned off", "No AI API
		# key is configured"). The button points at Settings -> AI & Follow-ups.
		raise
	except AIResponseError as error:
		frappe.throw(
			_("The AI could not write this brief: {0}").format(frappe.utils.escape_html(str(error))),
			title=_("Brief Failed"),
		)

	if not isinstance(answer, dict):
		frappe.throw(_("The AI answered with something that is not a brief."))

	return answer


# --- what the model is shown -----------------------------------------------


def record_context(doctype: str, name: str) -> dict:
	"""The whitelisted fields of one record, empties dropped."""
	fields = RECORD_FIELDS[doctype]
	row = frappe.db.get_value(doctype, name, list(fields), as_dict=True) or {}
	return {key: value for key, value in row.items() if value not in (None, "")}


def timeline_items(doctype: str, name: str) -> list[dict]:
	"""The record's timeline as flat, capped, HTML-free items, oldest first.

	Read through `crm.api.activities`, which is the app's one definition of what
	a record's timeline holds and which repeats the read-permission check itself.
	Version rows are deliberately NOT included: a "email changed to ..." row
	carries the customer's address, and no summary is worth mailing that out.

	Note the ceiling underneath ours: `frappe.desk.form.load.get_docinfo` reads
	the newest 21 communications and no more. The brief therefore summarises the
	same messages the agent can see on the record, which is the right answer for
	a reading aid -- it cannot claim to know something the timeline does not
	show -- but it does mean a thread's 22nd-newest email is outside its view.
	`crm/tests/test_ai_brief.py` asserts that number so a Frappe upgrade that
	changes it is noticed here rather than in a wrong summary.
	"""
	from crm.api.activities import get_deal_activities, get_lead_activities

	reader = get_deal_activities if doctype == DEAL_DOCTYPE else get_lead_activities
	activities, calls, notes, tasks, _attachments = reader(name)

	# `get_docinfo` parks its whole payload on the response. Ours is a small JSON
	# object and has no use for it.
	frappe.response.pop("docinfo", None)

	items = []
	items += communication_items(activities)
	items += comment_items(activities)
	items += call_items(calls)
	items += task_items(tasks)
	items += note_items(notes)
	items += whatsapp_items(doctype, name)

	items.sort(key=lambda item: item["at"], reverse=True)
	return trim(items)


def whatsapp_items(doctype: str, name: str) -> list[dict]:
	"""The record's WhatsApp conversation, as timeline items. Never raises.

	`frappe_whatsapp` is optional -- it is not installed in CI and may not be
	installed on a customer's site -- so a missing doctype returns nothing rather
	than failing a brief that has plenty else to say.

	A Deal's conversation is read on the Deal alone. A converted lead's older
	messages sit on the Lead, and the Brief summarises the record it was asked
	about; pulling a second record's history in would tell an agent things the
	timeline in front of them does not show, which is exactly the rule the
	Communication excerpt already follows.

	Permission note (master spec 3): the caller's read permission on `name` was
	checked by `require_record` before anything here ran, and the filter is that
	one record. Nothing widens it.
	"""
	try:
		if not frappe.db.exists("DocType", WHATSAPP_DOCTYPE):
			return []

		rows = frappe.get_all(
			WHATSAPP_DOCTYPE,
			filters={"reference_doctype": doctype, "reference_name": name},
			fields=["type", "message", "creation"],
			order_by="creation desc",
			limit=WHATSAPP_LIMIT,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM brief: WhatsApp excerpt failed")
		return []

	items = []
	for row in rows:
		text = join_text(row.get("message"))
		if not text:
			continue

		inbound = row.get("type") == "Incoming"
		items.append(
			item(
				at=row.get("creation"),
				speaker=_("Customer") if inbound else _("Agency"),
				kind=_("whatsapp"),
				text=text,
				inbound=inbound,
			)
		)
	return items


def communication_items(activities: list) -> list[dict]:
	rows = [a for a in activities if a.get("activity_type") == "communication"]
	if not rows:
		return []

	directions = message_directions([a.get("name") for a in rows])

	items = []
	for row in rows:
		data = row.get("data") or {}
		inbound = directions.get(row.get("name")) == "Received"
		text = join_text(data.get("subject"), data.get("content"))
		if not text:
			continue
		items.append(
			item(
				at=row.get("communication_date") or row.get("creation"),
				speaker=_("Customer") if inbound else _("Agency"),
				kind=_("email"),
				text=text,
				inbound=inbound,
			)
		)
	return items


def message_directions(names: list) -> dict:
	"""Which of these Communications came IN, keyed by name.

	`frappe.desk.form.load.get_docinfo` does not select `sent_or_received`, so
	the timeline payload cannot say who spoke. One extra query for the whole
	list, rather than a lookup per message. The names come from a permission-
	checked read of one record, so no scope is widened here.
	"""
	names = [name for name in names if name]
	if not names:
		return {}

	rows = frappe.get_all(
		"Communication",
		filters={"name": ["in", names]},
		fields=["name", "sent_or_received"],
	)
	return {row.name: row.sent_or_received for row in rows}


def comment_items(activities: list) -> list[dict]:
	return [
		item(
			at=row.get("creation"),
			speaker=_("Agency"),
			kind=_("comment"),
			text=join_text(row.get("content")),
			inbound=False,
		)
		for row in activities
		if row.get("activity_type") == "comment" and join_text(row.get("content"))
	]


def call_items(calls: list) -> list[dict]:
	items = []
	for row in calls or []:
		# A call is a fact about contact, not a transcript. Only the note is text
		# somebody wrote, and the rest is three words of shape.
		shape = _("{0} call, {1}").format(row.get("type") or _("Phone"), row.get("status") or _("Unknown"))
		text = join_text(shape, row.get("note"))
		items.append(
			item(
				at=row.get("start_time") or row.get("creation"),
				speaker=_("Agency"),
				kind=_("call"),
				text=text,
				inbound=False,
			)
		)
	return items


def task_items(tasks: list) -> list[dict]:
	items = []
	for row in tasks or []:
		shape = _("{0} ({1})").format(row.get("title") or _("Untitled"), row.get("status") or _("Open"))
		if row.get("due_date"):
			shape = _("{0}, due {1}").format(shape, frappe.utils.format_date(row["due_date"]))
		text = join_text(shape, row.get("description"))
		items.append(
			item(
				at=row.get("creation") or row.get("modified"),
				speaker=_("Agency"),
				kind=_("task"),
				text=text,
				inbound=False,
			)
		)
	return items


def note_items(notes: list) -> list[dict]:
	return [
		item(
			at=row.get("creation") or row.get("modified"),
			speaker=_("Agency"),
			kind=_("note"),
			text=join_text(row.get("title"), row.get("content")),
			inbound=False,
		)
		for row in notes or []
		if join_text(row.get("title"), row.get("content"))
	]


def item(at, speaker: str, kind: str, text: str, inbound: bool) -> dict:
	return {
		"at": as_datetime(at),
		"speaker": speaker,
		"kind": kind,
		"text": text[:ITEM_CHARS],
		"inbound": inbound,
	}


def as_datetime(value):
	"""A sortable moment for anything the timeline hands us, never None."""
	try:
		parsed = frappe.utils.get_datetime(value)
	except Exception:
		parsed = None
	return parsed or frappe.utils.get_datetime("1900-01-01 00:00:00")


def join_text(*parts) -> str:
	"""HTML-free, single-spaced text from any number of fragments."""
	pieces = []
	for part in parts:
		text = " ".join(frappe.utils.strip_html(frappe.utils.cstr(part or "")).split())
		if text:
			pieces.append(text)
	return " — ".join(pieces)


def trim(items: list[dict]) -> list[dict]:
	"""Newest `ACTIVITY_LIMIT` items, plus the newest inbound ones, oldest first.

	Two caps, because they guard different things. The item count keeps the
	prompt readable; the byte cap keeps a single pathological message from
	turning one budgeted request into a large bill (`crm.ai.client` counts
	requests, the provider charges tokens).

	Inbound messages get a floor of their own: the tone line is about what the
	customer said, and a record with a burst of internal notes on top of it must
	not push the customer's last words out of the excerpt.
	"""
	kept = items[:ACTIVITY_LIMIT]

	if len(items) > ACTIVITY_LIMIT:
		inbound = [row for row in items if row["inbound"]][:INBOUND_FLOOR]
		for row in inbound:
			if row not in kept:
				kept.append(row)

	kept.sort(key=lambda row: row["at"])

	# Drop from the oldest end: the newest lines are the ones a brief is about.
	while kept and payload_size(kept) > PAYLOAD_BYTES:
		kept.pop(0)

	return kept


def payload_size(items: list[dict]) -> int:
	return sum(len(row["text"].encode("utf-8")) + 40 for row in items)


def build_prompt(doctype: str, context: dict, items: list[dict], has_inbound: bool) -> str:
	label = _("Lead") if doctype == LEAD_DOCTYPE else _("Deal")

	fields = "\n".join(f"- {key}: {value}" for key, value in context.items())
	timeline = "\n".join(
		"- {0} [{1}] {2}: {3}".format(
			frappe.utils.format_datetime(row["at"]), row["kind"], row["speaker"], row["text"]
		)
		for row in items
	)

	tone_rule = (
		_("The customer has sent messages, so judge their tone.")
		if has_inbound
		else _("The customer has sent no message on this record, so tone MUST be null.")
	)

	return (
		f"{label} record:\n{fields or '- nothing on record'}\n\n"
		f"Timeline, oldest first:\n{timeline or '- nothing on record'}\n\n"
		f"Write {MIN_BULLETS} to {MAX_BULLETS} bullets covering what this customer wants, "
		"where the conversation stands, and anything outstanding.\n"
		f"{tone_rule}\n"
		"Suggest one next step, with a due hint of today, tomorrow, this_week or next_week, "
		"or null when nothing is outstanding."
	)


# --- what comes back -------------------------------------------------------


def clean_brief(answer: dict, context: dict, has_inbound: bool) -> dict:
	"""Turn the validated answer into what the card renders.

	The schema check in `crm.ai.client` says the SHAPE is right. It cannot say
	the model obeyed "no links" or "tone is null when nobody wrote in", so both
	are enforced here rather than hoped for.
	"""
	allowed = " ".join(frappe.utils.cstr(value) for value in context.values())

	bullets = []
	for raw in answer.get("bullets") or []:
		bullet = clean_text(raw, allowed)[:BULLET_CHARS]
		if bullet:
			bullets.append(bullet)
	bullets = bullets[:MAX_BULLETS]

	if not bullets:
		frappe.throw(_("The AI answered with an empty brief."), title=_("Brief Failed"))

	return {
		"bullets": bullets,
		"next_step": clean_next_step(answer.get("next_step"), allowed),
		# C6 and item 15: tone is on-demand and evidence-bound. With no inbound
		# message there is nothing to have a tone, whatever the model returned.
		"tone": answer.get("tone") if has_inbound and answer.get("tone") in TONES else None,
		"generated_at": frappe.utils.now(),
	}


def clean_next_step(raw, allowed: str):
	if not isinstance(raw, dict):
		return None

	description = clean_text(raw.get("description"), allowed)[:NEXT_STEP_CHARS]
	if not description:
		return None

	due_hint = raw.get("due_hint")
	return {"description": description, "due_hint": due_hint if due_hint in DUE_HINTS else None}


def clean_text(value, allowed: str) -> str:
	"""Strip HTML and any link the record does not itself contain.

	A hallucinated URL in front of an agent who is about to paste it to a
	customer is the same problem `crm.api.followup_engine.sanitize_param` exists
	to stop, so the rule is the same one: a link survives only if the record's
	own whitelisted data already holds it.
	"""
	if not isinstance(value, str):
		return ""

	text = " ".join(frappe.utils.strip_html(value).split())

	def keep_known_url(match):
		return match.group(0) if match.group(0) in allowed else ""

	return " ".join(URL_PATTERN.sub(keep_known_url, text).split())
