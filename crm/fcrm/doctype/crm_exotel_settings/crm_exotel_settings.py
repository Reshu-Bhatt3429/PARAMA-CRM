# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import re

import frappe
import requests
from frappe import _
from frappe.model.document import Document

EXOTEL_HOST_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")


def validate_exotel_subdomain(value: str | None) -> str:
	"""Return a normalized Exotel API host and reject arbitrary/inner-network hosts."""
	host = str(value or "").strip().lower().rstrip(".")
	if not EXOTEL_HOST_RE.fullmatch(host) or not (host == "exotel.com" or host.endswith(".exotel.com")):
		frappe.throw(_("Exotel API host must be an exotel.com domain."), frappe.ValidationError)
	return host


class CRMExotelSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		account_sid: DF.Data | None
		api_key: DF.Data | None
		api_token: DF.Password | None
		enabled: DF.Check
		record_call: DF.Check
		subdomain: DF.Data | None
		webhook_verify_token: DF.Password | None
	# end: auto-generated types

	def validate(self):
		self.verify_credentials()

	def verify_credentials(self):
		if self.enabled:
			subdomain = validate_exotel_subdomain(self.subdomain)
			response = requests.get(
				"https://{subdomain}/v1/Accounts/{sid}".format(
					subdomain=subdomain, sid=self.account_sid
				),
				auth=(self.api_key, self.get_password("api_token")),
				timeout=15,
			)
			if response.status_code != 200:
				frappe.throw(
					_(f"Please enter valid exotel Account SID, API key & API token: {response.reason}"),
					title=_("Invalid credentials"),
				)
