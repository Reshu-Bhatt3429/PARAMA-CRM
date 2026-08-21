"""GST invoices: the arithmetic, the numbering, and the rules that refuse.

Why the numbers live here and not on the doctype
------------------------------------------------
Every figure on a GST invoice is either a legal statement or a lie, and the
place a customer reads it (the PDF), the place an agent checks it (the editor)
and the place a manager totals it (the dashboard tiles) must all get the same
answer from the same code. So the arithmetic is a set of pure functions over
plain dicts here, and `CRM Invoice.validate` calls them. Nothing in this module
touches the database except the numbering, which cannot be pure.

The four rules that are not arithmetic
--------------------------------------
* **Rule 46(b) numbering.** At most 16 characters, drawn from
  `[A-Za-z0-9\\-\\/]`, unique for the financial year. This app additionally locks
  the number the first time the invoice leaves the building -- see
  `lock_number` -- because a number a customer already holds is not a draft.
* **Section 170 rounding.** Round half up to the whole rupee on the tax totals
  and the grand total. Per-line values keep their paise: rounding each line and
  then adding is a different number, and the one the law asks for is the one
  computed here.
* **Place of supply decides the split.** Same state as the supplier means CGST
  and SGST, each half the rate. A different state means IGST at the full rate.
  There is no third case in this module's world (v1 is domestic INR only).
* **Tour-package mode.** 5% on the gross amount, no input tax credit, and the
  mandatory statement printed on the face of the invoice. The per-line rates
  from the SAC master are OVERRIDDEN, not merged -- a tour package billed at
  5% gross that also charged 18% on one line would be neither scheme.

What this module deliberately does NOT do
-----------------------------------------
No IRN, no e-invoice QR, no upload to the Invoice Registration Portal. See
`EINVOICE_THRESHOLD_RUPEES` for the threshold this decision rests on and for an
honest account of how well that figure is verified.
"""

import re
from decimal import ROUND_HALF_UP, Decimal

import frappe
from frappe import _
from frappe.utils import cint, flt

INVOICE_DOCTYPE = "CRM Invoice"
COMPANY_PROFILE_DOCTYPE = "CRM Company Profile"
SAC_DOCTYPE = "CRM SAC Code"
DEAL_DOCTYPE = "CRM Deal"

# --- statuses ---
STATUS_DRAFT = "Draft"
STATUS_SENT = "Sent"
STATUS_PARTIALLY_PAID = "Partially Paid"
STATUS_PAID = "Paid"
STATUS_VOID = "Void"

# Overdue is NOT in this list on purpose. It is a function of the due date and
# the clock, so storing it would mean a nightly job whose only work is to make a
# column agree with `today()`, and a window every day in which it does not.
STATUSES = (STATUS_DRAFT, STATUS_SENT, STATUS_PARTIALLY_PAID, STATUS_PAID, STATUS_VOID)

# The states that still owe money, and therefore still carry reminder ladders.
OPEN_STATUSES = (STATUS_SENT, STATUS_PARTIALLY_PAID)

# --- modes ---
MODE_TOUR_PACKAGE = "Tour Package"
MODE_COMMISSION = "Commission"
MODES = (MODE_TOUR_PACKAGE, MODE_COMMISSION)

# Rule 32(3) tour-operator scheme: 5% on the gross amount, without input tax
# credit. The rate is fixed by the scheme, so it is a constant rather than a
# setting -- a configurable "tour package rate" would only ever be set wrong.
TOUR_PACKAGE_RATE = 5.0

# Printed verbatim on every tour-package invoice. The wording is prescribed; do
# not paraphrase it.
TOUR_PACKAGE_STATEMENT = (
	"The amount charged is gross amount and inclusive of charges of accommodation and transportation."
)

# --- Rule 46(b): what a document number may be ---
MAX_NUMBER_LENGTH = 16
NUMBER_CHARSET = re.compile(r"^[A-Za-z0-9\-/]+$")

