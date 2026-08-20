"""Snippets: reusable composer text with merge tokens (master spec item 23).

Endpoint authorization (master spec §3), stated here and in
`crm/tests/test_snippets.py`:

* `get_snippets` -- any signed-in user with a CRM role. Row-level scope is
  derived SERVER-side by `frappe.get_list`, which applies
  `get_snippet_permission_query_conditions`: own rows plus shared rows, and
  everything for a manager. The caller's `search` argument is a text filter on
  top of that scope, never a substitute for it.
* `render` -- any signed-in user, but only for a record they may READ. The
  record is named by the caller and then checked with `frappe.has_permission`
  (which runs the org-hierarchy `has_permission` hook for CRM Lead and CRM
  Deal), so a caller cannot read another agent's lead by naming it here.

Why the merge is not Jinja
--------------------------
Email templates render with `frappe.render_template`, and that is right for
them: an Email Template is written by an administrator. A snippet is written by
a Sales User. Handing user-authored Jinja to the server-side renderer would let
any agent read the site's Jinja context, so the merge here is a flat token
substitution with a fixed token grammar, resolved from a record the caller has
already been permission-checked against. Values are HTML-escaped on the way in,
because the body they land in is HTML.
"""

import re

import frappe
from frappe import _

DOCTYPE = "CRM Snippet"

MANAGER_ROLES = ("System Manager", "Sales Manager")

# The records a snippet may merge against. Not "any doctype": this endpoint
# would otherwise be a general-purpose field reader for every table the caller
# happens to hold a read permission on.
MERGEABLE_DOCTYPES = ("CRM Lead", "CRM Deal", "Contact", "CRM Organization")

# `{{ token }}`. A token is a fieldname, or `user.<field>` for the person
# composing. Anything else is left alone rather than guessed at.
TOKEN_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*\}\}")

USER_TOKENS = ("full_name", "first_name", "last_name", "email")

# A field the caller can already see on the record is fair game; a stored secret
# is not, whatever the doctype.
BLOCKED_FIELDTYPES = ("Password",)

MAX_SNIPPETS = 50


# --- permissions -----------------------------------------------------------


def is_snippet_manager(user: str | None = None) -> bool:
	"""True for the roles that own the shared library."""
	roles = frappe.get_roles(user or frappe.session.user)
	return any(role in roles for role in MANAGER_ROLES)


def get_snippet_permission_query_conditions(user=None) -> str:
	"""Row-level scope for every list read of CRM Snippet.

	A manager sees the whole library. Everybody else sees their own snippets and
	the shared ones. This is the ONLY place that rule is expressed for lists;
	`has_snippet_permission` expresses the same rule for single documents.
	"""
	user = user or frappe.session.user

	if user == "Administrator" or is_snippet_manager(user):
		return ""

	return f"(`tabCRM Snippet`.`owner` = {frappe.db.escape(user)} or `tabCRM Snippet`.`shared` = 1)"


def has_snippet_permission(doc, ptype, user) -> bool:
	"""Document-level scope. Read: own or shared. Write and delete: own only.

	A manager passes everything, which is what makes the shared library
	manageable. `create` is allowed for everyone -- a new snippet is private
	until somebody with the role shares it, and that transition is guarded in
	`CRMSnippet.check_shared_is_a_manager_decision`.
	"""
	user = user or frappe.session.user

	if user == "Administrator" or is_snippet_manager(user):
		return True

	if ptype == "create":
		return True

	owned = doc.owner == user
	if ptype == "read":
		return owned or bool(doc.shared)

	# write, delete, share, email, print, submit...
	return owned and not doc.shared


# --- the merge -------------------------------------------------------------


def token_values(doctype: str | None, docname: str | None) -> dict:
	"""Every value a token may resolve to, for one record and one user.

	Returns the session user's own fields under `user.*` even when no record is
	named, so a signature snippet works in a composer that is not on a record.
	"""
	values = {}

	user = frappe.db.get_value(
		"User", frappe.session.user, ["full_name", "first_name", "last_name", "email"], as_dict=True
	)
	for field in USER_TOKENS:
		values[f"user.{field}"] = (user or {}).get(field) or ""

	if not doctype or not docname:
		return values

	if doctype not in MERGEABLE_DOCTYPES:
		frappe.throw(_("{0} records cannot be merged into a snippet.").format(doctype))

	# The caller named this record. `check_permission` is what stops them naming
	# somebody else's: it runs the doctype's role permissions AND the
	# org-hierarchy `has_permission` hook, and it RAISES rather than returning a
	# verdict nobody reads.
	doc = frappe.get_doc(doctype, docname)
	doc.check_permission("read")
	meta = frappe.get_meta(doctype)

	values["name"] = doc.name
	for field in meta.fields:
		if field.fieldtype in BLOCKED_FIELDTYPES:
			continue
		value = doc.get(field.fieldname)
		if value is None or isinstance(value, list | dict):
			continue
		values[field.fieldname] = value

	return values


def merge(body: str | None, values: dict) -> str:
	"""Replace every known token in `body`. Unknown tokens are left as typed.

	Leaving an unknown token visible is deliberate: a misspelt `{{ frist_name }}`
	that silently became an empty string would be found by the customer, not by
	the agent. A KNOWN token with no value does become an empty string -- that is
	a record with a blank field, not a mistake in the snippet.
	"""
	if not body:
		return ""

	def replace(match):
		token = match.group(1)
		if token not in values:
			return match.group(0)
		return frappe.utils.escape_html(str(values[token]))

	return TOKEN_PATTERN.sub(replace, body)


# --- endpoints -------------------------------------------------------------


@frappe.whitelist()
def get_snippets(search: str | None = None, limit: int = MAX_SNIPPETS) -> list[dict]:
	"""The snippets this caller may use, most recently changed first.

	Scope is `frappe.get_list`, i.e. the permission query conditions above. The
	`search` argument narrows that scope and can never widen it.
	"""
	try:
		limit = min(int(limit or MAX_SNIPPETS), MAX_SNIPPETS)
	except (TypeError, ValueError):
		limit = MAX_SNIPPETS

	filters = {"enabled": 1}
	or_filters = None
	if search:
		term = f"%{str(search).strip()[:100]}%"
		or_filters = {"title": ["like", term], "shortcut": ["like", term]}

	return frappe.get_list(
		DOCTYPE,
		filters=filters,
		or_filters=or_filters,
		fields=["name", "title", "shortcut", "body", "shared", "owner"],
		order_by="modified desc",
		limit_page_length=limit,
	)


@frappe.whitelist()
def render(snippet: str, doctype: str | None = None, docname: str | None = None) -> dict:
	"""One snippet's body with its tokens resolved for one record.

	The snippet itself is read with `frappe.get_doc` plus an explicit
	`check_permission`, so `has_snippet_permission` decides whether the caller
	may see it. The record is checked separately, in `token_values`.
	"""
	doc = frappe.get_doc(DOCTYPE, snippet)
	doc.check_permission("read")

	if not doc.enabled:
		frappe.throw(_("That snippet is disabled."))

	return {
		"name": doc.name,
		"title": doc.title,
		"shortcut": doc.shortcut,
		"body": merge(doc.body, token_values(doctype, docname)),
	}
