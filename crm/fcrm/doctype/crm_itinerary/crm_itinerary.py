# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""The travel itinerary document, and the schema its days are stored in.

An itinerary is DATA, not prose. The AI drafts it, the agent edits it, and the
PDF, the editor and the WhatsApp summary all render from the same structure.
Nothing downstream ever parses free text, so the schema below is the contract
between the AI prompts, the Vue editor and the print format.

`days_json` holds one JSON object::

    {
        "days": [
            {
                "day_number": 1,  # int >= 1, unique in the list
                "title": "Arrival in Bali",  # str
                "summary": "Land, settle in, ...",  # str, may be empty
                "slots": [
                    {
                        "time_of_day": "morning",  # morning | afternoon | evening
                        "items": [
                            {
                                "title": "Airport pickup",  # str, required, non-empty
                                "description": "...",  # str, may be empty
                                "place_name": "Ngurah Rai",  # str or null
                                "duration_hours": 1.5,  # number or null
                                "est_cost": 1200,  # number or null
                                "verified": false,  # bool; AI output is always false
                            }
                        ],
                    }
                ],
            }
        ]
    }

`verified` is the honesty switch. The model invents plausible place names, so
every AI-written item lands unverified and the agent flips it only after
checking that the place is real. Nothing in the backend ever sets it to true.

