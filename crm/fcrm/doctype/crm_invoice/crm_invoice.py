# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

"""The invoice record: what it refuses, and what it recomputes.

None of the arithmetic is here. `crm.invoicing` owns every figure, because the
PDF, the editor and the dashboard tiles must all get the same answer from the
same code. This class is the part that cannot be pure: it reads the item rows,
asks `crm.invoicing` what they add up to, writes the answer back, and refuses
the saves that would make an issued invoice a different document from the one
the customer holds.

The four refusals
-----------------
1. **Client totals are never trusted.** `validate` recomputes `taxable_total`,
   the tax split and `grand_total` from the item rows on EVERY save. A browser
   that posts its own figures has them overwritten, not honoured.
2. **A locked number freezes the document.** Once `number_locked_at` is set the
   number, the dates, the GST treatment, the recipient block and the item rows
   are immutable. A draft may be edited and renumbered freely; an invoice a
   customer already holds may not become a different invoice with the same
   number.
3. **Payments are append-only.** An existing row may not be edited and may not be
   removed. A payment recorded wrongly is corrected by a NEGATIVE row with a
   mandatory note, so the history reads as what happened rather than as what
   somebody wishes had happened.
4. **Nothing is ever deleted.** `on_trash` refuses for every user including
   Administrator. Void is the terminal negative state and it is a manager's call.

Status is never set by a caller except for the two moves a caller owns -- Draft
to Sent (finalize) and anything to Void. Everything else comes from
`crm.invoicing.status_for`, which reads the payment rows.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from crm import invoicing

MANAGER_ROLES = ("Sales Manager", "System Manager")

# Frozen the moment the number is allocated. These are the fields a customer
# reads off the document they were sent; a change to any of them after the send
# makes the stored invoice a different document from the printed one.
LOCKED_FIELDS = (
	"invoice_number",
	"invoice_date",
	"service_date",
	"mode",
	"reverse_charge",
	"place_of_supply",
	"company_state_code",
	"customer_name",
	"customer_address",
	"customer_state",
	"customer_state_code",
	"customer_gstin",
)

# What one item row is, for the purpose of "did the items change". `name` is
# included so a row that was deleted and re-added is a change even when every
# other value matches.
ITEM_SIGNATURE_FIELDS = ("name", "description", "sac", "qty", "rate", "tax_rate")

MAX_REASON_LENGTH = 500


def is_manager(user: str | None = None) -> bool:
	"""True when the user may void an invoice or manage the masters."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	return bool(set(frappe.get_roles(user)) & set(MANAGER_ROLES))


