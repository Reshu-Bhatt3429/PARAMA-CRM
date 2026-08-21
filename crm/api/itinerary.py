# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

"""AI-assisted travel itineraries: draft, edit, print, send.

The agent opens a lead, presses "Generate itinerary" and gets a day-by-day plan
that is structured data from the first token onwards. Every surface -- the Vue
editor, the A4 PDF, the WhatsApp summary -- renders from the same `days_json`
described in `crm.fcrm.doctype.crm_itinerary.crm_itinerary`. Nothing here ever
parses model prose.

Why the generation is split in two
----------------------------------
`generate_skeleton` writes the shape of the trip: a title and a one-line summary
per day, nothing else. `generate_day` then fills one day at a time. The frontend
drives that loop and shows "Day 3 of 7", so the agent watches progress instead of
staring at a spinner for forty seconds. It also means a single failed day is a
single retry, not a lost itinerary.

Why nothing is ever verified
----------------------------
A language model produces plausible place names, not checked ones. Every item
the AI writes is stored with `verified: false` and the editor badges it. Only the
agent, in the editor, can flip that flag. No code path in this module sets it to
true, and there is no external Places lookup in this version.

The AI is optional
------------------
Every endpoint that calls the model raises `AIConfigurationError` with a message
that names the settings screen when the agency has not configured a provider.
Creating, editing, printing and sending an itinerary all work with the AI off.
"""

import re

import frappe
from frappe import _
from frappe.permissions import add_permission, update_permission_property
from frappe.utils import cint, flt

from crm import document_links
from crm.ai.client import AIConfigurationError, AIResponseError, complete, is_configured
from crm.fcrm.doctype.crm_itinerary.crm_itinerary import (
	MAX_DAYS,
	MIN_DAYS,
	TIMES_OF_DAY,
	ItinerarySchemaError,
	clamp_days,
	dump_days,
	duration_label,
	empty_day,
	parse_days,
	validate_days,
)

DOCTYPE = "CRM Itinerary"
LEAD_DOCTYPE = "CRM Lead"
PRINT_FORMAT = "Travel Itinerary A4"

# The skeleton is one line per day, so its budget scales with the trip length.
# A day's detail is a handful of items and costs far more room.
SKELETON_TOKENS_PER_DAY = 120
SKELETON_TOKENS_MIN = 800
DAY_TOKENS = 2000
IMPORT_TOKENS = 6000
MAX_IMPORT_LENGTH = 40_000

# How many neighbouring days the model is shown so it does not repeat itself.
DEDUP_NEIGHBOURS = 2

# How long the temporary public PDF stays reachable after a WhatsApp send.
# Meta fetches the media while it processes the message, not before the POST
# returns, so the file has to outlive the request. Two hours is far longer than
# the fetch needs and short enough that the link is not a lasting exposure.
#
# The PDF machinery below (render, name, attach, sweep) now lives in
# `crm.document_links`, which item 25 (quote PDF) shares. The names kept here are
# the itinerary's own vocabulary and its behaviour is unchanged; only the
# implementation moved.
PUBLIC_PDF_TTL_HOURS = document_links.PUBLIC_PDF_TTL_HOURS

# The random suffix `pdf_file_name` gives a send copy. The sweep deletes nothing
# that does not carry it, so a public file attached to an itinerary by hand is
# never touched.
PUBLIC_PDF_TOKEN_LENGTH = document_links.PUBLIC_TOKEN_LENGTH
SEND_COPY_PATTERN = document_links.SEND_COPY_PATTERN

SKELETON_SYSTEM = (
	"You are a senior itinerary designer at a travel agency. You plan the arc of a "
	"trip: arrival, the build-up, the highlight, the wind-down, departure. You write "
	"for a customer who has never been to the destination. Be concrete and realistic "
	"about travel time between places. Never invent prices, bookings or links."
)

DAY_SYSTEM = (
	"You are a senior itinerary designer at a travel agency. You are filling in one "
	"single day of an itinerary that is already planned. Keep the pacing humane: two "
	"to four items in a slot at most, and fewer on an arrival or departure day. Prefer "
	"real, well-known places at the destination. Never repeat an activity that another "
	"day already covers. Never write a URL, a phone number or a booking reference."
)

IMPORT_SYSTEM = (
	"You turn pasted travel proposals into structured itinerary data. Preserve factual "
	"details from the source, but never invent bookings, prices, links, contacts or places. "
	"Keep day descriptions customer-ready and return only the requested JSON."
)

NUMBER_WORDS = {
	"one": 1,
	"two": 2,
	"three": 3,
	"four": 4,
	"five": 5,
	"six": 6,
	"seven": 7,
	"eight": 8,
	"nine": 9,
	"ten": 10,
	"eleven": 11,
	"twelve": 12,
	"first": 1,
	"second": 2,
	"third": 3,
	"fourth": 4,
	"fifth": 5,
	"sixth": 6,
	"seventh": 7,
	"eighth": 8,
	"ninth": 9,
	"tenth": 10,
	"eleventh": 11,
	"twelfth": 12,
}

DAY_HEADER_PATTERN = re.compile(
	r"^(?:(?:day\s*[-:#]?\s*(?P<day>\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve))|"
	r"(?:(?P<ordinal>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth)\s+day)|"
	r"(?P<numeric>\d+)[.)])\s*[:—-]?\s*(?P<title>.*)$",
	re.IGNORECASE,
)

SKELETON_SCHEMA = {
	"type": "object",
	"properties": {
		"days": {
			"type": "array",
			"items": {
				"type": "object",
				"properties": {
					"day_number": {"type": "integer"},
					"title": {"type": "string"},
					"summary": {"type": "string"},
				},
				"required": ["day_number", "title", "summary"],
			},
		}
	},
	"required": ["days"],
}

DAY_ITEM_SCHEMA = {
	"type": "object",
	"properties": {
		"title": {"type": "string"},
		"description": {"type": "string"},
		"place_name": {"type": ["string", "null"]},
		"duration_hours": {"type": ["number", "null"]},
		"est_cost": {"type": ["number", "null"]},
	},
	"required": ["title", "description"],
}

