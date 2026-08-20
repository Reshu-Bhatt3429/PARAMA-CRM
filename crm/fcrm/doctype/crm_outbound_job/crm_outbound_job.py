# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CRMOutboundJob(Document):
	"""One scheduled unit of outbound work: a Send Later, a batch, a sequence step.

	The state column is the contract. `crm.outbound` is the only writer, and it
	only ever transitions a row while holding a `SELECT ... FOR UPDATE` lock on
	it, so a scheduler sweep and a user's Cancel cannot both act on the same row.

	`idempotency_key` carries a unique index. A caller that retries an enqueue --
	a browser double-click, a re-run job -- gets the existing row back rather than
	a second job that would double-send.
	"""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		cancelled_reason: DF.SmallText | None
		channel: DF.Literal["Email", "WhatsApp"]
		claimed_at: DF.Datetime | None
		completed_at: DF.Datetime | None
		failed_count: DF.Int
		idempotency_key: DF.Data
		job_type: DF.Data
		last_error: DF.SmallText | None
		owner_user: DF.Link | None
		payload: DF.LongText | None
		recipient_count: DF.Int
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		scheduled_at: DF.Datetime | None
		sender_timezone: DF.Data | None
		sent_count: DF.Int
		state: DF.Literal["Draft", "Scheduled", "Claimed", "Queued", "Sent", "Failed", "Cancelled"]
		subject: DF.Data | None
		suppressed_count: DF.Int
	# end: auto-generated types

	pass
