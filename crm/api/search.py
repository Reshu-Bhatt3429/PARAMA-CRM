# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

"""Global search behind the Cmd/Ctrl+K palette (master spec §5, items 10 and 11).

One endpoint, six groups, five rows each. Every group is a separate
`frappe.get_list`, which is the whole authorization story: `get_list` refuses a
doctype the caller cannot read and puts the registered
`permission_query_conditions` into the SQL, so a Sales User's palette is
row-filtered by `crm.permissions.org_hierarchy` exactly as their Leads list is.

The second endpoint, `resolve_records`, exists for item 11. "Recently viewed" is
a localStorage list, and localStorage is a client-side cache of what the user
COULD see when they visited. Rendering it straight back would show the title of
a lead that has since been reassigned away from them. So the palette sends the
list up and renders only what comes back.

Endpoint authorization (master spec §3)
---------------------------------------
* `palette_search(query, limit)` -- any signed-in user. Row scope: `get_list`
  per group, permission conditions applied in SQL. The caller supplies a search
  string and a page size and nothing else: there is no filter, field, doctype or
  order argument, so no request can widen the result set. `limit` is clamped.
* `resolve_records(records)` -- any signed-in user. The caller supplies
  `[{doctype, name}]`; the doctype is checked against the same fixed group list
  and the names go into a `name in (...)` filter on a permission-aware
  `get_list`. A name the caller may not read is dropped silently, which is the
  point of the endpoint.
* Neither runs in a background job, so §3's "re-check at execution time" clause
  does not apply.

What row-level scope actually means per doctype, stated honestly: CRM Lead and
CRM Deal have hierarchy conditions registered in `crm/hooks.py`. Contact, CRM
Organization, CRM Task and FCRM Note have NO row-level rule anywhere in this
app -- a Sales User's Tasks page already lists every task on the site. The
palette therefore shows exactly what those list pages already show, and adds no
new visibility. Narrowing them is a change to the app's permission model, not to
this endpoint, and it is not made here.
"""

import frappe
from frappe import _

from crm.contact_keys import EMAIL_FIELD, PHONE_FIELD
from crm.normalization import normalize_email

# One entry per palette section, in the order the palette renders them.
#
#   fields    -- what is selected and returned to the client
#   search    -- the columns the typed text is matched against with LIKE
#   contact_keys -- True where the Stage-1A normalised columns exist
#   title     -- title candidates, first non-empty wins
#   subtitle  -- muted second line candidates, first two non-empty are joined
GROUPS = (
	{
		"doctype": "CRM Lead",
		"label": "Leads",
		"fields": ("name", "lead_name", "organization", "email", "mobile_no", "status"),
		"search": ("name", "lead_name", "organization", "email", "mobile_no"),
		"contact_keys": True,
		"title": ("lead_name", "organization", "email", "name"),
		"subtitle": ("organization", "status", "email"),
	},
	{
		"doctype": "CRM Deal",
		"label": "Deals",
		"fields": ("name", "organization", "lead_name", "email", "mobile_no", "status"),
		"search": ("name", "organization", "lead_name", "email", "mobile_no"),
		"contact_keys": True,
		"title": ("organization", "lead_name", "email", "name"),
		"subtitle": ("lead_name", "status", "email"),
	},
	{
		"doctype": "Contact",
		"label": "Contacts",
		"fields": ("name", "full_name", "email_id", "mobile_no", "company_name"),
		"search": ("name", "full_name", "email_id", "mobile_no", "phone"),
		"contact_keys": True,
		"title": ("full_name", "company_name", "email_id", "name"),
		"subtitle": ("company_name", "email_id"),
	},
	{
		"doctype": "CRM Organization",
		"label": "Organizations",
		"fields": ("name", "organization_name", "industry", "website"),
		"search": ("name", "organization_name", "website"),
		"contact_keys": False,
		"title": ("organization_name", "name"),
		"subtitle": ("industry", "website"),
	},
	{
		"doctype": "CRM Task",
		"label": "Tasks",
		"fields": ("name", "title", "status", "priority", "reference_doctype", "reference_docname"),
		"search": ("name", "title"),
		"contact_keys": False,
		"title": ("title", "name"),
		"subtitle": ("status", "priority"),
	},
	{
		"doctype": "FCRM Note",
		"label": "Notes",
		"fields": ("name", "title", "reference_doctype", "reference_docname"),
		# `content` is searched but never selected: it is a Text Editor field and
		# a palette row has no room for a paragraph of HTML.
		"search": ("name", "title", "content"),
		"contact_keys": False,
		"title": ("title", "name"),
		"subtitle": ("reference_docname",),
	},
)

GROUP_BY_DOCTYPE = {group["doctype"]: group for group in GROUPS}

# One character matches most of the database and tells the user nothing.
MIN_QUERY_LENGTH = 2
DEFAULT_LIMIT = 5
MAX_LIMIT = 20
MAX_RESOLVE = 20


def escape_like(value: str) -> str:
	"""Neutralise the wildcards in a user-typed LIKE pattern.

	A search for "50%" would otherwise match every row. MariaDB's default escape
	character is a backslash, so the backslash itself has to be doubled first.
	"""
	return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def digits_of(value: str) -> str:
	return "".join(character for character in value if character.isdigit())


