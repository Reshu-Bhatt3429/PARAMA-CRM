"""Provider-agnostic server-side LLM client.

One public entry point, `complete()`. The agency plugs an API key into
"CRM AI Settings" and every AI feature in the CRM works -- no code change, no
new Python dependency. `requests` ships with Frappe, so the four supported
providers are reached over plain HTTP:

* Anthropic  -- Messages API (`POST /v1/messages`, `x-api-key` header).
* OpenAI     -- Chat Completions (`POST /v1/chat/completions`, bearer token).
* OpenRouter -- the same Chat Completions shape on another base url.
* Gemini     -- Google's OpenAI-compatible surface, so the same shape again.
  A free Google AI Studio key works. Suggested model: `gemini-2.0-flash`.

The module is deliberately generic: the follow-up engine uses it to fill
WhatsApp template variables today, and an itinerary generator can use the same
call tomorrow.

The API key is never logged. Failures are logged with the HTTP status code and
a truncated response body only.

Four guards, and what each one is for
-------------------------------------
* **The budget is reserved BEFORE the network call**, with one atomic statement
  (`crm.counters`). A read-then-write lets two workers both spend the last
  request of the month. Reserving first also means a crash mid-request
  over-counts by one instead of losing the charge, which is the safe direction
  for someone else's bill.
* **The answer is validated against the schema that was asked for**
  (`crm.ai.schema`), not merely parsed. "It is JSON" was never the question.
* **The request has a size ceiling.** A caller that loops a conversation into a
  prompt turns a capped monthly budget into an uncapped monthly bill, because
  the cap counts requests and the provider charges tokens.
* **No streaming.** Nothing in this app streams a model answer, and no caller
  may claim it does until it is implemented.

What leaves the site
--------------------
BYO key or not, every call sends agency data to a third party. Each call site is
listed here, and a call site that is not listed is a review blocker. The same
table is kept in `demo-package/specs/permission-matrix.md`.

* `crm.api.followup_engine.ai_params` -- fills WhatsApp template variables.
  Sends: the lead's `lead_name`, `first_name`, `destination`,
  `travel_start_date`, `travel_end_date`, `group_size`, `budget`
  (`AI_LEAD_FIELDS`), the approved template's name, and up to
  `CONVERSATION_HISTORY_LIMIT` WhatsApp messages of that conversation, stripped
  of HTML and cut to 200 characters each. The customer's phone number, email
  address, owner and every other lead field stay on the site.
* `crm.api.itinerary` (`skeleton_prompt`, `day_prompt`) -- drafts itinerary days.
  Sends: the itinerary's `destination`, `start_date`, `num_days`, `group_size`,
  `budget`, `currency`, and the day titles and summaries already on the
  itinerary. No customer name, phone, email or lead reference is included.
* `crm.api.ai_brief.generate` -- the timeline Brief card. Sends: the whitelisted
  record fields in `crm.api.ai_brief.RECORD_FIELDS` and an excerpt of the
  record's own timeline (emails, comments, calls, tasks, notes, and since Stage
  5.1 the newest `WHATSAPP_LIMIT` WhatsApp messages of that record's
  conversation), capped by `ACTIVITY_LIMIT` items and `PAYLOAD_BYTES` bytes. The
  customer's email address, phone number, WhatsApp number, the record owner and
  every other field stay on the site -- a WhatsApp message contributes its TEXT
  and nothing else. The full list is in that module's docstring.
* `crm.api.ai_draft.generate` -- the email composer's sparkle. Sends: the
  whitelisted record fields in `crm.api.ai_draft.RECORD_FIELDS`, the agent's own
  instruction, and up to `MESSAGE_HISTORY_LIMIT` email subjects/bodies from that
  record, stripped of HTML, of links the record does not itself contain, and cut
  to `MESSAGE_CHARS` characters each.

Nothing else in the app calls `complete()`.

Where the budget claim commits
------------------------------
`reserve_request` claims the budget slot with one atomic UPDATE. That UPDATE
takes a row lock on the settings row and, by default, holds it until the
CALLER's transaction ends -- which is after the provider answers. That is right
for the follow-up engine, whose claim and whose send bookkeeping have to stand
or fall together, and wrong for an interactive click, where it makes two agents
who press the same button queue behind each other for the length of a model
call. `isolate_budget_claim=True` commits the claim immediately and releases the
lock. See `reserve_request` for the rule a caller must satisfy to pass it.
"""

import json
import re
import time

import frappe
import requests
from frappe import _

from crm.ai import schema as json_schema_check
from crm.counters import rows_affected

