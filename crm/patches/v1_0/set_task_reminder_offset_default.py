"""Give the task-reminder offset its intended default of 60 minutes.

Why a patch and not just the field default: `FCRM Settings` is a Single, and a
Single that already exists gets its new Int columns written as `0`, not as the
JSON default. `0` is a legitimate value for this field -- it means "remind at
the due time itself" -- so the reader cannot tell an unset column from a
deliberate zero and must not guess. This patch settles it once, at the moment
the column appears.

Safety: it writes only when the value is falsy AND the feature has never been
switched on. A site that deliberately chose 0 after enabling reminders keeps its
choice; a site that never touched the feature gets the documented default.
Patches run once per site, so a later deliberate 0 is never overwritten.

Downgrade: set the field back to whatever you want. Nothing else reads it except
`crm.reminders.reminder_offset_minutes`.
"""

import frappe

SETTINGS = "FCRM Settings"
DEFAULT_OFFSET_MINUTES = 60


def execute():
	if not frappe.db.exists("DocType", SETTINGS):
		return

	meta = frappe.get_meta(SETTINGS)
	if not meta.get_field("task_reminder_offset_minutes"):
		return

	if frappe.db.get_single_value(SETTINGS, "task_reminders_enabled"):
		return

	if frappe.db.get_single_value(SETTINGS, "task_reminder_offset_minutes"):
		return

	frappe.db.set_single_value(SETTINGS, "task_reminder_offset_minutes", DEFAULT_OFFSET_MINUTES)
