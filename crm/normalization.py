"""Canonical forms for the two contact keys the CRM matches records on.

An email address and a phone number arrive in many shapes: mixed case, wrapped
in a display name, a national number, digits without the leading plus. A lookup
that compares raw values therefore misses rows it was meant to find, and a
suppression check that misses is a message sent to somebody who asked us to
stop. So normalisation lives in one place and every caller uses it.

Both functions return an empty string when the value cannot be normalised. An
empty key is never stored and never matches, so an unparseable value can never
be mistaken for a match.
"""

import frappe


def normalize_email(value) -> str:
	"""Lower-cased address part of an email, or an empty string.

	`validate_email_address` also unwraps `Name <a@b.com>`, which is the form a
	Communication's `sender` often carries.
	"""
	raw = frappe.utils.cstr(value).strip()
	if not raw:
		return ""

	address = frappe.utils.validate_email_address(raw, throw=False)
	return address.strip().lower() if address else ""


def normalize_phone(value) -> str:
	"""The number in strict E.164 form, or an empty string.

	The parse is `crm.api.whatsapp.normalize_whatsapp_number`, which is what the
	WhatsApp paths already store, so a number normalised here compares equal to
	one normalised there. That function hands back its input unchanged when the
	parse fails, so its result is re-validated before it is returned.

	The import is deferred: `crm.api.whatsapp` pulls in the notification and
	integration modules, and this module is imported from document hooks and from
	patches that must stay cheap.
	"""
	raw = frappe.utils.cstr(value).strip()
	if not raw:
		return ""

	from crm.api.whatsapp import normalize_whatsapp_number
	from crm.utils import parse_phone_number

	number = normalize_whatsapp_number(raw)
	if not number.startswith("+"):
		return ""

	return number if parse_phone_number(number).get("is_valid") else ""
