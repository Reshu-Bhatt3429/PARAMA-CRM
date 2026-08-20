# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the invoice arithmetic (master spec §5 item 29, design note 29).

This file tests `crm.invoicing` and nothing else. There is no doctype here, no
endpoint and no permission: every function under test is a pure function over
plain dicts, which is the whole reason the arithmetic was put in a module of its
own rather than on the controller.

Every expected figure below is HAND-COMPUTED and the working is written in the
test. A test whose expectation came out of the code it is testing proves that
the code is self-consistent, which is not the property a tax figure needs.

The four things this file is really about:

* **Section 170 rounding.** Round half UP to the whole rupee, on the tax totals
  and the grand total, and never on the individual lines. `round()` in Python is
  banker's rounding and sends 2.5 to 2; the law says 3.
* **The place of supply decides the split.** Same state as the supplier means
  CGST and SGST, each half the rate and each rounded in its own right. A
  different state means IGST at the full rate.
* **India's financial year begins on 1 April.** A series that reset on 1 January
  would break "unique per financial year" in exactly the month an auditor looks.
* **Rule 46(b) numbering.** At most 16 characters, from `[A-Za-z0-9\\-/]`.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm import invoicing

INTRA = "27"  # supplier's state; used on both sides for an intra-state case
OTHER = "29"  # a different state, so the split becomes IGST


def line(rate, qty=1, tax_rate=18.0) -> dict:
	return {"qty": qty, "rate": rate, "tax_rate": tax_rate}


class TestRounding(FrappeTestCase):
	"""Section 170: half goes UP, always, and only on the final figures."""

	def test_half_a_rupee_goes_up_not_to_the_even_number(self):
		# Python's own round(2.5) is 2 -- banker's rounding. Section 170 says 3.
		self.assertEqual(round(2.5), 2)
		self.assertEqual(invoicing.round_rupee(2.5), 3.0)

	def test_half_a_rupee_goes_up_from_zero_as_well(self):
		self.assertEqual(invoicing.round_rupee(0.5), 1.0)

	def test_just_below_half_goes_down(self):
		self.assertEqual(invoicing.round_rupee(0.4999), 0.0)

	def test_a_whole_rupee_is_left_alone(self):
		self.assertEqual(invoicing.round_rupee(180), 180.0)

	def test_paise_are_kept_to_two_places_half_up(self):
		self.assertEqual(invoicing.round_paise(1.005), 1.01)
		self.assertEqual(invoicing.round_paise(1.004), 1.0)

	def test_an_empty_value_is_zero_not_an_error(self):
		self.assertEqual(invoicing.round_paise(None), 0.0)
		self.assertEqual(invoicing.round_paise(""), 0.0)


class TestLineAmounts(FrappeTestCase):
	def test_a_line_is_always_recomputed_from_quantity_and_rate(self):
		# The stored `amount` is deliberately wrong here. It must be ignored.
		row = {"qty": 2, "rate": 65000, "amount": 1}
		self.assertEqual(invoicing.line_amount(row), 130000.0)

	def test_a_line_keeps_its_paise(self):
		self.assertEqual(invoicing.line_amount({"qty": 3, "rate": 333.33}), 999.99)

	def test_tour_package_mode_overrides_the_stored_rate(self):
		self.assertEqual(invoicing.line_rate({"tax_rate": 18}, invoicing.MODE_TOUR_PACKAGE), 5.0)

	def test_commission_mode_keeps_the_stored_rate(self):
		self.assertEqual(invoicing.line_rate({"tax_rate": 18}, invoicing.MODE_COMMISSION), 18.0)


class TestTheSplit(FrappeTestCase):
	"""Place of supply against the supplier's state, and nothing else."""

	def test_the_same_state_is_intra_state(self):
		self.assertTrue(invoicing.is_intra_state(INTRA, INTRA))

	def test_a_different_state_is_inter_state(self):
		self.assertFalse(invoicing.is_intra_state(INTRA, OTHER))

	def test_an_unknown_place_of_supply_is_treated_as_intra_state(self):
		"""The conservative direction: the total tax is the same either way, and
		the two-column CGST/SGST layout is what an Indian customer expects."""
		self.assertTrue(invoicing.is_intra_state(INTRA, ""))
		self.assertTrue(invoicing.is_intra_state(INTRA, None))