DAY_SCHEMA = {
	"type": "object",
	"properties": {
		"title": {"type": "string"},
		"summary": {"type": "string"},
		"highlights": {"type": "array", "items": {"type": "string"}},
		"description": {"type": "string"},
		"accommodation": {"type": "string"},
		"meals": {
			"type": "object",
			"properties": {
				"breakfast": {"type": "boolean"},
				"lunch": {"type": "boolean"},
				"dinner": {"type": "boolean"},
			},
			"required": ["breakfast", "lunch", "dinner"],
		},
		"slots": {
			"type": "array",
			"items": {
				"type": "object",
				"properties": {
					"time_of_day": {"type": "string", "enum": list(TIMES_OF_DAY)},
					"items": {"type": "array", "items": DAY_ITEM_SCHEMA},
				},
				"required": ["time_of_day", "items"],
			},
		},
	},
	"required": ["slots", "highlights", "description", "accommodation", "meals"],
}

IMPORT_DAY_SCHEMA = {
	"type": "object",
	"properties": {
		"day_number": {"type": "integer"},
		"title": {"type": "string"},
		"summary": {"type": "string"},
		"highlights": {"type": "array", "items": {"type": "string"}},
		"description": {"type": "string"},
		"accommodation": {"type": "string"},
		"meals": {
			"type": "object",
			"properties": {
				"breakfast": {"type": "boolean"},
				"lunch": {"type": "boolean"},
				"dinner": {"type": "boolean"},
			},
			"required": ["breakfast", "lunch", "dinner"],
		},
	},
	"required": ["day_number", "title", "summary", "highlights", "description", "accommodation", "meals"],
}

IMPORT_SCHEMA = {
	"type": "object",
	"properties": {
		"title": {"type": "string"},
		"subtitle": {"type": "string"},
		"destination": {"type": "string"},
		"duration_label": {"type": "string"},
		"days": {"type": "array", "items": IMPORT_DAY_SCHEMA},
	},
	"required": ["title", "subtitle", "destination", "duration_label", "days"],
}

# What the editor may write through `update_details`. `status`, `version`,
# `lead` and `days_json` are missing on purpose -- each has its own endpoint.
EDITABLE_FIELDS = (
	"title",
	"subtitle",
	"customer_name",
	"deal",
	"destination",
	"start_date",
	"num_days",
	"duration_label",
	"departure_type",
	"group_size",
	"group_size_label",
	"budget",
	"currency",
	"cover_image",
	"brand_logo",
	"theme",
	"font_preset",
	"title_weight",
	"title_style",
	"tagline_style",
	"title_case",
	"contact_email",
	"contact_phone",
	"contact_website",
	"trip_vibe",
	"ai_instructions",
	"inclusions",
	"exclusions",
	"terms",
	"internal_notes",
)

# The model is told not to write links. This strips the ones it writes anyway,
# because a hallucinated booking URL in a customer-facing PDF is a real problem.
URL_PATTERN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


# --- access ----------------------------------------------------------------


def get_itinerary(itinerary: str, permtype: str = "read"):
	"""Load an itinerary the session user may act on, or refuse.

	Both the itinerary and the lead behind it are checked, at the SAME
	permission level. An agent who may only read a lead must not be able to
	rewrite that customer's trip, so `permtype` is passed through rather than
	being downgraded to "read".
	"""
	if not itinerary:
		frappe.throw(_("No itinerary was given."), frappe.PermissionError)

	# A missing document and a forbidden one fail the same way, so a sales user
	# cannot probe which itinerary ids exist.
	refused = _("Not permitted to access itinerary {0}.").format(itinerary)
	if not frappe.db.exists(DOCTYPE, itinerary):
		frappe.throw(refused, frappe.PermissionError)

	doc = frappe.get_doc(DOCTYPE, itinerary)
	if not doc.has_permission(permtype):
		frappe.throw(refused, frappe.PermissionError)

	require_lead_permission(doc.lead, permtype)
	return doc


def require_lead_permission(lead: str, permtype: str = "read"):
	if not lead:
		frappe.throw(_("The itinerary has no lead."), frappe.ValidationError)

	refused = _("Not permitted to access lead {0}.").format(lead)
	if not frappe.db.exists(LEAD_DOCTYPE, lead):
		frappe.throw(refused, frappe.PermissionError)

	if not frappe.has_permission(LEAD_DOCTYPE, permtype, doc=lead):
		frappe.throw(refused, frappe.PermissionError)


# --- row-level permissions -------------------------------------------------
#
# The custom endpoints above are not the only way into this doctype. Frappe's
# generic API -- `frappe.client.get_list`, `get`, `set_value` -- reaches every
# doctype a role can read, and the itinerary carries the customer's name, their
# budget, the quoted prices and the agency's internal notes. Without the two
# hooks below, a sales user could list every other agent's itineraries.
#
# Both delegate to the lead's own rule, so an itinerary is exactly as visible as
# the customer it belongs to. Registered in `hooks.py` under
# `permission_query_conditions` and `has_permission`.


def get_itinerary_permission_query_conditions(user=None):
	"""Restrict itinerary rows to the leads the user may already see."""
	from crm.permissions.org_hierarchy import get_lead_permission_query_conditions

	lead_conditions = get_lead_permission_query_conditions(user)
	if not lead_conditions:
		return ""

	return (
		f"`tab{DOCTYPE}`.`lead` in "
		f"(select `tab{LEAD_DOCTYPE}`.`name` from `tab{LEAD_DOCTYPE}` where {lead_conditions})"
	)


def has_itinerary_permission(doc, ptype, user=None):
	from crm.permissions.org_hierarchy import has_lead_permission

	if ptype == "create" or not doc.get("lead"):
		return True

	return has_lead_permission(frappe._dict({"name": doc.get("lead")}), ptype, user)


# --- creation --------------------------------------------------------------


@frappe.whitelist()
def get_draft_for_lead(lead: str):
	"""The newest Draft itinerary on this lead, or None.

	The Lead page asks before it creates. Pressing the button twice should offer
	the draft the agent already started, not leave two half-written itineraries
	behind for the same trip.
	"""
	require_lead_permission(lead, "read")

	drafts = frappe.get_all(
		DOCTYPE,
		filters={"lead": lead, "status": "Draft"},
		fields=["name", "title", "modified"],
		order_by="modified desc",
		limit=1,
	)
	return drafts[0] if drafts else None


