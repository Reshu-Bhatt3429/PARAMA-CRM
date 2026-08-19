# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CRMWorkflowAction(Document):
	"""One "Then" step of a workflow rule.

	A child table rather than four doctypes: the four action types share an
	order, a parent and a daily cap, and the engine executes them as one list.
	The per-type fields are all optional at the schema level and are validated by
	the PARENT (`CRM Workflow Rule.validate`), because only the parent knows
	which doctype the field names must exist on.
	"""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		action_type: DF.Literal["Send email template", "Create task", "Notify user", "Update field"]
		email_template: DF.Link | None
		notify_mode: DF.Literal["Assigned user", "Specific user", "Everyone with a role"]
		notify_role: DF.Link | None
		notify_user: DF.Link | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		recipient_address: DF.Data | None
		recipient_mode: DF.Literal["Record email", "Assigned user", "Specific address"]
		task_due_offset_days: DF.Int
		task_priority: DF.Literal["Low", "Medium", "High"]
		task_title: DF.Data | None
		update_field: DF.Data | None
		update_value: DF.Data | None
	# end: auto-generated types

	pass
