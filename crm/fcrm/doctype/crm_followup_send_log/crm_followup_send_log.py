# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CRMFollowupSendLog(Document):
	"""Outbox record of one follow-up send.

	`dedup_key` carries a unique index. The engine inserts this row before it
	creates the WhatsApp Message, so a scheduler run that repeats a stage hits a
	duplicate-entry error instead of sending the customer a second copy.
	"""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		dedup_key: DF.Data
		followup: DF.Link | None
		lead: DF.Link | None
		sent_at: DF.Datetime | None
		stage: DF.Int
		status: DF.Literal["Claimed", "Sent", "Failed"]
		whatsapp_message: DF.Data | None
	# end: auto-generated types

	pass