class CRMInvoice(Document):
	# --- the save --------------------------------------------------------

	def validate(self):
		self.normalise_identity()
		self.refuse_locked_edits()
		self.validate_payments()
		self.recompute()
		self.settle_schedule_rows()
		self.apply_status()

	def on_trash(self):
		"""An invoice is never hard-deleted. Not by a manager, not by Administrator.

		A tax document that can disappear is not a record. Void is the terminal
		negative state and it leaves the row, the number and the history in place.
		The doctype grants `delete` to nobody as well, so this is the second of two
		locks rather than the only one.
		"""
		frappe.throw(
			_(
				"An invoice is never deleted. Void it instead: the number, the amounts and "
				"the history stay, and the invoice is excluded from every revenue figure."
			)
		)

	# --- identity and validation ----------------------------------------

	def normalise_identity(self):
		"""Clean the fields the tax rules care about, and refuse the impossible ones."""
		# An empty unique column must be NULL, not "". MariaDB allows many NULLs in
		# a unique index and exactly one empty string, so a second draft would
		# collide with the first one on a field neither of them has set yet.
		self.invoice_number = frappe.utils.cstr(self.invoice_number or "").strip() or None
		if self.invoice_number:
			self.invoice_number = invoicing.validate_number(self.invoice_number)

		self.currency = self.currency or "INR"
		self.customer_state_code = invoicing.validate_state_code(self.customer_state_code)
		self.place_of_supply = invoicing.validate_state_code(self.place_of_supply)
		self.customer_gstin = invoicing.validate_gstin(self.customer_gstin, self.customer_state_code)

		if not self.company_state_code:
			# Snapshotted once. Reading it live would let a corrected Company Profile
			# silently re-split invoices that were issued under the old state code.
			self.company_state_code = invoicing.validate_state_code(
				invoicing.get_company_profile().get("state_code")
			)

		if not self.place_of_supply:
			# The field's documented default: the customer's own state.
			self.place_of_supply = self.customer_state_code or self.company_state_code

		if not self.invoice_date:
			self.invoice_date = frappe.utils.nowdate()

		if self.mode not in invoicing.MODES:
			self.mode = invoicing.MODE_COMMISSION

		if self.status not in invoicing.STATUSES:
			self.status = invoicing.STATUS_DRAFT

	def number_is_locked(self) -> bool:
		"""True once the number has been allocated. The document is frozen then.

		NOT called `is_locked`. `frappe.model.document.Document.is_locked` is a
		PROPERTY that reports the framework's own file lock, and a method of that
		name on a controller shadows it with a bound method -- which is always
		truthy, so every save then failed inside `check_if_locked`. Found by a live
		run; see demo-package/specs/stage5-3-notes.md.
		"""
		return bool(self.number_locked_at)

	def refuse_locked_edits(self):
		"""Refuse every change a locked invoice may not make.

		The comparison is against `get_doc_before_save`, which is the document as
		it stands in the database, so this catches a change made through the API
		exactly as it catches one made in the browser.
		"""
		before = self.get_doc_before_save()
		if not before or not before.get("number_locked_at"):
			return

		changed = [field for field in LOCKED_FIELDS if not same(before.get(field), self.get(field))]
		if changed:
			frappe.throw(
				_(
					"Invoice {0} has been issued, so {1} can no longer be changed. Void it and "
					"issue a corrected invoice instead."
				).format(before.get("invoice_number") or self.name, ", ".join(sorted(changed)))
			)

		if item_signature(before.get("items")) != item_signature(self.get("items")):
			frappe.throw(
				_(
					"Invoice {0} has been issued, so its item lines can no longer be changed. "
					"Void it and issue a corrected invoice instead."
				).format(before.get("invoice_number") or self.name)
			)

		if before.get("status") == invoicing.STATUS_VOID and self.status != invoicing.STATUS_VOID:
			frappe.throw(_("A voided invoice cannot be brought back. Issue a new invoice instead."))

	def validate_payments(self):
		"""Payments only ever ADD. Every other change to the table is refused."""
		before = self.get_doc_before_save()
		existing = {row.name: row for row in (before.get("payments") if before else []) or []}
		seen = set()

		for row in self.get("payments") or []:
			if row.name in existing:
				seen.add(row.name)
				if payment_signature(existing[row.name]) != payment_signature(row):
					frappe.throw(
						_(
							"A recorded payment cannot be edited. Add a negative correction row "
							"with a note instead, so the history says what happened."
						)
					)
				continue

			if not flt(row.amount):
				frappe.throw(_("A payment of zero is not a payment. Remove the row or enter an amount."))

			if flt(row.amount) < 0 and not frappe.utils.cstr(row.note or "").strip():
				frappe.throw(
					_("A negative correction needs a note. Say why the money came back off the invoice.")
				)

			row.recorded_by = row.recorded_by or frappe.session.user
			row.recorded_at = row.recorded_at or frappe.utils.now_datetime()
			row.payment_date = row.payment_date or frappe.utils.nowdate()

		missing = set(existing) - seen
		if missing:
			frappe.throw(
				_(
					"A recorded payment cannot be removed. Add a negative correction row with a "
					"note instead, so the history says what happened."
				)
			)

	# --- the numbers -----------------------------------------------------

	def item_rows(self) -> list[dict]:
		"""The item rows as plain dicts, which is what `crm.invoicing` takes."""
		return [
			{
				"description": row.description,
				"sac": row.sac,
				"qty": row.qty,
				"rate": row.rate,
				"tax_rate": row.tax_rate,
			}
			for row in self.get("items") or []
		]

	def payment_rows(self) -> list[dict]:
		return [{"amount": row.amount} for row in self.get("payments") or []]

	def totals(self) -> dict:
		"""Every figure on the face of the invoice, recomputed from the rows."""
		return invoicing.compute_totals(
			self.item_rows(),
			self.mode,
			company_state_code=self.company_state_code,
			place_of_supply=self.place_of_supply,
		)

	def recompute(self):
		"""Write the computed figures onto the record. Never reads a stored total."""
		for row in self.get("items") or []:
			row.amount = invoicing.line_amount(
				{"qty": row.qty, "rate": row.rate}
			)

		totals = self.totals()
		self.taxable_total = totals["taxable_total"]
		self.cgst_amount = totals["cgst_amount"]
		self.sgst_amount = totals["sgst_amount"]
		self.igst_amount = totals["igst_amount"]
		self.tax_total = totals["tax_total"]
		self.rounding_adjustment = totals["rounding_adjustment"]
		self.grand_total = totals["grand_total"]
		self.intra_state = 1 if totals["intra_state"] else 0

		self.paid_total = invoicing.paid_amount(self.payment_rows())
		self.outstanding_amount = invoicing.remaining_amount(self.grand_total, self.payment_rows())

	def settle_schedule_rows(self):
		"""Mark the schedule rows a payment has been recorded against.

		A settled row stops its own reminder ladder and nobody else's — that is the
		whole point of a schedule, and criterion 5 of the design note.
		"""
		settled = {
			frappe.utils.cstr(row.schedule_row)
			for row in self.get("payments") or []
			if row.schedule_row and flt(row.amount) > 0
		}
		for row in self.get("payment_schedule") or []:
			row.settled = 1 if frappe.utils.cstr(row.name) in settled else 0

	# --- status ----------------------------------------------------------

	def apply_status(self):
		"""Let the payment rows decide the status, then write the history row."""
		before = self.get_doc_before_save()
		previous = before.get("status") if before else None

		self.status = invoicing.status_for(self.status, self.grand_total, self.payment_rows())

		if self.status != previous:
			self.log_status(previous, self.status, self.flags.status_note or "")

		self.flags.status_note = None

	def log_status(self, from_status, to_status, note: str = ""):
		"""One history row per status move. Send-log style: written, never edited."""
		self.append(
			"status_log",
			{
				"changed_at": frappe.utils.now_datetime(),
				"from_status": from_status or "",
				"to_status": to_status,
				"changed_by": frappe.session.user,
				"note": frappe.utils.cstr(note or "")[:MAX_REASON_LENGTH],
			},
		)

	# --- the two moves a caller owns -------------------------------------

	def finalize(self) -> dict:
		"""Issue the invoice: check, allocate the number, lock it, mark it Sent.

		Returns the blockers when there are any, rather than throwing one at a
		time: an agent who has to fix three fields should learn all three at once.
		The Rule 47 warning is returned too and is deliberately NOT a blocker —
		refusing a late invoice leaves an agency unable to bill at all, which does
		not make them more compliant, only unpaid.
		"""
		if self.status == invoicing.STATUS_VOID:
			frappe.throw(_("A voided invoice cannot be issued."))

		if self.number_is_locked():
			return {
				"issued": False,
				"already_issued": True,
				"invoice_number": self.invoice_number,
				"blockers": [],
				"warning": "",
			}

		blockers = invoicing.finalize_blockers(self, self.totals())
		if blockers:
			return {"issued": False, "blockers": blockers, "warning": ""}

		number = invoicing.allocate_number(self)
		self.number_locked_at = frappe.utils.now_datetime()
		self.sent_at = frappe.utils.now_datetime()
		self.status = invoicing.STATUS_SENT
		self.flags.status_note = _("Issued as {0}").format(number)
		self.save(ignore_permissions=True)

		return {
			"issued": True,
			"blockers": [],
			"invoice_number": number,
			"warning": invoicing.rule_47_warning(self),
		}

	def void(self, reason: str = "") -> None:
		"""The terminal negative state. Managers only, and never reversible."""
		if not is_manager():
			frappe.throw(_("Only a manager can void an invoice."), frappe.PermissionError)

		if self.status == invoicing.STATUS_VOID:
			return

		reason = frappe.utils.strip_html(frappe.utils.cstr(reason or "")).strip()[:MAX_REASON_LENGTH]
		if not reason:
			frappe.throw(_("Say why this invoice is being voided. The reason stays on the record."))

		self.voided_at = frappe.utils.now_datetime()
		self.voided_by = frappe.session.user
		self.void_reason = reason
		self.status = invoicing.STATUS_VOID
		self.flags.status_note = reason
		self.save(ignore_permissions=True)

	def record_payment(
		self,
		amount=None,
		mode: str = "UPI",
		reference: str = "",
		note: str = "",
		schedule_row: str | None = None,
	):
		"""Append one payment row. Over-payment is refused; the row is never edited.

		`amount` defaults to the remaining balance, which is what an agent means
		nine times out of ten and the only default that cannot be wrong by a paisa.
		"""
		if self.status == invoicing.STATUS_VOID:
			frappe.throw(_("A voided invoice takes no payments."))

		if not self.number_is_locked():
			frappe.throw(
				_("This invoice has not been issued yet. Issue it before you record a payment against it.")
			)

		remaining = invoicing.remaining_amount(self.grand_total, self.payment_rows())
		amount = invoicing.round_paise(remaining if amount in (None, "") else amount)

		if amount == 0:
			frappe.throw(_("A payment of zero is not a payment."))

		if amount > 0 and amount > remaining:
			frappe.throw(
				_(
					"That is more than the {0} still outstanding on this invoice. Record the "
					"outstanding amount, or correct the invoice first."
				).format(frappe.utils.fmt_money(remaining, currency=self.currency or "INR"))
			)

		if schedule_row and not any(
			frappe.utils.cstr(row.name) == frappe.utils.cstr(schedule_row)
			for row in self.get("payment_schedule") or []
		):
			frappe.throw(_("That payment-schedule row does not belong to this invoice."))

		self.append(
			"payments",
			{
				"payment_date": frappe.utils.nowdate(),
				"amount": amount,
				"mode": mode if mode in ("UPI", "Bank", "Cash", "Other") else "Other",
				"reference": frappe.utils.cstr(reference or "")[:140],
				"note": frappe.utils.cstr(note or "")[:MAX_REASON_LENGTH],
				"schedule_row": frappe.utils.cstr(schedule_row or ""),
				"recorded_by": frappe.session.user,
				"recorded_at": frappe.utils.now_datetime(),
			},
		)
		self.flags.status_note = _("Payment of {0} recorded").format(
			frappe.utils.fmt_money(amount, currency=self.currency or "INR")
		)
		self.save()
		return self

	# --- read helpers ----------------------------------------------------

	def is_overdue(self, on=None) -> bool:
		return invoicing.is_overdue(self, on)

	def remaining(self) -> float:
		return invoicing.remaining_amount(self.grand_total, self.payment_rows())


