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

import contextlib
import re

import frappe
from frappe import _
from frappe.permissions import add_permission, update_permission_property
from frappe.utils import cint, flt

from crm.ai.client import AIConfigurationError, AIResponseError, complete
from crm.fcrm.doctype.crm_itinerary.crm_itinerary import (
	MIN_DAYS,
	TIMES_OF_DAY,
	ItinerarySchemaError,
	clamp_days,
	dump_days,
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

# How many neighbouring days the model is shown so it does not repeat itself.
DEDUP_NEIGHBOURS = 2

# How long the temporary public PDF stays reachable after a WhatsApp send.
# Meta fetches the media while it processes the message, not before the POST
# returns, so the file has to outlive the request. Two hours is far longer than
# the fetch needs and short enough that the link is not a lasting exposure.
PUBLIC_PDF_TTL_HOURS = 2

# The random suffix `pdf_file_name` gives a send copy. The sweep deletes nothing
# that does not carry it, so a public file attached to an itinerary by hand is
# never touched.
# The trailing `,` is not slack for its own sake: when a file of the same name
# already exists, Frappe appends six hex characters of the content hash to the
# name it stores. A fixed-length match would then miss the very file it wrote.
PUBLIC_PDF_TOKEN_LENGTH = 24
SEND_COPY_PATTERN = re.compile(rf"-v\d+-[0-9a-f]{{{PUBLIC_PDF_TOKEN_LENGTH},}}\.pdf$", re.IGNORECASE)

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
	"required": ["slots"],
}

# What the editor may write through `update_details`. `status`, `version`,
# `lead` and `days_json` are missing on purpose -- each has its own endpoint.
EDITABLE_FIELDS = (
	"title",
	"deal",
	"destination",
	"start_date",
	"num_days",
	"group_size",
	"budget",
	"currency",
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

	doc = frappe.new_doc(DOCTYPE)
	doc.update(
		{
			"lead": lead,
			"title": build_title(destination, customer),
			"destination": destination,
			"start_date": lead_doc.get("travel_start_date"),
			"num_days": days_between(lead_doc.get("travel_start_date"), lead_doc.get("travel_end_date")),
			"group_size": cint(lead_doc.get("group_size")),
			"budget": flt(lead_doc.get("budget")),
			"currency": frappe.db.get_default("currency") or "INR",
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
def generate_day(itinerary: str, day_number):
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
			_("The AI could not draft this itinerary: {0}").format(error), title=_("AI Draft Failed")
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
	lines.append(f"Group size: {cint(doc.group_size) or 'not given'} travellers")

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
			"These activities are already booked on the neighbouring days. Do not " "repeat any of them:",
			"\n".join(f"- {title}" for title in already),
		]

	parts += [
		"",
		"Return the morning, afternoon and evening slots for this one day. Two to four "
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


# --- editing ---------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def update_days(itinerary: str, days_json):
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
def update_details(itinerary: str, values):
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

	for field in EDITABLE_FIELDS:
		if field in values:
			doc.set(field, coerce_field(field, values[field]))

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
		"lead": doc.lead,
		"destination": doc.destination,
		"start_date": doc.start_date,
		"num_days": doc.num_days,
		"group_size": doc.group_size,
		"budget": doc.budget,
		"currency": doc.currency,
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
	if not frappe.db.exists("Print Format", PRINT_FORMAT):
		install_print_format()

	return frappe.get_print(
		DOCTYPE,
		doc.name,
		print_format=PRINT_FORMAT,
		as_pdf=True,
		no_letterhead=1,
	)


def pdf_file_name(doc, token: str = "") -> str:
	"""The customer-facing file name, optionally with an unguessable suffix.

	The private attachment keeps the readable name: it lives behind a login and
	the agent has to recognise it. The public copy the WhatsApp send needs takes
	a random token, because a name built from the customer's own name would let
	anyone walk the /files/ directory and read other customers' quotes.
	"""
	stem = frappe.utils.cstr(doc.title or doc.name).strip() or doc.name
	# Anything that is not a plain word character becomes a dash, so the name is
	# safe in a URL, on disk and in the WhatsApp document card.
	stem = re.sub(r"[^\w\-]+", "-", stem).strip("-")[:60] or doc.name
	suffix = f"-{token}" if token else ""
	return f"{stem}-v{cint(doc.version) or 1}{suffix}.pdf"


def attach_pdf(doc, is_private: int, token: str = ""):
	"""Write the PDF as a File on the itinerary, replacing the same version's file.

	Regenerating version 3 twice must leave one attachment, not two, so a file
	with the same name on the same document is dropped first.
	"""
	content = render_pdf(doc)
	file_name = pdf_file_name(doc, token)

	stale = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": DOCTYPE,
			"attached_to_name": doc.name,
			"file_name": file_name,
			"is_private": cint(is_private),
		},
		pluck="name",
	)
	for name in stale:
		frappe.delete_doc("File", name, ignore_permissions=True, delete_permanently=True)

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"attached_to_doctype": DOCTYPE,
			"attached_to_name": doc.name,
			"is_private": cint(is_private),
			"content": content,
		}
	)
	file_doc.insert(ignore_permissions=True)
	return file_doc


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
	removed = 0
	try:
		cutoff = frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-cint(older_than_hours))
		candidates = frappe.get_all(
			"File",
			filters={
				"attached_to_doctype": DOCTYPE,
				"is_private": 0,
				"creation": ["<", cutoff],
			},
			fields=["name", "file_name"],
			limit=500,
		)

		for row in candidates:
			if not is_send_copy_name(row.file_name):
				continue
			try:
				frappe.delete_doc("File", row.name, ignore_permissions=True, delete_permanently=True)
				removed += 1
			except Exception:
				log_quietly(
					f"could not remove public itinerary PDF {row.file_name}",
					"CRM Itinerary: cleanup failed",
				)

		# No explicit commit. The background job runner commits when the method
		# returns, and committing here would also commit whatever the caller had
		# open -- which silently defeats the rollback a test relies on.
	except Exception:
		log_quietly(frappe.get_traceback(), "CRM Itinerary: public PDF sweep failed")

	return removed


def log_quietly(message: str, title: str):
	"""Write an error log, and give up silently if even that fails.

	`frappe.log_error` reads and writes the database, so whatever broke the
	caller can break the logging too. A scheduler job promising never to raise
	cannot make that promise if its own error handler can throw.
	"""
	with contextlib.suppress(Exception):
		frappe.log_error(message, title)


def is_send_copy_name(file_name: str) -> bool:
	"""True only for a file this module named for a WhatsApp send."""
	return bool(SEND_COPY_PATTERN.search(frappe.utils.cstr(file_name)))


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
		with open(path) as template:
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
