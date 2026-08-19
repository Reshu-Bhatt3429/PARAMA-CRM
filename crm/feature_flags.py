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
	"email_sequences_enabled": (
		"Lets a follow-up stage set to the Email channel build and schedule its "
		"message. While this is off every email stage parks its row with a stated "
		"reason, no outbound job is created, and an inbound email stops nothing -- "
		"so the sequence machine stays exactly as it was before email stages "
		"existed. Delivery ALSO needs 'Outbound engine' above: this flag creates "
		"the job, that one lets the sweep send it."
	),
	"deal_health_enabled": (
		"Lets the nightly sweep work out which open deals need attention (close date "
		"passed, stalled, awaiting a reply) and store the answer on the deal. While "
		"this is off the sweep reads no deal row, no deal carries a Needs attention "
		"chip, and the manager digest says nothing about deal health."
	),
	"workflow_rules_enabled": (
		"Lets a CRM Workflow Rule fire when a lead or a deal is created or changes. "
		"While this is off the engine returns on one cached read and no rule can "
		"act, whatever its own Enabled switch says. Both switches must be on: this "
		"one arms the engine, the rule's own arms the rule. Note that "
		"crm.workflows caches this flag in Redis, so a change made by raw SQL "
		"rather than by saving FCRM Settings needs crm.workflows.clear_cache()."
	),
	"invoices_enabled": (
		"Lets the invoice module be used at all: the Invoices page, the Convert to "
		"invoice action on a deal, and every endpoint in crm.api.invoices. While "
		"this is off each of those endpoints refuses with a permission error and "
		"the tokenised customer link answers exactly like a dead token, so a page "
		"that was hidden cannot be reached by typing its URL."
	),
	"invoice_reminders_enabled": (
		"Lets the hourly sweep chase unpaid invoices: one reminder ladder per "
		"payment-schedule row, on the due date, then after seven days, then after "
		"fourteen. While this is off the sweep reads no invoice row and no ladder "
		"exists. Separate from 'Invoices' above on purpose -- an agency may want "
		"to raise invoices long before it wants the app to chase them. Delivery "
		"ALSO needs 'Outbound engine': this flag creates the job, that one lets "
		"the sweep send it."
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
