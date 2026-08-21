# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the invoice doctype and its endpoints (design note 29).

The classes named `TestAC1` .. `TestAC10` are the design note's acceptance
criteria, one class each, so a reader can check the feature against the note
without reading the implementation. `crm/tests/test_invoicing.py` covers the
arithmetic on its own; this file is about the record, the endpoints and who may
call them.

Nothing here reaches a provider. `crm.api.email.send_email` and
`crm.api.whatsapp.create_whatsapp_message` are replaced by recorders wherever a
send is exercised, and `crm.document_links.render_print_pdf` is stubbed so the
suite stays off wkhtmltopdf.

Endpoint authorization (master spec §3), asserted in `TestAC9Permissions` and
`TestAC10Flag` rather than described:

* Every endpoint in `crm.api.invoices` calls `require_module()` first and refuses
  with `frappe.PermissionError` while `invoices_enabled` is off. The public
  `view` route answers like a dead token instead, so it says nothing about the
  site's configuration.
* `convert_deal` -- `write` on the named CRM Deal AND `create` on CRM Invoice.
* `get_invoice`, `get_invoices_for_deal`, `get_next_number`, `get_tiles` --
  `read`. Row scope comes from `CRM Invoice.deal` through
  `get_invoice_permission_query_conditions`, which wraps the deal's own
  org-hierarchy conditions. `get_tiles` aggregates over `frappe.get_list`.
* `finalize`, `record_payment`, `set_reminders_paused` -- `write` on the invoice.
* `void_invoice` -- `write` PLUS a manager role, checked in the endpoint and
  again inside `CRMInvoice.void`.
* `share_invoice`, `download_invoice`, `send_invoice_email`,
  `send_invoice_on_whatsapp` -- `read` and `print`; the two send endpoints also
  need `write`. Addresses come off the record, never off the request.