class TestTotals(FrappeTestCase):
	"""Every figure below is worked out by hand in the test that asserts it."""

	def test_one_clean_line_intra_state(self):
		# 1 x 1000.00 = 1000.00 taxable. 18% of 1000.00 = 180.00 raw tax.
		# Intra-state: half each = 90.00, each already whole. Grand = 1180.00.
		totals = invoicing.compute_totals([line(1000)], invoicing.MODE_COMMISSION, INTRA, INTRA)
		self.assertEqual(totals["taxable_total"], 1000.0)
		self.assertEqual(totals["cgst_amount"], 90.0)
		self.assertEqual(totals["sgst_amount"], 90.0)
		self.assertEqual(totals["igst_amount"], 0.0)
		self.assertEqual(totals["tax_total"], 180.0)
		self.assertEqual(totals["grand_total"], 1180.0)
		self.assertEqual(totals["rounding_adjustment"], 0.0)
		self.assertTrue(totals["intra_state"])

	def test_the_same_line_inter_state_is_one_igst_figure(self):
		# Same 180.00 of tax, in one column instead of two.
		totals = invoicing.compute_totals([line(1000)], invoicing.MODE_COMMISSION, INTRA, OTHER)
		self.assertEqual(totals["cgst_amount"], 0.0)
		self.assertEqual(totals["sgst_amount"], 0.0)
		self.assertEqual(totals["igst_amount"], 180.0)
		self.assertEqual(totals["tax_total"], 180.0)
		self.assertEqual(totals["grand_total"], 1180.0)
		self.assertFalse(totals["intra_state"])

	def test_paise_in_the_taxable_value_round_only_at_the_end(self):
		# 3 x 333.33 = 999.99 taxable (paise KEPT).
		# 18% of 999.99 = 179.9982 raw tax.
		# Half = 89.9991 -> 90 each -> tax total 180.00.
		# Pre-rounding grand = 999.99 + 180.00 = 1179.99 -> 1180.00.
		# Rounding adjustment = 1180.00 - 1179.99 = 0.01.
		totals = invoicing.compute_totals([line(333.33, qty=3)], invoicing.MODE_COMMISSION, INTRA, INTRA)
		self.assertEqual(totals["taxable_total"], 999.99)
		self.assertEqual(totals["cgst_amount"], 90.0)
		self.assertEqual(totals["sgst_amount"], 90.0)
		self.assertEqual(totals["tax_total"], 180.0)
		self.assertEqual(totals["grand_total"], 1180.0)
		self.assertEqual(totals["rounding_adjustment"], 0.01)

	def test_inter_state_rounds_the_whole_tax_once(self):
		# 179.9982 -> 180.00 in one step, not two.
		totals = invoicing.compute_totals([line(333.33, qty=3)], invoicing.MODE_COMMISSION, INTRA, OTHER)
		self.assertEqual(totals["igst_amount"], 180.0)
		self.assertEqual(totals["tax_total"], 180.0)

	def test_the_two_halves_are_rounded_separately_and_may_exceed_the_raw_tax(self):
		# 1 x 2020.00 at 5% = 101.00 raw tax. Half = 50.50 -> 51 each -> 102.00.
		# The docstring in crm/invoicing.py explains why this is the right answer:
		# each column has to be a whole rupee in its own right.
		totals = invoicing.compute_totals([line(2020, tax_rate=5)], invoicing.MODE_COMMISSION, INTRA, INTRA)
		self.assertEqual(totals["cgst_amount"], 51.0)
		self.assertEqual(totals["sgst_amount"], 51.0)
		self.assertEqual(totals["tax_total"], 102.0)
		self.assertEqual(totals["grand_total"], 2122.0)

	def test_lines_are_summed_before_the_tax_is_taken(self):
		# Three lines of 33.33 = 99.99 taxable. 18% = 17.9982 raw.
		# Half = 8.9991 -> 9 each -> 18.00. Pre = 117.99 -> 118.00, adj 0.01.
		#
		# Rounding EACH LINE to the rupee first would give 33 + 33 + 33 = 99
		# taxable and a different total. That is the order of operations Section
		# 170 forbids, and this test is what holds the correct one in place.
		rows = [line(33.33), line(33.33), line(33.33)]
		totals = invoicing.compute_totals(rows, invoicing.MODE_COMMISSION, INTRA, INTRA)
		self.assertEqual(totals["taxable_total"], 99.99)
		self.assertEqual(totals["tax_total"], 18.0)
		self.assertEqual(totals["grand_total"], 118.0)
		self.assertEqual(totals["rounding_adjustment"], 0.01)

	def test_tour_package_mode_taxes_every_line_at_five_percent(self):
		# 10000 at a stored 18% and 5000 at a stored 12%, both forced to 5%.
		# Taxable 15000.00, raw tax 750.00, half 375.00 each.
		rows = [line(10000, tax_rate=18), line(5000, tax_rate=12)]
		totals = invoicing.compute_totals(rows, invoicing.MODE_TOUR_PACKAGE, INTRA, INTRA)
		self.assertEqual(totals["taxable_total"], 15000.0)
		self.assertEqual(totals["cgst_amount"], 375.0)
		self.assertEqual(totals["sgst_amount"], 375.0)
		self.assertEqual(totals["grand_total"], 15750.0)
		self.assertEqual([band["rate"] for band in totals["tax_bands"]], [5.0])

	def test_commission_mode_keeps_two_rate_bands(self):
		rows = [line(10000, tax_rate=18), line(5000, tax_rate=12)]
		totals = invoicing.compute_totals(rows, invoicing.MODE_COMMISSION, INTRA, INTRA)
		# 18% of 10000 = 1800.00; 12% of 5000 = 600.00; raw 2400.00; half 1200.00.
		self.assertEqual(totals["tax_total"], 2400.0)
		self.assertEqual([band["rate"] for band in totals["tax_bands"]], [12.0, 18.0])
		self.assertEqual(totals["tax_bands"][0]["tax"], 600.0)
		self.assertEqual(totals["tax_bands"][1]["tax"], 1800.0)

	def test_no_lines_is_zero_everywhere_and_not_an_error(self):
		totals = invoicing.compute_totals([], invoicing.MODE_COMMISSION, INTRA, INTRA)
		self.assertEqual(totals["taxable_total"], 0.0)
		self.assertEqual(totals["grand_total"], 0.0)


