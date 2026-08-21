"""Item 14: the email composer's sparkle -- one drafted body, inserted at the caret.

Master spec §5 item 14: insert at cursor with immediate Undo, no streaming, and
the popover states which record fields will be sent. This module is the server
half of that. It drafts a BODY and nothing else -- no subject, no recipients, no
attachments, and above all no send. C6 is not negotiable: the model writes
words into an editor the agent is already sitting in, and the agent presses
Send. Nothing here queues, schedules or delivers anything.

Field discipline, reused rather than reinvented
-----------------------------------------------
`crm.api.followup_engine` already answered "what may a model see about a lead"
for WhatsApp: a fixed field whitelist, the last few messages, and no link the
lead's own data does not already contain. The same three rules apply here, and
the lead half of the whitelist IS that module's `AI_LEAD_FIELDS`, imported so
the two cannot drift apart.

The link rule runs in BOTH directions, which is the one thing this module adds:

* **Outbound.** Message history is stripped of every link the record's own
  whitelisted fields do not contain, BEFORE the prompt is built. A customer who
  pastes a competitor's booking link into an email has not consented to that
  link being forwarded to a model vendor, and an agency's internal links are
  nobody else's business either.
* **Inbound.** The drafted body is stripped the same way, so a model cannot put
  a URL it invented in front of an agent who is about to mail it to a customer.

What leaves the site
--------------------
Per call, exactly: the whitelisted record fields in `RECORD_FIELDS` (empties
dropped), the agent's own typed instruction, and up to `MESSAGE_HISTORY_LIMIT`
emails from that record -- subject and body, HTML stripped, links stripped as
above, each cut to `MESSAGE_CHARS` characters. Never: the customer's email
address, phone or mobile number, the record's owner or assignees, attachments,
or any other record.

Authorization (master spec §3)
------------------------------
`generate` and `sent_fields` are callable by any authenticated user who can READ
the record -- the same bar as opening the composer on it. Scope is derived
server-side from the doctype and name with `frappe.has_permission(..., doc=...)`;
the message history is read with a filter pinned to that one record. A missing
record and a forbidden one fail identically.
"""

import frappe
from frappe import _

from crm.ai.client import (
	AIConfigurationError,
	AIResponseError,
	complete,
)
from crm.api.followup_engine import AI_LEAD_FIELDS, URL_PATTERN

LEAD_DOCTYPE = "CRM Lead"
DEAL_DOCTYPE = "CRM Deal"

SUPPORTED_DOCTYPES = (LEAD_DOCTYPE, DEAL_DOCTYPE)

# The lead list is the follow-up engine's, imported. A field added there is a
# field the agency already decided a model may see about a lead; a second copy
# here would let the two answers diverge without anybody noticing.
RECORD_FIELDS = {
	LEAD_DOCTYPE: AI_LEAD_FIELDS,
	DEAL_DOCTYPE: (
		"lead_name",
		"first_name",
		"organization",
		"status",
		"currency",
		"deal_value",
		"expected_closure_date",
		"next_step",
	),
}

# Same count as the follow-up engine's `CONVERSATION_HISTORY_LIMIT`, for the same
# reason: enough thread to answer the customer's actual question, not enough to
# turn one budgeted request into a large token bill.
MESSAGE_HISTORY_LIMIT = 10

MESSAGE_CHARS = 600

# Master spec item 14. Enforced here rather than in the schema on purpose: a
# schema `maxLength` would fail an over-long answer and spend a SECOND budgeted
# request re-asking for it, when cutting the tail costs nothing.
MAX_BODY_CHARS = 2000

# The agent's own words. Long enough for a real instruction, short enough that
# the prompt cannot be stuffed through this field.
MAX_INSTRUCTION_CHARS = 500

DRAFT_TOKENS = 900

DRAFT_SCHEMA = {
	"type": "object",
	"properties": {
		"body": {
			"type": "string",
			"minLength": 1,
			"description": "The email body as plain text. Paragraphs separated by blank lines.",
		}
	},
	"required": ["body"],
	"additionalProperties": False,
}

DRAFT_SYSTEM = (
	"You draft the BODY of one email for a travel agency's sales agent to review "
	"before they send it. Write plain text in short paragraphs. Do not write a "
	"subject line. Do not write a signature block, a company name or contact "
	"details -- the agent's account adds those. Do not invent prices, dates, "
	"availability or bookings; use only what the record and the thread show. "
	"Write no links. Keep it under 200 words unless the instruction asks for more."
)


# --- endpoints -------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def generate(doctype: str, name: str, instruction: str = "") -> dict:
	"""Draft one email body for one record. Reached from the composer's sparkle.

	Authorization: the caller must be able to READ `name`. See the module
	docstring. Nothing is read, built or spent before that check passes.
	"""
	require_record(doctype, name)

	instruction = clean_instruction(instruction)
	if not instruction:
		frappe.throw(_("Say what the email should do before asking for a draft."))

	context = record_context(doctype, name)
	allowed = allowed_links(context)
	history = message_history(doctype, name, allowed)

	answer = ask_model(build_prompt(doctype, context, history, instruction))

	body = clean_body(answer.get("body"), allowed)
	if not body:
		frappe.throw(_("The AI answered with an empty draft."), title=_("Draft Failed"))

	return {"body": body, "generated_at": frappe.utils.now()}