SETTINGS_DOCTYPE = "CRM AI Settings"

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Google's OpenAI-compatible surface. The key travels in the Authorization
# header, never as a `?key=` query parameter, so it cannot end up in a log line.
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

# Every provider except Anthropic speaks the chat-completions shape, so they
# differ only by endpoint.
CHAT_COMPLETIONS_URLS = {
	"OpenAI": OPENAI_URL,
	"OpenRouter": OPENROUTER_URL,
	"Gemini": GEMINI_URL,
}

# Shown as a hint only. The agency picks the model; nothing here overrides it.
SUGGESTED_MODELS = {
	"Anthropic": "claude-sonnet-5",
	"OpenAI": "gpt-5",
	"OpenRouter": "anthropic/claude-sonnet-5",
	"Gemini": "gemini-3.5-flash",
}

REQUEST_TIMEOUT = 60

# Providers return these codes under load. We retry them a few times with a
# short, growing backoff so a momentary spike does not fail the request.
TRANSIENT_STATUSES = frozenset({429, 500, 502, 503, 504})
TRANSIENT_RETRIES = 3
TRANSIENT_BACKOFF = 2

# How much of a failing response body reaches the error log.
ERROR_BODY_LIMIT = 500

# The ceiling on one request, in UTF-8 bytes of prompt plus system prompt.
# Roughly 25k tokens: far more than any prompt this app builds, and far less than
# a runaway loop. The monthly budget counts REQUESTS, so without this a single
# request can cost more than the whole month was meant to.
MAX_REQUEST_BYTES = 100_000

JSON_INSTRUCTION = (
	"Reply with one JSON object and nothing else. No prose, no markdown fence. "
	"The object must match this JSON schema:\n{schema}"
)

# ```json ... ``` fences around an otherwise valid object.
CODE_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class AIConfigurationError(frappe.ValidationError):
	"""The AI provider is off, unconfigured, or over its monthly budget."""


class AIResponseError(frappe.ValidationError):
	"""The provider answered, but not with what was asked for."""


class AISchemaError(AIResponseError):
	"""The answer parsed, but does not match the schema the caller asked for.

	A subclass, so every existing caller's `except AIResponseError` keeps working
	and the retry can still word its correction accurately.
	"""


class AIRequestError(frappe.ValidationError):
	"""The request itself is refused, before any budget or network is spent."""


def is_configured() -> bool:
	"""True when `complete()` can run. Never raises, so callers can branch on it."""
	try:
		load_settings()
		return True
	except Exception:
		return False


def complete(
	prompt: str,
	system: str | None = None,
	max_tokens: int = 1024,
	json_schema: dict | None = None,
	isolate_budget_claim: bool = False,
) -> str | dict:
	"""Send one prompt to the configured provider and return its answer.

	Args:
	        prompt: the user message.
	        system: optional system prompt.
	        max_tokens: ceiling on the generated answer.
	        json_schema: when given, the model is told to answer with one JSON
	                object matching the schema and the parsed object is returned.
	        isolate_budget_claim: commit the budget claim before the network call
	                instead of leaving it in the caller's transaction. Interactive
	                endpoints pass True; see `reserve_request` for the rule.

	Returns:
	        The answer text, or the parsed object when `json_schema` is given.

	Raises:
	        AIConfigurationError: the provider is disabled, unconfigured or over
	                its monthly request budget.
	        AIRequestError: the prompt is larger than `MAX_REQUEST_BYTES`.
	        AIResponseError: the provider answered with something unusable, with
	                invalid JSON twice in a row, or twice with an object that does
	                not match `json_schema`.
	"""
	settings = load_settings()
	month, used = check_quota(settings)

	if json_schema:
		system = join_system(system, JSON_INSTRUCTION.format(schema=json.dumps(json_schema)))

	# Order matters: refuse an oversized request before spending a budget slot on
	# it, and spend the slot before the network call rather than after it.
	check_request_size(prompt, system)
	reserve_request(settings, month, used, isolate=isolate_budget_claim)
	text = dispatch(settings, prompt, system, max_tokens)

	if not json_schema:
		return text

	try:
		return read_answer(text, json_schema)
	except AIResponseError as first_error:
		# One retry: the model is told what it got wrong. A second failure is a
		# real fault, and the caller falls back to deterministic values.
		retry_prompt = f"{prompt}\n\n{correction(first_error)}"
		month, used = check_quota(settings)
		check_request_size(retry_prompt, system)
		reserve_request(settings, month, used, isolate=isolate_budget_claim)
		text = dispatch(settings, retry_prompt, system, max_tokens)
		return read_answer(text, json_schema)


