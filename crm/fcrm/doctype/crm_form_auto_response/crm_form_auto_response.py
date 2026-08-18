# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CRMFormAutoResponse(Document):
	"""The automatic reply one CRM web form sends to the person who submitted it.

	Per FORM, not per site. Two forms on the same site answer two different
	audiences, and a single site-wide reply would be wrong for at least one of
	them. The document is named after the Web Form, so the row and the form
	cannot drift apart and a second row for the same form cannot exist.

	Nothing here decides whether the reply is actually sent. `crm.api.form`
	checks the suppression ledger, the outgoing Email Account and the
	per-submission idempotency key before a message is rendered.
	"""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		enabled: DF.Check
		message: DF.TextEditor | None
		subject: DF.Data | None
		web_form: DF.Link
	# end: auto-generated types

	pass