class TestFinancialYear(FrappeTestCase):
	"""India's financial year begins on 1 April. The boundary is the test."""

	def test_the_first_of_april_starts_the_new_year(self):
		self.assertEqual(invoicing.financial_year("2026-04-01"), (2026, 2027))
		self.assertEqual(invoicing.financial_year_label("2026-04-01"), "26-27")

	def test_the_thirty_first_of_march_is_still_the_old_year(self):
		self.assertEqual(invoicing.financial_year("2026-03-31"), (2025, 2026))
		self.assertEqual(invoicing.financial_year_label("2026-03-31"), "25-26")

	def test_january_belongs_to_the_year_that_started_last_april(self):
		self.assertEqual(invoicing.financial_year("2026-01-15"), (2025, 2026))

	def test_the_thirty_first_of_december_is_not_a_boundary(self):
		self.assertEqual(invoicing.financial_year("2025-12-31"), (2025, 2026))

	def test_the_bounds_are_half_open_at_the_top(self):
		self.assertEqual(invoicing.financial_year_bounds("2026-01-15"), ("2025-04-01", "2026-04-01"))

	def test_a_label_pads_a_single_digit_year(self):
		self.assertEqual(invoicing.financial_year_label("2009-06-01"), "09-10")


class TestNumberValidation(FrappeTestCase):
	"""GST Rule 46(b): at most 16 characters, from letters, digits, hyphen, slash."""

	def test_a_normal_number_is_accepted(self):
		self.assertEqual(invoicing.validate_number("INV/25-26/0001"), "INV/25-26/0001")

	def test_sixteen_characters_is_the_limit_and_is_allowed(self):
		value = "A" * 16
		self.assertEqual(len(value), 16)
		self.assertEqual(invoicing.validate_number(value), value)

	def test_seventeen_characters_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			invoicing.validate_number("A" * 17)

	def test_seventeen_characters_is_refused_even_when_the_charset_is_legal(self):
		# Length and charset are checked separately on purpose: either check alone
		# lets a bad number through.
		with self.assertRaises(frappe.ValidationError):
			invoicing.validate_number("INVOICE/25-26/001")

	def test_a_hash_is_not_in_the_charset(self):
		with self.assertRaises(frappe.ValidationError):
			invoicing.validate_number("INV#1")

	def test_a_space_is_not_in_the_charset(self):
		with self.assertRaises(frappe.ValidationError):
			invoicing.validate_number("INV 25-26/1")

	def test_an_underscore_is_not_in_the_charset(self):
		with self.assertRaises(frappe.ValidationError):
			invoicing.validate_number("INV_1")

	def test_an_empty_number_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			invoicing.validate_number("")

	def test_surrounding_space_is_trimmed_rather_than_refused(self):
		self.assertEqual(invoicing.validate_number("  INV/1  "), "INV/1")

	def test_a_number_is_built_with_a_zero_padded_serial(self):
		self.assertEqual(invoicing.number_for(1, "2026-01-15", prefix="INV/"), "INV/25-26/0001")
		self.assertEqual(invoicing.number_for(42, "2026-01-15", prefix="INV/"), "INV/25-26/0042")

	def test_a_serial_past_the_padding_width_grows_rather_than_wrapping(self):
		self.assertEqual(invoicing.number_for(12345, "2026-01-15", prefix="INV/"), "INV/25-26/12345")

	def test_the_default_prefix_leaves_room_inside_the_sixteen_character_ceiling(self):
		value = invoicing.number_for(9999, "2026-01-15", prefix=invoicing.DEFAULT_NUMBER_PREFIX)
		self.assertEqual(value, "INV/25-26/9999")
		self.assertEqual(len(value), 14)
		self.assertEqual(invoicing.validate_number(value), value)


