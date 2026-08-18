# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

"""Duplicate warning on create (master spec §5, item 3).

The agency's real problem is one customer entered twice, once by phone and once
from the web form, with the number typed differently each time. Stage 1A already
solved the matching half of that: every Lead, Deal and Contact carries
`custom_parama_email_normalized` and `custom_parama_phone_e164`, written on save
by `crm.contact_keys` and indexed. This module is the read side -- an equality
lookup on those two columns, nothing more.

It WARNS. It never blocks. `check_duplicates` has no side effect, returns an
empty list when it finds nothing and never raises for a value it cannot parse:
an address that does not normalise simply has no key, and a record with no key
can never match.

Endpoint authorization (master spec §3)
---------------------------------------
* `check_duplicates` -- any signed-in user. Row scope is derived SERVER-side by
  `frappe.get_list`, which applies the `permission_query_conditions` hooks; for
  CRM Lead and CRM Deal that is `crm.permissions.org_hierarchy`, the same rule
  their list pages use. The caller supplies an email and a phone number and
  nothing else -- there is no filter, field or doctype argument that could widen
  what comes back, and the doctype that IS accepted is checked against a fixed
  allowlist before it reaches a query.
* A doctype the caller cannot read at all is skipped rather than raising, so one
  missing role does not turn the whole warning off.
* No background job is involved.

The consequence worth stating plainly: a Sales User who is about to re-enter a
lead that belongs to another team sees NOTHING. That is the correct trade -- the
alternative leaks the existence, name and owner of another team's customer to
anybody who can guess an email address.
"""

import frappe
from frappe import _

from crm.contact_keys import EMAIL_FIELD, PHONE_FIELD
from crm.normalization import normalize_email, normalize_phone

# Which doctypes may be named in the request, and what each one is searched
# against. Contact is in every set because a Contact is the same person as a
# Lead: entering a lead for somebody who is already a contact is the duplicate
# the agency hits most often.
SEARCH_SETS = {
	"CRM Lead": ("CRM Lead", "Contact"),
	"CRM Deal": ("CRM Deal", "Contact"),
	"Contact": ("Contact", "CRM Lead"),
}

# The field to show as the record's name in the banner, in priority order. The
# first non-empty one wins; `name` is the last resort and always exists.
TITLE_FIELDS = {
	"CRM Lead": ("lead_name", "organization", "email", "name"),
	"CRM Deal": ("organization", "lead_name", "email", "name"),
	"Contact": ("full_name", "company_name", "email_id", "name"),
}

MAX_RESULTS = 5


def title_of(doctype: str, row) -> str:
	for fieldname in TITLE_FIELDS[doctype]:
		value = frappe.utils.cstr(row.get(fieldname)).strip()
		if value:
			return value
	return frappe.utils.cstr(row.get("name"))


def _match(doctype: str, fieldname: str, value: str, limit: int) -> list[dict]:
	"""Rows of `doctype` whose derived key equals `value`, permission-filtered.

	`frappe.get_list` is what makes this safe: it runs the read check for the
	doctype and applies the registered `permission_query_conditions`, so the
	org-hierarchy rule is in the SQL rather than in a filter afterwards.
	"""
	if not frappe.has_permission(doctype, "read"):
		return []

	# The two columns are Custom Fields from the Stage-1A patch. On a site that
	# has not migrated they do not exist, and filtering on a missing column is
	# an SQL error rather than an empty result.
	meta = frappe.get_meta(doctype)
	if not meta.has_field(fieldname):
		return []

	fields = ["name", *dict.fromkeys(TITLE_FIELDS[doctype])]
	return frappe.get_list(
		doctype,
		filters={fieldname: value},
		fields=fields,
		order_by="modified desc",
		limit=limit,
	)


@frappe.whitelist()
def check_duplicates(doctype: str, email: str | None = None, phone: str | None = None) -> list[dict]:
	"""Records the caller can read that already hold this email or phone number.

	Returns `[{doctype, name, title, matched_field}]`, at most `MAX_RESULTS`
	entries, email matches before phone matches, each record at most once.
	"""
	if doctype not in SEARCH_SETS:
		frappe.throw(
			_("Duplicate checking is not available for {0}.").format(_(doctype)),
			frappe.PermissionError,
		)

	# An unparsable value normalises to "" and is dropped here, so it can never
	# reach the query as an empty-string key and match every keyless record.
	wanted = []
	if normalized_email := normalize_email(email):
		wanted.append(("email", EMAIL_FIELD, normalized_email))
	if normalized_phone := normalize_phone(phone):
		wanted.append(("phone", PHONE_FIELD, normalized_phone))

	if not wanted:
		return []

	results = []
	seen = set()
	for matched_field, fieldname, value in wanted:
		for target in SEARCH_SETS[doctype]:
			for row in _match(target, fieldname, value, MAX_RESULTS):
				key = (target, row.name)
				if key in seen:
					continue
				seen.add(key)
				results.append(
					{
						"doctype": target,
						"name": row.name,
						"title": title_of(target, row),
						"matched_field": matched_field,
					}
				)
				if len(results) >= MAX_RESULTS:
					return results

	return results