@frappe.whitelist(methods=["POST"])
def create_from_lead(lead: str):
	"""A blank itinerary prefilled from the lead's travel fields. No AI call."""
	# An itinerary is written on behalf of this customer, so it needs write
	# access to the lead, not merely the right to look at it.
	require_lead_permission(lead, "write")
	if not frappe.has_permission(DOCTYPE, "create"):
		frappe.throw(_("Not permitted to create an itinerary."), frappe.PermissionError)

	lead_doc = frappe.get_doc(LEAD_DOCTYPE, lead)
	destination = (lead_doc.get("destination") or "").strip()
	customer = (lead_doc.get("lead_name") or lead_doc.get("organization") or "").strip()
	num_days = days_between(lead_doc.get("travel_start_date"), lead_doc.get("travel_end_date"))
	group_size = cint(lead_doc.get("group_size"))

	doc = frappe.new_doc(DOCTYPE)
	agency = doc.render_agency()
	doc.update(
		{
			"lead": lead,
			"title": build_title(destination, customer),
			"customer_name": customer,
			"destination": destination,
			"start_date": lead_doc.get("travel_start_date"),
			"num_days": num_days,
			"duration_label": duration_label(num_days),
			"departure_type": "Group Departure",
			"group_size": group_size,
			"group_size_label": _("{0} travellers").format(group_size) if group_size else "",
			"budget": flt(lead_doc.get("budget")),
			"currency": frappe.db.get_default("currency") or "INR",
			"contact_email": agency.get("email"),
			"contact_phone": agency.get("phone"),
			"contact_website": agency.get("website"),
			"status": "Draft",
			"version": 1,
			"days_json": dump_days([]),
		}
	)
	doc.insert()
	return doc


def build_title(destination: str, customer: str) -> str:
	if destination and customer:
		return f"{destination} — {customer}"
	return destination or customer or _("Untitled Itinerary")


def days_between(start, end) -> int:
	"""Inclusive day count between the two travel dates, clamped to the bounds."""
	if not start or not end:
		return MIN_DAYS
	return clamp_days(frappe.utils.date_diff(end, start) + 1)


# --- generation ------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def generate_skeleton(itinerary: str):
	"""Ask the model for one title and summary per day, and merge them in.

	Merge, not replace: a day that already holds items keeps them. Only the
	title and the summary are overwritten, so re-running the skeleton on a
	half-finished itinerary cannot destroy the agent's work.
	"""
	doc = get_itinerary(itinerary, "write")
	num_days = clamp_days(doc.num_days)

	answer = ask_model(
		prompt=skeleton_prompt(doc, num_days),
		system=SKELETON_SYSTEM,
		max_tokens=max(SKELETON_TOKENS_MIN, SKELETON_TOKENS_PER_DAY * num_days),
		schema=SKELETON_SCHEMA,
	)

	drafted = {}
	for day in answer.get("days") or []:
		if not isinstance(day, dict):
			continue
		number = cint(day.get("day_number"))
		if MIN_DAYS <= number <= num_days:
			drafted[number] = day

	existing = {day["day_number"]: day for day in parse_days(doc.days_json)}

	days = []
	for number in range(1, num_days + 1):
		draft = drafted.get(number) or {}
		day = existing.get(number) or empty_day(number)
		day["title"] = clean_ai_text(draft.get("title")) or day.get("title") or ""
		day["summary"] = clean_ai_text(draft.get("summary")) or day.get("summary") or ""
		days.append(day)

	doc.num_days = num_days
	doc.days_json = dump_days(validate_days(days))
	doc.save()
	return {"name": doc.name, "days": doc.render_days(), "num_days": doc.num_days}


@frappe.whitelist(methods=["POST"])
def generate_day(itinerary: str, day_number: int):
	"""Fill in the slots of exactly one day. Every other day is left untouched."""
	doc = get_itinerary(itinerary, "write")
	day_number = cint(day_number)

	days = parse_days(doc.days_json)
	index = next((i for i, day in enumerate(days) if day["day_number"] == day_number), None)
	if index is None:
		if not MIN_DAYS <= day_number <= clamp_days(doc.num_days):
			frappe.throw(_("Day {0} is not part of this itinerary.").format(day_number))
		days.append(empty_day(day_number))
		days.sort(key=lambda day: day["day_number"])
		index = next(i for i, day in enumerate(days) if day["day_number"] == day_number)

	answer = ask_model(
		prompt=day_prompt(doc, days, day_number),
		system=DAY_SYSTEM,
		max_tokens=DAY_TOKENS,
		schema=DAY_SCHEMA,
	)

	day = days[index]
	day["title"] = day.get("title") or clean_ai_text(answer.get("title"))
	day["summary"] = day.get("summary") or clean_ai_text(answer.get("summary"))
	day["highlights"] = clean_ai_list(answer.get("highlights"))
	day["description"] = clean_ai_text(answer.get("description"))
	day["accommodation"] = clean_ai_text(answer.get("accommodation"))
	day["meals"] = clean_ai_meals(answer.get("meals"))
	day["slots"] = build_slots(answer.get("slots"))

	doc.days_json = dump_days(validate_days(days))
	doc.save()
	return {"name": doc.name, "day": doc.render_days()[index]}


def build_slots(slots) -> list[dict]:
	"""Turn the model's slot list into schema-clean, unverified slots."""
	by_part = {part: [] for part in TIMES_OF_DAY}

	for slot in slots or []:
		if not isinstance(slot, dict):
			continue
		part = slot.get("time_of_day")
		if part not in by_part:
			continue
		for item in slot.get("items") or []:
			if not isinstance(item, dict):
				continue
			title = clean_ai_text(item.get("title"))
			if not title:
				continue
			by_part[part].append(
				{
					"title": title,
					"description": clean_ai_text(item.get("description")),
					"place_name": clean_ai_text(item.get("place_name")) or None,
					"duration_hours": optional_number(item.get("duration_hours")),
					"est_cost": optional_number(item.get("est_cost")),
					# Not negotiable: the model does not get to declare a place
					# real. Only the agent, from the editor, can.
					"verified": False,
				}
			)

	return [{"time_of_day": part, "items": by_part[part]} for part in TIMES_OF_DAY]


