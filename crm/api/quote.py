# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

"""Quotes: turn a deal's products into a PDF the customer can read.

What this is
------------
One action on the deal record. The agent presses "Create quote", checks the line
items and the terms in a modal, and then either downloads the PDF or sends it on
WhatsApp. The PDF is the `Travel Quote A4` print format rendered from the deal's
own products -- there is no separate quote record to keep in step with the deal.

Why the file is private and the link is tokenised
-------------------------------------------------
The itinerary send (`crm.api.itinerary.send_via_whatsapp`) writes a PUBLIC copy
of its PDF so Meta can fetch the media, and sweeps that copy away two hours
later. That works, and the master spec says plainly it is not good enough for a
quote: while the file exists, anyone who learns its URL can read it, and nothing
records who did.

A quote therefore keeps its PDF PRIVATE and hands out a `CRM Document Link`
instead: a 32-character token on a whitelisted route that reads the private file
and streams it. The link expires, can be revoked, and every fetch is written to
`CRM Document Link View`. Re-quoting mints a new token and retires the old one,
so a price the agency has withdrawn cannot still be read at yesterday's URL.

The bot problem, and why the timeline is honest
-----------------------------------------------
Meta fetches the media from its own servers seconds after the send. Counting
that as "the customer opened your quote" would make the signal worthless within
a day. `crm.document_links.classify_fetch` flags a fetch as the platform's when
its user agent looks like a crawler AND it is the first fetch on that link. Only
the rest reach the deal's timeline.

Endpoint authorization (master spec §3), stated here and in
`crm/tests/test_quote.py`:

* `get_quote_preview` -- any signed-in user with `read` on the named CRM Deal,
  checked with `frappe.has_permission(..., doc=name)` so the org-hierarchy
  `has_deal_permission` hook decides. The deal name is the only scope input.
* `download_quote` -- `read` AND `print` on the named deal. Writes a private File
  attached to that deal and nothing else.
* `send_quote_on_whatsapp` -- `write` on the named deal (an outbound message
  writes to its timeline), and the recipient number must be one the deal already
  holds -- `crm.api.whatsapp.create_whatsapp_message` enforces that, and this
  module never passes a number that came from the request.
* `view` -- **Guest**, deliberately. The token IS the authorization: 128 bits,
  single-purpose, expiring, revocable, and bound to one file. A token that does
  not exist, one that was revoked and one that expired all get the same answer,
  so the route cannot be used to find out which tokens were ever real. No deal
  field other than the rendered PDF is reachable through it.
* `cleanup_quote_links` -- scheduler only.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from crm import document_links

DEAL_DOCTYPE = "CRM Deal"
PRINT_FORMAT = "Travel Quote A4"
PURPOSE = "Quote"

# How long a quote link stays readable. Long enough that a customer who opens the
# message the next morning still sees it; short enough that a chat forwarded
# around a family group does not become a permanent public URL.
LINK_TTL_HOURS = 24 * 14

# The starting terms. Editable in the modal on every quote, and stored on the
# link so the record of what the customer was sent survives an edit to the deal.
# A constant rather than a setting because FCRM Settings is owned by another
# worker this stage -- see demo-package/specs/stage3a-notes.md.
DEFAULT_TERMS = (
	"This quotation is valid for 14 days from the date above.",
	"Prices are subject to availability at the time of booking.",
	"A deposit is required to confirm the booking; the balance is due before departure.",
	"Cancellation and amendment charges apply as per our booking conditions.",
)

MAX_TERMS_LENGTH = 4000


def get_deal(deal: str, permtype: str = "read"):
	"""Read one deal, or refuse. Every endpoint in this module starts here."""
	if not frappe.db.exists(DEAL_DOCTYPE, deal):
		# A deal that does not exist and a deal the caller may not see get the
		# same answer, so the endpoint is not an existence oracle.
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	if not frappe.has_permission(DEAL_DOCTYPE, permtype, deal):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	return frappe.get_doc(DEAL_DOCTYPE, deal)


# --- the numbers -----------------------------------------------------------


def money(value, currency: str | None) -> str:
	"""One amount, formatted once, on the server."""
	return frappe.utils.fmt_money(flt(value), currency=currency or None)


def quantity(value) -> str:
	"""A quantity without a trailing `.0` on the common whole-number case."""
	number = flt(value)
	return str(cint(number)) if number == cint(number) else frappe.utils.fmt_money(number)


def discount_label(row, currency: str | None) -> str:
	"""What the discount column says for one product row.

	A percentage and an amount are both stored, and a row can carry either. The
	percentage is shown when there is one because that is what the agent
	negotiated; the amount is the fallback.
	"""
	if flt(row.get("discount_percentage")):
		return f"{flt(row.get('discount_percentage')):g}%"
	if flt(row.get("discount_amount")):
		return money(row.get("discount_amount"), currency)
	return "—"


def quote_rows(doc) -> list[dict]:
	"""The deal's products, formatted for the PDF and for the preview modal."""
	rows = []
	for row in doc.get("products") or []:
		rows.append(
			{
				"name": frappe.utils.cstr(row.get("product_name") or row.get("product_code") or ""),
				"code": frappe.utils.cstr(row.get("product_code") or ""),
				"rate": money(row.get("rate"), doc.currency),
				"qty": quantity(row.get("qty")),
				"discount": discount_label(row, doc.currency),
				"amount": money(row_amount(row), doc.currency),
				"net_amount": money(row_net_amount(row), doc.currency),
			}
		)
	return rows