class TestPaymentsAndStatus(FrappeTestCase):
	def test_payments_are_summed_including_negative_corrections(self):
		rows = [{"amount": 5000}, {"amount": 2000}, {"amount": -1000}]
		self.assertEqual(invoicing.paid_amount(rows), 6000.0)

	def test_the_remaining_amount_is_the_total_less_what_was_paid(self):
		self.assertEqual(invoicing.remaining_amount(11800, [{"amount": 5000}]), 6800.0)

	def test_no_payment_on_a_sent_invoice_stays_sent(self):
		self.assertEqual(
			invoicing.status_for(invoicing.STATUS_SENT, 11800, []), invoicing.STATUS_SENT
		)

	def test_a_part_payment_makes_it_partially_paid(self):
		self.assertEqual(
			invoicing.status_for(invoicing.STATUS_SENT, 11800, [{"amount": 5000}]),
			invoicing.STATUS_PARTIALLY_PAID,
		)

	def test_the_exact_remaining_amount_makes_it_paid(self):
		self.assertEqual(
			invoicing.status_for(invoicing.STATUS_SENT, 11800, [{"amount": 11800}]),
			invoicing.STATUS_PAID,
		)

	def test_a_negative_correction_takes_a_paid_invoice_back_to_partially_paid(self):
		rows = [{"amount": 11800}, {"amount": -800}]
		self.assertEqual(
			invoicing.status_for(invoicing.STATUS_PAID, 11800, rows), invoicing.STATUS_PARTIALLY_PAID
		)

	def test_void_is_terminal_even_when_money_arrives(self):
		self.assertEqual(
			invoicing.status_for(invoicing.STATUS_VOID, 11800, [{"amount": 11800}]),
			invoicing.STATUS_VOID,
		)

	def test_a_draft_is_never_partially_paid(self):
		self.assertEqual(
			invoicing.status_for(invoicing.STATUS_DRAFT, 11800, [{"amount": 500}]),
			invoicing.STATUS_DRAFT,
		)

	def test_overdue_is_computed_from_the_due_date_and_never_stored(self):
		self.assertNotIn("Overdue", invoicing.STATUSES)
		doc = {"status": invoicing.STATUS_SENT, "due_date": "2026-01-01"}
		self.assertTrue(invoicing.is_overdue(doc, on="2026-01-02"))
		self.assertFalse(invoicing.is_overdue(doc, on="2026-01-01"))

	def test_a_paid_invoice_is_never_overdue(self):
		doc = {"status": invoicing.STATUS_PAID, "due_date": "2020-01-01"}
		self.assertFalse(invoicing.is_overdue(doc, on="2026-01-02"))

	def test_a_voided_invoice_is_never_overdue(self):
		doc = {"status": invoicing.STATUS_VOID, "due_date": "2020-01-01"}
		self.assertFalse(invoicing.is_overdue(doc, on="2026-01-02"))