def has_contact_keys(doctype: str) -> bool:
	"""True once the Stage-1A patch has created the two derived columns.

	They are Custom Fields, so a site that has not migrated yet does not have
	them, and filtering on a column that is not there is an SQL error rather
	than an empty result. Meta is request-cached, so this costs nothing.
	"""
	meta = frappe.get_meta(doctype)
	return bool(meta.has_field(EMAIL_FIELD) and meta.has_field(PHONE_FIELD))


def first_of(row, candidates) -> str:
	for fieldname in candidates:
		value = frappe.utils.cstr(row.get(fieldname)).strip()
		if value:
			return value
	return ""


def subtitle_of(row, candidates, title: str) -> str:
	"""Up to two distinct values, joined. Never repeats what the title says."""
	parts = []
	for fieldname in candidates:
		value = frappe.utils.cstr(row.get(fieldname)).strip()
		if value and value != title and value not in parts:
			parts.append(value)
		if len(parts) == 2:
			break
	return " · ".join(parts)


def build_or_filters(group, query: str) -> list[list]:
	"""The LIKE clauses for one group.

	The two derived columns are searched as "contains", not as a prefix, so that
	typing the last four digits of a number finds the record. That gives up the
	Stage-1A index -- a leading wildcard cannot use it -- and it is the right
	trade at this scale: the index still earns its keep for the exact-equality
	lookups in `crm.api.duplicates`, which is what it was built for.
	"""
	pattern = "%" + escape_like(query) + "%"
	or_filters = [[fieldname, "like", pattern] for fieldname in group["search"]]

	if group["contact_keys"] and has_contact_keys(group["doctype"]):
		if email := normalize_email(query):
			or_filters.append([EMAIL_FIELD, "like", "%" + escape_like(email) + "%"])
		elif "@" in query:
			# A half-typed address never normalises, and it is exactly what the
			# user has on screen while they type.
			or_filters.append([EMAIL_FIELD, "like", pattern])

		if digits := digits_of(query):
			if len(digits) >= 3:
				or_filters.append([PHONE_FIELD, "like", "%" + digits + "%"])

	return or_filters


def search_group(group, query: str, limit: int) -> list[dict]:
	doctype = group["doctype"]
	if not frappe.has_permission(doctype, "read"):
		return []

	rows = frappe.get_list(
		doctype,
		fields=list(group["fields"]),
		or_filters=build_or_filters(group, query),
		order_by="modified desc",
		limit=limit,
	)
	return [as_result(doctype, row) for row in rows]


def as_result(doctype: str, row) -> dict:
	group = GROUP_BY_DOCTYPE[doctype]
	title = first_of(row, group["title"]) or frappe.utils.cstr(row.get("name"))
	return {
		"doctype": doctype,
		"name": row.get("name"),
		"title": title,
		"subtitle": subtitle_of(row, group["subtitle"], title),
		"reference_doctype": row.get("reference_doctype"),
		"reference_docname": row.get("reference_docname"),
	}


@frappe.whitelist()
def palette_search(query: str = "", limit: int = DEFAULT_LIMIT) -> dict:
	"""Up to `limit` records per group, permission-filtered, for one search box.

	Returns `{"query": str, "groups": [{doctype, label, items: [...]}]}`. Groups
	with no hit are dropped, so the palette never renders an empty header. A
	query shorter than `MIN_QUERY_LENGTH` returns no group at all -- the palette
	shows recents and quick actions instead, which is §2's "Cmd+K is never
	empty".
	"""
	query = frappe.utils.cstr(query).strip()
	limit = max(1, min(frappe.utils.cint(limit) or DEFAULT_LIMIT, MAX_LIMIT))

	if len(query) < MIN_QUERY_LENGTH:
		return {"query": query, "groups": []}

	groups = []
	for group in GROUPS:
		items = search_group(group, query, limit)
		if items:
			groups.append({"doctype": group["doctype"], "label": _(group["label"]), "items": items})

	return {"query": query, "groups": groups}


@frappe.whitelist()
def resolve_records(records: str | list) -> list[dict]:
	"""Fresh titles for `[{doctype, name}]`, dropping anything now unreadable.

	Input order is preserved, because the caller's order is "most recently
	viewed" and the server has no way to recompute it.
	"""
	# The client posts this as a JSON string. A malformed body is a dropped
	# recents list, never a 500: `parse_json` raises on anything that is not
	# JSON, and this endpoint has nothing worth failing over.
	try:
		records = frappe.parse_json(records) or []
	except Exception:
		return []

	if not isinstance(records, list):
		return []

	wanted = []
	for record in records[:MAX_RESOLVE]:
		if not isinstance(record, dict):
			continue
		doctype = frappe.utils.cstr(record.get("doctype"))
		name = frappe.utils.cstr(record.get("name")).strip()
		if doctype in GROUP_BY_DOCTYPE and name and (doctype, name) not in wanted:
			wanted.append((doctype, name))

	if not wanted:
		return []

	found = {}
	for doctype in dict.fromkeys(doctype for doctype, _name in wanted):
		names = [name for dt, name in wanted if dt == doctype]
		if not frappe.has_permission(doctype, "read"):
			continue
		group = GROUP_BY_DOCTYPE[doctype]
		for row in frappe.get_list(
			doctype,
			fields=list(group["fields"]),
			filters={"name": ["in", names]},
			limit=len(names),
		):
			found[(doctype, row.get("name"))] = as_result(doctype, row)

	return [found[key] for key in wanted if key in found]
