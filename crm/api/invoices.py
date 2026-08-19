# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

"""The invoice endpoints: convert, issue, collect, void, share.

What is here and what is not
----------------------------
No arithmetic. `crm.invoicing` owns every figure and
`crm.fcrm.doctype.crm_invoice.crm_invoice` owns every refusal. This module is
the request layer: it checks who is asking, reads the record they named, calls
the thing they asked for, and shapes the answer. When a rule looks like it is
implemented twice, it is not -- what is here is the permission check, and what
is there is the rule.

Endpoint authorization (master spec §3), stated here and asserted in
`crm/tests/test_invoices.py`:

* Every endpoint below first calls `require_module()`, which refuses while
  `invoices_enabled` is off. A flag that only hid a sidebar entry would not be a
  flag.
* `convert_deal` -- `write` on the named CRM Deal (an invoice is raised on
  behalf of that customer) AND `create` on CRM Invoice. The deal name is the
  only scope input; the customer block is read off the deal, never off the
  request.
* `get_invoice`, `get_next_number`, `get_tiles` -- `read`. Row scope comes from
  `CRM Invoice.deal` through the org-hierarchy conditions the doctype registers
  (`crm.fcrm.doctype.crm_invoice.crm_invoice.get_invoice_permission_query_conditions`),
  so a Sales User sees exactly the invoices of the deals they can already see.
  `get_tiles` aggregates over `frappe.get_list`, which applies those conditions,
  never over a raw query.
* `finalize`, `record_payment`, `set_reminders_paused` -- `write` on the invoice,
  which the same hook resolves to `write` on its deal.
* `void_invoice` -- `write` PLUS a manager role, checked twice: here, and again
  inside `CRMInvoice.void`.
* `download_invoice`, `share_invoice`, `send_invoice_email`,
  `send_invoice_on_whatsapp` -- `read` and `print`; the two send endpoints also
  need `write`, because an outbound message writes to the record's timeline. The
  recipient address is read off the invoice or the deal and NEVER off the
  request.
* `view` -- **Guest**, deliberately, exactly like the quote route. The token is
  the authorization: 128 bits, single-purpose, expiring, revocable, bound to one
  file. An unknown, a revoked and an expired token get the same answer.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from crm import document_links, invoicing
from crm.feature_flags import is_enabled

DOCTYPE = invoicing.INVOICE_DOCTYPE
DEAL_DOCTYPE = invoicing.DEAL_DOCTYPE
PRINT_FORMAT = "GST Invoice A4"
PURPOSE = "Invoice"

FLAG_INVOICES = "invoices_enabled"
FLAG_INVOICE_REMINDERS = "invoice_reminders_enabled"

# How long a tokenised invoice link stays readable. Longer than a quote's: an
# invoice is a document the customer may want to open again when they pay the
# balance, which on a travel booking is weeks after the deposit.
LINK_TTL_HOURS = 24 * 60

MAX_REASON_LENGTH = 500


# --- gates -----------------------------------------------------------------


def require_module() -> None:
	"""Refuse every endpoint in this module while the feature flag is off.

	Master spec C5: a default-OFF flag. Hiding the sidebar entry is not enough --
	the endpoints are reachable by URL, and a module that is switched off must not
	be able to issue a tax document.
	"""
	if not is_enabled(FLAG_INVOICES):
		frappe.throw(
			_("The invoice module is switched off. A manager can turn it on in Settings."),
			frappe.PermissionError,
		)


def get_invoice_doc(name: str, permtype: str = "read"):
	"""Read one invoice, or refuse. Every endpoint that names one starts here."""
	if not frappe.db.exists(DOCTYPE, name):
		# A missing invoice and a forbidden one get the same answer, so the
		# endpoint cannot be used to find out which invoices exist.
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	if not frappe.has_permission(DOCTYPE, permtype, name):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	return frappe.get_doc(DOCTYPE, name)


def get_deal_doc(deal: str, permtype: str = "read"):
	if not frappe.db.exists(DEAL_DOCTYPE, deal):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	if not frappe.has_permission(DEAL_DOCTYPE, permtype, deal):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	return frappe.get_doc(DEAL_DOCTYPE, deal)


def require_manager() -> None:
	from crm.fcrm.doctype.crm_invoice.crm_invoice import is_manager

	if not is_manager():
		frappe.throw(_("Only a manager can do that."), frappe.PermissionError)


# --- conversion ------------------------------------------------------------


def customer_snapshot(deal) -> dict:
	"""Who the invoice is for, copied off the deal at the moment it is raised.

	A snapshot rather than a live read. The invoice names a person at an address
	on a date; a contact who later moves must not silently rewrite a document that
	has already been issued.
	"""
	name = frappe.utils.cstr(deal.get("lead_name") or "").strip()
	if not name:
		name = " ".join(
			part
			for part in (
				frappe.utils.cstr(deal.get("first_name") or "").strip(),
				frappe.utils.cstr(deal.get("last_name") or "").strip(),
			)
			if part
		)
	organization = frappe.utils.cstr(deal.get("organization") or deal.get("organization_name") or "").strip()

	address = ""
	state = ""
	if organization and frappe.db.exists("CRM Organization", organization):
		fields = frappe.db.get_value(
			"CRM Organization", organization, ["address", "territory"], as_dict=True
		)
		state = frappe.utils.cstr((fields or {}).get("territory") or "")
		address_name = frappe.utils.cstr((fields or {}).get("address") or "")
		if address_name and frappe.db.exists("Address", address_name):
			row = frappe.db.get_value(
				"Address", address_name, ["address_line1", "address_line2", "city", "state", "pincode"], as_dict=True
			)
			address = "\n".join(
				line for line in
				(row.address_line1, row.address_line2, row.city, row.state, row.pincode)
				if line
			)
			state = frappe.utils.cstr(row.state or state)

	return {
		"customer_name": organization or name,
		"customer_address": address,
		"customer_state": state,
		"customer_email": frappe.utils.cstr(deal.get("email") or ""),
		"customer_phone": frappe.utils.cstr(deal.get("mobile_no") or deal.get("phone") or ""),
	}


def items_from_deal(deal, default_sac, default_rate) -> list[dict]:
	"""The deal's product rows, as invoice lines.

	Summed from the PRODUCT ROWS, never from `CRM Deal.total`. That field is
	maintained by a client script, so a deal created by the API, an import or the
	demo seeder carries products worth lakhs and a stored total of zero -- the
	Stage 3A finding the design note names.

	The line value is the NET amount where the deal carries one, because a
	discount the agent negotiated is part of what the customer is being billed.
	"""
	from crm.api.quote import row_net_amount

	rows = []
	for row in deal.get("products") or []:
		qty = flt(row.get("qty")) or 1
		net = flt(row_net_amount(row))
		rows.append(
			{
				"description": frappe.utils.cstr(
					row.get("product_name") or row.get("product_code") or _("Travel services")
				),
				"sac": default_sac,
				"qty": qty,
				# Rate back-computed from the net amount so the printed line adds up
				# to what the customer agreed, discount included.
				"rate": invoicing.round_paise(net / qty) if qty else net,
				"tax_rate": default_rate,
			}
		)
	return rows


def default_sac_and_rate() -> tuple[str, float]:
	"""The SAC a new line starts on, and the rate that code carries."""
	profile = invoicing.get_company_profile()
	sac = frappe.utils.cstr(profile.get("default_sac") or "")
	if sac and frappe.db.exists(invoicing.SAC_DOCTYPE, sac):
		return sac, flt(frappe.db.get_value(invoicing.SAC_DOCTYPE, sac, "tax_rate"))
	return "", 18.0


@frappe.whitelist(methods=["POST"])
def convert_deal(deal: str) -> dict:
	"""One click on the deal: a Draft invoice with its items, customer and mode.

	Nothing is issued. The draft may be edited and renumbered freely; `finalize`
	is the step that allocates a number and freezes the document.
	"""
	require_module()
	# An invoice is raised on behalf of this customer, so it needs write access to
	# the deal, not merely the right to look at it. Same rule as the itinerary.
	deal_doc = get_deal_doc(deal, "write")
	if not frappe.has_permission(DOCTYPE, "create"):
		frappe.throw(_("Not permitted to create an invoice."), frappe.PermissionError)

	profile = invoicing.get_company_profile()
	sac, rate = default_sac_and_rate()
	snapshot = customer_snapshot(deal_doc)

	doc = frappe.new_doc(DOCTYPE)
	doc.update(
		{
			"deal": deal_doc.name,
			"status": invoicing.STATUS_DRAFT,
			"invoice_date": frappe.utils.nowdate(),
			"due_date": frappe.utils.add_days(frappe.utils.nowdate(), 7),
			"mode": invoicing.MODE_TOUR_PACKAGE
			if cint(profile.get("tour_package_mode_default"))
			else invoicing.MODE_COMMISSION,
			"currency": "INR",
			"terms": frappe.utils.cstr(profile.get("terms_default") or ""),
			**snapshot,
		}
	)

	for row in items_from_deal(deal_doc, sac, rate):
		doc.append("items", row)

	doc.insert()
	return read_invoice(doc)


# --- reading ---------------------------------------------------------------


def tour_package_statement(doc) -> str:
	"""The prescribed statement, on a tour-package invoice and nowhere else."""
	return invoicing.TOUR_PACKAGE_STATEMENT if doc.get("mode") == invoicing.MODE_TOUR_PACKAGE else ""


def read_invoice(doc) -> dict:
	"""One invoice as the UI and the PDF both read it.

	Overdue, the remaining balance, the UPI URI and the Rule 47 warning are all
	COMPUTED here rather than stored, so they can never be a day or a payment out
	of date.
	"""
	profile = invoicing.get_company_profile()
	remaining = invoicing.remaining_amount(doc.get("grand_total"), doc.get("payments"))

	return {
		"name": doc.name,
		"invoice_number": doc.get("invoice_number"),
		"status": doc.get("status"),
		"is_overdue": invoicing.is_overdue(doc),
		"is_locked": bool(doc.get("number_locked_at")),
		"deal": doc.get("deal"),
		"invoice_date": doc.get("invoice_date"),
		"due_date": doc.get("due_date"),
		"service_date": doc.get("service_date"),
		"mode": doc.get("mode"),
		"reverse_charge": cint(doc.get("reverse_charge")),
		"currency": doc.get("currency") or "INR",
		"customer": {
			"name": doc.get("customer_name"),
			"address": doc.get("customer_address"),
			"state": doc.get("customer_state"),
			"state_code": doc.get("customer_state_code"),
			"gstin": doc.get("customer_gstin"),
			"email": doc.get("customer_email"),
			"phone": doc.get("customer_phone"),
		},
		"place_of_supply": doc.get("place_of_supply"),
		"intra_state": cint(doc.get("intra_state")),
		"items": [
			{
				"name": row.name,
				"description": row.description,
				"sac": row.sac,
				"qty": flt(row.qty),
				"rate": flt(row.rate),
				"tax_rate": flt(row.tax_rate),
				"amount": flt(row.amount),
			}
			for row in doc.get("items") or []
		],
		"totals": {
			"taxable_total": flt(doc.get("taxable_total")),
			"cgst_amount": flt(doc.get("cgst_amount")),
			"sgst_amount": flt(doc.get("sgst_amount")),
			"igst_amount": flt(doc.get("igst_amount")),
			"tax_total": flt(doc.get("tax_total")),
			"rounding_adjustment": flt(doc.get("rounding_adjustment")),
			"grand_total": flt(doc.get("grand_total")),
			"paid_total": flt(doc.get("paid_total")),
			"outstanding_amount": remaining,
		},
		"payments": [
			{
				"name": row.name,
				"payment_date": row.payment_date,
				"amount": flt(row.amount),
				"mode": row.mode,
				"reference": row.reference,
				"note": row.note,
				"schedule_row": row.schedule_row,
				"recorded_by": row.recorded_by,
			}
			for row in doc.get("payments") or []
		],
		"payment_schedule": [
			{
				"name": row.name,
				"label": row.label,
				"due_date": row.due_date,
				"amount": flt(row.amount),
				"settled": cint(row.settled),
				"reminders_paused": cint(row.reminders_paused),
			}
			for row in doc.get("payment_schedule") or []
		],
		"status_log": [
			{
				"changed_at": row.changed_at,
				"from_status": row.from_status,
				"to_status": row.to_status,
				"changed_by": row.changed_by,
				"note": row.note,
			}
			for row in doc.get("status_log") or []
		],
		"reminders_paused": cint(doc.get("reminders_paused")),
		"reminders_enabled": is_enabled(FLAG_INVOICE_REMINDERS),
		"tour_package_statement": tour_package_statement(doc),
		"rule_47_warning": invoicing.rule_47_warning(doc),
		"einvoice_note": invoicing.einvoice_note(),
		"upi_uri": invoicing.upi_uri(profile.get("upi_vpa"), remaining),
		"terms": doc.get("terms"),
		"void_reason": doc.get("void_reason"),
	}


@frappe.whitelist()
def get_invoice(invoice: str) -> dict:
	require_module()
	return read_invoice(get_invoice_doc(invoice, "read"))


@frappe.whitelist()
def get_invoices_for_deal(deal: str) -> list[dict]:
	"""Every invoice raised on one deal, newest first. For the deal sidebar."""
	require_module()
	get_deal_doc(deal, "read")
	return frappe.get_list(
		DOCTYPE,
		filters={"deal": deal},
		fields=["name", "invoice_number", "status", "invoice_date", "due_date", "grand_total", "outstanding_amount"],
		order_by="creation desc",
		limit_page_length=50,
	)


@frappe.whitelist()
def get_next_number() -> dict:
	"""What the next invoice in this financial year would be called.

	Explicitly a PREVIEW. Two agents drafting at the same time both see the same
	string; `crm.invoicing.allocate_number` and the unique index decide which of
	them keeps it.
	"""
	require_module()
	if not frappe.has_permission(DOCTYPE, "create"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	return {
		"next_number": invoicing.next_number(),
		"financial_year": invoicing.financial_year_label(),
		"prefix": invoicing.number_prefix(),
		"is_preview": True,
	}


# --- issuing ---------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def finalize(invoice: str) -> dict:
	"""Issue the invoice: refuse, allocate, lock, mark Sent, log the move.

	The Rule 47 warning comes back as a `msgprint` as well as in the payload,
	because it is the one thing on this path a user must SEE and may still ignore.
	"""
	require_module()
	doc = get_invoice_doc(invoice, "write")

	result = doc.finalize()

	if result.get("blockers"):
		frappe.throw(
			"<br>".join(frappe.utils.escape_html(line) for line in result["blockers"]),
			title=_("This invoice cannot be issued yet"),
		)

	if result.get("warning"):
		frappe.msgprint(result["warning"], title=_("Issued late"), indicator="orange")

	result["invoice"] = read_invoice(frappe.get_doc(DOCTYPE, doc.name))
	return result


@frappe.whitelist(methods=["POST"])
def record_payment(
	invoice: str,
	amount: float | str | None = None,
	mode: str = "UPI",
	reference: str | None = None,
	note: str | None = None,
	schedule_row: str | None = None,
	send_thank_you: int | str = 0,
) -> dict:
	"""Record one payment. Defaults to the remaining balance; over-payment refused.

	The thank-you email is OPTIONAL and off by default. It goes through
	`crm.api.email.send_email`, so it is suppression-checked like every other send
	in this app, and a refusal to send never undoes the payment that was recorded.
	"""
	require_module()
	doc = get_invoice_doc(invoice, "write")

	doc.record_payment(
		amount=amount,
		mode=mode,
		reference=reference or "",
		note=note or "",
		schedule_row=schedule_row,
	)

	payload = read_invoice(frappe.get_doc(DOCTYPE, doc.name))
	payload["thank_you"] = send_thank_you_email(doc) if cint(send_thank_you) else {"sent": False}
	return payload


def send_thank_you_email(doc) -> dict:
	"""The optional "payment received" note. Never raises into the payment path."""
	address = frappe.utils.cstr(doc.get("customer_email") or "").strip()
	if not address:
		return {"sent": False, "reason": "no_address"}

	from crm.api.email import send_email

	remaining = invoicing.remaining_amount(doc.get("grand_total"), doc.get("payments"))
	currency = doc.get("currency") or "INR"
	body = "<br>".join(
		[
			_("Thank you — we have received your payment."),
			_("Invoice {0}: {1} paid, {2} outstanding.").format(
				doc.get("invoice_number") or doc.name,
				frappe.utils.fmt_money(flt(doc.get("paid_total")), currency=currency),
				frappe.utils.fmt_money(remaining, currency=currency),
			),
		]
	)

	try:
		send_email(
			doctype=DOCTYPE,
			name=doc.name,
			recipients=address,
			subject=_("Payment received for invoice {0}").format(doc.get("invoice_number") or doc.name),
			content=body,
		)
	except Exception as error:
		frappe.log_error(frappe.get_traceback(), "CRM Invoice: thank-you email failed")
		return {"sent": False, "reason": "send_failed", "error": frappe.utils.cstr(error)}

	return {"sent": True, "to": address}


@frappe.whitelist(methods=["POST"])
def void_invoice(invoice: str, reason: str) -> dict:
	"""The terminal negative state. Managers only, and it needs a reason."""
	require_module()
	require_manager()
	doc = get_invoice_doc(invoice, "write")
	doc.void(reason)
	return read_invoice(frappe.get_doc(DOCTYPE, doc.name))


@frappe.whitelist(methods=["POST"])
def set_reminders_paused(invoice: str, paused: int | str = 1) -> dict:
	"""Stop or restart every reminder ladder on one invoice."""
	require_module()
	doc = get_invoice_doc(invoice, "write")
	doc.db_set("reminders_paused", 1 if cint(paused) else 0)
	return {"invoice": doc.name, "reminders_paused": 1 if cint(paused) else 0}


# --- the tiles -------------------------------------------------------------


def permitted_invoices(fields: list[str]) -> list[dict]:
	"""Every invoice the caller may read, through the permission-aware reader.

	`frappe.get_list` applies the doctype's permission query conditions, which for
	`CRM Invoice` resolve to the deal's org hierarchy. A raw query or the query
	builder would return the whole table -- master spec §3 forbids it, and the
	tiles are exactly the place where a leak would be invisible.
	"""
	return frappe.get_list(
		DOCTYPE,
		filters={"status": ["!=", invoicing.STATUS_VOID]},
		fields=fields,
		limit_page_length=0,
	)


@frappe.whitelist()
def get_tiles() -> dict:
	"""Outstanding, Overdue and Collected this month, scoped to the caller.

	Void invoices are excluded from all three, which is what "terminal negative
	state" means: the number stays on the record and the money never appears in a
	revenue figure again.
	"""
	require_module()
	if not frappe.has_permission(DOCTYPE, "read"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	today = frappe.utils.nowdate()
	rows = permitted_invoices(["name", "status", "due_date", "outstanding_amount"])

	outstanding = 0.0
	overdue = 0.0
	overdue_count = 0
	open_count = 0

	for row in rows:
		if row.get("status") not in invoicing.OPEN_STATUSES:
			continue
		amount = flt(row.get("outstanding_amount"))
		outstanding += amount
		open_count += 1
		if row.get("due_date") and frappe.utils.getdate(row["due_date"]) < frappe.utils.getdate(today):
			overdue += amount
			overdue_count += 1

	collected, collected_count = collected_this_month([row["name"] for row in rows])
	currency = frappe.db.get_single_value("FCRM Settings", "currency") or "INR"

	return {
		"currency": currency,
		"outstanding": {
			"label": _("Outstanding"),
			"value": invoicing.round_paise(outstanding),
			"count": open_count,
		},
		"overdue": {"label": _("Overdue"), "value": invoicing.round_paise(overdue), "count": overdue_count},
		"collected": {
			"label": _("Collected this month"),
			"value": invoicing.round_paise(collected),
			"count": collected_count,
			"period": frappe.utils.formatdate(frappe.utils.get_first_day(today), "MMMM yyyy"),
		},
	}


def collected_this_month(invoice_names: list[str]) -> tuple[float, int]:
	"""Payments recorded this calendar month, on the invoices the caller may read.

	Corrections count, because they are negative rows: money that came back off
	an invoice this month has not been collected this month.
	"""
	if not invoice_names:
		return 0.0, 0

	start = frappe.utils.get_first_day(frappe.utils.nowdate())
	end = frappe.utils.add_days(frappe.utils.get_last_day(start), 1)

	# A list of conditions, not a dict: a dict cannot hold two bounds on the same
	# column -- the second key silently replaces the first, and the tile would then
	# report every payment ever recorded as this month's.
	rows = frappe.get_all(
		"CRM Invoice Payment",
		filters=[
			["parenttype", "=", DOCTYPE],
			["parent", "in", invoice_names],
			["payment_date", ">=", start],
			["payment_date", "<", end],
		],
		fields=["amount", "payment_date"],
		limit_page_length=0,
	)

	total = 0.0
	for row in rows:
		total += flt(row.get("amount"))

	return total, len(rows)


# --- the PDF ---------------------------------------------------------------


def install_print_format():
	"""Create or refresh the A4 GST invoice format from the app's template.

	Same contract as the quote's: the HTML lives in a file so it is reviewable in
	git, and the Print Format row is rewritten only when the file actually
	changed, so a migrate on an unchanged app does not churn the version history
	and an administrator's own edit survives.
	"""
	from crm.api.itinerary import same_value

	if not frappe.db.exists("DocType", DOCTYPE):
		return

	path = frappe.get_app_path("crm", "templates", "print_formats", "gst_invoice_a4.html")
	try:
		with open(path) as template:
			html = template.read()
	except OSError:
		frappe.log_error(f"missing template: {path}", "CRM Invoice: print format not installed")
		return

	values = {
		"doc_type": DOCTYPE,
		"module": "FCRM",
		"standard": "No",
		"custom_format": 1,
		"print_format_type": "Jinja",
		"disabled": 0,
		"html": html,
		"margin_top": 12,
		"margin_bottom": 14,
		"margin_left": 10,
		"margin_right": 10,
		"font_size": 12,
		"page_number": "Bottom Center",
	}

	if frappe.db.exists("Print Format", PRINT_FORMAT):
		existing = frappe.get_doc("Print Format", PRINT_FORMAT)
		if all(same_value(existing.get(key), value) for key, value in values.items()):
			return
		existing.update(values)
		existing.flags.ignore_permissions = True
		existing.save(ignore_permissions=True)
		return

	doc = frappe.get_doc({"doctype": "Print Format", "name": PRINT_FORMAT, **values})
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)


def agency_block() -> dict:
	"""The supplier half of Rule 46, off Company Profile."""
	profile = invoicing.get_company_profile()
	return {
		"legal_name": frappe.utils.cstr(profile.get("legal_name") or ""),
		"trade_name": frappe.utils.cstr(profile.get("trade_name") or ""),
		"address": frappe.utils.cstr(profile.get("address") or ""),
		"state": frappe.utils.cstr(profile.get("state") or ""),
		"state_code": frappe.utils.cstr(profile.get("state_code") or ""),
		"gstin": frappe.utils.cstr(profile.get("gstin") or ""),
		"phone": frappe.utils.cstr(profile.get("phone") or ""),
		"email": frappe.utils.cstr(profile.get("email") or ""),
		"upi_vpa": frappe.utils.cstr(profile.get("upi_vpa") or ""),
	}


def decorate(doc):
	"""Put everything the print format reads onto the document, in memory.

	Nothing is saved. The QR is generated at RENDER time from the balance that is
	outstanding right now, so a customer who re-downloads the PDF after paying the
	deposit is asked for the balance rather than for the whole invoice again.
	"""
	agency = agency_block()
	remaining = invoicing.remaining_amount(doc.get("grand_total"), doc.get("payments"))
	currency = doc.get("currency") or "INR"

	def money(value):
		return frappe.utils.fmt_money(flt(value), currency=currency)

	doc.inv_agency = agency
	doc.inv_meta = {
		"number": doc.get("invoice_number") or _("DRAFT — not yet issued"),
		"date": frappe.utils.formatdate(doc.get("invoice_date"), "d MMM yyyy") if doc.get("invoice_date") else "",
		"due_date": frappe.utils.formatdate(doc.get("due_date"), "d MMM yyyy") if doc.get("due_date") else "",
		"service_date": frappe.utils.formatdate(doc.get("service_date"), "d MMM yyyy")
		if doc.get("service_date")
		else "",
		"place_of_supply": frappe.utils.cstr(doc.get("place_of_supply") or ""),
		"reverse_charge": _("Yes") if cint(doc.get("reverse_charge")) else _("No"),
		"is_void": doc.get("status") == invoicing.STATUS_VOID,
		"status": doc.get("status"),
	}
	doc.inv_customer = {
		"name": frappe.utils.cstr(doc.get("customer_name") or ""),
		"address": frappe.utils.cstr(doc.get("customer_address") or ""),
		"state": frappe.utils.cstr(doc.get("customer_state") or ""),
		"state_code": frappe.utils.cstr(doc.get("customer_state_code") or ""),
		"gstin": frappe.utils.cstr(doc.get("customer_gstin") or ""),
	}
	doc.inv_rows = [
		{
			"description": frappe.utils.cstr(row.description or ""),
			"sac": frappe.utils.cstr(row.sac or ""),
			"qty": frappe.utils.fmt_money(flt(row.qty)) if flt(row.qty) != cint(row.qty) else str(cint(row.qty)),
			"rate": money(row.rate),
			"tax_rate": f"{flt(invoicing.line_rate({'tax_rate': row.tax_rate}, doc.get('mode'))):g}%",
			"amount": money(row.amount),
		}
		for row in doc.get("items") or []
	]
	doc.inv_totals = {
		"taxable": money(doc.get("taxable_total")),
		"cgst": money(doc.get("cgst_amount")),
		"sgst": money(doc.get("sgst_amount")),
		"igst": money(doc.get("igst_amount")),
		"intra_state": cint(doc.get("intra_state")),
		"rounding": money(doc.get("rounding_adjustment")),
		"has_rounding": flt(doc.get("rounding_adjustment")) != 0,
		"grand_total": money(doc.get("grand_total")),
		"paid": money(doc.get("paid_total")),
		"outstanding": money(remaining),
		"has_payments": bool(doc.get("payments")),
	}
	doc.inv_schedule = [
		{
			"label": frappe.utils.cstr(row.label or ""),
			"due_date": frappe.utils.formatdate(row.due_date, "d MMM yyyy") if row.due_date else "",
			"amount": money(row.amount),
			"settled": _("Paid") if cint(row.settled) else "",
		}
		for row in doc.get("payment_schedule") or []
	]
	doc.inv_statement = tour_package_statement(doc)
	doc.inv_terms = [
		line.strip() for line in frappe.utils.cstr(doc.get("terms") or "").splitlines() if line.strip()
	]
	doc.inv_qr = invoicing.upi_qr_data_uri(agency.get("upi_vpa"), remaining)
	doc.inv_qr_caption = _("Scan to pay {0}").format(money(remaining)) if doc.inv_qr else ""
	return doc


def render(doc) -> bytes:
	document_links.ensure_print_format(PRINT_FORMAT, install_print_format)
	return document_links.render_print_pdf(DOCTYPE, doc.name, PRINT_FORMAT, doc=decorate(doc))


def file_stem(doc) -> str:
	number = frappe.utils.cstr(doc.get("invoice_number") or "").strip()
	return f"Invoice-{number}" if number else f"Invoice-Draft-{doc.name}"


def invoice_version(name: str) -> int:
	"""How many links this invoice has produced, plus one. Names the file copy."""
	return (
		frappe.db.count(
			document_links.LINK_DOCTYPE,
			{"reference_doctype": DOCTYPE, "reference_name": name, "purpose": PURPOSE},
		)
		+ 1
	)


def build_pdf(doc):
	"""Render the invoice and attach it to the record, privately. Returns the File."""
	version = invoice_version(doc.name)
	content = render(doc)
	name = document_links.pdf_file_name(file_stem(doc), version)
	return document_links.attach_pdf(DOCTYPE, doc.name, name, content, is_private=1), version


@frappe.whitelist(methods=["POST"])
def download_invoice(invoice: str) -> dict:
	"""Render the invoice and hand back the private attachment's URL."""
	require_module()
	doc = get_invoice_doc(invoice, "read")
	if not frappe.has_permission(DOCTYPE, "print", invoice):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	file_doc, _version = build_pdf(doc)
	return {"file_url": file_doc.file_url, "file_name": file_doc.file_name}