* `view` -- Guest. The token is the authorization.
"""

import base64
import io
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm import document_links, invoice_reminders, invoicing, outbound
from crm.api import invoices
from crm.fcrm.doctype.crm_invoice.crm_invoice import add_invoice_roles

DOCTYPE = "CRM Invoice"
DEAL_DOCTYPE = "CRM Deal"
SETTINGS = "FCRM Settings"
PROFILE = "CRM Company Profile"
FOLLOWUP_SETTINGS = "CRM Followup Settings"

FLAG = invoices.FLAG_INVOICES
REMINDER_FLAG = invoices.FLAG_INVOICE_REMINDERS

AGENT = "invoice-agent@example.com"
OUTSIDER = "invoice-outsider@example.com"
MANAGER = "invoice-manager@example.com"

COMPANY_STATE = "27"
OTHER_STATE = "29"
COMPANY_GSTIN = "27AAPFU0939F1ZV"
CUSTOMER_GSTIN = "29AAPFU0939F1ZV"
VPA = "agency@okbank"


def stub_pdf() -> bytes:
	"""A real, minimal PDF.

	Not a placeholder byte string: the File doctype runs every `.pdf` upload
	through `pdf_contains_js`, which parses it. Anything unparseable is rejected
	before the row is written, so a stub has to be a genuine document.
	"""
	from pypdf import PdfWriter

	writer = PdfWriter()
	writer.add_blank_page(width=595, height=842)
	buffer = io.BytesIO()
	writer.write(buffer)
	return buffer.getvalue()


def deal_status() -> str:
	status = frappe.db.get_value("CRM Deal Status", {"type": ["!=", "Lost"]}, "name")
	if not status:
		status = (
			frappe.get_doc(
				{"doctype": "CRM Deal Status", "deal_status": "Qualification", "position": 1, "type": "Open"}
			)
			.insert(ignore_permissions=True)
			.name
		)
	return status


def make_user(user: str, role: str):
	if not frappe.db.exists("User", user):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": user,
				"first_name": user.split("@")[0],
				"send_welcome_email": 0,
				"roles": [{"role": role}],
			}
		).insert(ignore_permissions=True)
	return user


def clear_single_cache(doctype: str):
	"""Drop a Single from the document cache.

	`crm.invoicing.get_company_profile` reads through `frappe.get_cached_doc`, and
	Redis is not rolled back with the test transaction. Without this a value one
	test wrote would survive its own rollback and be read by the next one.
	"""
	try:
		frappe.clear_document_cache(doctype, doctype)
	except TypeError:
		frappe.clear_document_cache(doctype)


class InvoiceTestCase(FrappeTestCase):
	"""A filled company profile, the module flag on, one deal with two products."""

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.set_single_value(SETTINGS, FLAG, 1)
		frappe.db.set_single_value(SETTINGS, REMINDER_FLAG, 0)
		self.set_profile()
		self.disable_quiet_hours()
		self.deal = self.new_deal()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		# Redis is not rolled back. A cached profile or settings row would leak
		# this test's configuration into every module that runs after it.
		clear_single_cache(PROFILE)
		clear_single_cache(FOLLOWUP_SETTINGS)

	def set_profile(self, **overrides):
		profile = frappe.get_doc(PROFILE)
		profile.update(
			{
				"legal_name": "Parama Travels Private Limited",
				"trade_name": "Parama Travels",
				"address": "12 MG Road\nPune 411001",
				"state": "Maharashtra",
				"state_code": COMPANY_STATE,
				"gstin": COMPANY_GSTIN,
				"upi_vpa": VPA,
				"invoice_number_prefix": "INV/",
				"default_sac": None,
				"terms_default": "Payment due on receipt.",
				**overrides,
			}
		)
		profile.save(ignore_permissions=True)
		clear_single_cache(PROFILE)
		return profile

	def disable_quiet_hours(self):
		"""Equal start and end switches the window off — see `in_quiet_hours`."""
		settings = frappe.get_doc(FOLLOWUP_SETTINGS)
		settings.quiet_hours_start = "00:00:00"
		settings.quiet_hours_end = "00:00:00"
		settings.save(ignore_permissions=True)
		clear_single_cache(FOLLOWUP_SETTINGS)

	def new_contact(self):
		contact = frappe.new_doc("Contact")
		contact.first_name = "Priya"
		contact.last_name = "Sharma"
		contact.append("email_ids", {"email_id": "priya.invoice@example.com", "is_primary": 1})
		contact.append("phone_nos", {"phone": "+919876543211", "is_primary_mobile_no": 1})
		return contact.insert(ignore_permissions=True)

	def new_deal(self, with_products=True):
		contact = self.new_contact()
		doc = frappe.new_doc(DEAL_DOCTYPE)
		doc.update(
			{
				"status": deal_status(),
				"organization_name": "Sharma Travels",
				"first_name": "Priya",
				"last_name": "Sharma",
				"currency": "INR",
				# `CRM Deal.validate_forecasting_fields` makes both mandatory when
				# forecasting is on, which it is on this site.
				"expected_deal_value": 121000,
				"expected_closure_date": frappe.utils.add_days(frappe.utils.nowdate(), 30),
			}
		)
		doc.append("contacts", {"contact": contact.name, "is_primary": 1})
		if with_products:
			# 2 x 65000 less 10% = 117000 net; plus 4000 = 121000 taxable.
			doc.append(
				"products",
				{
					"product_name": "Bali 5N/6D package",
					"qty": 2,
					"rate": 65000,
					"amount": 130000,
					"discount_percentage": 10,
					"net_amount": 117000,
				},
			)
			doc.append(
				"products", {"product_name": "Airport transfers", "qty": 1, "rate": 4000, "amount": 4000}
			)
		return doc.insert(ignore_permissions=True)

	# --- fixtures ---------------------------------------------------------

	def new_invoice(self, **overrides):
		"""A Draft invoice on the deal, through the real conversion endpoint."""
		payload = invoices.convert_deal(self.deal.name)
		doc = frappe.get_doc(DOCTYPE, payload["name"])
		if overrides:
			doc.update(overrides)
			doc.save()
		return doc

	def ready_invoice(self, **overrides):
		"""A Draft that will pass `finalize_blockers`.

		The deal carries no postal address, and the invoice it produces is over the
		Rule 46 B2C threshold, so a draft straight off `convert_deal` deliberately
		REFUSES to be issued. That refusal is criterion 4 and is tested on its own;
		every other class needs an invoice that can actually be issued.
		"""
		defaults = {
			"customer_name": "Priya Sharma",
			"customer_address": "7 Residency Road\nPune 411001",
			"customer_state_code": COMPANY_STATE,
		}
		defaults.update(overrides)
		return self.new_invoice(**defaults)

	def issued_invoice(self, **overrides):
		doc = self.ready_invoice(**overrides)
		result = doc.finalize()
		self.assertTrue(result["issued"], msg=f"fixture failed to issue: {result.get('blockers')}")
		return frappe.get_doc(DOCTYPE, doc.name)

	def stub_render(self):
		return patch.object(document_links, "render_print_pdf", return_value=stub_pdf())


# --- AC1 -------------------------------------------------------------------


class TestAC1ConvertDeal(InvoiceTestCase):
	"""One click on a deal gives a Draft with correct items, totals and split."""

	def test_the_draft_carries_the_deal_products_as_lines(self):
		doc = self.new_invoice()
		self.assertEqual(doc.status, invoicing.STATUS_DRAFT)
		self.assertEqual([row.description for row in doc.items], ["Bali 5N/6D package", "Airport transfers"])

	def test_the_lines_bill_the_net_amount_the_agent_negotiated(self):
		# 2 x 65000 = 130000 less 10% = 117000 net, so 58500 a head.
		doc = self.new_invoice()
		self.assertEqual(doc.items[0].qty, 2)
		self.assertEqual(doc.items[0].rate, 58500.0)
		self.assertEqual(doc.items[0].amount, 117000.0)
		self.assertEqual(doc.items[1].amount, 4000.0)

	def test_the_totals_are_hand_computable_and_intra_state(self):
		# Taxable 117000 + 4000 = 121000. 18% = 21780 raw tax.
		# Same state, so half each: 10890 CGST + 10890 SGST = 21780.
		# Grand total 121000 + 21780 = 142780. Nothing to round.
		doc = self.new_invoice()
		self.assertEqual(doc.taxable_total, 121000.0)
		self.assertEqual(doc.cgst_amount, 10890.0)
		self.assertEqual(doc.sgst_amount, 10890.0)
		self.assertEqual(doc.igst_amount, 0.0)
		self.assertEqual(doc.grand_total, 142780.0)
		self.assertEqual(doc.rounding_adjustment, 0.0)
		self.assertTrue(doc.intra_state)

	def test_a_different_place_of_supply_becomes_one_igst_figure(self):
		doc = self.new_invoice(place_of_supply=OTHER_STATE)
		self.assertEqual(doc.cgst_amount, 0.0)
		self.assertEqual(doc.sgst_amount, 0.0)
		self.assertEqual(doc.igst_amount, 21780.0)
		self.assertEqual(doc.grand_total, 142780.0)
		self.assertFalse(doc.intra_state)

	def test_tour_package_mode_taxes_the_gross_at_five_percent(self):
		# 121000 at 5% = 6050 raw; half 3025 each; grand 127050.
		doc = self.new_invoice(mode=invoicing.MODE_TOUR_PACKAGE)
		self.assertEqual(doc.cgst_amount, 3025.0)
		self.assertEqual(doc.sgst_amount, 3025.0)
		self.assertEqual(doc.grand_total, 127050.0)

	def test_the_mandatory_statement_appears_only_in_tour_package_mode(self):
		"""Criterion 3, checked here because it is one line off the same record."""
		commission = self.new_invoice()
		self.assertEqual(invoices.tour_package_statement(commission), "")
		tour = self.new_invoice(mode=invoicing.MODE_TOUR_PACKAGE)
		self.assertEqual(invoices.tour_package_statement(tour), invoicing.TOUR_PACKAGE_STATEMENT)
		self.assertIn("gross amount", invoices.tour_package_statement(tour))

	def test_the_supplier_state_is_snapshotted_not_read_live(self):
		doc = self.new_invoice()
		self.assertEqual(doc.company_state_code, COMPANY_STATE)
		self.set_profile(state_code="29", gstin="29AAPFU0939F1ZV")
		doc.reload()
		doc.save()
		self.assertEqual(doc.company_state_code, COMPANY_STATE)

	def test_a_client_supplied_total_is_overwritten_not_honoured(self):
		doc = self.new_invoice()
		doc.grand_total = 1.0
		doc.taxable_total = 1.0
		doc.save()
		self.assertEqual(doc.grand_total, 142780.0)
		self.assertEqual(doc.taxable_total, 121000.0)

	def test_the_draft_starts_with_a_status_history_row(self):
		doc = self.new_invoice()
		self.assertEqual([row.to_status for row in doc.status_log], [invoicing.STATUS_DRAFT])

	def test_the_mode_default_comes_from_the_company_profile(self):
		self.set_profile(tour_package_mode_default=1)
		doc = self.new_invoice()
		self.assertEqual(doc.mode, invoicing.MODE_TOUR_PACKAGE)


# --- AC2 -------------------------------------------------------------------


class TestAC2NumberLock(InvoiceTestCase):
	"""Finalize locks the number; bad numbers are refused; two cannot share one."""

	def test_a_draft_has_no_number_at_all(self):
		doc = self.ready_invoice()
		self.assertIsNone(doc.invoice_number)
		self.assertIsNone(doc.number_locked_at)

	def test_two_drafts_can_coexist_without_colliding_on_the_unique_index(self):
		"""An unset unique column must be NULL, not "". MariaDB allows many NULLs
		and exactly one empty string."""
		first = self.ready_invoice()
		second = self.ready_invoice()
		self.assertIsNone(first.invoice_number)
		self.assertIsNone(second.invoice_number)

	def test_finalize_allocates_locks_and_marks_it_sent(self):
		doc = self.ready_invoice()
		result = doc.finalize()
		self.assertTrue(result["issued"])
		doc.reload()
		self.assertEqual(doc.status, invoicing.STATUS_SENT)
		self.assertTrue(doc.number_locked_at)
		self.assertEqual(doc.invoice_number, result["invoice_number"])
		self.assertTrue(doc.invoice_number.startswith(invoicing.number_prefix()))
		self.assertEqual(invoicing.validate_number(doc.invoice_number), doc.invoice_number)

	def test_the_issue_is_written_to_the_status_history(self):
		doc = self.issued_invoice()
		moves = [(row.from_status, row.to_status) for row in doc.status_log]
		self.assertIn((invoicing.STATUS_DRAFT, invoicing.STATUS_SENT), moves)

	def test_finalizing_twice_keeps_the_first_number(self):
		doc = self.issued_invoice()
		number = doc.invoice_number
		result = doc.finalize()
		self.assertFalse(result["issued"])
		self.assertTrue(result["already_issued"])
		self.assertEqual(result["invoice_number"], number)

	def test_a_seventeen_character_number_is_refused_by_the_record(self):
		doc = self.ready_invoice()
		doc.invoice_number = "A" * 17
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_a_bad_charset_number_is_refused_by_the_record(self):
		doc = self.ready_invoice()
		doc.invoice_number = "INV#1"
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_a_prefix_that_cannot_produce_a_legal_number_is_refused_at_the_profile(self):
		with self.assertRaises(frappe.ValidationError):
			self.set_profile(invoice_number_prefix="A-VERY-LONG-PREFIX/")

	def test_two_invoices_in_one_financial_year_cannot_share_a_number(self):
		"""The unique index is the authority. Writing the taken number by hand
		must fail at the database, not merely at a Python check."""
		first = self.issued_invoice()
		second = self.ready_invoice()
		with self.assertRaises(Exception) as caught:
			second.db_set("invoice_number", first.invoice_number, update_modified=False)
		self.assertTrue(invoicing.is_duplicate_entry(caught.exception))

	def test_the_allocator_loses_the_race_once_and_takes_the_next_serial(self):
		"""Two workers read the same highest serial and build the same string. The
		first insert wins; the second must collide, re-read and take the next one.

		`highest_serial` is stubbed to return the STALE value first and the true
		value second, which is exactly what the loser of a real race sees."""
		first = self.issued_invoice()
		taken = first.invoice_number
		second = self.ready_invoice()

		with patch.object(invoicing, "highest_serial", side_effect=[0, 1, 2, 3]):
			allocated = invoicing.allocate_number(second)

		self.assertNotEqual(allocated, taken)
		self.assertEqual(allocated, invoicing.number_for(2, second.invoice_date))
		self.assertEqual(frappe.db.get_value(DOCTYPE, second.name, "invoice_number"), allocated)

	def test_the_number_carries_the_financial_year_of_the_invoice_date(self):
		doc = self.issued_invoice()
		self.assertIn(invoicing.financial_year_label(doc.invoice_date), doc.invoice_number)

	def test_the_next_number_endpoint_is_a_preview_and_reserves_nothing(self):
		before = invoices.get_next_number()
		self.assertTrue(before["is_preview"])
		self.assertEqual(invoices.get_next_number()["next_number"], before["next_number"])
		self.issued_invoice()
		self.assertNotEqual(invoices.get_next_number()["next_number"], before["next_number"])

	# --- what the lock freezes ------------------------------------------

	def test_an_issued_invoice_refuses_a_change_to_its_items(self):
		doc = self.issued_invoice()
		doc.items[0].rate = 1
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_an_issued_invoice_refuses_a_new_item_line(self):
		doc = self.issued_invoice()
		doc.append("items", {"description": "Extra night", "qty": 1, "rate": 5000, "tax_rate": 18})
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_an_issued_invoice_refuses_a_change_to_its_number(self):
		doc = self.issued_invoice()
		doc.invoice_number = invoicing.number_for(99, doc.invoice_date)
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_an_issued_invoice_refuses_a_change_to_the_gst_treatment(self):
		doc = self.issued_invoice()
		doc.mode = invoicing.MODE_TOUR_PACKAGE
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_an_issued_invoice_refuses_a_change_to_the_recipient_block(self):
		doc = self.issued_invoice()
		doc.customer_name = "Somebody Else"
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_a_draft_may_be_edited_and_renumbered_freely(self):
		doc = self.ready_invoice()
		doc.items[0].rate = 50000
		doc.customer_name = "Renamed Customer"
		doc.save()
		self.assertEqual(doc.taxable_total, 104000.0)


# --- AC4 -------------------------------------------------------------------


class TestAC4B2CThreshold(InvoiceTestCase):
	"""Rule 46: an unregistered customer billed Rs 50,000 or more must be named."""

	def small_invoice(self, **overrides):
		"""One line under the threshold, with the recipient block emptied."""
		doc = self.new_invoice()
		doc.items = []
		doc.append("items", {"description": "Visa assistance", "qty": 1, "rate": 1000, "tax_rate": 18})
		doc.update({"customer_name": "", "customer_address": "", "customer_state_code": "", **overrides})
		doc.save()
		return doc

	def test_a_b2c_invoice_at_or_above_the_threshold_refuses_to_finalize(self):
		doc = self.new_invoice(customer_name="", customer_address="", customer_state_code="")
		self.assertGreaterEqual(doc.grand_total, invoicing.B2C_RECIPIENT_THRESHOLD)
		result = doc.finalize()
		self.assertFalse(result["issued"])
		self.assertEqual(len(result["blockers"]), 1)
		self.assertIn("address", result["blockers"][0])
		self.assertIsNone(frappe.db.get_value(DOCTYPE, doc.name, "invoice_number"))

	def test_the_message_names_every_missing_field_at_once(self):
		doc = self.new_invoice(customer_name="", customer_address="", customer_state_code="")
		blocker = doc.finalize()["blockers"][0]
		for word in ("name", "address", "state code"):
			self.assertIn(word, blocker)

	def test_filling_the_recipient_block_lets_it_finalize(self):
		doc = self.new_invoice(
			customer_name="Priya Sharma",
			customer_address="7 Residency Road\nPune 411001",
			customer_state_code=COMPANY_STATE,
		)
		self.assertTrue(doc.finalize()["issued"])

	def test_a_b2c_invoice_below_the_threshold_finalizes_with_nothing_filled(self):
		doc = self.small_invoice()
		self.assertLess(doc.grand_total, invoicing.B2C_RECIPIENT_THRESHOLD)
		self.assertTrue(doc.finalize()["issued"])

	def test_a_registered_customer_needs_no_address_whatever_the_amount(self):
		doc = self.new_invoice(
			customer_name="",
			customer_address="",
			customer_state_code=OTHER_STATE,
			customer_gstin=CUSTOMER_GSTIN,
		)
		self.assertTrue(doc.finalize()["issued"])

	def test_an_invoice_with_no_lines_refuses_to_finalize(self):
		doc = self.new_invoice()
		doc.items = []
		doc.save()
		blockers = doc.finalize()["blockers"]
		self.assertTrue(any("no item lines" in line for line in blockers))

	def test_a_company_profile_gap_refuses_to_finalize_and_says_which_field(self):
		self.set_profile(gstin="")
		doc = self.new_invoice()
		blockers = doc.finalize()["blockers"]
		self.assertTrue(any("GSTIN" in line for line in blockers))

	def test_the_endpoint_raises_the_blockers_rather_than_issuing_silently(self):
		doc = self.new_invoice(customer_name="", customer_address="", customer_state_code="")
		with self.assertRaises(frappe.ValidationError):
			invoices.finalize(doc.name)

	def test_a_late_invoice_warns_but_is_still_issued(self):
		"""Rule 47 is a warning, never a refusal — an agency that cannot invoice a
		late-noticed booking is not more compliant, only unpaid."""
		doc = self.new_invoice(
			service_date=frappe.utils.add_days(frappe.utils.nowdate(), -60),
			customer_name="Priya Sharma",
			customer_address="7 Residency Road",
			customer_state_code=COMPANY_STATE,
		)
		result = doc.finalize()
		self.assertTrue(result["issued"])
		self.assertIn("30", result["warning"])


# --- AC5 -------------------------------------------------------------------


class TestAC5ReminderLadders(InvoiceTestCase):
	"""A 2-row schedule fires two ladders; paying the deposit stops only its own."""

	def setUp(self):
		super().setUp()
		frappe.db.set_single_value(SETTINGS, REMINDER_FLAG, 1)
		self.due = frappe.utils.getdate(frappe.utils.nowdate())
		self.invoice = self.ready_invoice(customer_email="priya.invoice@example.com")
		self.invoice.append("payment_schedule", {"label": "Deposit", "due_date": self.due, "amount": 50000})
		self.invoice.append("payment_schedule", {"label": "Balance", "due_date": self.due, "amount": 92780})
		self.invoice.save()
		self.invoice.finalize()
		self.invoice.reload()

	def rows(self):
		return {row.label: row for row in self.invoice.payment_schedule}

	def sweep(self, now):
		"""Run the ladder for our invoice at a stated moment. Returns new job keys."""
		before = set(frappe.get_all(outbound.JOB_DOCTYPE, pluck="idempotency_key"))
		row = next(item for item in invoice_reminders.open_invoices() if item["name"] == self.invoice.name)
		invoice_reminders.remind_about(row, now)
		after = set(frappe.get_all(outbound.JOB_DOCTYPE, pluck="idempotency_key"))
		return after - before

	def test_two_schedule_rows_fire_two_independent_ladders(self):
		keys = self.sweep(self.due)
		self.assertEqual(len(keys), 2)
		for row in self.invoice.payment_schedule:
			self.assertTrue(any(row.name in key for key in keys), msg=f"no ladder started for {row.label}")

	def test_a_second_sweep_at_the_same_moment_adds_nothing(self):
		self.sweep(self.due)
		self.assertEqual(self.sweep(self.due), set())

	def test_each_step_of_a_ladder_is_its_own_job(self):
		self.sweep(self.due)
		later = self.sweep(frappe.utils.add_days(self.due, 7))
		self.assertEqual(len(later), 2)
		self.assertTrue(all(key.endswith("day-7") for key in later))

	def test_paying_the_deposit_stops_only_the_deposit_ladder(self):
		deposit = self.rows()["Deposit"]
		balance = self.rows()["Balance"]

		self.invoice.record_payment(amount=50000, schedule_row=deposit.name)
		self.invoice.reload()
		self.assertTrue(self.rows()["Deposit"].settled)
		self.assertFalse(self.rows()["Balance"].settled)

		keys = self.sweep(self.due)
		self.assertEqual(len(keys), 1)
		self.assertIn(balance.name, keys.pop())

	def test_pausing_the_invoice_stops_every_ladder(self):
		self.invoice.db_set("reminders_paused", 1)
		names = [row["name"] for row in invoice_reminders.open_invoices()]
		self.assertNotIn(self.invoice.name, names)

	def test_pausing_one_row_stops_only_that_row(self):
		deposit = self.rows()["Deposit"]
		frappe.db.set_value("CRM Invoice Schedule", deposit.name, "reminders_paused", 1)
		keys = self.sweep(self.due)
		self.assertEqual(len(keys), 1)
		self.assertNotIn(deposit.name, keys.pop())

	def test_a_step_that_is_not_due_yet_is_not_queued(self):
		self.assertEqual(self.sweep(frappe.utils.add_days(self.due, -1)), set())

	def test_the_ladder_is_due_date_then_seven_then_fourteen(self):
		self.assertEqual(invoice_reminders.OFFSET_DAYS, (0, 7, 14))
		row = {"due_date": self.due}
		self.assertEqual(invoice_reminders.due_steps(row, self.due), [0])
		self.assertEqual(invoice_reminders.due_steps(row, frappe.utils.add_days(self.due, 7)), [0, 7])
		# At day 14 the FIRST step is 14 days old, which is outside the 7-day
		# catch-up window, so it is not re-fired. Only the steps whose own moment
		# fell inside the window are queued.
		self.assertEqual(invoice_reminders.due_steps(row, frappe.utils.add_days(self.due, 14)), [7, 14])

	def test_the_catch_up_window_stops_a_switch_on_replaying_history(self):
		"""Switching the flag on must not mail a customer about an instalment they
		settled months ago."""
		row = {"due_date": self.due}
		far = frappe.utils.add_days(self.due, 90)
		self.assertEqual(invoice_reminders.due_steps(row, far), [])

	def test_a_suppressed_address_is_never_given_a_job(self):
		from crm import suppression

		suppression.suppress(
			outbound.CHANNEL_EMAIL,
			"priya.invoice@example.com",
			state=suppression.STATE_OPTED_OUT,
			source="test",
		)
		self.assertEqual(self.sweep(self.due), set())

	def test_an_invoice_with_no_customer_email_queues_nothing(self):
		self.invoice.db_set("customer_email", "")
		self.assertEqual(self.sweep(self.due), set())

	def test_quiet_hours_defer_the_job_rather_than_dropping_it(self):
		settings = frappe.get_doc(FOLLOWUP_SETTINGS)
		settings.quiet_hours_start = "21:00:00"
		settings.quiet_hours_end = "09:00:00"
		settings.save(ignore_permissions=True)
		clear_single_cache(FOLLOWUP_SETTINGS)

		night = frappe.utils.get_datetime(f"{self.due} 23:30:00")
		when = invoice_reminders.send_at(night)
		self.assertEqual(when.hour, 9)
		self.assertGreater(when, night)

	def test_the_job_names_the_invoice_and_carries_the_reminder_body(self):
		self.sweep(self.due)
		key = invoice_reminders.reminder_key(self.invoice.name, self.rows()["Deposit"].name, 0)
		job = frappe.get_doc(outbound.JOB_DOCTYPE, {"idempotency_key": key})
		self.assertEqual(job.reference_doctype, DOCTYPE)
		self.assertEqual(job.reference_name, self.invoice.name)
		self.assertEqual(job.channel, outbound.CHANNEL_EMAIL)
		self.assertEqual(job.state, outbound.JOB_SCHEDULED)
		payload = frappe.parse_json(job.payload)
		self.assertIn("Deposit", payload["subject"])
		self.assertIn("Deposit", payload["content"])

	# --- the link contract ----------------------------------------------
	# The sweep reuses a door that is already open. It never mints a link and
	# never renders a PDF, whatever the reminder ends up saying.

	def payload_of(self, key: str) -> dict:
		return frappe.parse_json(frappe.get_doc(outbound.JOB_DOCTYPE, {"idempotency_key": key}).payload)

	def share(self) -> str:
		"""Mint one live tokenised link the way a real send would."""
		with self.stub_render():
			return invoices.share_invoice(self.invoice.name)["link_url"]

	def test_a_live_share_link_is_offered_in_the_reminder_body(self):
		url = self.share()
		keys = self.sweep(self.due)
		self.assertEqual(len(keys), 2)
		for key in keys:
			self.assertIn(url, self.payload_of(key)["content"])

	def test_the_reminder_carries_no_link_when_the_invoice_was_never_shared(self):
		keys = self.sweep(self.due)
		self.assertEqual(len(keys), 2)
		for key in keys:
			content = self.payload_of(key)["content"]
			self.assertNotIn("token=", content)
			self.assertNotIn("<a href=", content)
			self.assertIn("reminder about the", content)

	def test_the_sweep_never_mints_a_link_and_never_renders_a_pdf(self):
		"""The whole reason the sweep reads a link rather than making one."""
		with (
			patch.object(document_links, "render_print_pdf") as renderer,
			patch.object(document_links, "create_link") as minter,
		):
			self.sweep(self.due)
			renderer.assert_not_called()
			minter.assert_not_called()

		self.assertEqual(
			frappe.db.count(document_links.LINK_DOCTYPE, {"reference_name": self.invoice.name}), 0
		)

	def test_an_expired_link_is_not_offered(self):
		self.share()
		name = frappe.db.get_value(
			document_links.LINK_DOCTYPE, {"reference_name": self.invoice.name, "active": 1}, "name"
		)
		frappe.db.set_value(
			document_links.LINK_DOCTYPE,
			name,
			"expires_at",
			frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-1),
			update_modified=False,
		)
		self.assertEqual(invoice_reminders.live_link_url(self.invoice.name), "")
		for key in self.sweep(self.due):
			self.assertNotIn("token=", self.payload_of(key)["content"])

	def test_a_revoked_link_is_not_offered(self):
		self.share()
		document_links.revoke_links(DOCTYPE, self.invoice.name, "Invoice")
		self.assertEqual(invoice_reminders.live_link_url(self.invoice.name), "")
		for key in self.sweep(self.due):
			self.assertNotIn("token=", self.payload_of(key)["content"])

	def test_a_link_whose_file_is_gone_is_not_offered(self):
		"""`expire_links` clears `file` before it deletes the document, so a row can
		look active for the length of one sweep."""
		self.share()
		name = frappe.db.get_value(
			document_links.LINK_DOCTYPE, {"reference_name": self.invoice.name, "active": 1}, "name"
		)
		frappe.db.set_value(document_links.LINK_DOCTYPE, name, "file", None, update_modified=False)
		self.assertEqual(invoice_reminders.live_link_url(self.invoice.name), "")

	def test_a_quote_link_on_the_same_record_is_not_offered_as_an_invoice_link(self):
		with self.stub_render():
			file_doc = document_links.attach_pdf(
				DOCTYPE, self.invoice.name, "probe-v1.pdf", stub_pdf(), is_private=1
			)
			document_links.create_link(DOCTYPE, self.invoice.name, file_doc, purpose="Quote")
		self.assertEqual(invoice_reminders.live_link_url(self.invoice.name), "")

	def test_the_offered_url_is_the_invoice_view_route(self):
		url = self.share()
		self.assertEqual(invoice_reminders.live_link_url(self.invoice.name), url)
		self.assertIn("crm.api.invoices.view", url)

	def test_the_sweep_reads_nothing_while_its_own_flag_is_off(self):
		frappe.db.set_single_value(SETTINGS, REMINDER_FLAG, 0)
		with patch.object(invoice_reminders, "open_invoices") as reader:
			self.assertEqual(invoice_reminders.send_invoice_reminders(), 0)
			reader.assert_not_called()

	def test_the_sweep_reads_nothing_while_the_module_flag_is_off(self):
		frappe.db.set_single_value(SETTINGS, FLAG, 0)
		with patch.object(invoice_reminders, "open_invoices") as reader:
			self.assertEqual(invoice_reminders.send_invoice_reminders(), 0)
			reader.assert_not_called()

	def test_the_sweep_never_raises(self):
		with patch.object(invoice_reminders, "open_invoices", side_effect=RuntimeError("boom")):
			self.assertEqual(invoice_reminders.send_invoice_reminders(), 0)

	def test_a_paid_invoice_leaves_the_reminder_population(self):
		self.invoice.record_payment()
		self.invoice.reload()
		self.assertEqual(self.invoice.status, invoicing.STATUS_PAID)
		self.assertNotIn(self.invoice.name, [row["name"] for row in invoice_reminders.open_invoices()])


# --- AC6 -------------------------------------------------------------------


class TestAC6Payments(InvoiceTestCase):
	"""Partial, exact, correction — and every recorded row immutable."""

	def setUp(self):
		super().setUp()
		self.invoice = self.issued_invoice()
		self.total = self.invoice.grand_total  # 142780.00

	def test_a_partial_payment_makes_it_partially_paid(self):
		self.invoice.record_payment(amount=50000)
		self.invoice.reload()
		self.assertEqual(self.invoice.status, invoicing.STATUS_PARTIALLY_PAID)
		self.assertEqual(self.invoice.paid_total, 50000.0)
		self.assertEqual(self.invoice.outstanding_amount, self.total - 50000)

	def test_the_exact_remaining_amount_makes_it_paid(self):
		self.invoice.record_payment(amount=50000)
		self.invoice.reload()
		self.invoice.record_payment(amount=self.total - 50000)
		self.invoice.reload()
		self.assertEqual(self.invoice.status, invoicing.STATUS_PAID)
		self.assertEqual(self.invoice.outstanding_amount, 0.0)

	def test_the_amount_defaults_to_everything_still_outstanding(self):
		self.invoice.record_payment()
		self.invoice.reload()
		self.assertEqual(self.invoice.status, invoicing.STATUS_PAID)
		self.assertEqual(self.invoice.payments[0].amount, self.total)

	def test_a_negative_correction_restores_partially_paid(self):
		self.invoice.record_payment()
		self.invoice.reload()
		self.invoice.record_payment(amount=-800, note="Cheque bounced")
		self.invoice.reload()
		self.assertEqual(self.invoice.status, invoicing.STATUS_PARTIALLY_PAID)
		self.assertEqual(self.invoice.outstanding_amount, 800.0)

	def test_a_negative_correction_without_a_note_is_refused(self):
		self.invoice.record_payment()
		self.invoice.reload()
		with self.assertRaises(frappe.ValidationError):
			self.invoice.record_payment(amount=-800)

	def test_over_payment_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.invoice.record_payment(amount=self.total + 1)

	def test_a_zero_payment_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.invoice.record_payment(amount=0)

	def test_a_recorded_payment_cannot_be_edited(self):
		self.invoice.record_payment(amount=1000)
		self.invoice.reload()
		self.invoice.payments[0].amount = 9999
		with self.assertRaises(frappe.ValidationError):
			self.invoice.save()

	def test_a_recorded_payment_cannot_be_removed(self):
		self.invoice.record_payment(amount=1000)
		self.invoice.reload()
		self.invoice.payments = []
		with self.assertRaises(frappe.ValidationError):
			self.invoice.save()

	def test_a_payment_records_who_took_it_and_when(self):
		self.invoice.record_payment(amount=1000, mode="Bank", reference="UTR123")
		self.invoice.reload()
		row = self.invoice.payments[0]
		self.assertEqual(row.recorded_by, "Administrator")
		self.assertEqual(row.mode, "Bank")
		self.assertEqual(row.reference, "UTR123")
		self.assertTrue(row.recorded_at)

	def test_a_draft_takes_no_payments(self):
		draft = self.ready_invoice()
		with self.assertRaises(frappe.ValidationError):
			draft.record_payment(amount=100)

	def test_every_status_move_leaves_a_history_row(self):
		self.invoice.record_payment(amount=1000)
		self.invoice.reload()
		moves = [(row.from_status, row.to_status) for row in self.invoice.status_log]
		self.assertIn((invoicing.STATUS_SENT, invoicing.STATUS_PARTIALLY_PAID), moves)

	def test_the_endpoint_can_send_a_thank_you_and_can_be_asked_not_to(self):
		with patch("crm.api.email.send_email", return_value={"name": "COMM-1"}) as sender:
			invoices.record_payment(self.invoice.name, amount=1000, send_thank_you=0)
			sender.assert_not_called()
			result = invoices.record_payment(self.invoice.name, amount=1000, send_thank_you=1)
			self.assertTrue(result["thank_you"]["sent"])
			self.assertEqual(sender.call_args.kwargs["recipients"], "priya.invoice@example.com")

	def test_a_failed_thank_you_does_not_undo_the_payment(self):
		with patch("crm.api.email.send_email", side_effect=RuntimeError("smtp down")):
			result = invoices.record_payment(self.invoice.name, amount=1000, send_thank_you=1)
		self.assertFalse(result["thank_you"]["sent"])
		self.assertEqual(result["totals"]["paid_total"], 1000.0)


# --- AC7 (the half that is not the PDF watermark) --------------------------


class TestVoidAndTiles(InvoiceTestCase):
	"""Void is terminal, manager-only, and excluded from all three tiles."""

	def setUp(self):
		super().setUp()
		self.invoice = self.issued_invoice()

	def test_void_needs_a_reason(self):
		with self.assertRaises(frappe.ValidationError):
			self.invoice.void("")

	def test_void_is_terminal(self):
		self.invoice.void("Customer cancelled the trip")
		self.invoice.reload()
		self.assertEqual(self.invoice.status, invoicing.STATUS_VOID)
		self.invoice.status = invoicing.STATUS_SENT
		with self.assertRaises(frappe.ValidationError):
			self.invoice.save()

	def test_void_keeps_the_number_and_the_reason(self):
		number = self.invoice.invoice_number
		self.invoice.void("Duplicate of INV/25-26/0007")
		self.invoice.reload()
		self.assertEqual(self.invoice.invoice_number, number)
		self.assertEqual(self.invoice.void_reason, "Duplicate of INV/25-26/0007")
		self.assertEqual(self.invoice.voided_by, "Administrator")

	def test_a_voided_invoice_takes_no_payments(self):
		self.invoice.void("Cancelled")
		self.invoice.reload()
		with self.assertRaises(frappe.ValidationError):
			self.invoice.record_payment(amount=100)

	def test_a_voided_invoice_can_never_be_issued_again(self):
		self.invoice.void("Cancelled")
		self.invoice.reload()
		with self.assertRaises(frappe.ValidationError):
			self.invoice.finalize()

	def test_an_invoice_is_never_deleted_even_by_administrator(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc(DOCTYPE, self.invoice.name, ignore_permissions=True)

	def test_the_tiles_count_an_open_invoice_as_outstanding(self):
		tiles = invoices.get_tiles()
		self.assertGreaterEqual(tiles["outstanding"]["value"], self.invoice.grand_total)

	def test_void_removes_the_invoice_from_all_three_tiles(self):
		before = invoices.get_tiles()
		self.invoice.record_payment(amount=1000)
		self.invoice.reload()
		mid = invoices.get_tiles()
		self.assertEqual(mid["collected"]["value"] - before["collected"]["value"], 1000.0)

		self.invoice.void("Cancelled")
		after = invoices.get_tiles()
		self.assertEqual(after["collected"]["value"], before["collected"]["value"])
		self.assertEqual(
			after["outstanding"]["value"], before["outstanding"]["value"] - self.invoice.grand_total
		)

	def test_an_invoice_past_its_due_date_lands_in_the_overdue_tile(self):
		self.invoice.db_set("due_date", frappe.utils.add_days(frappe.utils.nowdate(), -1))
		tiles = invoices.get_tiles()
		self.assertGreaterEqual(tiles["overdue"]["value"], self.invoice.outstanding_amount)

	def test_overdue_is_computed_and_is_not_a_stored_status(self):
		self.invoice.db_set("due_date", frappe.utils.add_days(frappe.utils.nowdate(), -1))
		self.invoice.reload()
		self.assertEqual(self.invoice.status, invoicing.STATUS_SENT)
		self.assertTrue(self.invoice.is_overdue())


# --- AC8 -------------------------------------------------------------------


class TestAC8UpiQr(InvoiceTestCase):
	"""The QR encodes `upi://pay?pa=<vpa>&am=<remaining>` and nothing else.

	The container has no independent QR DECODER (`pyzbar`, `cv2` and `qrcode` are
	all absent — checked on 2026-08-19), so this is not a third-party decode. It
	is a pixel-level read of the rendered PNG back into a module matrix, compared
	against the matrix `segno` builds for the URI the test wrote by hand. That
	proves the image on the invoice carries that symbol and no other. The gap is
	recorded in demo-package/specs/stage5-3-notes.md.
	"""

	def read_matrix(self, data_uri: str, size: int, scale: int = 6, border: int = 2):
		"""Sample the centre pixel of every module of the rendered code."""
		from PIL import Image

		self.assertTrue(data_uri.startswith("data:image/png;base64,"))
		raw = base64.b64decode(data_uri.split(",", 1)[1])
		self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")

		image = Image.open(io.BytesIO(raw)).convert("L")
		self.assertEqual(image.width, (size + 2 * border) * scale)
		self.assertEqual(image.height, image.width)

		half = scale // 2
		return [
			[
				1
				if image.getpixel(((border + col) * scale + half, (border + row) * scale + half)) < 128
				else 0
				for col in range(size)
			]
			for row in range(size)
		]

	def test_the_uri_is_the_remaining_balance_not_the_grand_total(self):
		invoice = self.issued_invoice()
		invoice.record_payment(amount=42780)
		invoice.reload()
		payload = invoices.read_invoice(invoice)
		self.assertEqual(payload["totals"]["outstanding_amount"], 100000.0)
		self.assertEqual(payload["upi_uri"], f"upi://pay?pa={VPA}&am=100000.00")

	def test_the_data_uri_is_a_png_that_encodes_exactly_that_string(self):
		import segno

		uri = invoicing.upi_uri(VPA, 142780)
		self.assertEqual(uri, f"upi://pay?pa={VPA}&am=142780.00")

		expected = segno.make(uri, error="m").matrix
		read = self.read_matrix(invoicing.upi_qr_data_uri(VPA, 142780), len(expected))
		self.assertEqual(read, [list(row) for row in expected])

	def test_a_different_amount_produces_a_different_symbol(self):
		first = invoicing.upi_qr_data_uri(VPA, 142780)
		second = invoicing.upi_qr_data_uri(VPA, 100000)
		self.assertNotEqual(first, second)

	def test_no_vpa_means_no_qr_rather_than_a_broken_one(self):
		self.set_profile(upi_vpa="")
		invoice = self.issued_invoice()
		payload = invoices.read_invoice(invoice)
		self.assertEqual(payload["upi_uri"], "")
		self.assertEqual(invoicing.upi_qr_data_uri("", 100), "")

	def test_the_print_payload_carries_the_qr_and_the_void_marker(self):
		invoice = self.issued_invoice()
		invoices.decorate(invoice)
		self.assertTrue(invoice.inv_qr.startswith("data:image/png;base64,"))
		self.assertFalse(invoice.inv_meta["is_void"])

		invoice.void("Cancelled")
		invoice.reload()
		invoices.decorate(invoice)
		self.assertTrue(invoice.inv_meta["is_void"])


# --- AC9 -------------------------------------------------------------------


class TestAC9Permissions(InvoiceTestCase):
	"""Master spec §3. No patching: the real org-hierarchy rule refuses."""

	def setUp(self):
		super().setUp()
		make_user(AGENT, "Sales User")
		make_user(OUTSIDER, "Sales User")
		make_user(MANAGER, "Sales Manager")
		frappe.db.set_value(DEAL_DOCTYPE, self.deal.name, "deal_owner", AGENT)
		self.invoice = self.issued_invoice()

	def test_the_deals_own_agent_can_read_their_invoice(self):
		frappe.set_user(AGENT)
		self.assertEqual(invoices.get_invoice(self.invoice.name)["name"], self.invoice.name)

	def test_a_sales_user_outside_the_deal_cannot_read_the_invoice(self):
		frappe.set_user(OUTSIDER)
		self.assertRaises(frappe.PermissionError, invoices.get_invoice, self.invoice.name)

	def test_a_missing_invoice_is_refused_exactly_like_a_forbidden_one(self):
		frappe.set_user(OUTSIDER)
		self.assertRaises(frappe.PermissionError, invoices.get_invoice, "CRM-INVOICE-NOPE")

	def test_the_row_filter_hides_an_out_of_scope_invoice_from_the_list(self):
		frappe.set_user(OUTSIDER)
		names = frappe.get_list(DOCTYPE, pluck="name", limit_page_length=0)
		self.assertNotIn(self.invoice.name, names)

		frappe.set_user(AGENT)
		names = frappe.get_list(DOCTYPE, pluck="name", limit_page_length=0)
		self.assertIn(self.invoice.name, names)

	def test_the_tiles_are_scoped_to_the_caller(self):
		frappe.set_user(OUTSIDER)
		outsider = invoices.get_tiles()
		frappe.set_user(AGENT)
		agent = invoices.get_tiles()
		self.assertGreaterEqual(agent["outstanding"]["value"], self.invoice.grand_total)
		self.assertEqual(outsider["outstanding"]["value"], 0.0)

	def test_a_sales_user_cannot_void_even_their_own_invoice(self):
		frappe.set_user(AGENT)
		self.assertRaises(frappe.PermissionError, invoices.void_invoice, self.invoice.name, "Changed my mind")

	def test_the_document_method_refuses_a_sales_user_as_well_as_the_endpoint(self):
		"""Checked twice on purpose: a future caller that skips the endpoint must
		still not be able to void."""
		frappe.set_user(AGENT)
		doc = frappe.get_doc(DOCTYPE, self.invoice.name)
		self.assertRaises(frappe.PermissionError, doc.void, "Changed my mind")

	def test_a_manager_can_void(self):
		frappe.set_user(MANAGER)
		result = invoices.void_invoice(self.invoice.name, "Customer cancelled")
		self.assertEqual(result["status"], invoicing.STATUS_VOID)

	def test_a_sales_user_can_record_a_payment_on_their_own_invoice(self):
		frappe.set_user(AGENT)
		result = invoices.record_payment(self.invoice.name, amount=1000)
		self.assertEqual(result["totals"]["paid_total"], 1000.0)

	def test_a_sales_user_outside_the_deal_cannot_record_a_payment(self):
		frappe.set_user(OUTSIDER)
		self.assertRaises(frappe.PermissionError, invoices.record_payment, self.invoice.name, 1000)

	def test_a_sales_user_cannot_edit_an_issued_invoices_amounts(self):
		frappe.set_user(AGENT)
		doc = frappe.get_doc(DOCTYPE, self.invoice.name)
		doc.items[0].rate = 1
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_a_sales_user_cannot_write_the_sac_master(self):
		frappe.set_user(AGENT)
		self.assertTrue(frappe.has_permission("CRM SAC Code", "read"))
		self.assertFalse(frappe.has_permission("CRM SAC Code", "write"))
		self.assertFalse(frappe.has_permission("CRM SAC Code", "create"))

	def test_a_sales_user_cannot_write_the_company_profile(self):
		frappe.set_user(AGENT)
		self.assertTrue(frappe.has_permission(PROFILE, "read"))
		self.assertFalse(frappe.has_permission(PROFILE, "write"))

	def test_nobody_is_granted_delete_on_an_invoice(self):
		for role in ("Sales User", "Sales Manager", "System Manager"):
			self.assertFalse(
				frappe.db.get_value("Custom DocPerm", {"parent": DOCTYPE, "role": role}, "delete"),
				msg=f"{role} must not hold delete on {DOCTYPE}",
			)

	def test_a_sales_user_gets_neither_report_nor_export(self):
		row = frappe.db.get_value(
			"Custom DocPerm", {"parent": DOCTYPE, "role": "Sales User"}, ["report", "export"], as_dict=True
		)
		self.assertFalse(row.report)
		self.assertFalse(row.export)

	def test_the_role_grants_are_corrective_and_idempotent(self):
		frappe.db.set_value("Custom DocPerm", {"parent": DOCTYPE, "role": "Sales User"}, "export", 1)
		add_invoice_roles()
		self.assertFalse(
			frappe.db.get_value("Custom DocPerm", {"parent": DOCTYPE, "role": "Sales User"}, "export")
		)

	def test_state_changing_endpoints_are_post_only(self):
		for method in (
			invoices.convert_deal,
			invoices.finalize,
			invoices.record_payment,
			invoices.void_invoice,
			invoices.set_reminders_paused,
			invoices.download_invoice,
			invoices.share_invoice,
			invoices.send_invoice_email,
			invoices.send_invoice_on_whatsapp,
		):
			self.assertIn(method, frappe.whitelisted, msg=f"{method.__name__} must be whitelisted")
			self.assertEqual(
				tuple(frappe.allowed_http_methods_for_whitelisted_func[method]),
				("POST",),
				msg=f"{method.__name__} must be POST only",
			)

	def test_the_link_doctypes_are_not_readable_by_a_sales_user(self):
		"""A view log names which customers opened which invoices. Managers only."""
		frappe.set_user(AGENT)
		self.assertFalse(frappe.has_permission(document_links.LINK_DOCTYPE, "read"))
		self.assertFalse(frappe.has_permission(document_links.VIEW_DOCTYPE, "read"))


# --- AC10 ------------------------------------------------------------------


class TestAC10Flag(InvoiceTestCase):
	"""The module is behind `invoices_enabled`, default OFF, and OFF refuses."""

	def test_both_flags_are_registered_in_the_python_half(self):
		from crm.feature_flags import FLAGS

		self.assertIn(FLAG, FLAGS)
		self.assertIn(REMINDER_FLAG, FLAGS)

	def test_both_flags_are_fields_on_the_settings_doctype(self):
		meta = frappe.get_meta(SETTINGS)
		for flag in (FLAG, REMINDER_FLAG):
			field = meta.get_field(flag)
			self.assertIsNotNone(field, msg=f"{flag} must be a field on {SETTINGS}")
			self.assertEqual(field.fieldtype, "Check")
			self.assertEqual(frappe.utils.cint(field.default), 0, msg=f"{flag} must default to OFF")

	def test_every_endpoint_refuses_while_the_flag_is_off(self):
		invoice = self.issued_invoice()
		frappe.db.set_single_value(SETTINGS, FLAG, 0)

		calls = (
			(invoices.convert_deal, (self.deal.name,)),
			(invoices.get_invoice, (invoice.name,)),
			(invoices.get_invoices_for_deal, (self.deal.name,)),
			(invoices.get_next_number, ()),
			(invoices.get_tiles, ()),
			(invoices.finalize, (invoice.name,)),
			(invoices.record_payment, (invoice.name,)),
			(invoices.void_invoice, (invoice.name, "why")),
			(invoices.set_reminders_paused, (invoice.name,)),
			(invoices.download_invoice, (invoice.name,)),
			(invoices.share_invoice, (invoice.name,)),
			(invoices.send_invoice_email, (invoice.name,)),
			(invoices.send_invoice_on_whatsapp, (invoice.name,)),
		)
		for method, args in calls:
			with self.assertRaises(frappe.PermissionError, msg=f"{method.__name__} must refuse"):
				method(*args)

	def test_the_public_route_answers_like_a_dead_token_while_the_flag_is_off(self):
		"""A guest route must say nothing about the site's configuration."""
		frappe.db.set_single_value(SETTINGS, FLAG, 0)
		with self.assertRaises(frappe.PermissionError):
			invoices.view("whatever")

	def test_an_unknown_flag_reads_as_off_rather_than_raising(self):
		from crm.feature_flags import is_enabled

		self.assertFalse(is_enabled("invoices_that_do_not_exist"))


