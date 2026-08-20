"""Store the guardrail defaults that a later doctype change added.

A Single only falls back to its field defaults while it has no stored row at
all. Once anything has been saved, a field added afterwards reads back as
`None`, and `cint(None)` is 0. For these two fields 0 is a real setting -- "no
idle bound" and "unlimited AI requests" -- so a site that migrated the earlier
version would silently run with both guardrails off.

Only fields that were never stored are touched, so a deliberate 0 is kept.
"""

import frappe

DEFAULTS = {
	"CRM Followup Settings": {"ignore_older_than_days": 14},
	"CRM AI Settings": {"max_monthly_requests": 1000},
}


def execute():
	for doctype, fields in DEFAULTS.items():
		if not frappe.db.exists("DocType", doctype):
			continue

		for fieldname, value in fields.items():
			if is_stored(doctype, fieldname):
				continue
			frappe.db.set_single_value(doctype, fieldname, value)

		frappe.clear_document_cache(doctype, doctype)


def is_stored(doctype: str, fieldname: str) -> bool:
	return bool(
		frappe.db.sql(
			"select 1 from tabSingles where doctype = %s and field = %s limit 1",
			(doctype, fieldname),
		)
	)
