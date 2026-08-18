# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""One reusable piece of composer text (master spec item 23).

Ownership is the whole permission model. A snippet belongs to the user who made
it. `shared` promotes it to the agency's library: everybody reads it, only a
manager writes it. The row-level rules live in `crm/api/snippets.py`, wired
through the `permission_query_conditions` and `has_permission` hooks so that a
plain `frappe.get_list("CRM Snippet")` from anywhere is already scoped.

The body is stored exactly as typed, tokens and all. Nothing is rendered here:
merging happens at insert time, on the server, against the record the composer
is open on -- see `crm.api.snippets.render`.
"""

import re

import frappe
from frappe import _
from frappe.model.document import Document

# A shortcut is typed after "/" in a composer, so it has to be one word. The
# same expression is the contract the frontend's trigger relies on: anything it
# cannot type, this doctype does not store.
SHORTCUT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
MAX_SHORTCUT_LENGTH = 40


class CRMSnippet(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		body: DF.TextEditor | None
		enabled: DF.Check
		shared: DF.Check
		shortcut: DF.Data
		title: DF.Data
	# end: auto-generated types

	def validate(self):
		self.normalize_shortcut()
		self.check_shared_is_a_manager_decision()
		self.check_shortcut_is_free()

	def normalize_shortcut(self):
		"""Fold the shortcut to the one spelling a composer can produce."""
		value = (self.shortcut or "").strip().lower()
		value = re.sub(r"\s+", "-", value)
		value = value[:MAX_SHORTCUT_LENGTH]

		if not SHORTCUT_PATTERN.match(value):
			frappe.throw(
				_(
					"A shortcut must start with a letter or a digit and may contain "
					"only letters, digits, hyphens and underscores."
				)
			)

		self.shortcut = value

	def check_shared_is_a_manager_decision(self):
		"""Only a manager publishes to the shared library, or unpublishes from it.

		Checked on the transition, not on the state, so a manager's shared snippet
		survives an ordinary save by its owner -- there is no such save, because
		`has_snippet_permission` gives write on a shared row to managers only.
		"""
		from crm.api.snippets import is_snippet_manager

		was_shared = bool(self.get_doc_before_save().shared) if not self.is_new() else False
		if bool(self.shared) == was_shared:
			return

		if not is_snippet_manager():
			frappe.throw(
				_("Only a sales manager can share a snippet with the team."),
				frappe.PermissionError,
			)

	def check_shortcut_is_free(self):
		"""No two snippets one user can reach may answer to the same shortcut.

		"Reach" is the point: a private snippet may reuse a shortcut somebody
		else's private snippet already has, because neither user ever sees both.
		A shared one collides with everything.
		"""
		clash = frappe.db.sql(
			"""
			select name from `tabCRM Snippet`
			where shortcut = %(shortcut)s
			  and name != %(name)s
			  and (shared = 1 or owner = %(owner)s)
			limit 1
			""",
			{
				"shortcut": self.shortcut,
				"name": self.name or "",
				"owner": self.owner or frappe.session.user,
			},
		)
		if clash:
			frappe.throw(_("The shortcut /{0} is already taken by another snippet.").format(self.shortcut))

	@staticmethod
	def default_list_data():
		columns = [
			{"label": "Title", "type": "Data", "key": "title", "width": "16rem"},
			{"label": "Shortcut", "type": "Data", "key": "shortcut", "width": "10rem"},
			{"label": "Shared", "type": "Check", "key": "shared", "width": "6rem"},
			{"label": "Enabled", "type": "Check", "key": "enabled", "width": "6rem"},
			{"label": "Last Modified", "type": "Datetime", "key": "modified", "width": "8rem"},
		]
		rows = ["name", "title", "shortcut", "shared", "enabled", "body", "owner", "modified"]
		return {"columns": columns, "rows": rows}