def optional_number(value):
	if value in (None, ""):
		return None
	try:
		number = flt(value)
	except (TypeError, ValueError):
		return None
	return number if number >= 0 else None


def clean_ai_text(value) -> str:
	if not isinstance(value, str):
		return ""
	return URL_PATTERN.sub("", value).strip()


def clean_ai_list(values, limit: int = 8) -> list[str]:
	if isinstance(values, str):
		values = values.split(",")
	if not isinstance(values, list):
		return []
	return [text for value in values[:limit] if (text := clean_ai_text(value))]


def clean_ai_meals(value) -> dict[str, bool]:
	value = value if isinstance(value, dict) else {}
	return {meal: value.get(meal) is True for meal in ("breakfast", "lunch", "dinner")}


def ask_model(prompt: str, system: str, max_tokens: int, schema: dict) -> dict:
	"""One `complete()` call, with the two AI failures turned into clear errors."""
	try:
		answer = complete(prompt, system=system, max_tokens=max_tokens, json_schema=schema)
	except AIConfigurationError:
		# The message already names what to fix ("AI is turned off", "No AI API
		# key is configured"). Re-raising keeps it, and the editor points the
		# agent at Settings -> AI & Follow-ups.
		raise
	except AIResponseError as error:
		frappe.throw(
			_("The AI could not draft this itinerary: {0}").format(
				frappe.utils.escape_html(str(error))
			),
			title=_("AI Draft Failed"),
		)

	if not isinstance(answer, dict):
		frappe.throw(_("The AI answered with something that is not an itinerary."))

	return answer


# --- prompts ---------------------------------------------------------------


def trip_context(doc) -> str:
	lines = [f"Destination: {doc.destination or 'not given'}"]

	if doc.start_date:
		lines.append(f"Start date: {frappe.utils.formatdate(doc.start_date, 'd MMMM yyyy')}")
		# The month drives the weather, the crowds and what is even open.
		lines.append(f"Season: {frappe.utils.formatdate(doc.start_date, 'MMMM')}")
	else:
		lines.append("Start date: not given")

	lines.append(f"Length: {clamp_days(doc.num_days)} days")
	lines.append(f"Group size: {doc.group_size_label or cint(doc.group_size) or 'not given'}")
	lines.append(f"Departure type: {doc.departure_type or 'not given'}")
	lines.append(f"Trip style: {doc.trip_vibe or 'not given'}")
	if doc.ai_instructions:
		lines.append(f"Agency instructions: {clean_ai_text(doc.ai_instructions)}")

	if flt(doc.budget):
		lines.append(f"Total budget: {doc.render_amount(doc.budget)}")
	else:
		lines.append("Total budget: not given")

	lines.append(f"Currency for every cost you write: {doc.currency or 'INR'}")
	return "\n".join(lines)


def skeleton_prompt(doc, num_days: int) -> str:
	return (
		"Plan the shape of this trip.\n\n"
		f"{trip_context(doc)}\n\n"
		f"Return exactly {num_days} days, numbered 1 to {num_days}. For each day give a "
		"short title of at most eight words and a one-sentence summary of what the day "
		"is about. Do not list activities, times or costs yet. Make the days flow: "
		"arrival first, the heaviest travel early, the highlight in the middle, a light "
		"final day for departure. No two days may have the same focus."
	)


def day_prompt(doc, days: list[dict], day_number: int) -> str:
	day = next(entry for entry in days if entry["day_number"] == day_number)

	outline = "\n".join(
		f"Day {entry['day_number']}: {entry['title'] or 'untitled'}"
		+ (f" — {entry['summary']}" if entry["summary"] else "")
		for entry in days
	)

	parts = [
		f"Fill in day {day_number} of this itinerary in full detail.",
		"",
		trip_context(doc),
		"",
		"The whole itinerary, for context:",
		outline,
		"",
		f"Day {day_number} is: {day['title'] or 'untitled'}",
	]

	if day["summary"]:
		parts.append(f"Its summary: {day['summary']}")

	already = neighbouring_titles(days, day_number)
	if already:
		parts += [
			"",
			"These activities are already booked on the neighbouring days. Do not repeat any of them:",
			"\n".join(f"- {title}" for title in already),
		]

	parts += [
		"",
		"Return three to six short highlights, one customer-facing paragraph describing "
		"the day, the accommodation or an empty string, and explicit breakfast, lunch, "
		"and dinner booleans. Also return the morning, afternoon and evening slots. Two to four "
		"items per slot at most. For each item give a short title, a description of one "
		"or two sentences a customer can read, the name of a real well-known place at "
		f"the destination or null, the duration in hours or null, and the estimated cost "
		f"per person in {doc.currency or 'INR'} as a plain number or null. Write no URLs "
		"and no phone numbers.",
	]

	return "\n".join(parts)


def neighbouring_titles(days: list[dict], day_number: int) -> list[str]:
	"""Item titles from the days around this one, so the model does not repeat them."""
	titles = []
	for entry in days:
		distance = abs(entry["day_number"] - day_number)
		if distance == 0 or distance > DEDUP_NEIGHBOURS:
			continue
		for slot in entry["slots"]:
			for item in slot["items"]:
				titles.append(item["title"])
	return titles