class TestRule47(FrappeTestCase):
	"""Thirty days from the service. A warning, never a refusal."""

	def test_exactly_thirty_days_later_is_still_in_time(self):
		doc = {"service_date": "2026-01-01", "invoice_date": "2026-01-31"}
		self.assertEqual(invoicing.rule_47_warning(doc), "")

	def test_thirty_one_days_later_warns(self):
		doc = {"service_date": "2026-01-01", "invoice_date": "2026-02-01"}
		self.assertIn("30", invoicing.rule_47_warning(doc))

	def test_no_service_date_means_nothing_to_warn_about(self):
		self.assertEqual(invoicing.rule_47_warning({"invoice_date": "2026-02-01"}), "")


class TestGstinAndStateCode(FrappeTestCase):
	def test_a_well_formed_gstin_is_accepted_and_upper_cased(self):
		self.assertEqual(invoicing.validate_gstin("27aapfu0939f1zv"), "27AAPFU0939F1ZV")

	def test_a_short_gstin_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			invoicing.validate_gstin("27AAPFU0939F1Z")

	def test_a_gstin_that_disagrees_with_its_state_code_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			invoicing.validate_gstin("27AAPFU0939F1ZV", "29")

	def test_a_gstin_that_agrees_with_its_state_code_is_accepted(self):
		self.assertEqual(invoicing.validate_gstin("27AAPFU0939F1ZV", "27"), "27AAPFU0939F1ZV")

	def test_an_empty_gstin_is_a_b2c_invoice_not_an_error(self):
		self.assertEqual(invoicing.validate_gstin(""), "")

	def test_a_state_code_is_two_digits(self):
		self.assertEqual(invoicing.validate_state_code("27"), "27")
		with self.assertRaises(frappe.ValidationError):
			invoicing.validate_state_code("271")
		with self.assertRaises(frappe.ValidationError):
			invoicing.validate_state_code("MH")


class TestB2CThreshold(FrappeTestCase):
	"""Rule 46: an unregistered customer billed Rs 50,000 or more must be named."""

	def test_an_invoice_with_no_gstin_is_b2c(self):
		self.assertTrue(invoicing.is_b2c({"customer_gstin": ""}))

	def test_an_invoice_with_a_gstin_is_not_b2c(self):
		self.assertFalse(invoicing.is_b2c({"customer_gstin": "27AAPFU0939F1ZV"}))

	def test_the_threshold_is_fifty_thousand(self):
		self.assertEqual(invoicing.B2C_RECIPIENT_THRESHOLD, 50000.0)


class TestUpiUri(FrappeTestCase):
	"""Criterion 8: the URI the QR encodes, exactly."""

	def test_the_uri_is_the_two_parameters_in_order(self):
		self.assertEqual(
			invoicing.upi_uri("agency@okbank", 11800), "upi://pay?pa=agency@okbank&am=11800.00"
		)

	def test_the_amount_carries_two_decimal_places(self):
		self.assertEqual(invoicing.upi_uri("a@b", 5), "upi://pay?pa=a@b&am=5.00")
		self.assertEqual(invoicing.upi_uri("a@b", 5.5), "upi://pay?pa=a@b&am=5.50")

	def test_no_vpa_means_no_uri_rather_than_a_broken_one(self):
		self.assertEqual(invoicing.upi_uri("", 11800), "")
		self.assertEqual(invoicing.upi_uri(None, 11800), "")


class TestEinvoiceAccount(FrappeTestCase):
	"""The threshold this module's out-of-scope decision rests on, and its honesty."""

	def test_the_threshold_is_recorded_as_unverified(self):
		self.assertFalse(invoicing.EINVOICE_THRESHOLD_VERIFIED)

	def test_the_source_and_the_check_date_are_both_stated(self):
		self.assertIn("10/2023", invoicing.EINVOICE_THRESHOLD_SOURCE)
		self.assertEqual(invoicing.EINVOICE_THRESHOLD_CHECKED_ON, "2026-08-19")

	def test_the_note_says_what_the_module_does_not_do(self):
		note = invoicing.einvoice_note_for(invoicing.EINVOICE_THRESHOLD_RUPEES)
		self.assertIn("does not generate IRN", note)
		self.assertIn("CA", note)
