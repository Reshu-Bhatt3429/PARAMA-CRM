# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CRMFollowupStage(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		ai_instruction: DF.SmallText | None
		channel: DF.Literal["WhatsApp", "Email"]
		email_subject_override: DF.Data | None
		email_template: DF.Link | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		silence_days: DF.Int
		stage_number: DF.Int
		template: DF.Data | None
		use_ai: DF.Check
	# end: auto-generated types

	# `channel` is deliberately NOT mandatory. Stage rows saved before Stage 5.1
	# hold no value for it, and a mandatory field would refuse to save the
	# settings on every site that already configured a sequence. An empty channel
	# reads as WhatsApp in `crm.api.followup_engine.get_stages`, and
	# `crm.patches.v1_0.backfill_followup_stage_channel` writes the value in.
	pass
