# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CRMWorkflowExecutionLog(Document):
	"""What one workflow action did, and the key that stops it doing it twice.

	`execution_key` carries a unique index and is claimed BEFORE the action runs,
	then committed. A retried job, a double-fired hook and a second worker all
	build the same key and collide on the index, so the customer is emailed once.
	A process that dies between the claim and the action leaves a `Claimed` row
	that is never retried: at-most-once, the same direction every outbound path
	in this app takes.

	A Sales Manager may read and create rows but NOT delete them (the
	`CRM Followup Send Log` precedent). A log an operator can quietly delete is
	not a guard -- deleting the row would let the same action run again.
	"""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		action_index: DF.Int
		action_type: DF.Data | None
		event: DF.Data | None
		executed_at: DF.Datetime | None
		execution_key: DF.Data
		reason: DF.SmallText | None
		reference_docname: DF.DynamicLink | None
		reference_doctype: DF.Link | None
		rule: DF.Link | None
		rule_title: DF.Data | None
		status: DF.Literal["Claimed", "Executed", "Skipped-cap", "Skipped-suppressed", "Failed"]
	# end: auto-generated types

	pass
