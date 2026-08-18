# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CRMOutboundRecipient(Document):
	"""One address inside one outbound job, and the outbox guard for that send.

	This is a standalone doctype rather than a child table on purpose. A child
	row cannot carry a table-wide unique index, and the unique `idempotency_key`
	is the whole mechanism: a claimed key means this address was already
	attempted for this job, so a repeated sweep, a retried worker or a crashed
	job can never produce a second copy for the same person.

	`email_queue` is the correlation back to the framework. "Sent" is set from
	that row's own status, so the CRM never reports delivered for a message the
	queue has not actually sent.
	"""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		address: DF.Data
		channel: DF.Literal["Email", "WhatsApp"]
		claimed_at: DF.Datetime | None
		communication: DF.Link | None
		email_queue: DF.Data | None
		idempotency_key: DF.Data
		in_reply_to: DF.Data | None
		job: DF.Link
		last_error: DF.SmallText | None
		message_id: DF.Data | None
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		replied_at: DF.Datetime | None
		sent_at: DF.Datetime | None
		state: DF.Literal["Pending", "Claimed", "Queued", "Sent", "Failed", "Suppressed", "Cancelled"]
	# end: auto-generated types

	pass