def correction(error: AIResponseError) -> str:
	"""What to tell the model about its last answer.

	The two faults get different words on purpose. "Not valid JSON" to a model
	that returned a perfectly valid object with the wrong members is a correction
	it cannot act on.
	"""
	if isinstance(error, AISchemaError):
		return (
			f"Your previous answer did not match the schema ({error}). "
			"Answer again with one JSON object and nothing else."
		)

	return (
		f"Your previous answer was not valid JSON ({error}). "
		"Answer again with one JSON object and nothing else."
	)


def check_request_size(prompt: str, system: str | None) -> None:
	"""Refuse a request bigger than the ceiling, before it costs anything.

	The monthly budget counts requests, so a caller that accidentally builds a
	prompt out of an entire conversation history stays inside its request budget
	while multiplying the bill. This is the guard that makes the request count a
	meaningful cap.
	"""
	size = len(frappe.utils.cstr(prompt).encode("utf-8")) + len(frappe.utils.cstr(system).encode("utf-8"))
	if size > MAX_REQUEST_BYTES:
		raise AIRequestError(
			_("The AI request is {0} bytes, over the {1} byte limit.").format(size, MAX_REQUEST_BYTES)
		)


def read_answer(text: str, json_schema: dict):
	"""Parse the answer and hold it against the schema that was asked for.

	Parsing alone was never the question. A model that answers `{}` when three
	template variables were required returns valid JSON and an unusable answer,
	and the caller then writes it into a message a customer reads.
	"""
	parsed = parse_json(text)

	try:
		json_schema_check.validate(parsed, json_schema)
	except json_schema_check.SchemaViolation as violation:
		raise AISchemaError(frappe.utils.cstr(violation)) from violation
	except json_schema_check.UnsupportedSchema:
		# The schema, not the answer, is the problem: this app wrote it. Refusing
		# the answer would hide a bug that belongs in the error log.
		frappe.log_error(frappe.get_traceback(), "CRM AI: unsupported response schema")

	return parsed


# --- settings and budget ---------------------------------------------------


def load_settings():
	"""The AI settings, with the key decrypted. Raises when it cannot be used."""
	if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
		raise AIConfigurationError(_("AI settings are not installed on this site."))

	# Not `get_cached_doc`: the counter below writes to this Single on every
	# request, and a stale cached copy would re-spend an exhausted budget.
	settings = frappe.get_doc(SETTINGS_DOCTYPE)
	if not settings.enabled:
		raise AIConfigurationError(_("AI is turned off. Enable it in AI & Follow-ups settings."))

	api_key = settings.get_password("api_key", raise_exception=False)
	if not api_key:
		raise AIConfigurationError(_("No AI API key is configured."))

	if not settings.model:
		raise AIConfigurationError(_("No AI model is configured."))

	return frappe._dict(
		{
			"provider": settings.provider or "Anthropic",
			"model": settings.model,
			"api_key": api_key,
			"max_monthly_requests": frappe.utils.cint(settings.max_monthly_requests),
			"requests_this_month": frappe.utils.cint(settings.requests_this_month),
			"usage_month": settings.usage_month,
		}
	)


def current_month() -> str:
	return frappe.utils.nowdate()[:7]


def check_quota(settings) -> tuple[str, int]:
	"""Return (month marker, requests already spent this month), or raise.

	The cheap first gate, on the settings this caller already loaded. It refuses
	an obviously-over-budget call without touching the database again. It is NOT
	the gate that makes the cap hold under concurrency -- `reserve_request` is.
	"""
	month = current_month()
	used = settings.requests_this_month if settings.usage_month == month else 0

	limit = settings.max_monthly_requests
	if limit and used >= limit:
		raise AIConfigurationError(_("The monthly AI request limit of {0} is reached.").format(limit))

	return month, used


def commit():
	"""The one commit in this module, behind a seam the tests can neutralise.

	`crm.api.followup_engine` uses the same pattern and for the same reason: a
	commit inside a `FrappeTestCase` would end the transaction the test harness
	rolls back, so the tests replace this function rather than the database.
	"""
	frappe.db.commit()  # nosemgrep