# --- the tokenised link ----------------------------------------------------


class TestShareLink(InvoiceTestCase):
	"""The customer-facing link: private file, token, view log, expiry."""

	def setUp(self):
		super().setUp()
		self.invoice = self.issued_invoice()

	def test_sharing_mints_a_token_and_keeps_the_file_private(self):
		with self.stub_render():
			result = invoices.share_invoice(self.invoice.name)
		self.assertIn("token=", result["link_url"])
		link = frappe.get_doc(document_links.LINK_DOCTYPE, {"reference_name": self.invoice.name})
		self.assertEqual(link.purpose, "Invoice")
		self.assertTrue(frappe.db.get_value("File", link.file, "is_private"))

	def test_re_sharing_retires_the_previous_token(self):
		with self.stub_render():
			first = invoices.share_invoice(self.invoice.name)
			invoices.share_invoice(self.invoice.name)
		token = first["link_url"].split("token=")[1]
		self.assertIsNone(document_links.resolve_link(token))

	def test_a_dead_token_and_an_unknown_token_are_indistinguishable(self):
		with self.stub_render():
			result = invoices.share_invoice(self.invoice.name)
		token = result["link_url"].split("token=")[1]
		document_links.revoke_links(DOCTYPE, self.invoice.name, "Invoice")
		self.assertRaises(frappe.PermissionError, invoices.view, token)
		self.assertRaises(frappe.PermissionError, invoices.view, "not-a-real-token")

	def test_a_live_token_streams_the_file_and_logs_the_view(self):
		with self.stub_render():
			result = invoices.share_invoice(self.invoice.name)
		token = result["link_url"].split("token=")[1]
		invoices.view(token)
		self.assertEqual(frappe.response.type, "pdf")
		views = document_links.customer_views(DOCTYPE, self.invoice.name, purpose="Invoice")
		self.assertEqual(len(views), 1)

	def test_a_quote_token_cannot_be_redeemed_on_the_invoice_route(self):
		"""The purpose is checked, so a token is bound to one kind of document."""
		with self.stub_render():
			file_doc = document_links.attach_pdf(
				DOCTYPE, self.invoice.name, "probe-v1.pdf", stub_pdf(), is_private=1
			)
			link = document_links.create_link(DOCTYPE, self.invoice.name, file_doc, purpose="Quote")
		self.assertRaises(frappe.PermissionError, invoices.view, link.token)

	def test_the_email_send_uses_the_address_on_the_record(self):
		with self.stub_render(), patch("crm.api.email.send_email", return_value={"name": "C1"}) as sender:
			result = invoices.send_invoice_email(self.invoice.name)
		self.assertTrue(result["success"])
		self.assertEqual(sender.call_args.kwargs["recipients"], "priya.invoice@example.com")
		self.assertIn(result["link_url"], sender.call_args.kwargs["content"])

	def test_the_email_endpoint_takes_no_address_argument(self):
		import inspect

		self.assertEqual(list(inspect.signature(invoices.send_invoice_email).parameters), ["invoice"])

	def test_the_whatsapp_send_uses_a_number_the_deal_already_holds(self):
		with (
			self.stub_render(),
			patch("crm.api.whatsapp.create_whatsapp_message", return_value="WA-1") as sender,
		):
			result = invoices.send_invoice_on_whatsapp(self.invoice.name)
		self.assertTrue(result["success"])
		self.assertEqual(sender.call_args.kwargs["to"], "+919876543211")
		self.assertEqual(sender.call_args.kwargs["attach"], result["link_url"])

	def test_a_whatsapp_failure_retires_the_link_and_reports_the_window(self):
		with (
			self.stub_render(),
			patch("crm.api.whatsapp.create_whatsapp_message", side_effect=RuntimeError("outside window")),
		):
			result = invoices.send_invoice_on_whatsapp(self.invoice.name)
		self.assertFalse(result["success"])
		self.assertIn("24 hours", result["hint"])
		self.assertEqual(
			frappe.db.count(document_links.LINK_DOCTYPE, {"reference_name": self.invoice.name, "active": 1}),
			0,
		)