# --- paste importer --------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def import_pasted_itinerary(itinerary: str, text: str, prefer_ai: int = 1):
	"""Replace the day plan with structured data parsed from pasted prose.

	A configured server-side provider gets the first attempt. If it is missing or
	returns an unusable answer, the deterministic parser still handles common
	"Day 1", bullet, accommodation and meal formats. API keys never enter the
	browser or the itinerary document.
	"""
	doc = get_itinerary(itinerary, "write")
	raw_text = frappe.utils.cstr(text).strip()
	if not raw_text:
		frappe.throw(_("Paste an itinerary before importing it."))
	if len(raw_text) > MAX_IMPORT_LENGTH:
		frappe.throw(
			_("The pasted itinerary is too long. Keep it below {0} characters.").format(MAX_IMPORT_LENGTH)
		)

	parsed = None
	method = "local"
	if cint(prefer_ai) and is_configured():
		parsed = parse_import_with_ai(raw_text)
		if parsed:
			method = "ai"

	parsed = parsed or parse_pasted_itinerary(raw_text)
	days = imported_days(parsed.get("days"))
	if not days:
		frappe.throw(_("No itinerary days could be found in the pasted text."))

	for source, field in (
		("title", "title"),
		("subtitle", "subtitle"),
		("destination", "destination"),
		("duration_label", "duration_label"),
	):
		value = clean_ai_text(parsed.get(source))
		if value:
			doc.set(field, value)

	doc.num_days = len(days)
	if not doc.duration_label:
		doc.duration_label = duration_label(len(days))
	doc.days_json = dump_days(validate_days(days))
	doc.save()

	payload = itinerary_payload(doc)
	payload["import_method"] = method
	return payload


def parse_import_with_ai(raw_text: str) -> dict | None:
	prompt = (
		"Extract the itinerary below. Keep every day in source order. Highlights must be "
		"short phrases. Summary is one sentence; description keeps the useful operational "
		"detail. Use an empty string for a missing accommodation and false for an unmentioned meal.\n\n"
		f"PASTED ITINERARY\n{raw_text}"
	)
	try:
		answer = complete(
			prompt,
			system=IMPORT_SYSTEM,
			max_tokens=IMPORT_TOKENS,
			json_schema=IMPORT_SCHEMA,
		)
	except (AIConfigurationError, AIResponseError):
		return None
	return (
		answer
		if isinstance(answer, dict) and isinstance(answer.get("days"), list) and answer["days"]
		else None
	)


def parse_pasted_itinerary(raw_text: str) -> dict:
	"""Parse the common itinerary format without a model.

	This intentionally favours preserving prose over guessing. It recognises day
	headings, labelled highlights/accommodation/duration/destination and meal words;
	anything else remains in the day's description for the agent to edit.
	"""
	lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
	if not lines:
		return {"days": []}

	preamble = []
	blocks = []
	current = None
	for line in lines:
		match = DAY_HEADER_PATTERN.match(line)
		if match:
			if current:
				blocks.append(current)
			current = {"title": (match.group("title") or "").strip(), "lines": []}
		elif current:
			current["lines"].append(line)
		else:
			preamble.append(line)
	if current:
		blocks.append(current)

	# A proposal without day headings is still importable as a one-day draft.
	if not blocks:
		blocks = [{"title": "", "lines": lines}]
		preamble = []

	days = []
	for index, block in enumerate(blocks, 1):
		content = block["lines"]
		title = block["title"] or first_content_line(content) or _("Day {0}").format(index)
		highlights = extract_highlights(content)
		accommodation = extract_label(content, ("accommodation", "overnight", "stay"))
		description_lines = [
			line
			for line in content
			if not is_labeled_line(line, ("highlights", "accommodation", "overnight", "stay", "meals"))
		]
		description = " ".join(strip_bullet(line) for line in description_lines).strip()
		lower = " ".join(content).lower()
		days.append(
			{
				"day_number": index,
				"title": title,
				"summary": first_sentence(description),
				"highlights": highlights,
				"description": description,
				"accommodation": accommodation,
				"meals": {
					"breakfast": bool(re.search(r"\b(?:breakfast|b'fast|morning tea)\b", lower)),
					"lunch": bool(re.search(r"\b(?:lunch|packed lunch|afternoon meal)\b", lower)),
					"dinner": bool(re.search(r"\b(?:dinner|supper|evening meal)\b", lower)),
				},
			}
		)

	return {
		"title": extract_label(preamble, ("title", "trip", "expedition")) or first_content_line(preamble),
		"subtitle": extract_label(preamble, ("subtitle", "tagline")),
		"destination": extract_label(preamble, ("destination", "location")),
		"duration_label": extract_label(preamble, ("duration",)),
		"days": days,
	}


def imported_days(values) -> list[dict]:
	days = []
	for index, value in enumerate(values or [], 1):
		if not isinstance(value, dict) or index > MAX_DAYS:
			continue
		day = empty_day(index)
		day.update(
			{
				"title": clean_ai_text(value.get("title")) or _("Day {0}").format(index),
				"summary": clean_ai_text(value.get("summary")),
				"highlights": clean_ai_list(value.get("highlights")),
				"description": clean_ai_text(value.get("description")),
				"accommodation": clean_ai_text(value.get("accommodation")),
				"meals": clean_ai_meals(value.get("meals")),
			}
		)
		days.append(day)
	return days


def extract_label(lines: list[str], labels: tuple[str, ...]) -> str:
	pattern = re.compile(rf"^(?:{'|'.join(re.escape(label) for label in labels)})\s*[:\-]\s*(.+)$", re.I)
	for line in lines:
		match = pattern.match(strip_bullet(line))
		if match:
			return match.group(1).strip()
	return ""


def is_labeled_line(line: str, labels: tuple[str, ...]) -> bool:
	return bool(
		re.match(rf"^(?:{'|'.join(re.escape(label) for label in labels)})\s*[:\-]", strip_bullet(line), re.I)
	)


def extract_highlights(lines: list[str]) -> list[str]:
	labelled = extract_label(lines, ("highlights", "high points", "key experiences"))
	if labelled:
		return [part.strip() for part in labelled.split(",") if part.strip()][:8]
	return [strip_bullet(line) for line in lines if re.match(r"^[•*\-]", line)][:8]


def strip_bullet(value: str) -> str:
	return re.sub(r"^[•*\-]\s*", "", value).strip()


def first_content_line(lines: list[str]) -> str:
	for line in lines:
		value = strip_bullet(line)
		if value and not re.match(r"^[A-Za-z ]+\s*[:\-]", value):
			return value
	return ""


def first_sentence(value: str) -> str:
	if not value:
		return ""
	match = re.match(r"^(.+?[.!?])(?:\s|$)", value)
	return (match.group(1) if match else value)[:240]