# The number is `<prefix><FY>/<serial>`, e.g. `INV/25-26/0001` -- 14 characters
# with the default prefix, which leaves two characters of headroom inside the
# 16-character ceiling for a longer prefix. A serial is zero-padded to this
# width and grows past it rather than wrapping.
SERIAL_WIDTH = 4
DEFAULT_NUMBER_PREFIX = "INV/"

# How many times `allocate_number` re-reads the highest serial and tries again
# after losing the unique index to another writer. Bounded: an unbounded retry
# under real contention is an unbounded transaction.
ALLOCATION_ATTEMPTS = 8

# --- Rule 46(f)/(g): when a B2C invoice must name its recipient ---
B2C_RECIPIENT_THRESHOLD = 50000.0

# --- Rule 47: how long after the service the invoice may be issued ---
RULE_47_DAYS = 30

# ---------------------------------------------------------------------------
# E-INVOICING (IRN) THRESHOLD -- read this before changing the figure.
#
# This module generates NO IRN and talks to NO Invoice Registration Portal. That
# is only safe for an agency whose aggregate annual turnover is below the
# e-invoicing threshold, so the threshold is stated to the user in Company
# Profile rather than assumed.
#
# Last figure this project could establish: Rs 5,00,00,000 (Rs 5 crore), set by
# Notification No. 10/2023 - Central Tax dated 10 May 2023, with effect from
# 1 August 2023.
#
# VERIFICATION STATUS: **UNCONFIRMED for 2026.** A live re-check was attempted on
# 2026-08-19 and FAILED. What was tried and what it returned:
#   * WebSearch                       -- session budget exhausted, zero results.
#   * https://taxinformation.cbic.gov.in/api/notification/search
#                                     -- HTTP 401, "Full authentication is
#                                        required to access this resource".
#   * https://taxinformation.cbic.gov.in (notification archive)
#                                     -- TLS error "unable to verify the first
#                                        certificate"; the archive is also a
#                                        JavaScript app that a fetcher cannot
#                                        render.
#   * https://einvoice.gst.gov.in/einvoice/laws-and-regulations/notifications
#                                     -- HTTP 302 to /error/notfound.
#   * https://einvoice1.gst.gov.in/Others/Notifications
#                                     -- reachable, notification table empty
#                                        without JavaScript.
#   * https://cbic-gst.gov.in/        -- reachable; the "What's New" list showed
#                                        no e-invoicing threshold item, but it is
#                                        not a notification archive, so its
#                                        silence proves nothing.
#   * cleartax.in, taxguru.in, irisgst.com, mastersindia.co, indiafilings.com,
#     zoho.com/in/books                -- 404, 403 or 503 on every article slug
#                                        tried.
#
# So the constant below is a DEFAULT, not a verified fact, and the field it
# seeds (`CRM Company Profile.einvoice_threshold`) is editable. An agency near
# the line must confirm the current threshold with their CA. Do not present this
# number to a user as verified.
#
# See demo-package/specs/stage5-3-notes.md for the same account in prose.
# ---------------------------------------------------------------------------
EINVOICE_THRESHOLD_RUPEES = 50000000.0
EINVOICE_THRESHOLD_SOURCE = "Notification No. 10/2023 - Central Tax, 10 May 2023 (w.e.f. 1 Aug 2023)"
EINVOICE_THRESHOLD_CHECKED_ON = "2026-08-19"
EINVOICE_THRESHOLD_VERIFIED = False

# A GSTIN is 15 characters: 2-digit state code, 10-character PAN, entity digit,
# 'Z', checksum. Only the shape is checked here -- the checksum is the tax
# portal's job, and a checksum this app got wrong would refuse a valid GSTIN.
GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z]{1}[Z][0-9A-Z]{1}$")

STATE_CODE_PATTERN = re.compile(r"^[0-9]{2}$")

MAX_NOTE_LENGTH = 500


# --- money -----------------------------------------------------------------


def to_decimal(value) -> Decimal:
	"""One amount as an exact decimal. `flt` first, so None and "" are 0."""
	return Decimal(str(flt(value)))