def row_amount(row) -> float:
	"""One row's gross amount, computed when it was never stored."""
	amount = flt(row.get("amount"))
	return amount if amount else flt(row.get("rate")) * flt(row.get("qty"))


def row_net_amount(row) -> float:
	"""One row's amount after its discount, computed when it was never stored."""
	net = flt(row.get("net_amount"))
	if net:
		return net
	gross = row_amount(row)
	if flt(row.get("discount_amount")):
		return gross - flt(row.get("discount_amount"))
	if flt(row.get("discount_percentage")):
		return gross * (1 - flt(row.get("discount_percentage")) / 100.0)
	return gross


def quote_totals(doc) -> dict:
	"""Subtotal, discount and total, summed from the rows that are printed.

	Summed here rather than read off `CRM Deal.total` / `.net_total`, and that is
	a deliberate correction. Those two fields are maintained by a CLIENT script
	(`crm/fcrm/doctype/crm_deal/crm_deal.js`), so they are filled in only when a
	human edited the products grid in the browser: a deal created by the API, by
	an import or by the demo seeder carries products worth lakhs and a stored
	total of zero. A quote whose lines add up to one figure and whose total says
	another is worse than no quote at all.

	The stored fields are the fallback for a deal with no product rows, which is
	the one case where there is nothing to add up.
	"""
	rows = doc.get("products") or []
	if rows:
		total = sum(row_amount(row) for row in rows)
		net_total = sum(row_net_amount(row) for row in rows)
	else:
		total = flt(doc.get("total"))
		net_total = flt(doc.get("net_total")) or total

	discount = total - net_total if total > net_total else 0.0
	return {
		"total": money(total, doc.currency),
		"discount": money(discount, doc.currency) if discount else "",
		"net_total": money(net_total or total, doc.currency),
		"currency": doc.currency,
	}


def customer_meta(doc, sequence: int) -> dict:
	"""Who the quote is for, and what it is called."""
	name = frappe.utils.cstr(doc.get("lead_name") or "").strip()
	if not name:
		name = " ".join(
			x
			for x in (
				frappe.utils.cstr(doc.get("first_name") or "").strip(),
				frappe.utils.cstr(doc.get("last_name") or "").strip(),
			)
			if x
		)

	today = frappe.utils.nowdate()
	return {
		"number": f"{doc.name}-Q{sequence}",
		"date": frappe.utils.formatdate(today, "d MMM yyyy"),
		"valid_until": frappe.utils.formatdate(frappe.utils.add_days(today, 14), "d MMM yyyy"),
		"customer_name": name,
		"organization": frappe.utils.cstr(doc.get("organization") or doc.get("organization_name") or ""),
		"customer_email": frappe.utils.cstr(doc.get("email") or ""),
		"customer_phone": frappe.utils.cstr(doc.get("mobile_no") or doc.get("phone") or ""),
	}


