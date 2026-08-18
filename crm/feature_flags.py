"""The registry of expansion feature flags, and the one way to read them.

Every automation and sweep added by the feature expansion ships behind a named
switch that is OFF by default (master spec C5). Two things make that a registry
rather than a scattering of `frappe.db.get_single_value` calls:

* the flag names live in one place, so the FCRM Settings section, the scheduler
  guards and the tests cannot drift apart; and
* `is_enabled` fails CLOSED. A missing field, an unsaved Single, an unreadable
  settings row -- all of them read as OFF. A flag that failed open would turn a
  configuration problem into an unintended send.

Adding a flag: add one entry to `FLAGS`, add the matching Check field to
`crm/fcrm/doctype/fcrm_settings/fcrm_settings.json` under the Feature Flags
section with `"default": "0"`, and say what it does in the field description.
Both halves are required -- the registry is the contract, the field is the UI.
"""

import frappe

SETTINGS_DOCTYPE = "FCRM Settings"

# fieldname -> what turning it on actually starts doing.
FLAGS = {
	"outbound_engine_enabled": (
		"Lets the scheduler claim and process CRM Outbound Jobs. While this is off "
		"the outbound sweep returns without reading a single job, so nothing is "
		"ever sent through the outbound engine."
	),
	"task_reminders_enabled": (
		"Lets the scheduler remind assignees about tasks that are about to fall due. "
		"While this is off the reminder sweep returns without reading a single task "
		"row, so no notification and no email is ever produced."
	),
}


def is_enabled(flag: str) -> bool:
	"""True only when the flag exists in the registry AND is switched on.

	Never raises. Callers are scheduler entry points and send paths; an exception
	here would take the rest of a queue down with it.
	"""
	if flag not in FLAGS:
		frappe.log_error(f"Unknown feature flag {flag!r}.", "CRM feature flags: unknown flag")
		return False

	try:
		return bool(frappe.db.get_single_value(SETTINGS_DOCTYPE, flag))
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"CRM feature flags: could not read {flag}")
		return False


def all_flags() -> dict[str, bool]:
	"""Current value of every registered flag. For diagnostics and tests."""
	return {flag: is_enabled(flag) for flag in FLAGS}
