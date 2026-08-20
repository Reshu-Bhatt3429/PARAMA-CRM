# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CRMInvoiceStatusLog(Document):
	"""A child row of CRM Invoice. Every rule that governs it lives on the parent.

	Child doctypes in Frappe are saved only through their parent, so there is no
	hook here that a caller could reach on its own. `crm.fcrm.doctype.crm_invoice`
	recomputes, validates and refuses; this class exists so the row has a document
	class of its own.
	"""
