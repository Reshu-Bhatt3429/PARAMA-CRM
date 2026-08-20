# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

"""The SAC master: what an invoice line says it is selling, and at what rate.

Why this is data and not a constant table
-----------------------------------------
The research behind this module was SINGLE-SOURCE. A hard-coded table of service
accounting codes would be wrong for some agency on the day it shipped and could
not be corrected without a release, so the codes live in an admin-editable
doctype and every row carries a note that says, in the agency's own screen, that
the code has to be checked with their CA. `SEED_ROWS` is a starting point, not an
authority.

The seeder is idempotent by code: a row an administrator edited, renamed the
description of, or disabled is never overwritten by a later migrate. Only a code
that is absent is inserted.
"""

import frappe
from frappe.model.document import Document

VERIFY_NOTE = (
	"Placeholder shipped with the app — verify this code and its rate with your CA "
	"before you issue invoices."
)

# (code, description, tax rate). Deliberately short: six codes a small travel
# agency actually bills on, rather than a copy of a chapter nobody checked.
SEED_ROWS = (
	("996311", "Room or unit accommodation services", 12.0),
	("996601", "Rental services of road vehicles with operator", 18.0),
	("998552", "Reservation services for accommodation, cruises and package tours", 18.0),
	("998555", "Tour operator services", 5.0),
	("998556", "Tourist guide services", 18.0),
	("998559", "Other travel arrangement and related services", 18.0),
)


class CRMSACCode(Document):
	def validate(self):
		self.code = frappe.utils.cstr(self.code or "").strip()
		if not self.code:
			frappe.throw(frappe._("A SAC code is required."))

		# The note is written by the app, not by the administrator, so that it
		# cannot be edited away from a screen that is about to print a tax figure.
		self.verify_note = VERIFY_NOTE


def seed_sac_codes() -> int:
	"""after_migrate: put the placeholder codes on a site that has none.

	Returns how many rows were inserted. Idempotent and non-destructive — an
	existing code is left exactly as the administrator left it.
	"""
	if not frappe.db.exists("DocType", "CRM SAC Code"):
		return 0

	created = 0
	for code, description, rate in SEED_ROWS:
		if frappe.db.exists("CRM SAC Code", code):
			continue
		doc = frappe.new_doc("CRM SAC Code")
		doc.update({"code": code, "description": description, "tax_rate": rate, "enabled": 1})
		doc.insert(ignore_permissions=True)
		created += 1

	return created