No key outside the sets below is accepted. A silently-dropped key would let the
editor and the print format drift apart without anyone noticing.
"""

import base64
import binascii
import json
import re
from xml.etree import ElementTree

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt

# --- schema ----------------------------------------------------------------

TIMES_OF_DAY = ("morning", "afternoon", "evening")

DAY_KEYS = (
	"day_number",
	"title",
	"summary",
	"highlights",
	"description",
	"accommodation",
	"meals",
	"image",
	"slots",
)
SLOT_KEYS = ("time_of_day", "items")
ITEM_KEYS = ("title", "description", "place_name", "duration_hours", "est_cost", "verified")
MEAL_KEYS = ("breakfast", "lunch", "dinner")

# An itinerary shorter than a day is meaningless and one longer than a month is
# a data-entry accident. The bound also caps how many AI calls one document can
# ever spend.
MIN_DAYS = 1
MAX_DAYS = 30

# Field-length guards. The editor is a free-text form, and an unbounded string
# would break the PDF layout and blow up `days_json`.
MAX_TITLE_LENGTH = 200
MAX_TEXT_LENGTH = 2000
MAX_DESCRIPTION_LENGTH = 6000
MAX_HIGHLIGHTS = 8
MAX_IMAGE_LENGTH = 300_000
MAX_ITEMS_PER_SLOT = 20

LOCAL_IMAGE_PATTERN = re.compile(r"^/(?:private/)?files/[^\x00-\x1f?#]+(?:\?[^\x00-\x1f#]*)?$", re.IGNORECASE)
SVG_DATA_PREFIX = "data:image/svg+xml;base64,"
SAFE_SVG_TAGS = {"svg", "rect", "circle", "path", "text"}
SAFE_SVG_ATTRIBUTES = {
	"viewBox",
	"x",
	"y",
	"width",
	"height",
	"rx",
	"cx",
	"cy",
	"r",
	"d",
	"fill",
	"opacity",
	"stroke",
	"stroke-opacity",
	"stroke-width",
	"font-family",
	"font-size",
	"font-weight",
	"letter-spacing",
}

# The fields whose change turns a Sent itinerary back into a Revised one.
CONTENT_FIELDS = (
	"title",
	"subtitle",
	"customer_name",
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
	"days_json",
	"inclusions",
	"exclusions",
	"terms",
)


class ItinerarySchemaError(frappe.ValidationError):
	"""`days_json` does not match the documented schema."""


def empty_day(day_number: int, title: str = "", summary: str = "") -> dict:
	"""A day with the three slots present and no items in them."""
	return {
		"day_number": cint(day_number),
		"title": title or "",
		"summary": summary or "",
		"highlights": [],
		"description": "",
		"accommodation": "",
		"meals": {meal: False for meal in MEAL_KEYS},
		"image": None,
		"slots": [{"time_of_day": part, "items": []} for part in TIMES_OF_DAY],
	}


def parse_days(days_json) -> list[dict]:
	"""Read `days_json` in any of the shapes callers hand over.

	Accepts the stored object, a bare list of days, or a JSON string of either.
	Always returns a validated list. An empty or unset value returns `[]`.
	"""
	value = days_json
	if isinstance(value, str):
		value = value.strip()
		if not value:
			return []
		try:
			value = json.loads(value)
		except ValueError as error:
			raise ItinerarySchemaError(_("The itinerary days are not valid JSON.")) from error

	if value is None:
		return []

	if isinstance(value, dict):
		value = value.get("days")
		if value is None:
			return []

	return validate_days(value)


def dump_days(days: list[dict]) -> str:
	"""The canonical stored form: one object with a `days` list."""
	return json.dumps({"days": days}, indent=1, ensure_ascii=False)


def validate_days(days) -> list[dict]:
	"""Check the day list against the schema and return it normalised.

	Raises `ItinerarySchemaError` naming the offending path, so the editor can
	show the agent exactly which day broke.
	"""
	if not isinstance(days, list):
		raise ItinerarySchemaError(_("The itinerary days must be a list."))

	if len(days) > MAX_DAYS:
		raise ItinerarySchemaError(_("An itinerary cannot hold more than {0} days.").format(MAX_DAYS))

	seen_numbers = set()
	clean_days = []

	for index, day in enumerate(days):
		where = _("day at position {0}").format(index + 1)
		if not isinstance(day, dict):
			raise ItinerarySchemaError(_("The {0} must be an object.").format(where))

		reject_unknown_keys(day, DAY_KEYS, where)

		day_number = require_positive_int(day.get("day_number"), _("day_number"), where)
		# The list length is capped above, but the values are what reach
		# `render_day_date`. Without this bound a day numbered 5000 prints a
		# date thirteen years after the trip starts.
		if day_number > MAX_DAYS:
			raise ItinerarySchemaError(
				_("Day number {0} is above the limit of {1}.").format(day_number, MAX_DAYS)
			)
		if day_number in seen_numbers:
			raise ItinerarySchemaError(_("Day number {0} appears more than once.").format(day_number))
		seen_numbers.add(day_number)

		clean_days.append(
			{
				"day_number": day_number,
				"title": clean_text(day.get("title"), _("title"), where, MAX_TITLE_LENGTH),
				"summary": clean_text(day.get("summary"), _("summary"), where, MAX_TEXT_LENGTH),
				"highlights": validate_highlights(day.get("highlights"), where),
				"description": clean_text(
					day.get("description"), _("description"), where, MAX_DESCRIPTION_LENGTH
				),
				"accommodation": clean_text(
					day.get("accommodation"), _("accommodation"), where, MAX_TITLE_LENGTH
				),
				"meals": validate_meals(day.get("meals"), where),
				"image": clean_image(day.get("image"), where),
				"slots": validate_slots(day.get("slots"), _("day {0}").format(day_number)),
			}
		)

	clean_days.sort(key=lambda day: day["day_number"])
	return clean_days


def validate_highlights(highlights, where: str) -> list[str]:
	if highlights is None:
		return []
	if isinstance(highlights, str):
		highlights = highlights.split(",")
	if not isinstance(highlights, list):
		raise ItinerarySchemaError(_("The highlights of {0} must be a list.").format(where))
	if len(highlights) > MAX_HIGHLIGHTS:
		raise ItinerarySchemaError(
			_("The highlights of {0} cannot contain more than {1} items.").format(where, MAX_HIGHLIGHTS)
		)

	clean = []
	for highlight in highlights:
		value = clean_text(highlight, _("highlight"), where, MAX_TITLE_LENGTH)
		if value:
			clean.append(value)
	return clean


def validate_meals(meals, where: str) -> dict[str, bool]:
	if meals is None:
		return {meal: False for meal in MEAL_KEYS}
	if not isinstance(meals, dict):
		raise ItinerarySchemaError(_("The meals of {0} must be an object.").format(where))
	reject_unknown_keys(meals, MEAL_KEYS, _("the meals of {0}").format(where))
	return {meal: meals.get(meal) is True for meal in MEAL_KEYS}


def clean_image(value, where: str) -> str | None:
	if value is None or value == "":
		return None
	if not isinstance(value, str):
		raise ItinerarySchemaError(_("The image of {0} must be text or null.").format(where))

	value = value.strip()
	if len(value) > MAX_IMAGE_LENGTH:
		raise ItinerarySchemaError(_("The image of {0} is too large.").format(where))
	if LOCAL_IMAGE_PATTERN.fullmatch(value):
		return value
	if value.lower().startswith(SVG_DATA_PREFIX):
		_validate_generated_svg(value, where)
		return value

	raise ItinerarySchemaError(
		_("The image of {0} must be an uploaded file or generated artwork.").format(where)
	)


def _validate_generated_svg(value: str, where: str) -> None:
	"""Accept the small, passive SVG vocabulary produced by the itinerary UI.

	Arbitrary remote URLs are forbidden because the PDF renderer fetches image
	sources server-side. Arbitrary SVG is forbidden for the same reason: SVG can
	contain scripts, event attributes and nested remote-resource references.
	"""
	payload = value[len(SVG_DATA_PREFIX) :]
	try:
		raw = base64.b64decode(payload, validate=True)
		text = raw.decode("utf-8")
	except (binascii.Error, UnicodeDecodeError, ValueError):
		raise ItinerarySchemaError(_("The generated image of {0} is invalid.").format(where)) from None

	if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
		raise ItinerarySchemaError(_("The generated image of {0} is invalid.").format(where))

	try:
		root = ElementTree.fromstring(text)
	except ElementTree.ParseError:
		raise ItinerarySchemaError(_("The generated image of {0} is invalid.").format(where)) from None

	elements = list(root.iter())
	if len(elements) > 64:
		raise ItinerarySchemaError(_("The generated image of {0} is too complex.").format(where))

	for element in elements:
		tag = element.tag.rsplit("}", 1)[-1]
		if tag not in SAFE_SVG_TAGS or any(
			attribute.rsplit("}", 1)[-1] not in SAFE_SVG_ATTRIBUTES for attribute in element.attrib
		):
			raise ItinerarySchemaError(_("The generated image of {0} contains unsafe markup.").format(where))
		for attribute_value in element.attrib.values():
			lowered = attribute_value.lower()
			if any(marker in lowered for marker in ("url(", "javascript:", "data:", "http:", "https:", "//")):
				raise ItinerarySchemaError(
					_("The generated image of {0} contains an external resource.").format(where)
				)


def validate_slots(slots, where: str) -> list[dict]:
	if slots is None:
		return [{"time_of_day": part, "items": []} for part in TIMES_OF_DAY]

	if not isinstance(slots, list):
		raise ItinerarySchemaError(_("The slots of {0} must be a list.").format(where))

	seen_parts = set()
	clean_slots = []

	for slot in slots:
		if not isinstance(slot, dict):
			raise ItinerarySchemaError(_("Every slot of {0} must be an object.").format(where))

		reject_unknown_keys(slot, SLOT_KEYS, _("a slot of {0}").format(where))

		part = slot.get("time_of_day")
		if part not in TIMES_OF_DAY:
			raise ItinerarySchemaError(
				_("The slot of {0} has time_of_day {1}; allowed values are {2}.").format(
					where, frappe.as_json(part), ", ".join(TIMES_OF_DAY)
				)
			)
		if part in seen_parts:
			raise ItinerarySchemaError(_("The slot {0} appears twice in {1}.").format(part, where))
		seen_parts.add(part)

		clean_slots.append({"time_of_day": part, "items": validate_items(slot.get("items"), where, part)})

	# The three parts of the day always exist, so the editor never has to create
	# a slot before it can add an item to it.
	for part in TIMES_OF_DAY:
		if part not in seen_parts:
			clean_slots.append({"time_of_day": part, "items": []})

	clean_slots.sort(key=lambda slot: TIMES_OF_DAY.index(slot["time_of_day"]))
	return clean_slots


def validate_items(items, where: str, part: str) -> list[dict]:
	if items is None:
		return []

	location = _("the {0} slot of {1}").format(part, where)

	if not isinstance(items, list):
		raise ItinerarySchemaError(_("The items of {0} must be a list.").format(location))

	if len(items) > MAX_ITEMS_PER_SLOT:
		raise ItinerarySchemaError(_("{0} holds more than {1} items.").format(location, MAX_ITEMS_PER_SLOT))

	clean_items = []
	for item in items:
		if not isinstance(item, dict):
			raise ItinerarySchemaError(_("Every item of {0} must be an object.").format(location))

		reject_unknown_keys(item, ITEM_KEYS, location)

		title = clean_text(item.get("title"), _("title"), location, MAX_TITLE_LENGTH)
		if not title:
			raise ItinerarySchemaError(_("An item of {0} has no title.").format(location))

		clean_items.append(
			{
				"title": title,
				"description": clean_text(
					item.get("description"), _("description"), location, MAX_TEXT_LENGTH
				),
				"place_name": clean_optional_text(
					item.get("place_name"), _("place_name"), location, MAX_TITLE_LENGTH
				),
				"duration_hours": clean_optional_number(
					item.get("duration_hours"), _("duration_hours"), location
				),
				"est_cost": clean_optional_number(item.get("est_cost"), _("est_cost"), location),
				# Anything that is not exactly `True` is unverified. A string
				# "false" from a hand-written payload must not read as verified.
				"verified": item.get("verified") is True,
			}
		)

	return clean_items


def reject_unknown_keys(value: dict, allowed: tuple, where: str):
	unknown = sorted(set(value) - set(allowed))
	if unknown:
		raise ItinerarySchemaError(_("The {0} holds unknown keys: {1}.").format(where, ", ".join(unknown)))


def require_positive_int(value, field: str, where: str) -> int:
	# `True` is an int in Python, and a bool here is always a mistake.
	if isinstance(value, bool) or not isinstance(value, int):
		raise ItinerarySchemaError(_("The {0} of the {1} must be a whole number.").format(field, where))
	if value < 1:
		raise ItinerarySchemaError(_("The {0} of the {1} must be 1 or more.").format(field, where))
	return value


def clean_text(value, field: str, where: str, limit: int) -> str:
	if value is None:
		return ""
	if not isinstance(value, str):
		raise ItinerarySchemaError(_("The {0} of {1} must be text.").format(field, where))
	return value.strip()[:limit]


def clean_optional_text(value, field: str, where: str, limit: int) -> str | None:
	if value is None:
		return None
	if not isinstance(value, str):
		raise ItinerarySchemaError(_("The {0} of {1} must be text or null.").format(field, where))
	text = value.strip()[:limit]
	return text or None


def clean_optional_number(value, field: str, where: str) -> float | None:
	if value is None or value == "":
		return None
	if isinstance(value, bool) or not isinstance(value, int | float):
		raise ItinerarySchemaError(_("The {0} of {1} must be a number or null.").format(field, where))
	if value < 0:
		raise ItinerarySchemaError(_("The {0} of {1} cannot be negative.").format(field, where))
	return flt(value)


# --- document --------------------------------------------------------------


class CRMItinerary(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from crm.fcrm.doctype.crm_itinerary_price_tier.crm_itinerary_price_tier import (
			CRMItineraryPriceTier,
		)

		ai_instructions: DF.SmallText | None
		brand_logo: DF.AttachImage | None
		budget: DF.Currency
		contact_email: DF.Data | None
		contact_phone: DF.Data | None
		contact_website: DF.Data | None
		cover_image: DF.AttachImage | None
		currency: DF.Link | None
		customer_name: DF.Data | None
		days_json: DF.LongText | None
		deal: DF.Link | None
		departure_type: DF.Literal["Private Departure", "Group Departure"]
		destination: DF.Data | None
		duration_label: DF.Data | None
		exclusions: DF.SmallText | None
		font_preset: DF.Literal["Modern Alpine", "Classic Bold", "Elegant Serif", "Clean Geometric"]
		group_size: DF.Int
		group_size_label: DF.Data | None
		inclusions: DF.SmallText | None
		internal_notes: DF.SmallText | None
		lead: DF.Link
		num_days: DF.Int
		price_tiers: DF.Table[CRMItineraryPriceTier]
		start_date: DF.Date | None
		status: DF.Literal["Draft", "Sent", "Revised"]
		subtitle: DF.SmallText | None
		tagline_style: DF.Literal["Bold Normal", "Bold Italic", "Medium Normal", "Medium Italic"]
		terms: DF.SmallText | None
		theme: DF.Literal["Sunrise", "Midnight", "Meadow"]
		title: DF.Data
		title_case: DF.Literal["Uppercase", "Capitalize", "Normal"]
		title_style: DF.Literal["Normal", "Italic"]
		title_weight: DF.Literal["900", "700", "500", "400"]
		trip_vibe: DF.Literal["Adventure", "Cultural", "Leisure", "Budget"]
		version: DF.Int
	# end: auto-generated types

	def validate(self):
		self.num_days = clamp_days(self.num_days)
		self.cover_image = clean_image(self.cover_image, _("the itinerary cover"))
		self.brand_logo = clean_image(self.brand_logo, _("the itinerary logo"))
		if not self.currency:
			self.currency = "INR"
		if not self.duration_label:
			self.duration_label = duration_label(self.num_days)
		if not self.group_size_label and cint(self.group_size):
			self.group_size_label = _("{0} travellers").format(cint(self.group_size))
		self.departure_type = self.departure_type or "Group Departure"
		self.theme = self.theme or "Sunrise"
		self.font_preset = self.font_preset or "Modern Alpine"
		self.title_weight = self.title_weight or "900"
		self.title_style = self.title_style or "Normal"
		self.tagline_style = self.tagline_style or "Bold Normal"
		self.title_case = self.title_case or "Uppercase"
		self.trip_vibe = self.trip_vibe or "Adventure"
		if self.version < 1:
			self.version = 1

		# The stored form is re-serialised from the validated structure, so a
		# malformed payload can never reach the database, the PDF or the editor.
		self.days_json = dump_days(parse_days(self.days_json))

	def before_save(self):
		self.mark_revised_when_edited_after_send()

	def mark_revised_when_edited_after_send(self):
		"""An edit to an already-sent itinerary moves it back to Revised.

		The customer holds a PDF that no longer matches the document. Revised
		says so, and `send_via_whatsapp` bumps the version when it goes out
		again. The guard on the previous status is what keeps the send itself --
		which writes Revised -> Sent -- from flipping straight back to Revised.
		"""
		previous = self.get_doc_before_save()
		if not previous or previous.status != "Sent" or self.status != "Sent":
			return

		if self.content_differs_from(previous):
			self.status = "Revised"

	def content_differs_from(self, previous) -> bool:
		if any(self.get(field) != previous.get(field) for field in CONTENT_FIELDS):
			return True

		mine = [(row.tier_label, flt(row.price_per_person)) for row in self.price_tiers or []]
		theirs = [(row.tier_label, flt(row.price_per_person)) for row in previous.price_tiers or []]
		return mine != theirs

	# --- render helpers, called from the print format ----------------------

	def render_days(self) -> list[dict]:
		"""The validated day list. The print format's single entry point."""
		return parse_days(self.days_json)

	def render_amount(self, value) -> str:
		"""One money value in the itinerary's own currency."""
		if value in (None, ""):
			return ""
		return frappe.utils.fmt_money(flt(value), currency=self.currency or "INR")

	def render_agency(self) -> dict:
		details = get_agency_details()
		for source, target in (
			("contact_phone", "phone"),
			("contact_email", "email"),
			("contact_website", "website"),
		):
			if self.get(source):
				details[target] = self.get(source)
		details["logo"] = self.brand_logo or ""
		return details

	def render_duration(self) -> str:
		return self.duration_label or duration_label(self.num_days)

	def render_group_size(self) -> str:
		if self.group_size_label:
			return self.group_size_label
		return _("{0} travellers").format(cint(self.group_size)) if cint(self.group_size) else ""

	def render_title(self) -> str:
		value = frappe.utils.cstr(self.title)
		if self.title_case == "Uppercase":
			return value.upper()
		if self.title_case == "Capitalize":
			return value.title()
		return value

	def render_theme(self) -> str:
		return self.theme if self.theme in ("Sunrise", "Midnight", "Meadow") else "Sunrise"

	def render_font_family(self) -> str:
		return {
			"Modern Alpine": '"Trebuchet MS", Arial, sans-serif',
			"Classic Bold": 'Arial, "Helvetica Neue", sans-serif',
			"Elegant Serif": 'Georgia, "Times New Roman", serif',
			"Clean Geometric": "Verdana, Arial, sans-serif",
		}.get(self.font_preset, '"Trebuchet MS", Arial, sans-serif')

	def render_day_date(self, day_number) -> str:
		"""The calendar date of one day, when the trip has a start date."""
		if not self.start_date:
			return ""
		date = frappe.utils.add_days(self.start_date, cint(day_number) - 1)
		return frappe.utils.formatdate(date, "EEE, d MMM yyyy")

	def render_lines(self, value) -> list[str]:
		"""One Small Text field split into printable bullet lines.

		Done here rather than in the template: the print format's Jinja is
		sandboxed and has no autoescape, so the fewer string operations it does
		on customer text, the smaller the surface for a broken PDF.
		"""
		if not value:
			return []
		return [line.strip() for line in frappe.utils.cstr(value).splitlines() if line.strip()]