def agency_details() -> dict:
	"""Who the PDF says it is from. Shared with the itinerary print format."""
	from crm.fcrm.doctype.crm_itinerary.crm_itinerary import get_agency_details

	details = dict(get_agency_details() or {})
	# The app stores no agency postal address anywhere yet. The template shows a
	# placeholder rather than an empty line, so the gap is visible before the
	# quote reaches a customer instead of after.
	details.setdefault("address", "")
	return details


def terms_lines(terms=None) -> list[str]:
	"""The terms, as printable bullet lines. Falls back to the defaults."""
	if terms is None:
		return list(DEFAULT_TERMS)
	text = frappe.utils.cstr(terms)[:MAX_TERMS_LENGTH]
	lines = [line.strip() for line in text.splitlines() if line.strip()]
	return lines


def quote_sequence(deal: str) -> int:
	"""How many quotes this deal has produced, including retired ones, plus one."""
	return (
		frappe.db.count(
			document_links.LINK_DOCTYPE,
			{"reference_doctype": DEAL_DOCTYPE, "reference_name": deal, "purpose": PURPOSE},
		)
		+ 1
	)


# --- rendering -------------------------------------------------------------


def install_print_format():
	"""Create or refresh the A4 quote format from the app's template.

	Same shape as `crm.api.itinerary.install_print_format`: the HTML lives in a
	file so it is reviewable in git, and the Print Format row is rewritten only
	when the file actually changed, so a migrate on an unchanged app does not
	churn the version history and an administrator's own edit survives.
	"""
	from crm.api.itinerary import same_value

	if not frappe.db.exists("DocType", DEAL_DOCTYPE):
		return

	path = frappe.get_app_path("crm", "templates", "print_formats", "travel_quote_a4.html")
	try:
		with open(path) as template:
			html = template.read()
	except OSError:
		frappe.log_error(f"missing template: {path}", "CRM Quote: print format not installed")
		return

	values = {
		"doc_type": DEAL_DOCTYPE,
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


def decorate(doc, terms=None, sequence: int = 1):
	"""Put everything the print format reads onto the document, in memory.

	Nothing is saved. A quote's terms belong to the quote, not to the deal, and
	writing them onto the record would change the deal every time somebody
	previewed a PDF.
	"""
	doc.quote_agency = agency_details()
	doc.quote_rows = quote_rows(doc)
	doc.quote_totals = quote_totals(doc)
	doc.quote_terms = terms_lines(terms)
	doc.quote_meta = customer_meta(doc, sequence)
	return doc


def render(doc, terms=None, sequence: int = 1) -> bytes:
	document_links.ensure_print_format(PRINT_FORMAT, install_print_format)
	return document_links.render_print_pdf(
		DEAL_DOCTYPE, doc.name, PRINT_FORMAT, doc=decorate(doc, terms, sequence)
	)


def file_stem(doc) -> str:
	organization = frappe.utils.cstr(doc.get("organization") or doc.get("lead_name") or "").strip()
	return f"Quote-{organization}" if organization else f"Quote-{doc.name}"


def build_pdf(doc, terms=None):
	"""Render the quote and attach it to the deal, privately. Returns the File."""
	sequence = quote_sequence(doc.name)
	content = render(doc, terms, sequence)
	name = document_links.pdf_file_name(file_stem(doc), sequence)
	return document_links.attach_pdf(DEAL_DOCTYPE, doc.name, name, content, is_private=1), sequence


# --- endpoints -------------------------------------------------------------


@frappe.whitelist()
def get_quote_preview(deal: str) -> dict:
	"""Everything the preview modal shows, computed by the same code as the PDF."""
	doc = get_deal(deal, "read")
	sequence = quote_sequence(doc.name)
	return {
		"deal": doc.name,
		"rows": quote_rows(doc),
		"totals": quote_totals(doc),
		"terms": "\n".join(DEFAULT_TERMS),
		"meta": customer_meta(doc, sequence),
		"agency": agency_details(),
		"has_products": bool(doc.get("products")),
		"whatsapp_numbers": sorted(whatsapp_numbers(doc)),
	}


def whatsapp_numbers(doc) -> set:
	"""The numbers this deal already holds. Never a number from a request."""
	from crm.api.whatsapp import get_reference_whatsapp_numbers

	try:
		return get_reference_whatsapp_numbers(doc)
	except Exception:
		return set()


@frappe.whitelist(methods=["POST"])
def download_quote(deal: str, terms: str | None = None) -> dict:
	"""Render the quote and hand back the private attachment's URL."""
	doc = get_deal(deal, "read")
	if not frappe.has_permission(DEAL_DOCTYPE, "print", deal):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	file_doc, _sequence = build_pdf(doc, terms)
	return {"file_url": file_doc.file_url, "file_name": file_doc.file_name}


@frappe.whitelist(methods=["POST"])
def send_quote_on_whatsapp(deal: str, terms: str | None = None) -> dict:
	"""Send the quote to the deal's WhatsApp number as a document.

	Returns a structured result rather than raising on a send failure, because
	the commonest failure is not a bug: outside Meta's 24-hour customer-service
	window a free-form document send is rejected, and the agent needs to be told
	that in words they can act on. The itinerary send answers the same way.

	What Meta is handed is the TOKENISED link, not a public file. Meta fetches it
	like any other URL, that fetch lands in the view log flagged as the
	platform's, and the customer's own open later is the first row that counts.
	"""
	from crm.api.whatsapp import create_whatsapp_message

	doc = get_deal(deal, "write")

	numbers = sorted(whatsapp_numbers(doc))
	if not numbers:
		return {
			"success": False,
			"reason": "no_number",
			"error": _("This deal has no WhatsApp number. Add a mobile number first."),
		}

	file_doc, sequence = build_pdf(doc, terms)
	link = document_links.create_link(
		DEAL_DOCTYPE,
		doc.name,
		file_doc,
		purpose=PURPOSE,
		payload={"terms": terms_lines(terms), "totals": quote_totals(doc), "sequence": sequence},
		ttl_hours=LINK_TTL_HOURS,
	)
	url = document_links.public_url(link.token)

	try:
		message = create_whatsapp_message(
			reference_doctype=DEAL_DOCTYPE,
			reference_name=doc.name,
			message=whatsapp_summary(doc),
			to=numbers[0],
			attach=url,
			reply_to="",
			content_type="document",
		)
	except frappe.PermissionError:
		# A permission refusal is a real error, not a delivery problem. The link
		# is retired first: nothing reached the customer either way, and a live
		# token nobody was given is a loose end.
		document_links.revoke_links(DEAL_DOCTYPE, doc.name, PURPOSE)
		raise
	except Exception as error:
		frappe.log_error(frappe.get_traceback(), "CRM Quote: WhatsApp send failed")
		document_links.revoke_links(DEAL_DOCTYPE, doc.name, PURPOSE)
		return {
			"success": False,
			"reason": "send_failed",
			"error": frappe.utils.cstr(error),
			"hint": _(
				"WhatsApp only accepts a free-form message within 24 hours of the "
				"customer's last message. Ask the customer to message first, or send "
				"the PDF by email."
			),
		}

	return {
		"success": True,
		"message": message,
		"to": numbers[0],
		"file_url": file_doc.file_url,
		"link_url": url,
	}


def whatsapp_summary(doc) -> str:
	"""The short text that rides along with the PDF."""
	name = frappe.utils.cstr(doc.get("lead_name") or doc.get("organization") or "").strip()
	greeting = _("Hi {0},").format(name) if name else _("Hello,")
	return "\n".join(
		[
			greeting,
			_("Here is your quotation. Please review it and tell us if anything should change."),
		]
	)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def view(token: str | None = None):
	"""Stream the quote behind a token, and write down that it was read.

	Guest by design -- see the module docstring. Sets `frappe.local.flags.commit`
	because the framework only commits a GET when it is told to, and the view row
	is the whole point of this route.
	"""
	link = document_links.resolve_link(token)
	if not link:
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


def cleanup_quote_links(older_than_hours: int = LINK_TTL_HOURS) -> int:
	"""Hourly: retire expired links and delete the files they held.

	Never raises: a scheduler job that throws takes the rest of its queue down.
	`crm.document_links.expire_links` owns both halves.
	"""
	return document_links.expire_links(older_than_hours)


def install_quote_print_format():
	"""after_migrate entry point. See demo-package/specs/stage3a-notes.md."""
	install_print_format()
