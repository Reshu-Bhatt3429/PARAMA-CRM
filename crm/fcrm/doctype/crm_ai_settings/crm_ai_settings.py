# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CRMAISettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		api_key: DF.Password | None
		enabled: DF.Check
		max_monthly_requests: DF.Int
		model: DF.Data | None
		provider: DF.Literal["Anthropic", "OpenAI", "OpenRouter"]
		requests_this_month: DF.Int
		usage_month: DF.Data | None
	# end: auto-generated types

	def validate(self):
		if not self.enabled:
			return

		if not self.model:
			frappe.throw(_("Set a model before you enable the AI provider."))

		if not self.get_password("api_key", raise_exception=False):
			frappe.throw(_("Set an API key before you enable the AI provider."))
