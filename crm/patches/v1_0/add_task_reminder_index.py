"""Composite index on CRM Task (due_date, status) for the reminder sweep (spec F5).

The reminder sweep asks one question every tick: which tasks fall due in the
next window and are not already Done or Cancelled. Without this index that is a
full scan of `tabCRM Task`, run on the scheduler's clock for ever.

The column order matters. `due_date` is the selective, range-scanned half and
therefore leads; `status` follows so the Done and Cancelled rows are excluded
inside the same index rather than by reading the rows.

Idempotent: `frappe.db.add_index` checks `SHOW INDEX` first and does nothing
when the index is already there.

Downgrade: `DROP INDEX due_date_status_index ON \\`tabCRM Task\\``. Dropping it
costs query time and nothing else -- no code depends on the index existing.
"""

import frappe

INDEX_NAME = "due_date_status_index"


def execute():
	if not frappe.db.exists("DocType", "CRM Task"):
		return

	frappe.db.add_index("CRM Task", ["due_date", "status"], INDEX_NAME)