def clamp_days(num_days) -> int:
	return max(MIN_DAYS, min(MAX_DAYS, cint(num_days) or MIN_DAYS))


def duration_label(num_days) -> str:
	days = clamp_days(num_days)
	nights = max(0, days - 1)
	day_unit = _("Day") if days == 1 else _("Days")
	night_unit = _("Night") if nights == 1 else _("Nights")
	return _("{0} {1} / {2} {3}").format(days, day_unit, nights, night_unit)


def get_agency_details() -> dict:
	"""Who the PDF says it is from.

	Kept deliberately small: the default Company when ERPNext-style data is on
	the site, otherwise the website's app name, otherwise the site name. None of
	these is guaranteed to exist, so every lookup is guarded.
	"""
	details = {"name": "", "phone": "", "email": "", "website": ""}

	company = frappe.db.get_default("company")
	if company and frappe.db.exists("DocType", "Company") and frappe.db.exists("Company", company):
		details["name"] = company
		for source, target in (("phone_no", "phone"), ("email", "email"), ("website", "website")):
			details[target] = frappe.db.get_value("Company", company, source) or ""

	if not details["name"] and frappe.db.exists("DocType", "Website Settings"):
		details["name"] = frappe.db.get_single_value("Website Settings", "app_name") or ""

	if not details["name"]:
		details["name"] = frappe.local.site or "Travel Desk"

	return details