# --- editing ---------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def update_days(itinerary: str, days_json: dict | list | str):
	"""The editor's save path. A payload that breaks the schema is refused."""
	doc = get_itinerary(itinerary, "write")

	try:
		days = parse_days(days_json)
	except ItinerarySchemaError as error:
		frappe.throw(str(error), ItinerarySchemaError, title=_("Invalid Itinerary"))

	doc.days_json = dump_days(days)
	doc.save()
	return {"name": doc.name, "days": doc.render_days(), "status": doc.status, "version": doc.version}


@frappe.whitelist(methods=["POST"])
def update_details(itinerary: str, values: dict | str):
	"""Save the itinerary's own fields: the trip facts, the prices, the terms.

	The editor could have used `frappe.client.set_value`, but that checks the
	doctype's permissions only. An itinerary is a customer's trip, so the write
	has to go through the same lead check every other endpoint here uses.

	`status`, `version`, `lead` and `days_json` are deliberately not writable:
	each has its own path, and letting the browser set them would let an agent
	mark an itinerary Sent without ever sending it.
	"""
	doc = get_itinerary(itinerary, "write")

	if isinstance(values, str):
		try:
			values = frappe.parse_json(values)
		except ValueError:
			frappe.throw(_("The itinerary details are not valid JSON."))

	if not isinstance(values, dict):
		frappe.throw(_("The itinerary details must be an object."))

	unknown = sorted(set(values) - set(EDITABLE_FIELDS) - {"price_tiers"})
	if unknown:
		frappe.throw(_("These fields cannot be edited here: {0}.").format(", ".join(unknown)))

	previous_num_days = doc.num_days
	previous_duration = doc.duration_label
	previous_group_size = doc.group_size
	previous_group_label = doc.group_size_label

	for field in EDITABLE_FIELDS:
		if field in values:
			doc.set(field, coerce_field(field, values[field]))

	if "num_days" in values and "duration_label" not in values:
		if not previous_duration or previous_duration == duration_label(previous_num_days):
			doc.duration_label = duration_label(doc.num_days)
	if "group_size" in values and "group_size_label" not in values:
		old_default = (
			_("{0} travellers").format(cint(previous_group_size)) if cint(previous_group_size) else ""
		)
		if not previous_group_label or previous_group_label == old_default:
			doc.group_size_label = (
				_("{0} travellers").format(cint(doc.group_size)) if cint(doc.group_size) else ""
			)

	# A shorter trip drops the days past the new end. The editor asks the agent
	# to confirm before it sends a smaller number, because those days may hold
	# work the AI and the agent did together. Doing it here, at the moment the
	# count changes, is what stops a later `generate_skeleton` from discarding
	# them with no warning at all.
	if "num_days" in values:
		days = parse_days(doc.days_json)
		if len(days) > doc.num_days:
			doc.days_json = dump_days([day for day in days if day["day_number"] <= doc.num_days])

	if "price_tiers" in values:
		doc.set("price_tiers", [])
		for row in values["price_tiers"] or []:
			if not isinstance(row, dict) or not frappe.utils.cstr(row.get("tier_label")).strip():
				continue
			doc.append(
				"price_tiers",
				{
					"tier_label": frappe.utils.cstr(row.get("tier_label")).strip()[:140],
					"price_per_person": flt(row.get("price_per_person")),
				},
			)

	doc.save()
	return itinerary_payload(doc)


def coerce_field(field: str, value):
	"""Turn a browser value into the type the field actually stores.

	`BaseDocument.set` stores whatever it is handed, so a date arriving as the
	string "2026-11-02" would sit next to the `datetime.date` the database
	returns. The two never compare equal, every save would look like a change,
	and a Sent itinerary would flip to Revised on a no-op save and inflate its
	version. Coercing here makes an unchanged value compare equal.
	"""
	fieldtype = frappe.get_meta(DOCTYPE).get_field(field).fieldtype

	if value in (None, ""):
		# An emptied Date must clear rather than become today.
		return None if fieldtype in ("Date", "Datetime") else value

	if fieldtype == "Date":
		return frappe.utils.getdate(value)
	if fieldtype == "Datetime":
		return frappe.utils.get_datetime(value)
	if fieldtype == "Int":
		return cint(value)
	if fieldtype in ("Currency", "Float", "Percent"):
		return flt(value)

	return frappe.utils.cstr(value)


def itinerary_payload(doc) -> dict:
	"""Everything the editor needs to redraw itself after a write."""
	return {
		"name": doc.name,
		"title": doc.title,
		"subtitle": doc.subtitle,
		"customer_name": doc.customer_name,
		"lead": doc.lead,
		"deal": doc.deal,
		"destination": doc.destination,
		"start_date": doc.start_date,
		"num_days": doc.num_days,
		"duration_label": doc.render_duration(),
		"departure_type": doc.departure_type or "Group Departure",
		"group_size": doc.group_size,
		"group_size_label": doc.render_group_size(),
		"budget": doc.budget,
		"currency": doc.currency,
		"cover_image": doc.cover_image,
		"brand_logo": doc.brand_logo,
		"theme": doc.render_theme(),
		"font_preset": doc.font_preset or "Modern Alpine",
		"title_weight": doc.title_weight or "900",
		"title_style": doc.title_style or "Normal",
		"tagline_style": doc.tagline_style or "Bold Normal",
		"title_case": doc.title_case or "Uppercase",
		"contact_email": doc.contact_email,
		"contact_phone": doc.contact_phone,
		"contact_website": doc.contact_website,
		"trip_vibe": doc.trip_vibe or "Adventure",
		"ai_instructions": doc.ai_instructions,
		"status": doc.status,
		"version": doc.version,
		"inclusions": doc.inclusions,
		"exclusions": doc.exclusions,
		"terms": doc.terms,
		"internal_notes": doc.internal_notes,
		"price_tiers": [
			{"tier_label": row.tier_label, "price_per_person": row.price_per_person}
			for row in doc.price_tiers or []
		],
		"days": doc.render_days(),
		"agency": doc.render_agency(),
	}


@frappe.whitelist()
def get_itinerary_for_editor(itinerary: str):
	"""One round trip that opens the editor, permission-checked like every write."""
	return itinerary_payload(get_itinerary(itinerary, "read"))


