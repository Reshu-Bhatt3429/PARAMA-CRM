# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""One workflow rule: when, if, then.

Everything a rule can do is checked HERE, at save time, and never at fire time.
A rule that names a field that does not exist, or an action that has no target,
must be refused by the manager's Save button -- not discovered by a background
job at two in the morning, when the only person who can fix it is asleep and the
only record of it is a Failed log row.

The controller also owns the cache. `crm.workflows` reads its enabled rules out
of one Redis blob so that a save of a hot doctype costs no query at all; the
blob is dropped whenever a rule is inserted, changed or deleted, which is what
makes "cached" safe rather than stale.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document


class CRMWorkflowRule(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from crm.fcrm.doctype.crm_workflow_action.crm_workflow_action import CRMWorkflowAction

		actions: DF.Table[CRMWorkflowAction]
		actions_today: DF.Int
		apply_on: DF.Literal["CRM Lead", "CRM Deal"]
		cap_notified_on: DF.Date | None
		condition_json: DF.LongText | None
		counter_day: DF.Date | None
		daily_action_cap: DF.Int
		enabled: DF.Check
		event: DF.Literal["Record created", "Field changed", "Stage changed"]
		title: DF.Data
		watched_field: DF.Data | None
	# end: auto-generated types

	def validate(self):
		from crm import workflows

		if self.apply_on not in workflows.APPLY_ON:
			frappe.throw(_("A workflow rule runs on a lead or a deal, not on {0}.").format(self.apply_on))

		if self.event not in workflows.EVENTS:
			frappe.throw(_("{0} is not a workflow event.").format(self.event))

		self.validate_watched_field()
		self.validate_conditions()
		self.validate_actions()

	def validate_watched_field(self):
		from crm import workflows

		if self.event != workflows.EVENT_FIELD_CHANGED:
			# The field only means something for one event. Clearing it keeps the
			# cache blob honest: a stale watched field on a stage rule would read
			# like a rule that watches two things.
			self.watched_field = None
			return

		if not self.watched_field:
			frappe.throw(_("A Field changed rule has to say which field it watches."))

		self.field_or_throw(self.watched_field)

	def validate_conditions(self):
		from crm import workflows

		raw = (self.condition_json or "").strip()
		if not raw:
			self.condition_json = None
			return

		try:
			conditions = json.loads(raw)
		except ValueError:
			frappe.throw(_("The condition is not valid JSON."))

		if not isinstance(conditions, list):
			frappe.throw(_("The condition has to be a list of condition rows."))

		for fieldname in workflows.condition_fields(conditions):
			self.field_or_throw(fieldname)

		# Re-serialised, so what is stored is exactly what the engine will read.
		self.condition_json = json.dumps(conditions)

	def validate_actions(self):
		from crm import workflows

		if not self.actions:
			frappe.throw(_("A rule with no action would do nothing. Add at least one."))

		for action in self.actions:
			if action.action_type == workflows.ACTION_EMAIL:
				self.validate_email_action(action)
			elif action.action_type == workflows.ACTION_TASK:
				self.validate_task_action(action)
			elif action.action_type == workflows.ACTION_NOTIFY:
				self.validate_notify_action(action)
			elif action.action_type == workflows.ACTION_UPDATE:
				self.validate_update_action(action)
			else:
				frappe.throw(_("{0} is not a workflow action.").format(action.action_type))

	def validate_email_action(self, action):
		from crm import workflows

		if not action.email_template:
			frappe.throw(_("Row {0}: choose the email template to send.").format(action.idx))

		if action.recipient_mode == workflows.RECIPIENT_SPECIFIC:
			if not action.recipient_address:
				frappe.throw(_("Row {0}: give the address to send to.").format(action.idx))
			from frappe.utils import validate_email_address

			validate_email_address(action.recipient_address, throw=True)

	def validate_task_action(self, action):
		if not action.task_title:
			frappe.throw(_("Row {0}: give the task a title.").format(action.idx))

	def validate_notify_action(self, action):
		from crm import workflows

		if action.notify_mode == workflows.NOTIFY_SPECIFIC and not action.notify_user:
			frappe.throw(_("Row {0}: choose the user to notify.").format(action.idx))
		if action.notify_mode == workflows.NOTIFY_ROLE and not action.notify_role:
			frappe.throw(_("Row {0}: choose the role to notify.").format(action.idx))

	def validate_update_action(self, action):
		from crm import workflows

		if not action.update_field:
			frappe.throw(_("Row {0}: choose the field to update.").format(action.idx))

		if action.update_field in workflows.PROTECTED_FIELDS:
			frappe.throw(
				_("Row {0}: {1} is written by the framework and an automation may not set it.").format(
					action.idx, action.update_field
				)
			)

		self.field_or_throw(action.update_field)

	def field_or_throw(self, fieldname: str) -> None:
		"""Refuse a fieldname that is not a real field of the target doctype."""
		from crm import workflows

		if fieldname in workflows.STANDARD_READABLE_FIELDS:
			return

		if not frappe.get_meta(self.apply_on).get_field(fieldname):
			frappe.throw(_("{0} has no field called {1}.").format(_(self.apply_on), fieldname))

	def on_update(self):
		self.drop_cache()

	def after_insert(self):
		self.drop_cache()

	def on_trash(self):
		self.drop_cache()

	def drop_cache(self):
		from crm import workflows

		workflows.clear_cache()