def round_paise(value) -> float:
	"""Two decimal places, half up. What a line amount keeps."""
	return float(to_decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def round_rupee(value) -> float:
	"""Whole rupees, half up. Section 170, and the ONLY rounding that is legal here.

	`round()` is banker's rounding -- it sends 0.5 to the nearest EVEN integer,
	so 2.5 becomes 2. Section 170 says half goes up, always. Every rounded figure
	on a GST invoice in this app comes through this function.
	"""
	return float(to_decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# --- financial year --------------------------------------------------------


def financial_year(date=None) -> tuple[int, int]:
	"""The Indian financial year containing a date, as (start_year, end_year).

	India's financial year begins on 1 April. A date in January 2026 belongs to
	FY 2025-26, not FY 2026-27, and an invoice series that reset on 1 January
	would break the "unique per financial year" requirement in exactly the month
	an auditor looks at.
	"""
	date = frappe.utils.getdate(date or frappe.utils.nowdate())
	start = date.year if date.month >= 4 else date.year - 1
	return start, start + 1


def financial_year_label(date=None) -> str:
	"""The FY as it is written on an invoice: `25-26`."""
	start, end = financial_year(date)
	return f"{start % 100:02d}-{end % 100:02d}"


def financial_year_bounds(date=None) -> tuple[str, str]:
	"""(first day, day after the last day) of the FY containing `date`.

	Half-open at the top so a comparison never has to care whether the stored
	value is a date or a datetime.
	"""
	start, end = financial_year(date)
	return f"{start}-04-01", f"{end}-04-01"


# --- Rule 46(b): the number ------------------------------------------------


def validate_number(number: str) -> str:
	"""The number, proven to satisfy Rule 46(b). Raises otherwise.

	Both halves are checked because either alone lets a bad number through: a
	17-character number made only of letters passes the charset, and `INV#1`
	passes the length.
	"""
	number = frappe.utils.cstr(number).strip()

	if not number:
		frappe.throw(_("An invoice number is required."))

	if len(number) > MAX_NUMBER_LENGTH:
		frappe.throw(
			_(
				"The invoice number {0} is {1} characters. GST Rule 46(b) allows at "
				"most {2}. Shorten the invoice number prefix in Company Profile."
			).format(number, len(number), MAX_NUMBER_LENGTH)
		)

	if not NUMBER_CHARSET.match(number):
		frappe.throw(
			_(
				"The invoice number {0} contains a character GST Rule 46(b) does not "
				"allow. Use only letters, digits, hyphen and slash."
			).format(number)
		)

	return number


def number_prefix() -> str:
	"""The configured prefix, cleaned to the Rule 46(b) charset."""
	value = frappe.utils.cstr(
		frappe.db.get_single_value(COMPANY_PROFILE_DOCTYPE, "invoice_number_prefix") or ""
	).strip()
	value = value or DEFAULT_NUMBER_PREFIX
	# A prefix an administrator typed is the likeliest source of an illegal
	# character, and it is far better to strip it here than to mint a number the
	# invoice then refuses to save under.
	return re.sub(r"[^A-Za-z0-9\-/]", "", value)


def number_for(serial: int, date=None, prefix: str | None = None) -> str:
	"""Build one invoice number. Pure -- no reads, no allocation."""
	prefix = number_prefix() if prefix is None else prefix
	return f"{prefix}{financial_year_label(date)}/{cint(serial):0{SERIAL_WIDTH}d}"


def highest_serial(date=None, prefix: str | None = None) -> int:
	"""The largest serial already issued in this financial year. 0 when none.

	Scoped by the number's own FY segment rather than by `invoice_date`, so an
	invoice whose date is later edited cannot make the series jump backwards.
	"""
	prefix = number_prefix() if prefix is None else prefix
	stem = f"{prefix}{financial_year_label(date)}/"

	rows = frappe.get_all(
		INVOICE_DOCTYPE,
		filters={"invoice_number": ["like", f"{stem}%"]},
		pluck="invoice_number",
		limit_page_length=0,
	)

	best = 0
	for value in rows:
		tail = frappe.utils.cstr(value)[len(stem) :]
		if tail.isdigit():
			best = max(best, int(tail))
	return best


def next_number(date=None) -> str:
	"""What the NEXT invoice in this FY would be called. Reserves nothing.

	The draft editor shows this so an agent knows roughly what they are about to
	issue. It is explicitly a preview: two agents drafting at once both see the
	same string, and `allocate_number` is what makes only one of them keep it.
	"""
	return number_for(highest_serial(date) + 1, date)


def is_duplicate_entry(error) -> bool:
	"""True for every spelling of "that invoice number is taken" this stack raises.

	Stage 5.3 correction, found by a live probe rather than by reading. `db_set`
	goes through `frappe.db.set_value`, and only the DOCUMENT save path
	(`frappe.model.base_document`) maps the driver's error onto
	`frappe.UniqueValidationError`. A `set_value` that loses the unique index
	raises `pymysql.err.IntegrityError` unchanged, so catching the framework
	exceptions alone let the raw driver error straight out of the retry loop
	below -- and the allocator crashed on the one collision it exists to survive.

	`frappe.db.is_unique_key_violation` is the framework's own test for that
	error, so this asks the database layer rather than matching on a message.
	"""
	if isinstance(error, frappe.UniqueValidationError | frappe.DuplicateEntryError):
		return True
	try:
		return bool(frappe.db.is_unique_key_violation(error))
	except Exception:
		return False


def allocate_number(doc) -> str:
	"""Claim the next free number for this invoice and write it. Returns it.

	The unique index on `invoice_number` is the authority, not this function. Two
	workers that read the same highest serial both build the same string; the
	first insert wins, the second collides, re-reads and takes the next one. A
	loop around a unique index is the only allocator that is correct under
	concurrency -- a `SELECT max + 1` on its own is a race with a fixed outcome.
	"""
	if doc.get("invoice_number") and doc.get("number_locked_at"):
		return doc.invoice_number

	prefix = number_prefix()

	for _attempt in range(ALLOCATION_ATTEMPTS):
		candidate = validate_number(
			number_for(highest_serial(doc.get("invoice_date"), prefix) + 1, doc.get("invoice_date"), prefix)
		)
		try:
			# `db_set` goes straight to the column, so the unique index answers
			# now rather than at the end of the caller's transaction.
			doc.db_set("invoice_number", candidate, update_modified=False)
			doc.invoice_number = candidate
			return candidate
		except Exception as error:
			# Only a collision is retried. Anything else -- a dead connection, a
			# missing column -- is a different problem, and spending one of the
			# eight attempts on it would turn a clear error into a vague one.
			if not is_duplicate_entry(error):
				raise
			continue

	frappe.throw(
		_("Could not allocate an invoice number after {0} attempts. Try again.").format(ALLOCATION_ATTEMPTS),
		exc=frappe.ValidationError,
	)


# --- the tax split ---------------------------------------------------------


def is_intra_state(company_state_code, place_of_supply) -> bool:
	"""True when supplier and place of supply are the same state.

	An unknown place of supply is treated as INTRA-state, matching the field's
	default (the customer's own state). That is the conservative direction: a
	wrongly-intra-state invoice over-collects nothing -- the total tax is the
	same either way -- while it keeps the two-column CGST/SGST layout an Indian
	customer expects to see.
	"""
	company = frappe.utils.cstr(company_state_code or "").strip()
	supply = frappe.utils.cstr(place_of_supply or "").strip()
	if not supply:
		return True
	return company == supply


def line_rate(row, mode: str) -> float:
	"""The tax rate one line actually carries.

	Tour-package mode overrides every stored rate. That is the point of the
	scheme: the whole package is taxed once, at 5% on the gross, and a line that
	kept its own 18% would make the invoice neither scheme nor legal.
	"""
	if mode == MODE_TOUR_PACKAGE:
		return TOUR_PACKAGE_RATE
	return flt(row.get("tax_rate"))


def line_amount(row) -> float:
	"""One line's amount, in paise precision. qty x rate, always recomputed.

	Never read from a stored `amount`: the field exists so the grid can show it,
	and a stored total that disagrees with its own quantity and rate is the
	single commonest way an invoice becomes indefensible.
	"""
	return round_paise(to_decimal(row.get("qty")) * to_decimal(row.get("rate")))


def compute_totals(items, mode: str, company_state_code=None, place_of_supply=None) -> dict:
	"""Every figure on the face of the invoice, from the item rows.

	The order of operations IS the rule (Section 170): sum the lines in paise,
	take the tax on that sum per rate band, and round only at the end. Rounding
	each line to the rupee first and adding gives a different -- and wrong --
	answer on any invoice with more than a couple of lines.

	CGST and SGST are each half the raw tax, ROUNDED SEPARATELY, because they are
	two separate tax amounts on the face of the invoice and each has to be a
	whole rupee in its own right. On a raw tax of 101.00 that yields 51 + 51 =
	102 rather than 101; the alternative is a printed invoice whose two tax
	columns do not add up to its own tax total, which is worse.
	"""
	rows = list(items or [])
	intra = is_intra_state(company_state_code, place_of_supply)

	taxable = Decimal("0")
	raw_tax = Decimal("0")
	# Per-rate subtotals, for the HSN/SAC-wise tax table Rule 46 asks for.
	bands: dict[float, dict] = {}

	for row in rows:
		amount = to_decimal(line_amount(row))
		rate = flt(line_rate(row, mode))
		tax = amount * Decimal(str(rate)) / Decimal("100")

		taxable += amount
		raw_tax += tax

		band = bands.setdefault(rate, {"rate": rate, "taxable": Decimal("0"), "tax": Decimal("0")})
		band["taxable"] += amount
		band["tax"] += tax

	taxable_total = round_paise(taxable)

	if intra:
		half = raw_tax / Decimal("2")
		cgst = round_rupee(half)
		sgst = round_rupee(half)
		igst = 0.0
	else:
		cgst = 0.0
		sgst = 0.0
		igst = round_rupee(raw_tax)

	tax_total = round_paise(Decimal(str(cgst)) + Decimal(str(sgst)) + Decimal(str(igst)))
	pre_rounding = to_decimal(taxable_total) + to_decimal(tax_total)
	grand_total = round_rupee(pre_rounding)

	return {
		"intra_state": intra,
		"taxable_total": taxable_total,
		"cgst_amount": cgst,
		"sgst_amount": sgst,
		"igst_amount": igst,
		"tax_total": tax_total,
		# What Section 170 rounding actually moved. Printed as its own line so a
		# customer who adds the columns up can see where the paise went.
		"rounding_adjustment": round_paise(to_decimal(grand_total) - pre_rounding),
		"grand_total": grand_total,
		"tax_bands": [
			{
				"rate": band["rate"],
				"taxable": round_paise(band["taxable"]),
				"tax": round_paise(band["tax"]),
			}
			for band in sorted(bands.values(), key=lambda b: b["rate"])
		],
	}


# --- payments --------------------------------------------------------------


def paid_amount(payments) -> float:
	"""Everything recorded against the invoice, corrections included.

	A correction is a NEGATIVE row, never an edit and never a delete, so this is
	a plain sum and the history stays readable: an auditor can see the payment
	that was recorded wrongly and the row that took it back.
	"""
	total = Decimal("0")
	for row in payments or []:
		total += to_decimal(row.get("amount"))
	return round_paise(total)


def remaining_amount(grand_total, payments) -> float:
	"""What the customer still owes. Never negative on a well-formed invoice."""
	return round_paise(to_decimal(grand_total) - to_decimal(paid_amount(payments)))


def status_for(current: str, grand_total, payments) -> str:
	"""The status the payment rows imply. Void and Draft are never overridden.

	Void is terminal: a payment recorded against a voided invoice must not quietly
	bring it back to life. Draft has not been issued, so it cannot be "partially
	paid" either -- and no endpoint in this module lets a payment reach a draft.
	"""
	if current == STATUS_VOID:
		return STATUS_VOID
	if current == STATUS_DRAFT:
		return STATUS_DRAFT

	paid = paid_amount(payments)
	if paid <= 0:
		return STATUS_SENT
	if to_decimal(paid) >= to_decimal(grand_total):
		return STATUS_PAID
	return STATUS_PARTIALLY_PAID


def is_overdue(doc, on=None) -> bool:
	"""True when money is still owed and the due date has passed.

	Computed, never stored -- see `STATUSES`. A Void or Paid invoice is never
	overdue whatever its due date says.
	"""
	if doc.get("status") not in OPEN_STATUSES:
		return False
	due = doc.get("due_date")
	if not due:
		return False
	return frappe.utils.getdate(due) < frappe.utils.getdate(on or frappe.utils.nowdate())


# --- the refusals ----------------------------------------------------------


def is_b2c(doc) -> bool:
	"""True when the recipient has given no GSTIN. A person, not a business."""
	return not frappe.utils.cstr(doc.get("customer_gstin") or "").strip()


def finalize_blockers(doc, totals: dict) -> list[str]:
	"""Every reason this invoice may not be issued yet, in words a user can act on.

	Returned as a list rather than thrown one at a time: an agent who has to fix
	three fields should learn all three at once, not discover them over three
	failed attempts.
	"""
	problems = []

	if not (doc.get("items") or []):
		# Planner addendum (c). An invoice with no lines has no taxable value, no
		# SAC and nothing to describe -- it fails Rule 46 in four places at once,
		# and the useful thing to say is the first one.
		problems.append(_("This invoice has no item lines. Add at least one line before you issue it."))

	if is_b2c(doc) and flt(totals.get("grand_total")) >= B2C_RECIPIENT_THRESHOLD:
		missing = [
			label
			for field, label in (
				("customer_name", _("name")),
				("customer_address", _("address")),
				("customer_state_code", _("state code")),
			)
			if not frappe.utils.cstr(doc.get(field) or "").strip()
		]
		if missing:
			problems.append(
				_(
					"This is an unregistered-customer invoice of {0} or more, so GST "
					"Rule 46 requires the recipient's {1}. Fill it in before you issue "
					"the invoice."
				).format(frappe.utils.fmt_money(B2C_RECIPIENT_THRESHOLD, currency="INR"), ", ".join(missing))
			)

	profile_gaps = company_profile_gaps()
	if profile_gaps:
		problems.append(
			_("Company Profile is missing {0}. A GST invoice cannot be issued without it.").format(
				", ".join(profile_gaps)
			)
		)

	return problems


def company_profile_gaps() -> list[str]:
	"""The supplier fields Rule 46 makes mandatory, and which are still empty."""
	profile = get_company_profile()
	return [
		label
		for field, label in (
			("legal_name", _("legal name")),
			("address", _("address")),
			("state_code", _("state code")),
			("gstin", _("GSTIN")),
		)
		if not frappe.utils.cstr(profile.get(field) or "").strip()
	]


def rule_47_warning(doc) -> str:
	"""The non-blocking late-issue warning, or an empty string.

	Rule 47 gives 30 days from the supply of a service. This app WARNS and issues
	anyway: refusing would leave an agency unable to invoice a late-noticed
	booking at all, which does not make them more compliant, only unpaid.
	"""
	service_date = doc.get("service_date")
	invoice_date = doc.get("invoice_date")
	if not service_date or not invoice_date:
		return ""

	deadline = frappe.utils.add_days(frappe.utils.getdate(service_date), RULE_47_DAYS)
	if frappe.utils.getdate(invoice_date) <= deadline:
		return ""

	return _(
		"This invoice is dated more than {0} days after the service date. GST Rule 47 "
		"asks for the invoice within {0} days."
	).format(RULE_47_DAYS)


# --- the company profile ---------------------------------------------------


def get_company_profile():
	"""The agency's own details, cached. Feeds invoices, quotes and the PDF."""
	return frappe.get_cached_doc(COMPANY_PROFILE_DOCTYPE)


def validate_gstin(gstin, state_code=None) -> str:
	"""A GSTIN's shape, and its agreement with the state code. Raises otherwise.

	The first two characters of a GSTIN ARE the state code, so a profile whose
	two fields disagree is certainly wrong in one of them. Checking that is worth
	more than a checksum this app could get wrong: it catches the real mistake,
	which is a profile copied from another branch.
	"""
	gstin = frappe.utils.cstr(gstin or "").strip().upper()
	if not gstin:
		return ""

	if not GSTIN_PATTERN.match(gstin):
		frappe.throw(_("{0} is not a valid GSTIN. A GSTIN is 15 characters.").format(gstin))

	state_code = frappe.utils.cstr(state_code or "").strip()
	if state_code and gstin[:2] != state_code:
		frappe.throw(
			_(
				"The GSTIN {0} belongs to state code {1}, but the state code is set to "
				"{2}. Correct one of them."
			).format(gstin, gstin[:2], state_code)
		)

	return gstin


def validate_state_code(state_code) -> str:
	"""A two-digit GST state code, or an empty string.

	Deliberately not checked against a hard-coded list of states. The state-code
	table is exactly the kind of reference data this project ships as
	admin-editable placeholders (see `CRM SAC Code`), and a list compiled from a
	single source would refuse a legitimate code the day one is added.
	"""
	value = frappe.utils.cstr(state_code or "").strip()
	if not value:
		return ""
	if not STATE_CODE_PATTERN.match(value):
		frappe.throw(_("{0} is not a GST state code. A state code is two digits.").format(value))
	return value


def einvoice_note_for(threshold) -> str:
	"""The one line Company Profile shows about e-invoicing. Never a promise.

	Takes the threshold as an argument rather than reading the profile, because
	its only caller is the profile's own `validate` and a read of the cached
	Single from inside its own save would return the value being replaced.

	The wording says what this module does NOT do, because that is the part that
	matters to whoever reads it, and it names the source so a reader can tell how
	old the figure is instead of trusting it.
	"""
	return _(
		"E-invoicing applies above {0} turnover — this module does not generate IRN. "
		"Threshold last recorded from {1}; confirm the current figure with your CA."
	).format(frappe.utils.fmt_money(flt(threshold), currency="INR"), EINVOICE_THRESHOLD_SOURCE)


def einvoice_note() -> str:
	"""The stored note, or one built from the stored threshold. For the API."""
	profile = get_company_profile()
	stored = frappe.utils.cstr(profile.get("einvoice_note") or "").strip()
	if stored:
		return stored
	return einvoice_note_for(flt(profile.get("einvoice_threshold")) or EINVOICE_THRESHOLD_RUPEES)


# --- the UPI QR ------------------------------------------------------------


def upi_uri(vpa, amount) -> str:
	"""The payment URI the QR encodes: `upi://pay?pa=<vpa>&am=<amount>`.

	Exactly two parameters, in this order. A UPI app will accept more -- payee
	name, currency, a note -- and a richer URI would be friendlier, but the shape
	here is the one the acceptance criteria name and the one the decoder test
	asserts, and a payment URI is not the place to be creative.

	`amount` is the REMAINING balance, not the grand total (planner addendum d).
	A customer who has paid the deposit and then scans the QR on the same PDF
	must be asked for the balance, not for the whole invoice again.
	"""
	vpa = frappe.utils.cstr(vpa or "").strip()
	if not vpa:
		return ""
	return f"upi://pay?pa={vpa}&am={round_paise(amount):.2f}"


def upi_qr_data_uri(vpa, amount) -> str:
	"""The QR as a PNG data URI, or an empty string when there is no VPA.

	`segno` is a pure-Python encoder with no dependencies of its own and no
	system libraries -- see demo-package/specs/stage5-3-notes.md for why that
	mattered. Nothing here touches the network: the QR is computed from the URI
	string, on this machine, every time the PDF is rendered.

	Rendered at render time rather than stored, so a re-download after a payment
	regenerates the code for the new balance.
	"""
	uri = upi_uri(vpa, amount)
	if not uri:
		return ""

	import base64
	import io

	import segno

	buffer = io.BytesIO()
	# `scale` is pixels per module. 6 gives a code around 250px wide, which is
	# large enough for a phone camera off an A4 print and small enough not to
	# push the totals table onto a second page.
	segno.make(uri, error="m").save(buffer, kind="png", scale=6, border=2)
	return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
