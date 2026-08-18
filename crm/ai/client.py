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
"""

import json
import re
import time

import frappe
import requests
from frappe import _

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
) -> str | dict:
	"""Send one prompt to the configured provider and return its answer.

	Args:
	        prompt: the user message.
	        system: optional system prompt.
	        max_tokens: ceiling on the generated answer.
	        json_schema: when given, the model is told to answer with one JSON
	                object matching the schema and the parsed object is returned.

	Returns:
	        The answer text, or the parsed object when `json_schema` is given.

	Raises:
	        AIConfigurationError: the provider is disabled, unconfigured or over
	                its monthly request budget.
	        AIResponseError: the provider answered with something unusable, or
	                with invalid JSON twice in a row.
	"""
	settings = load_settings()
	month, used = check_quota(settings)

	if json_schema:
		system = join_system(system, JSON_INSTRUCTION.format(schema=json.dumps(json_schema)))

	text = dispatch(settings, prompt, system, max_tokens)
	record_usage(month, used)

	if not json_schema:
		return text

	try:
		return parse_json(text)
	except AIResponseError as first_error:
		# One retry: the model is told what it got wrong. A second failure is a
		# real fault, and the caller falls back to deterministic values.
		retry_prompt = (
			f"{prompt}\n\n"
			f"Your previous answer was not valid JSON ({first_error}). "
			"Answer again with one JSON object and nothing else."
		)
		month, used = check_quota(settings)
		text = dispatch(settings, retry_prompt, system, max_tokens)
		record_usage(month, used)
		return parse_json(text)


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
	"""Return (month marker, requests already spent this month), or raise."""
	month = current_month()
	used = settings.requests_this_month if settings.usage_month == month else 0

	limit = settings.max_monthly_requests
	if limit and used >= limit:
		raise AIConfigurationError(_("The monthly AI request limit of {0} is reached.").format(limit))

	return month, used


def record_usage(month: str, used: int):
	"""Count one request. A counter write must never sink the caller's work."""
	try:
		frappe.db.set_single_value(SETTINGS_DOCTYPE, {"usage_month": month, "requests_this_month": used + 1})
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
