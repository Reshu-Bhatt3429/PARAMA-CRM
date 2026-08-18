# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CRMSuppression(Document):
	"""One consent record: this address, on this channel, must not be written to.

	`suppression_key` carries a unique index, so the row is an upsert target
	rather than an append-only log. A reversal clears `active` and keeps the row,
	which is what lets a later audit see that the address was once suppressed and
	who released it.

	Write through `crm.suppression`, never directly: that module owns the address
	normalisation, and a row stored with an un-normalised address is a row no
	send path will ever find.
	"""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		active: DF.Check
		address: DF.Data
		channel: DF.Literal["Email", "WhatsApp"]
		reference_doctype: DF.Link | None
		reference_name: DF.DynamicLink | None
		release_reason: DF.SmallText | None
		released_at: DF.Datetime | None
		released_by: DF.Link | None
		source: DF.SmallText | None
		state: DF.Literal["Opted Out", "Bounced", "Complained"]
		suppressed_at: DF.Datetime | None
		suppression_key: DF.Data
	# end: auto-generated types

	pass