# --- pdf -------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def get_pdf(itinerary: str):
	"""Render the branded A4 PDF and attach it to the itinerary, privately."""
	doc = get_itinerary(itinerary, "read")
	file_doc = attach_pdf(doc, is_private=1)
	return {"file_url": file_doc.file_url, "file_name": file_doc.file_name}


def render_pdf(doc) -> bytes:
	document_links.ensure_print_format(PRINT_FORMAT, install_print_format)
	return document_links.render_print_pdf(DOCTYPE, doc.name, PRINT_FORMAT)


def pdf_file_name(doc, token: str = "") -> str:
	"""The customer-facing file name, optionally with an unguessable suffix.

	The private attachment keeps the readable name: it lives behind a login and
	the agent has to recognise it. The public copy the WhatsApp send needs takes
	a random token, because a name built from the customer's own name would let
	anyone walk the /files/ directory and read other customers' quotes.
	"""
	stem = frappe.utils.cstr(doc.title or doc.name).strip() or doc.name
	name = document_links.pdf_file_name(stem, cint(doc.version), token)
	# `document_links.pdf_file_name` strips the stem to word characters, and a
	# title made entirely of punctuation strips to nothing. The itinerary's own
	# fallback has always been the document name, so keep it.
	if name.startswith("-v"):
		name = document_links.pdf_file_name(doc.name, cint(doc.version), token)
	return name


def attach_pdf(doc, is_private: int, token: str = ""):
	"""Write the PDF as a File on the itinerary, replacing the same version's file.

	Regenerating version 3 twice must leave one attachment, not two, so a file
	with the same name on the same document is dropped first.
	"""
	# `render_pdf` and `pdf_file_name` are called through the module so a test
	# that patches either of them still reaches the patch.
	content = render_pdf(doc)
	file_name = pdf_file_name(doc, token)
	return document_links.attach_pdf(DOCTYPE, doc.name, file_name, content, is_private)


def cleanup_public_itinerary_pdfs(older_than_hours: int = PUBLIC_PDF_TTL_HOURS) -> int:
	"""Hourly: sweep away the temporary public PDFs the WhatsApp send leaves.

	The send cannot delete its own file. Meta's Cloud API accepts the POST and
	returns a message id, then fetches the media from the link while it processes
	the message. Deleting in the same request races that fetch and the customer
	receives nothing. So the file stays live for a couple of hours and a sweep
	removes it afterwards.

	A sweep is also what survives a worker that dies mid-send: an in-request
	timer or an enqueued job tied to the send would leak the file forever, while
	the next hourly run picks it up regardless.

	Three conditions must all hold before a file is touched. It has to be public,
	attached to an itinerary, and carry the random token this module puts in the
	name. That last one is what keeps the sweep off a public file somebody
	attached to an itinerary by hand.

	Never raises: a scheduler job that throws takes the rest of its queue down.
	"""
	return document_links.cleanup_public_pdfs(DOCTYPE, older_than_hours)


def log_quietly(message: str, title: str):
	"""Write an error log, and give up silently if even that fails.

	`frappe.log_error` reads and writes the database, so whatever broke the
	caller can break the logging too. A scheduler job promising never to raise
	cannot make that promise if its own error handler can throw.
	"""
	document_links.log_quietly(message, title)


def is_send_copy_name(file_name: str) -> bool:
	"""True only for a file this module named for a WhatsApp send."""
	return document_links.is_send_copy_name(file_name)


# --- whatsapp --------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def send_via_whatsapp(itinerary: str):
	"""Send the itinerary PDF to the lead's WhatsApp number.

	Returns a structured result rather than raising on a send failure, because
	the most common failure is not a bug: outside Meta's 24-hour customer-service
	window a free-form document send is rejected, and the agent needs to be told
	that in words they can act on.
	"""
	from crm.api.whatsapp import create_whatsapp_message, get_reference_whatsapp_numbers

	doc = get_itinerary(itinerary, "write")
	require_lead_permission(doc.lead, "write")

	lead_doc = frappe.get_doc(LEAD_DOCTYPE, doc.lead)
	numbers = sorted(get_reference_whatsapp_numbers(lead_doc))
	if not numbers:
		return {
			"success": False,
			"reason": "no_number",
			"error": _("This lead has no WhatsApp number. Add a mobile number to the lead first."),
		}

	# The version is bumped BEFORE the PDF is rendered. A re-send after an edit
	# is a new version in the customer's hands, and the document they receive has
	# to print the same version number the record carries. Rendering first would
	# post a PDF stamped "Version 1" for a record that had just become 2.
	previous_status, previous_version = doc.status, cint(doc.version)
	if doc.status == "Revised":
		doc.version = cint(doc.version) + 1
	doc.status = "Sent"
	doc.save()

	# Meta fetches the attachment over plain HTTP with no session, so the PDF it
	# is pointed at has to be readable without a login. A private file would come
	# back as a login page and the send would fail with an unhelpful Meta error.
	#
	# This copy is NOT deleted here. Meta accepts the POST and returns a message
	# id, then fetches the media while it processes the message. A delete in this
	# request races that fetch and the customer receives nothing. The file lives
	# for `PUBLIC_PDF_TTL_HOURS` and `cleanup_public_itinerary_pdfs` sweeps it.
	#
	# What bounds the exposure meanwhile is the name: a random token, so the URL
	# cannot be derived from the customer's name, and no two sends collide.
	# The private copy that `get_pdf` writes is the one the agency keeps.
	token = frappe.generate_hash(length=PUBLIC_PDF_TOKEN_LENGTH)
	file_doc = attach_pdf(doc, is_private=0, token=token)

	try:
		message = create_whatsapp_message(
			reference_doctype=LEAD_DOCTYPE,
			reference_name=doc.lead,
			message=whatsapp_summary(doc),
			to=numbers[0],
			attach=file_doc.file_url,
			reply_to="",
			content_type="document",
		)
	except frappe.PermissionError:
		# A permission refusal is a real error, not a delivery problem. The
		# record is still rolled back first: nothing was sent either way, and a
		# direct call does not get the request handler's rollback.
		restore(doc, previous_status, previous_version)
		raise
	except Exception as error:
		frappe.log_error(frappe.get_traceback(), "CRM Itinerary: WhatsApp send failed")
		# Nothing reached the customer, so the record must not claim a new
		# version or a Sent status.
		restore(doc, previous_status, previous_version)
		return {
			"success": False,
			"reason": "send_failed",
			"error": frappe.utils.cstr(error),
			"hint": _(
				"WhatsApp only accepts a free-form message within 24 hours of the "
				"customer's last message. Ask the customer to message first, or send "
				"the PDF manually."
			),
		}

	# No cleanup here, on either path. The sweep is the single owner of this
	# file's lifetime, which is what makes the leak impossible when a worker
	# dies between the insert and the send.
	return {
		"success": True,
		"message": message,
		"status": doc.status,
		"version": doc.version,
		"to": numbers[0],
	}


