"""Fill the normalised email/phone columns on existing records (spec F7).

Resumable and silent, both by construction.

* **Resumable.** The work runs through `crm.sweeps.run_sweep`, which pages on a
  `(modified, name)` cursor stored outside the transaction and commits each
  batch. A patch that times out, or a container that is restarted mid-migration,
  resumes at the next batch rather than starting the table again. Running the
  patch twice therefore costs the remaining rows, not all of them, and produces
  the same columns either way.
* **Silent.** It calls `frappe.db.set_value(..., update_modified=False)`. No
  document is loaded, so no `validate`, no `on_update`, no notification and no
  send path can run. `modified` is untouched, so the sweep cannot re-find its own
  rows and no record's "last modified" line changes.

Downgrade: the columns can be emptied or dropped; nothing else reads them yet.
Clear the cursors with `crm.sweeps.reset_watermark("contact_keys:<doctype>")`
if the backfill has to run again from the start.
"""

import frappe

from crm.contact_keys import EMAIL_FIELD, PHONE_FIELD, SOURCE_FIELDS, TARGETS, backfill_row
from crm.sweeps import run_sweep

BATCH_SIZE = 500


def execute():
	for doctype in TARGETS:
		if not frappe.db.exists("DocType", doctype):
			continue

		# The patch that creates the columns runs first, but a partially migrated
		# site is exactly the case this has to survive.
		meta = frappe.get_meta(doctype)
		if not (meta.has_field(EMAIL_FIELD) and meta.has_field(PHONE_FIELD)):
			continue

		run_sweep(
			job_name=sweep_name(doctype),
			doctype=doctype,
			handler=lambda row, doctype=doctype: backfill_row(doctype, row),
			fields=[*SOURCE_FIELDS[doctype], EMAIL_FIELD, PHONE_FIELD],
			batch_size=BATCH_SIZE,
		)


def sweep_name(doctype: str) -> str:
	return f"contact_keys:{doctype}"
