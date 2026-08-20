"""Normalised, indexed email and phone columns on Lead, Deal and Contact.

What this is for
----------------
Three things the expansion needs are all the same lookup: does this address
already exist in the CRM (duplicate warning), who does this reply belong to
(reply matching), and may we write to this person (the suppression ledger).
Today each of them would have to `LIKE` its way across `email`, `mobile_no` and
`phone` in three doctypes, comparing values that were typed by hand and never
agree on case, spacing or country code.

So every record carries two derived columns, written on save and indexed:

* `custom_parama_email_normalized` -- the address, lower-cased.
* `custom_parama_phone_e164` -- the first parsable number, in E.164.

They are derived, never authored. Nothing reads them as the truth of a record;
they exist so an equality lookup can find the row whose real fields hold the
same person.

Why custom fields on all three
------------------------------
Contact is a framework doctype, so a custom field is the only option there
(master spec F9). CRM Lead and CRM Deal get custom fields TOO, rather than new
entries in their own JSON, for two reasons: the three doctypes then share one
field name, so every consumer is a single lookup rather than a per-doctype map;
and the fork keeps a merge-clean `crm_lead.json`. Both are `hidden` and
`read_only` -- §2.9 forbids adding fields the user has to fill in, and these are
not fields a user has any business editing.

Nothing here sends anything. The backfill patch is silent by construction: it
writes two columns and calls no notification, no hook and no send path.
"""

import frappe

from crm.normalization import normalize_email, normalize_phone

EMAIL_FIELD = "custom_parama_email_normalized"
PHONE_FIELD = "custom_parama_phone_e164"

# doctype -> the source fields to read, in priority order. The first value that
# normalises wins; a record with a mobile and a landline keys on the mobile,
# which is the number the agency actually reaches people on.
TARGETS = {
	"CRM Lead": {"email": ("email",), "phone": ("mobile_no", "phone")},
	"CRM Deal": {"email": ("email",), "phone": ("mobile_no", "phone")},
	"Contact": {"email": ("email_id",), "phone": ("mobile_no", "phone")},
}

# Every source field the backfill has to read, per doctype.
SOURCE_FIELDS = {doctype: sorted({*spec["email"], *spec["phone"]}) for doctype, spec in TARGETS.items()}


def compute_keys(doctype: str, source) -> dict:
	"""The two derived values for one record. `source` is a doc or a dict."""
	spec = TARGETS.get(doctype)
	if not spec:
		return {}

	def read(fieldname):
		return source.get(fieldname) if hasattr(source, "get") else getattr(source, fieldname, None)

	email = ""
	for fieldname in spec["email"]:
		email = normalize_email(read(fieldname))
		if email:
			break

	phone = ""
	for fieldname in spec["phone"]:
		phone = normalize_phone(read(fieldname))
		if phone:
			break

	return {EMAIL_FIELD: email or None, PHONE_FIELD: phone or None}


def set_contact_keys(doc, method=None):
	"""`validate` hook for Lead, Deal and Contact. Writes the derived columns.

	Runs in the document's own transaction, so the columns are never out of step
	with the fields they are derived from. Never raises: an unparsable number is
	an empty key, and a record must remain saveable whatever its phone field
	holds.
	"""
	try:
		keys = compute_keys(doc.doctype, doc)
		for fieldname, value in keys.items():
			doc.set(fieldname, value)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"CRM contact keys: could not derive for {doc.doctype}")


def backfill_row(doctype: str, row) -> bool:
	"""Write the derived columns for one existing row. True when it changed.

	`update_modified=False` is load-bearing twice over. It keeps the sweep from
	moving the very column it pages on -- a sweep that touched `modified` would
	keep re-finding its own rows and never finish -- and it keeps a silent data
	migration out of every record's "last modified" line in the UI.
	"""
	keys = compute_keys(doctype, row)
	if not keys:
		return False

	current = {EMAIL_FIELD: row.get(EMAIL_FIELD), PHONE_FIELD: row.get(PHONE_FIELD)}
	if current == keys:
		return False

	frappe.db.set_value(doctype, row.get("name"), keys, update_modified=False)
	return True
