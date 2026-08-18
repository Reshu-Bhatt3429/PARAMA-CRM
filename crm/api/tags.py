# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

"""Tag chips on Lead and Deal (master spec §5, item 2).

Frappe already stores tags. `frappe.desk.doctype.tag.tag.DocTags` writes the
comma-joined `_user_tags` column on the record's own table, creates the `Tag`
master row and keeps one `Tag Link` per pair. None of that is reimplemented
here. This module is a permission-checked front door in front of it, plus the
input validation the core writer does not do.

Why a wrapper at all
--------------------
`frappe.desk.doctype.tag.tag.add_tag` is whitelisted upstream and reaches
`DocTags.add`, which writes `_user_tags` with `frappe.db.set_value` BEFORE
`update_tags` gets as far as `doc.check_permission("write")`. The write is
rolled back with the request, so it is not a data leak, but "check afterwards"
is not a rule this codebase states anywhere, and §3 asks every endpoint to name
its check. It also accepts a tag containing a comma, which is the separator the
column is joined on -- one such tag silently becomes two.

Endpoint authorization (master spec §3)
---------------------------------------
* `get_tags` -- any signed-in user who may READ the named record. Row scope
  comes from `frappe.has_permission(doctype, "read", doc=name)`, which runs the
  controller `has_permission` hooks, so `crm.permissions.org_hierarchy`
  decides for CRM Lead and CRM Deal exactly as it does on the record page.
  Nothing is taken from the request except the doctype and the name, and the
  doctype is checked against a fixed allowlist first.
* `add_tag` / `remove_tag` -- POST only, and the same check with `write`.
  Tagging a record is a change to that record.
* `search_tags` -- the tag vocabulary. `Tag` names are a site-wide vocabulary
  ("VIP", "Honeymoon"), not record data: the row holds a name and an optional
  description and no link to any customer. The caller still has to be able to
  read the doctype they are tagging, so a user with no CRM access gets nothing.

No background job is involved, so the "re-check at execution time" clause of §3
does not apply to this module.
"""

import frappe
from frappe import _
from frappe.desk.doctype.tag.tag import DocTags

# Only these two carry chips in the UI, and an endpoint that would write
# `_user_tags` on an arbitrary doctype is a bigger door than the feature needs.
TAGGABLE_DOCTYPES = ("CRM Lead", "CRM Deal")

MAX_TAG_LENGTH = 60
MAX_TAGS_PER_RECORD = 20
MAX_SEARCH_RESULTS = 50


def escape_like(value: str) -> str:
	"""Neutralise the wildcards in a user-typed LIKE pattern.

	Without this, a search for "50%" matches every tag, and a search for "a_b"
	matches "axb". MariaDB's default escape character is a backslash, so the
	backslash itself has to be doubled first.
	"""
	return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _guard(doctype: str, name: str, ptype: str):
	"""Refuse anything the caller may not do, before any write is attempted.

	A record the caller cannot see and a record that does not exist give the
	same answer on purpose: a different error for each would turn this endpoint
	into an existence oracle over other teams' leads.
	"""
	if doctype not in TAGGABLE_DOCTYPES:
		frappe.throw(_("Tags are not available on {0}.").format(_(doctype)), frappe.PermissionError)

	name = frappe.utils.cstr(name).strip()
	if not name or not frappe.db.exists(doctype, name):
		frappe.throw(_("You are not allowed to tag this record."), frappe.PermissionError)

	if not frappe.has_permission(doctype, ptype, doc=name):
		frappe.throw(_("You are not allowed to tag this record."), frappe.PermissionError)

	return name


def clean_tag(tag) -> str:
	"""The stored form of one tag, or a thrown error.

	A comma is rejected rather than stripped. `_user_tags` is a comma-joined
	string, so "Bali, Honeymoon" saved as one tag comes back as two on the next
	read, and the second one can never be removed by name. Control characters go
	for the same reason: they survive the round trip and render as nothing.
	"""
	tag = frappe.utils.cstr(tag).strip()
	if not tag:
		frappe.throw(_("A tag cannot be empty."), frappe.ValidationError)

	if "," in tag:
		frappe.throw(_("A tag cannot contain a comma."), frappe.ValidationError)

	if any(character in tag for character in "\n\r\t"):
		frappe.throw(_("A tag cannot contain a line break."), frappe.ValidationError)

	if len(tag) > MAX_TAG_LENGTH:
		frappe.throw(
			_("A tag cannot be longer than {0} characters.").format(MAX_TAG_LENGTH),
			frappe.ValidationError,
		)

	return tag


def split_tags(value) -> list[str]:
	"""`_user_tags` as a list. The column is stored as ",a,b", so it needs it."""
	return [tag.strip() for tag in frappe.utils.cstr(value).split(",") if tag.strip()]


def read_tags(doctype: str, name: str) -> list[str]:
	"""The record's tags, without a permission check. Callers do the check."""
	return split_tags(frappe.db.get_value(doctype, name, "_user_tags", ignore=1))


@frappe.whitelist()
def get_tags(doctype: str, name: str) -> list[str]:
	"""Every tag on one record. Requires READ on that record."""
	name = _guard(doctype, name, "read")
	return read_tags(doctype, name)


@frappe.whitelist(methods=["POST"])
def add_tag(doctype: str, name: str, tag: str) -> list[str]:
	"""Add one tag to one record. Requires WRITE on that record.

	Returns the record's whole tag list, so the client never has to guess what
	the server ended up with -- the core writer is case-preserving on add and
	case-insensitive on remove, and it silently ignores a duplicate.
	"""
	name = _guard(doctype, name, "write")
	tag = clean_tag(tag)

	existing = read_tags(doctype, name)
	if tag.casefold() in {t.casefold() for t in existing}:
		return existing

	if len(existing) >= MAX_TAGS_PER_RECORD:
		frappe.throw(
			_("A record can carry at most {0} tags.").format(MAX_TAGS_PER_RECORD),
			frappe.ValidationError,
		)

	DocTags(doctype).add(name, tag)
	return read_tags(doctype, name)


@frappe.whitelist(methods=["POST"])
def remove_tag(doctype: str, name: str, tag: str) -> list[str]:
	"""Remove one tag from one record. Requires WRITE on that record."""
	name = _guard(doctype, name, "write")
	tag = clean_tag(tag)

	DocTags(doctype).remove(name, tag)
	return read_tags(doctype, name)


@frappe.whitelist()
def search_tags(doctype: str, txt: str = "", limit: int = MAX_SEARCH_RESULTS) -> list[str]:
	"""The tag vocabulary, for the "+" chip's picker.

	Gated on doctype-level read of the doctype being tagged: somebody who cannot
	open a single lead has no business enumerating the agency's tag list. The
	rows themselves carry no customer data.
	"""
	if doctype not in TAGGABLE_DOCTYPES:
		frappe.throw(_("Tags are not available on {0}.").format(_(doctype)), frappe.PermissionError)

	if not frappe.has_permission(doctype, "read"):
		frappe.throw(_("You are not allowed to read {0}.").format(_(doctype)), frappe.PermissionError)

	limit = max(1, min(frappe.utils.cint(limit) or MAX_SEARCH_RESULTS, MAX_SEARCH_RESULTS))

	txt = frappe.utils.cstr(txt).strip()
	filters = {}
	if txt:
		filters["name"] = ["like", "%" + escape_like(txt) + "%"]

	# `get_all` is deliberate: the gate above is the check, and `Tag` carries no
	# per-user rule that a second one could apply.
	return frappe.get_all(
		"Tag",
		filters=filters,
		pluck="name",
		order_by="name asc",
		limit=limit,
	)