@frappe.whitelist(methods=["GET"])
def sent_fields(doctype: str) -> list[str]:
	"""The human labels of the fields `generate` would send, for the disclosure.

	The popover has to name what leaves the site, and a hand-typed list in a Vue
	file is a list that goes stale the first time `RECORD_FIELDS` changes. This
	reads the same constant the prompt builder reads.

	Authorization: any authenticated user. It returns field LABELS from the
	doctype's meta, which every CRM user can already see on the form; no record
	is touched.
	"""
	if doctype not in SUPPORTED_DOCTYPES:
		return []

	meta = frappe.get_meta(doctype)
	labels = []
	for fieldname in RECORD_FIELDS[doctype]:
		field = meta.get_field(fieldname)
		labels.append(_(field.label) if field and field.label else fieldname)
	return labels


def require_record(doctype: str, name: str) -> None:
	"""Refuse anything the caller may not read, without saying which it was."""
	if doctype not in SUPPORTED_DOCTYPES:
		frappe.throw(_("A draft can only be written for a lead or a deal."))

	refused = _("Not permitted to access {0} {1}.").format(doctype, name)

	if not name or not frappe.db.exists(doctype, name):
		frappe.throw(refused, frappe.PermissionError)

	if not frappe.has_permission(doctype, "read", doc=name):
		frappe.throw(refused, frappe.PermissionError)


def ask_model(prompt: str) -> dict:
	"""One `complete()` call, with the AI failures turned into clear errors.

	`isolate_budget_claim=True`: this endpoint writes nothing, and a person is
	waiting on the answer. See `crm.ai.client.reserve_request`.
	"""
	try:
		answer = complete(
			prompt,
			system=DRAFT_SYSTEM,
			max_tokens=DRAFT_TOKENS,
			json_schema=DRAFT_SCHEMA,
			isolate_budget_claim=True,
		)
	except AIConfigurationError:
		raise
	except AIResponseError as error:
		frappe.throw(
			_("The AI could not draft this email: {0}").format(frappe.utils.escape_html(str(error))),
			title=_("Draft Failed"),
		)

	if not isinstance(answer, dict):
		frappe.throw(_("The AI answered with something that is not a draft."))

	return answer


# --- what the model is shown -----------------------------------------------


def record_context(doctype: str, name: str) -> dict:
	"""The whitelisted fields of one record, empties dropped."""
	fields = RECORD_FIELDS[doctype]
	row = frappe.db.get_value(doctype, name, list(fields), as_dict=True) or {}
	return {key: value for key, value in row.items() if value not in (None, "")}


def allowed_links(context: dict) -> str:
	"""Everything the record itself says, as one blob to test links against."""
	return " ".join(frappe.utils.cstr(value) for value in context.values())


def message_history(doctype: str, name: str, allowed: str) -> list[str]:
	"""The last few emails on this record, oldest first, scrubbed.

	Read with a filter pinned to the one record whose permission was checked
	above; nothing here derives scope from anything the caller sent beyond that
	name.
	"""
	rows = frappe.get_all(
		"Communication",
		filters={
			"reference_doctype": doctype,
			"reference_name": name,
			"communication_type": ["in", ["Communication", "Automated Message"]],
		},
		fields=["subject", "content", "sent_or_received", "communication_date"],
		order_by="communication_date desc, creation desc",
		limit=MESSAGE_HISTORY_LIMIT,
	)

	history = []
	for row in reversed(rows):
		speaker = _("Customer") if row.sent_or_received == "Received" else _("Agency")
		text = scrub(f"{frappe.utils.cstr(row.subject)} {frappe.utils.cstr(row.content)}", allowed)
		if text:
			history.append(f"{speaker}: {text[:MESSAGE_CHARS]}")
	return history


def clean_instruction(instruction) -> str:
	return " ".join(frappe.utils.strip_html(frappe.utils.cstr(instruction)).split())[:MAX_INSTRUCTION_CHARS]


def build_prompt(doctype: str, context: dict, history: list[str], instruction: str) -> str:
	label = _("Lead") if doctype == LEAD_DOCTYPE else _("Deal")

	fields = "\n".join(f"- {key}: {value}" for key, value in context.items())
	thread = "\n".join(f"- {line}" for line in history)

	return (
		f"{label} record:\n{fields or '- nothing on record'}\n\n"
		f"Last messages (oldest first):\n{thread or '- no messages on record'}\n\n"
		f"What this email should do:\n{instruction}\n\n"
		"Answer with a JSON object holding one member, `body`."
	)


# --- text rules ------------------------------------------------------------


def scrub(value, allowed: str) -> str:
	"""HTML-free, one-line text with every unknown link removed.

	The rule and the pattern are `crm.api.followup_engine.sanitize_param`'s: a
	link survives only when the record's own whitelisted data already holds it.
	Applied to the PROMPT, so an unknown link never reaches the provider at all.
	"""
	text = " ".join(frappe.utils.strip_html(frappe.utils.cstr(value or "")).split())
	if not text:
		return ""

	def keep_known_url(match):
		return match.group(0) if match.group(0) in allowed else ""

	return " ".join(URL_PATTERN.sub(keep_known_url, text).split())


def clean_body(value, allowed: str) -> str:
	"""The drafted body, link-scrubbed, paragraphs kept, cut to the cap.

	`scrub` collapses everything onto one line, which is right for a WhatsApp
	template variable and wrong for an email. Blank lines are the only structure
	a plain-text body has, so they survive; runs of them do not.
	"""
	if not isinstance(value, str):
		return ""

	lines = [scrub(line, allowed) for line in frappe.utils.strip_html(value).splitlines()]

	paragraphs = []
	for line in lines:
		if line:
			paragraphs.append(line)
		elif paragraphs and paragraphs[-1] != "":
			paragraphs.append("")

	body = "\n".join(paragraphs).strip()
	return body[:MAX_BODY_CHARS].strip()
