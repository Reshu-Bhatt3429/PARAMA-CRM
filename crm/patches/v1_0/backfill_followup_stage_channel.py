"""Write `WhatsApp` into the channel of every stage saved before it existed.

Stage 5.1 gave `CRM Followup Stage` a channel. A child row that was saved before
the field existed holds NULL for it, and a doctype default only applies to a row
being created, so those rows would read back with no channel at all.

Nothing DEPENDS on this patch: `crm.api.followup_engine.get_stages` reads an
empty channel as WhatsApp, which is the channel every stage sent on before the
field existed, and that coercion is what the tests assert. The patch exists so
the stored data says what the behaviour is, and so the settings screen shows a
manager the right channel in the dropdown instead of an empty box.

Silent and idempotent: one UPDATE over rows that have no value, no send path, no
notification, and nothing to do on a second run. Downgrade behaviour: the column
is simply ignored, and the sequence runs on WhatsApp exactly as before.
"""

import frappe

DOCTYPE = "CRM Followup Stage"
DEFAULT_CHANNEL = "WhatsApp"


def execute():
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	table = f"tab{DOCTYPE}"
	if not frappe.db.has_column(DOCTYPE, "channel"):
		# The migrate that adds the column has not run yet on this site. Nothing
		# to backfill, and the next migrate brings the patch round again.
		return

	frappe.db.sql(
		f"update `{table}` set channel = %s where channel is null or channel = ''",
		(DEFAULT_CHANNEL,),
	)
	frappe.clear_document_cache("CRM Followup Settings", "CRM Followup Settings")