# --- comparison helpers ----------------------------------------------------


def same(left, right) -> bool:
	"""Two stored values, compared the way the database would answer.

	Numbers are compared as numbers and everything else as trimmed strings, so a
	form that posts `"5.0"` where the row holds `5` is not reported as an edit to
	an issued invoice.
	"""
	if isinstance(left, int | float) or isinstance(right, int | float):
		if is_number(left) and is_number(right):
			return flt(left) == flt(right)
	return frappe.utils.cstr(left or "").strip() == frappe.utils.cstr(right or "").strip()


def is_number(value) -> bool:
	if value in (None, ""):
		return True
	try:
		float(value)
	except (TypeError, ValueError):
		return False
	return True


def item_signature(rows) -> list[tuple]:
	"""What "the items did not change" means, as a comparable value."""
	signature = []
	for row in rows or []:
		signature.append(
			tuple(
				flt(row.get(field)) if field in ("qty", "rate", "tax_rate") else frappe.utils.cstr(row.get(field) or "")
				for field in ITEM_SIGNATURE_FIELDS
			)
		)
	return signature


def payment_signature(row) -> tuple:
	"""What "this payment row was not edited" means."""
	return (
		frappe.utils.cstr(row.get("payment_date") or ""),
		flt(row.get("amount")),
		frappe.utils.cstr(row.get("mode") or ""),
		frappe.utils.cstr(row.get("reference") or ""),
		frappe.utils.cstr(row.get("note") or ""),
		frappe.utils.cstr(row.get("schedule_row") or ""),
	)