def restore(doc, status: str, version: int):
	"""Undo the pre-send status and version bump after a send that never landed."""
	doc.status, doc.version = status, version
	doc.save()


def whatsapp_summary(doc) -> str:
	"""The short text that rides along with the PDF."""
	days = clamp_days(doc.num_days)
	lines = [doc.title or _("Your itinerary")]

	if doc.destination:
		lines.append(_("{0}, {1} days").format(doc.destination, days))
	else:
		lines.append(_("{0} days").format(days))

	if doc.start_date:
		lines.append(_("From {0}").format(frappe.utils.formatdate(doc.start_date, "d MMMM yyyy")))

	for tier in doc.price_tiers or []:
		if tier.tier_label:
			lines.append(f"{tier.tier_label}: {doc.render_amount(tier.price_per_person)} per person")

	lines.append(_("The full day-by-day plan is in the attached PDF."))
	return "\n".join(lines)


# --- installation ----------------------------------------------------------


SALES_ROLES = ("Sales User", "Sales Manager", "System Manager")


def require_sales_role():
	if not any(role in SALES_ROLES for role in frappe.get_roles()):
		frappe.throw(_("Only sales users can use itineraries."), frappe.PermissionError)


@frappe.whitelist()
def can_use_itineraries() -> bool:
	"""Whether to show the Itineraries entry in the sidebar. Never raises."""
	try:
		return bool(
			any(role in SALES_ROLES for role in frappe.get_roles()) and frappe.has_permission(DOCTYPE, "read")
		)
	except Exception:
		return False


@frappe.whitelist()
def is_ai_configured() -> bool:
	"""Whether the generate buttons can do anything.

	Gated on a sales role. Without the gate this is an unauthenticated-ish probe
	that tells any logged-in user whether the agency pays for an AI provider.
	"""
	require_sales_role()

	from crm.ai.client import is_configured

	return is_configured()


def same_value(stored, wanted) -> bool:
	"""Compare a stored Print Format value with the one this module wants.

	The margins are Float fields, so the database returns 12.0 where the code
	says 12. Comparing them as strings never matches, which made every migrate
	rewrite the format and silently discard any change an administrator had made
	to it. Numbers are compared as numbers, everything else as text.
	"""
	if isinstance(wanted, int | float) and not isinstance(wanted, bool):
		return flt(stored) == flt(wanted)
	return frappe.utils.cstr(stored) == frappe.utils.cstr(wanted)


def install_print_format():
	"""after_migrate: create or refresh the A4 print format from the app's template.

	The HTML lives in a file so it is reviewable in git. The Print Format row is
	rewritten only when the file actually changed, so a migrate on an unchanged
	app does not churn the document's version history, and an administrator's own
	edit to the format survives.
	"""
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	path = frappe.get_app_path("crm", "templates", "print_formats", "travel_itinerary_a4.html")
	try:
		with open(path) as template:  # nosemgrep
			html = template.read()
	except OSError:
		frappe.log_error(f"missing template: {path}", "CRM Itinerary: print format not installed")
		return

	values = {
		"doc_type": DOCTYPE,
		"module": "FCRM",
		"standard": "No",
		"custom_format": 1,
		"print_format_type": "Jinja",
		"disabled": 0,
		"html": html,
		"margin_top": 12,
		"margin_bottom": 14,
		"margin_left": 10,
		"margin_right": 10,
		"font_size": 12,
		"page_number": "Bottom Center",
	}

	if frappe.db.exists("Print Format", PRINT_FORMAT):
		existing = frappe.get_doc("Print Format", PRINT_FORMAT)
		if all(same_value(existing.get(key), value) for key, value in values.items()):
			return
		existing.update(values)
		existing.flags.ignore_permissions = True
		existing.save(ignore_permissions=True)
		return

	doc = frappe.get_doc({"doctype": "Print Format", "name": PRINT_FORMAT, **values})
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)


def add_itinerary_roles():
	"""after_migrate: let the sales roles use the itinerary doctypes.

	Both roles draft itineraries, so both get create and write. Only a System
	Manager may delete one -- a sent itinerary is what the customer was promised.

	A sales user gets neither report nor export. The row filter in
	`get_itinerary_permission_query_conditions` keeps the list honest, but report
	and export are the two paths that hand a whole table to a leaving employee,
	and an agent never needs them to plan one trip. Managers keep both.
	"""
	grant(DOCTYPE, "Sales Manager", report=True, export=True, share=True)
	grant(DOCTYPE, "Sales User", report=False, export=False, share=False)


def grant(doctype: str, role: str, report: bool, export: bool, share: bool):
	"""Set one role's permissions on the itinerary. Idempotent, and corrective.

	The properties are written on every migrate, not only when the permission row
	is new. A site that already ran a looser version of this function is
	tightened by the next migrate rather than keeping the old grant forever.
	"""
	if not frappe.db.exists("DocType", doctype):
		return

	if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role}):
		# `add_permission` copies the standard permissions into Custom DocPerm
		# first, so the System Manager row survives this call.
		add_permission(doctype, role, 0, "write")

	for property_name, value in (
		("read", 1),
		("write", 1),
		("create", 1),
		("print", 1),
		# Deleting a sent itinerary destroys the record of what was promised.
		("delete", 0),
		("report", int(report)),
		("export", int(export)),
		("share", int(share)),
	):
		update_permission_property(doctype, role, 0, property_name, value)