def reserve_request(settings, month: str, used: int, isolate: bool = False) -> None:
	"""Spend one request from this month's budget, BEFORE the network call.

	Two workers that both read "999 of 1000 used" must not both send. The claim
	is therefore one atomic statement rather than a read followed by a write, and
	it happens before the request leaves: a crash between the claim and the answer
	over-counts by one, which is the safe way to be wrong about someone's bill.

	The limit comes from the settings the caller loaded, so a manager who raises
	the cap and a job that is mid-flight cannot disagree about which cap applied.

	`isolate` and why it exists
	---------------------------
	The claim is an UPDATE, so it holds a row lock on the settings row until the
	transaction that made it ends. By default that transaction is the caller's,
	and it does not end until after the provider has answered -- seconds later.
	For the follow-up engine that is correct and deliberate: its claim and the
	bookkeeping of the message it is about to send belong to one transaction.

	For an interactive endpoint it is a bug the user can feel. Two agents who
	press "Summarize" at the same moment serialise on that lock, and the second
	one waits out the first one's model call for no reason. `isolate=True`
	commits the claim at once, releasing the lock before the request leaves.

	The rule for passing `isolate=True`: the caller must have NO uncommitted
	writes of its own, because this commits the whole transaction, not just the
	counter. Both callers that pass it -- `crm.api.ai_brief.generate` and
	`crm.api.ai_draft.generate` -- read records and write none.

	Isolating also fixes the direction the failure leans. An in-transaction claim
	is undone by a rollback, so a request that WAS sent can end up uncounted; a
	committed claim survives, and the worst case is the over-count by one this
	function was designed around.
	"""
	limit = frappe.utils.cint(settings.max_monthly_requests)

	if not claim_request(month, limit):
		raise AIConfigurationError(_("The monthly AI request limit of {0} is reached.").format(limit))

	record_usage(month, used)

	if isolate:
		try:
			commit()
		except Exception:
			# The slot is claimed either way; only the lock hold time is at stake.
			# A commit that fails must not cost the user their answer.
			frappe.log_error(frappe.get_traceback(), "CRM AI: budget claim commit failed")


def claim_request(month: str, limit: int) -> bool:
	"""Increment this month's counter while there is room. True when claimed.

	`limit <= 0` means unlimited, which matches the field's own description.

	The counter lives on the settings Single, so the statement updates the
	Singles row directly -- that is the only way to make the read of the current
	count and the write of the new one one operation.

	An UNEXPECTED failure here fails OPEN, with a log entry: an AI feature that
	stops working because a counter row is unreadable is a worse outcome than a
	budget that overruns by the requests made while someone fixes it. Being over
	the cap is not an unexpected failure and fails closed.
	"""
	try:
		if frappe.db.get_single_value(SETTINGS_DOCTYPE, "usage_month", cache=False) != month:
			# A new month. Two workers that both do this at the boundary cost at
			# most one uncounted request between them.
			frappe.db.set_single_value(SETTINGS_DOCTYPE, {"usage_month": month, "requests_this_month": 0})

		frappe.db.sql(
			"""
			update `tabSingles` set value = cast(value as signed) + 1
			where doctype = %(doctype)s and field = 'requests_this_month'
				and (%(limit)s <= 0 or cast(value as signed) < %(limit)s)
			""",
			{"doctype": SETTINGS_DOCTYPE, "limit": frappe.utils.cint(limit)},
		)
		if rows_affected() == 1:
			return True

		spent = frappe.db.get_single_value(SETTINGS_DOCTYPE, "requests_this_month", cache=False)
		if spent is None:
			# The Single has never been saved, so there is no row to update yet.
			# Write the first request rather than refusing it.
			frappe.db.set_single_value(SETTINGS_DOCTYPE, {"usage_month": month, "requests_this_month": 1})
			return True

		return not (limit and frappe.utils.cint(spent) >= limit)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM AI: budget reservation failed")
		return True


def record_usage(month: str, used: int):
	"""Make the month's count visible in the settings screen.

	The count itself is claimed by `claim_request` before the network call; this
	only keeps the month marker in step and raises the stored count when it reads
	LOWER than what this caller already knows was spent. The write never lowers
	the counter, so it cannot undo a concurrent claim.

	A counter write must never sink the caller's work.
	"""
	try:
		stored = frappe.utils.cint(
			frappe.db.get_single_value(SETTINGS_DOCTYPE, "requests_this_month", cache=False)
		)
		updates = {"usage_month": month}
		if stored < used + 1:
			updates["requests_this_month"] = used + 1

		frappe.db.set_single_value(SETTINGS_DOCTYPE, updates)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM AI: usage counter update failed")


# --- provider dispatch -----------------------------------------------------


def join_system(system: str | None, extra: str) -> str:
	return f"{system}\n\n{extra}" if system else extra


def dispatch(settings, prompt: str, system: str | None, max_tokens: int) -> str:
	if settings.provider == "Anthropic":
		return call_anthropic(settings, prompt, system, max_tokens)
	return call_openai_compatible(settings, prompt, system, max_tokens)