def mint_link(doc):
	"""Render, attach privately, and hand out a fresh tokenised URL.

	Every call retires the record's previous live invoice links. A re-send after a
	payment must not leave the old balance -- and the old QR -- readable at the URL
	the customer already has.
	"""
	file_doc, version = build_pdf(doc)
	link = document_links.create_link(
		DOCTYPE,
		doc.name,
		file_doc,
		purpose=PURPOSE,
		payload={
			"invoice_number": doc.get("invoice_number"),
			"grand_total": flt(doc.get("grand_total")),
			"version": version,
		},
		ttl_hours=LINK_TTL_HOURS,
	)
	return file_doc, link


@frappe.whitelist(methods=["POST"])
def share_invoice(invoice: str) -> dict:
	"""Mint a tokenised, view-logged link to the invoice PDF."""
	require_module()
	doc = get_invoice_doc(invoice, "read")
	if not frappe.has_permission(DOCTYPE, "print", invoice):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	file_doc, link = mint_link(doc)
	return {
		"link_url": document_links.public_url(link.token, method="crm.api.invoices.view"),
		"file_url": file_doc.file_url,
		"expires_at": link.expires_at,
	}


@frappe.whitelist(allow_guest=True, methods=["GET"])
def view(token: str | None = None):
	"""Stream the invoice behind a token, and write down that it was read.

	Guest by design -- see the module docstring. `frappe.local.flags.commit` is set
	because the framework only commits a GET when it is told to, and the view row
	is the whole point of this route.

	While the module flag is off this answers exactly like a dead token, rather
	than saying "the invoice module is switched off". A public route must not tell
	an anonymous caller anything about the site's configuration.
	"""
	if not is_enabled(FLAG_INVOICES):
		document_links.throw_gone()

	link = document_links.resolve_link(token)
	if not link or link.get("purpose") != PURPOSE:
		document_links.throw_gone()

	if not link.get("file") or not frappe.db.exists("File", link["file"]):
		document_links.throw_gone()

	file_doc = frappe.get_doc("File", link["file"])

	document_links.record_view(
		link,
		user_agent=document_links.request_user_agent(),
		ip_address=document_links.request_ip(),
	)
	frappe.local.flags.commit = True

	document_links.stream_file(file_doc, link.get("file_name"))