# --- row-level scope -------------------------------------------------------
# Registered in `crm/hooks.py`. An invoice has no scope of its own: it belongs to
# the deal it bills, and the deal's org-hierarchy conditions are the answer. This
# is the same shape `crm.api.itinerary` uses for the lead.


def get_invoice_permission_query_conditions(user=None):
	"""Restrict invoice rows to the deals the user may already see."""
	from crm.permissions.org_hierarchy import get_deal_permission_query_conditions

	deal_conditions = get_deal_permission_query_conditions(user)
	if not deal_conditions:
		return ""

	return (
		f"`tabCRM Invoice`.`deal` in "
		f"(select `tabCRM Deal`.`name` from `tabCRM Deal` where {deal_conditions})"
	)


def has_invoice_permission(doc, ptype, user=None):
	from crm.permissions.org_hierarchy import has_deal_permission

	if ptype == "create" or not doc.get("deal"):
		return True

	return has_deal_permission(frappe._dict({"name": doc.get("deal")}), ptype, user)


# --- role grants -----------------------------------------------------------


def add_invoice_roles():
	"""after_migrate: let the sales roles use the invoice doctypes.

	A Sales User creates drafts and records payments, so they get create and
	write. Nobody gets `delete` — `on_trash` refuses anyway, and a doctype that
	granted a permission its controller always refuses would be a lie told in the
	permission screen.

	Report and export stay with managers, for the same reason as the itinerary:
	they are the two paths that hand a whole revenue table to a leaving employee.
	"""
	from frappe.permissions import add_permission, update_permission_property

	grants = (
		("CRM Invoice", "Sales Manager", 1, 1, 1),
		("CRM Invoice", "Sales User", 0, 0, 0),
		("CRM SAC Code", "Sales Manager", 1, 1, 1),
	)

	for doctype, role, report, export, share in grants:
		if not frappe.db.exists("DocType", doctype):
			continue

		if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role}):
			add_permission(doctype, role, 0, "write")

		for property_name, value in (
			("read", 1),
			("write", 1),
			("create", 1),
			("print", 1),
			("email", 1),
			("delete", 0),
			("report", int(report)),
			("export", int(export)),
			("share", int(share)),
		):
			update_permission_property(doctype, role, 0, property_name, value)

	# A Sales User may read the SAC master and nothing more: the codes and rates
	# on it decide what every invoice charges, so it is manager configuration.
	if frappe.db.exists("DocType", "CRM SAC Code"):
		if not frappe.db.exists("Custom DocPerm", {"parent": "CRM SAC Code", "role": "Sales User"}):
			add_permission("CRM SAC Code", "Sales User", 0, "read")
		for property_name, value in (
			("read", 1),
			("write", 0),
			("create", 0),
			("delete", 0),
			("report", 0),
			("export", 0),
			("share", 0),
		):
			update_permission_property("CRM SAC Code", "Sales User", 0, property_name, value)
