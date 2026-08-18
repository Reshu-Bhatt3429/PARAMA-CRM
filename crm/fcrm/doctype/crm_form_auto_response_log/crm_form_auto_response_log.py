# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CRMFormAutoResponseLog(Document):
	"""One row per web-form submission that reached the auto-response path.

	The row is the idempotency key, and it is inserted BEFORE the message is
	rendered. `submission_key` carries a unique index, so a retried background
	job, a double POST or two workers racing the same submission all collide on
	the index and the second one stops. The pattern is `crm.outbound`'s claim,
	commit, send, one size smaller.

	Every outcome is written back onto the same row, so "why did this visitor get
	no reply" is one lookup and not an inference from an empty mailbox.
	"""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		communication: DF.Link | None
		detail: DF.SmallText | None
		recipient: DF.Data | None
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		sent_at: DF.Datetime | None
		status: DF.Literal[
			"Claimed", "Sent", "Suppressed", "No Recipient", "No Email Account", "Disabled", "Failed"
		]
		submission_key: DF.Data
		web_form: DF.Link | None
	# end: auto-generated types

	pass
