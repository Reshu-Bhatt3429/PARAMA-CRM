# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CRMFollowupSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from crm.fcrm.doctype.crm_followup_stage.crm_followup_stage import CRMFollowupStage

		auto_enroll: DF.Check
		daily_send_cap: DF.Int
		enabled: DF.Check
		ignore_older_than_days: DF.Int
		quiet_hours_end: DF.Time | None
		quiet_hours_start: DF.Time | None
		send_mode: DF.Literal["Auto-send", "Draft for approval"]
		stages: DF.Table[CRMFollowupStage]
		stop_keywords: DF.SmallText | None
	# end: auto-generated types

	def validate(self):
		self.renumber_stages()

	def on_update(self):
		"""Give parked rows another chance once the configuration changes.

		The engine parks a row when its stage cannot be sent -- usually a
		template Meta has not approved. Without this, fixing the template would
		leave every parked row stuck until an agent wrote in the thread again.
		"""
		from crm.api.followup_engine import unpark_blocked_followups

		unpark_blocked_followups()

	def renumber_stages(self):
		"""Stage numbers drive the send order, so keep them a 1..n run in grid order.

		The engine reads stages by their position in the sequence. A hand-edited
		grid could otherwise hold duplicate or missing stage numbers and silently
		skip a stage.
		"""
		for index, stage in enumerate(self.stages or [], start=1):
			stage.stage_number = index
			if frappe.utils.cint(stage.silence_days) < 1:
				frappe.throw(_("Stage {0}: silence days must be at least 1.").format(index))