# --- the SAC master --------------------------------------------------------


class TestSacMaster(InvoiceTestCase):
	"""Admin-editable placeholders, each one labelled as unverified."""

	def test_the_seeded_codes_are_present(self):
		self.assertGreaterEqual(frappe.db.count("CRM SAC Code"), 6)

	def test_every_seeded_row_says_it_must_be_verified_with_a_ca(self):
		for name in frappe.get_all("CRM SAC Code", pluck="name"):
			note = frappe.db.get_value("CRM SAC Code", name, "verify_note") or ""
			self.assertIn("CA", note, msg=f"{name} must carry the verification note")

	def test_the_note_cannot_be_edited_away(self):
		doc = frappe.get_doc("CRM SAC Code", "998555")
		doc.verify_note = "All good, no need to check"
		doc.save(ignore_permissions=True)
		self.assertIn("CA", doc.verify_note)

	def test_the_seeder_is_idempotent(self):
		from crm.fcrm.doctype.crm_sac_code.crm_sac_code import seed_sac_codes

		before = frappe.db.count("CRM SAC Code")
		self.assertEqual(seed_sac_codes(), 0)
		self.assertEqual(frappe.db.count("CRM SAC Code"), before)

	def test_a_line_takes_its_rate_from_the_default_sac(self):
		self.set_profile(default_sac="998555")
		sac, rate = invoices.default_sac_and_rate()
		self.assertEqual(sac, "998555")
		self.assertEqual(rate, 5.0)
		doc = self.new_invoice()
		self.assertEqual(doc.items[0].sac, "998555")