# --- sending ---------------------------------------------------------------


def invoice_message(doc, url: str) -> str:
	name = frappe.utils.cstr(doc.get("customer_name") or "").strip()
	greeting = _("Hi {0},").format(name) if name else _("Hello,")
	remaining = invoicing.remaining_amount(doc.get("grand_total"), doc.get("payments"))
	return "\n".join(
		[
			greeting,
			_("Here is invoice {0} for {1}.").format(
				doc.get("invoice_number") or doc.name,
				frappe.utils.fmt_money(remaining, currency=doc.get("currency") or "INR"),
			),
			url,
		]
	)


@frappe.whitelist(methods=["POST"])
def send_invoice_email(invoice: str) -> dict:
	"""Email the invoice link to the address stored on the invoice.

	The address comes off the RECORD, never off the request, and the send goes
	through `crm.api.email.send_email`, which drops suppressed recipients and
	raises when every recipient is suppressed. An agent who is told nothing would
	assume the mail went.
	"""
	require_module()
	doc = get_invoice_doc(invoice, "write")
	if not frappe.has_permission(DOCTYPE, "print", invoice):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	address = frappe.utils.cstr(doc.get("customer_email") or "").strip()
	if not address:
		return {
			"success": False,
			"reason": "no_address",
			"error": _("This invoice has no customer email address."),
		}

	from crm.api.email import send_email

	file_doc, link = mint_link(doc)
	url = document_links.public_url(link.token, method="crm.api.invoices.view")

	result = send_email(
		doctype=DOCTYPE,
		name=doc.name,
		recipients=address,
		subject=_("Invoice {0}").format(doc.get("invoice_number") or doc.name),
		content="<br>".join(
			frappe.utils.escape_html(line) if not line.startswith("http") else f'<a href="{line}">{line}</a>'
			for line in invoice_message(doc, url).splitlines()
		),
	)

	return {
		"success": True,
		"to": address,
		"link_url": url,
		"file_url": file_doc.file_url,
		"communication": (result or {}).get("name"),
	}