def call_anthropic(settings, prompt: str, system: str | None, max_tokens: int) -> str:
	payload = {
		"model": settings.model,
		"max_tokens": max_tokens,
		"messages": [{"role": "user", "content": prompt}],
	}
	if system:
		payload["system"] = system

	data = post_json(
		ANTHROPIC_URL,
		headers={
			"x-api-key": settings.api_key,
			"anthropic-version": ANTHROPIC_VERSION,
			"content-type": "application/json",
		},
		payload=payload,
	)

	if data.get("stop_reason") == "refusal":
		raise AIResponseError(_("The AI provider declined the request."))

	# Current Claude models answer with a list of content blocks. Only text
	# blocks carry the answer; thinking blocks are skipped.
	blocks = data.get("content") or []
	text = "".join(block.get("text") or "" for block in blocks if block.get("type") == "text")
	if not text.strip():
		raise AIResponseError(_("The AI provider returned an empty answer."))

	return text.strip()


def call_openai_compatible(settings, prompt: str, system: str | None, max_tokens: int) -> str:
	url = CHAT_COMPLETIONS_URLS.get(settings.provider, OPENAI_URL)
	messages = []
	if system:
		messages.append({"role": "system", "content": system})
	messages.append({"role": "user", "content": prompt})

	payload = {"model": settings.model, "messages": messages, "max_tokens": max_tokens}
	if settings.provider == "Gemini":
		# Gemini models "think" by default, and the reasoning tokens are drawn from
		# the same max_tokens budget, so a normal budget can be exhausted before the
		# JSON answer is finished and the reply comes back truncated. We ask for
		# structured data, not chain-of-thought, so turn thinking off. This makes the
		# answer complete and the call faster. Google accepts the OpenAI-style key.
		payload["reasoning_effort"] = "none"
	try:
		data = post_json(
			url,
			headers={
				"authorization": f"Bearer {settings.api_key}",
				"content-type": "application/json",
			},
			payload=payload,
		)
	except AIResponseError as error:
		# Newer OpenAI models reject `max_tokens` and want `max_completion_tokens`.
		# Retrying on that one message keeps both model generations working
		# without asking the agency which generation its model belongs to.
		if "max_completion_tokens" not in str(error):
			raise
		payload.pop("max_tokens")
		payload["max_completion_tokens"] = max_tokens
		data = post_json(
			url,
			headers={
				"authorization": f"Bearer {settings.api_key}",
				"content-type": "application/json",
			},
			payload=payload,
		)

	choices = data.get("choices") or []
	text = (choices[0].get("message", {}).get("content") if choices else "") or ""
	if not text.strip():
		raise AIResponseError(_("The AI provider returned an empty answer."))

	return text.strip()


def post_json(url: str, headers: dict, payload: dict) -> dict:
	"""POST one JSON body. Logs status and a truncated body, never the API key.

	Providers return transient 429/5xx codes under load. We retry those a few
	times with a short backoff, so a momentary spike does not fail the request.
	"""
	response = None
	for attempt in range(TRANSIENT_RETRIES + 1):
		try:
			response = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
		except requests.RequestException as error:
			if attempt < TRANSIENT_RETRIES:
				time.sleep(TRANSIENT_BACKOFF * (attempt + 1))
				continue
			frappe.log_error(f"{url}: {type(error).__name__}", "CRM AI: request failed")
			raise AIResponseError(_("Could not reach the AI provider.")) from error

		if response.status_code in TRANSIENT_STATUSES and attempt < TRANSIENT_RETRIES:
			time.sleep(TRANSIENT_BACKOFF * (attempt + 1))
			continue
		break

	if response.status_code >= 400:
		body = frappe.utils.cstr(response.text)[:ERROR_BODY_LIMIT]
		frappe.log_error(f"{url}\nHTTP {response.status_code}\n{body}", "CRM AI: provider error")
		raise AIResponseError(
			_("The AI provider answered with HTTP {0}: {1}").format(response.status_code, body)
		)

	try:
		return response.json()
	except ValueError as error:
		body = frappe.utils.cstr(response.text)[:ERROR_BODY_LIMIT]
		frappe.log_error(f"{url}\n{body}", "CRM AI: unreadable response")
		raise AIResponseError(_("The AI provider answered with a body that is not JSON.")) from error


def parse_json(text: str) -> dict:
	"""Parse a model answer that should hold exactly one JSON object."""
	candidate = text.strip()
	fenced = CODE_FENCE.match(candidate)
	if fenced:
		candidate = fenced.group(1).strip()

	try:
		parsed = json.loads(candidate)
	except ValueError as error:
		raise AIResponseError(_("the answer is not valid JSON")) from error

	if not isinstance(parsed, dict):
		raise AIResponseError(_("the answer is not a JSON object"))

	return parsed
