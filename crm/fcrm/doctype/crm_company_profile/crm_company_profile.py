# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

"""The agency's own details: who the invoice says it is from.

A Single, because there is one agency. It is read on every invoice save, every
PDF render and every quote, so `crm.invoicing.get_company_profile` reads it
through the document cache rather than the table.

The two fields that decide money
--------------------------------
`state_code` decides CGST+SGST versus IGST on every invoice, and `gstin` is
printed on the face of one. They are checked against EACH OTHER on save, because
the first two characters of a GSTIN are the state code: a profile where they
disagree is certainly wrong in one of them, and it is far cheaper to say so here
than to reissue a quarter of invoices later.
"""

import frappe
from frappe.model.document import Document

from crm import invoicing


class CRMCompanyProfile(Document):
	def validate(self):
		self.state_code = invoicing.validate_state_code(self.state_code)
		self.gstin = invoicing.validate_gstin(self.gstin, self.state_code)
		self.invoice_number_prefix = (
			frappe.utils.cstr(self.invoice_number_prefix or "").strip() or invoicing.DEFAULT_NUMBER_PREFIX
		)
		self.upi_vpa = frappe.utils.cstr(self.upi_vpa or "").strip()

		if not frappe.utils.flt(self.einvoice_threshold):
			self.einvoice_threshold = invoicing.EINVOICE_THRESHOLD_RUPEES

		# Refuse a prefix that cannot produce a legal number, at the moment it is
		# typed rather than at the moment an invoice is issued. The serial and the
		# financial year add `25-26/0001` -- ten characters -- to whatever is here.
		probe = f"{self.invoice_number_prefix}{invoicing.financial_year_label()}/{'9' * invoicing.SERIAL_WIDTH}"
		invoicing.validate_number(probe)

		self.einvoice_note = invoicing.einvoice_note_for(
			frappe.utils.flt(self.einvoice_threshold) or invoicing.EINVOICE_THRESHOLD_RUPEES
		)