@frappe.whitelist(methods=["POST"])
def send_invoice_on_whatsapp(invoice: str) -> dict:
	"""Send the invoice link to the deal's WhatsApp number as a document.

	Structured result rather than a raise on a delivery failure: outside Meta's
	24-hour window a free-form send is rejected, and that is not a bug the agent
	can do anything about except read the hint.
	"""
	require_module()
	doc = get_invoice_doc(invoice, "write")
	if not frappe.has_permission(DOCTYPE, "print", invoice):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	from crm.api.whatsapp import create_whatsapp_message

	deal_doc = frappe.get_doc(DEAL_DOCTYPE, doc.deal)
	numbers = sorted(whatsapp_numbers(deal_doc))
	if not numbers:
		return {
			"success": False,
			"reason": "no_number",
			"error": _("This deal has no WhatsApp number. Add a mobile number first."),
		}

	file_doc, link = mint_link(doc)
	url = document_links.public_url(link.token, method="crm.api.invoices.view")

	try:
		message = create_whatsapp_message(
			reference_doctype=DEAL_DOCTYPE,
			reference_name=deal_doc.name,
			message=invoice_message(doc, url),
			to=numbers[0],
			attach=url,
			reply_to="",
			content_type="document",
		)
	except frappe.PermissionError:
		document_links.revoke_links(DOCTYPE, doc.name, PURPOSE)
		raise
	except Exception as error:
		frappe.log_error(frappe.get_traceback(), "CRM Invoice: WhatsApp send failed")
		document_links.revoke_links(DOCTYPE, doc.name, PURPOSE)
		return {
			"success": False,
			"reason": "send_failed",
			"error": frappe.utils.cstr(error),
			"hint": _(
				"WhatsApp only accepts a free-form message within 24 hours of the "
				"customer's last message. Ask the customer to message first, or send "
				"the invoice by email."
			),
		}

	return {"success": True, "message": message, "to": numbers[0], "link_url": url, "file_url": file_doc.file_url}


def whatsapp_numbers(deal_doc) -> set:
	"""The numbers the deal already holds. Never a number from a request."""
	from crm.api.whatsapp import get_reference_whatsapp_numbers

	try:
		return get_reference_whatsapp_numbers(deal_doc)
	except Exception:
		return set()


# --- housekeeping ----------------------------------------------------------


def cleanup_invoice_links(older_than_hours: int = LINK_TTL_HOURS) -> int:
	"""Hourly: retire expired invoice links and delete the private PDF each held.

	Never raises: a scheduler job that throws takes the rest of its queue down.
	`crm.document_links.expire_links` owns both halves and is shared with the
	quote, so this entry exists only to name the TTL.
	"""
	return document_links.expire_links(older_than_hours)


def install_invoice_print_format():
	"""after_migrate entry point. See demo-package/specs/stage5-3-notes.md."""
	install_print_format()
