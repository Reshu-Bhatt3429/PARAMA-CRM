"""Create the normalised email/phone columns and their indexes (spec F7).

Idempotent on both halves. `create_custom_fields` skips a field that already
exists, and `frappe.db.add_index` checks `SHOW INDEX` before it alters anything,
so re-running this patch -- which `bench migrate` will do on a fresh site after
an app reinstall -- is a no-op.

Downgrade: drop the four Custom Field rows and the four indexes. No other code
depends on the columns existing; `crm.contact_keys.set_contact_keys` sets an
attribute the framework then ignores, and the readers of these columns ship in
later stages.

This patch writes no record data. The values are filled in by
`backfill_parama_contact_keys`, which is resumable and equally silent.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from crm.contact_keys import EMAIL_FIELD, PHONE_FIELD, TARGETS

# Where the two columns sit in each doctype's form. They are hidden, so this only
# decides column order in the table, but Frappe requires an anchor.
INSERT_AFTER = {
	"CRM Lead": "email",
	"CRM Deal": "email",
	"Contact": "email_id",
}


def execute():
	fields = {}

	for doctype in TARGETS:
		if not frappe.db.exists("DocType", doctype):
			continue

		anchor = INSERT_AFTER[doctype]
		fields[doctype] = [
			{
				"fieldname": EMAIL_FIELD,
				"fieldtype": "Data",
				"label": "Normalized Email",
				"description": "Derived: the record's email address, lower-cased. Written on save by crm.contact_keys. Do not edit.",
				"insert_after": anchor,
				"hidden": 1,
				"read_only": 1,
				"no_copy": 1,
				"allow_on_submit": 0,
			},
			{
				"fieldname": PHONE_FIELD,
				"fieldtype": "Data",
				"label": "Normalized Phone (E.164)",
				"description": "Derived: the record's first parsable phone number in E.164. Written on save by crm.contact_keys. Do not edit.",
				"insert_after": EMAIL_FIELD,
				"hidden": 1,
				"read_only": 1,
				"no_copy": 1,
				"allow_on_submit": 0,
			},
		]

	if not fields:
		return

	create_custom_fields(fields)

	for doctype in fields:
		# One index per column. These are equality lookups -- "which record holds
		# this address" -- so a single-column index on each is exactly right, and
		# a composite would only serve one of the two questions.
		frappe.db.add_index(doctype, [EMAIL_FIELD])
		frappe.db.add_index(doctype, [PHONE_FIELD])
		frappe.clear_cache(doctype=doctype)
