# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CRMWhatsAppFollowup(Document):
	"""One follow-up state machine per lead. The engine lives in crm.api.followup_engine."""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		blocked_reason: DF.SmallText | None
		current_stage: DF.Int
		cycle: DF.Int
		last_agency_message: DF.Datetime | None
		last_customer_message: DF.Datetime | None
		lead: DF.Link
		next_due: DF.Datetime | None
		opted_out_at: DF.Datetime | None
		opted_out_source: DF.SmallText | None
		pending_params: DF.LongText | None
		pending_stage: DF.Int
		phone: DF.Data | None
		state: DF.Literal["Active", "Replied", "Opted Out", "Exhausted", "Stopped", "Pending Approval"]
	# end: auto-generated types

	pass
