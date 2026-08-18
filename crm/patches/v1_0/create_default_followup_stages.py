"""Seed the three default WhatsApp follow-up stages.

The stages are the shape of the sequence, not its content: each one is left
without a template on purpose. Only the agency can choose which Meta-approved
template a stage sends, and the engine skips any stage whose template is
missing or unapproved. The sequence also stays disabled until a manager turns
it on, so this patch can never cause a message to be sent.
"""

import frappe

DEFAULT_SILENCE_DAYS = 2
DEFAULT_STAGE_COUNT = 3

# Quiet window: no follow-up between 21:00 and 09:00 local time.
DEFAULT_QUIET_START = "21:00:00"
DEFAULT_QUIET_END = "09:00:00"


def execute():
	if not frappe.db.exists("DocType", "CRM Followup Settings"):
		return

	settings = frappe.get_doc("CRM Followup Settings")
	if settings.stages:
		return

	# `frappe.model.create_new.get_new_doc` stamps every Time field with
	# `nowtime()` and ignores the field's own default, so the quiet window has to
	# be seeded here rather than in the doctype JSON.
	settings.quiet_hours_start = DEFAULT_QUIET_START
	settings.quiet_hours_end = DEFAULT_QUIET_END

	for stage_number in range(1, DEFAULT_STAGE_COUNT + 1):
		settings.append(
			"stages",
			{
				"stage_number": stage_number,
				"silence_days": DEFAULT_SILENCE_DAYS,
				"use_ai": 0,
			},
		)

	settings.enabled = 0
	settings.flags.ignore_permissions = True
	settings.save(ignore_permissions=True)
