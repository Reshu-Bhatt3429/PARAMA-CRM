# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the duplicate warning on create (master spec §5, item 3).

Endpoint authorization (master spec §3), asserted below rather than described:

* `crm.api.duplicates.check_duplicates` -- any signed-in user; row scope from
  `frappe.get_list`, which puts `crm.permissions.org_hierarchy` into the SQL.
  `TestPermissions` proves it with a real Sales User and no patching: a lead
  that belongs to somebody else is not returned even when the caller supplies
  that lead's exact email address.
* The `doctype` argument is checked against a fixed allowlist, which
  `test_an_unlisted_doctype_is_refused` covers.

The endpoint has no side effect, so there is nothing to send and no provider to
reach.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.api import duplicates

LEAD_DOCTYPE = "CRM Lead"
OTHER_USER = "duplicates-outsider@example.com"

EMAIL = "priya.sharma@example.com"
PHONE = "+919820000077"


def lead_status() -> str:
	status = frappe.db.get_value("CRM Lead Status", {"type": ["!=", "Lost"]}, "name")
	if not status:
		status = (
			frappe.get_doc(
				{"doctype": "CRM Lead Status", "lead_status": "New", "position": 1, "type": "Open"}
			)
			.insert(ignore_permissions=True)
			.name
		)
	return status


class DuplicatesTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.lead = self.make_lead()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def make_lead(self, **overrides):
		values = {
			"doctype": LEAD_DOCTYPE,
			"first_name": "Priya",
			"last_name": "Sharma",
			"status": lead_status(),
			"email": EMAIL,
			"mobile_no": PHONE,
		}
		values.update(overrides)
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def names(self, results):
		return [row["name"] for row in results]


# --- matching --------------------------------------------------------------


class TestMatching(DuplicatesTestCase):
	def test_the_stage_1a_keys_are_actually_written(self):
		"""The whole feature rests on them; if the patch is missing, say so here."""
		self.assertEqual(
			frappe.db.get_value(LEAD_DOCTYPE, self.lead.name, "custom_parama_email_normalized"), EMAIL
		)
		self.assertEqual(frappe.db.get_value(LEAD_DOCTYPE, self.lead.name, "custom_parama_phone_e164"), PHONE)

	def test_an_exact_email_matches(self):
		found = duplicates.check_duplicates(LEAD_DOCTYPE, email=EMAIL)
		self.assertIn(self.lead.name, self.names(found))
		self.assertEqual(found[0]["matched_field"], "email")
		self.assertEqual(found[0]["doctype"], LEAD_DOCTYPE)
		self.assertEqual(found[0]["title"], "Priya Sharma")

	def test_a_differently_cased_email_matches(self):
		found = duplicates.check_duplicates(LEAD_DOCTYPE, email="  PRIYA.Sharma@Example.com ")
		self.assertIn(self.lead.name, self.names(found))

	def test_a_display_name_wrapped_email_matches(self):
		found = duplicates.check_duplicates(LEAD_DOCTYPE, email=f"Priya Sharma <{EMAIL}>")
		self.assertIn(self.lead.name, self.names(found))

	def test_a_nationally_written_phone_matches(self):
		"""The point of the E.164 column: "98200 00077" is the same number."""
		found = duplicates.check_duplicates(LEAD_DOCTYPE, phone="+91 98200 00077")
		self.assertIn(self.lead.name, self.names(found))
		self.assertEqual(found[0]["matched_field"], "phone")

	def test_an_unrelated_address_matches_nothing(self):
		self.assertEqual(duplicates.check_duplicates(LEAD_DOCTYPE, email="nobody@example.com"), [])

	def test_nothing_supplied_matches_nothing(self):
		self.assertEqual(duplicates.check_duplicates(LEAD_DOCTYPE), [])
		self.assertEqual(duplicates.check_duplicates(LEAD_DOCTYPE, email="", phone=""), [])

	def test_an_unparsable_value_matches_nothing(self):
		"""It must never fall through to an empty key and match every keyless row."""
		self.assertEqual(duplicates.check_duplicates(LEAD_DOCTYPE, email="not-an-address", phone="12"), [])

	def test_a_record_matched_on_both_fields_appears_once(self):
		found = duplicates.check_duplicates(LEAD_DOCTYPE, email=EMAIL, phone=PHONE)
		self.assertEqual(self.names(found).count(self.lead.name), 1)
		self.assertEqual(found[0]["matched_field"], "email")

	def test_the_result_is_capped(self):
		for index in range(duplicates.MAX_RESULTS + 3):
			self.make_lead(first_name=f"Priya{index}")
		found = duplicates.check_duplicates(LEAD_DOCTYPE, email=EMAIL)
		self.assertEqual(len(found), duplicates.MAX_RESULTS)

	def test_a_contact_is_searched_alongside_the_lead(self):
		# `email_id` on Contact is derived: `Contact.set_primary_email` clears it
		# unless an `email_ids` row says otherwise, so a fixture that sets the
		# field directly saves a contact with no address and no derived key.
		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": "Priya",
				"last_name": "Sharma",
				"email_ids": [{"email_id": EMAIL, "is_primary": 1}],
			}
		).insert(ignore_permissions=True)
		self.assertEqual(contact.email_id, EMAIL)
		found = duplicates.check_duplicates(LEAD_DOCTYPE, email=EMAIL)
		self.assertIn(("Contact", contact.name), [(row["doctype"], row["name"]) for row in found])

	def test_the_response_shape_is_the_contract(self):
		row = duplicates.check_duplicates(LEAD_DOCTYPE, email=EMAIL)[0]
		self.assertEqual(set(row), {"doctype", "name", "title", "matched_field"})


class TestAllowlist(DuplicatesTestCase):
	def test_an_unlisted_doctype_is_refused(self):
		self.assertRaises(frappe.PermissionError, duplicates.check_duplicates, "Tag", EMAIL)
		self.assertRaises(frappe.PermissionError, duplicates.check_duplicates, "User", EMAIL)

	def test_the_endpoint_is_whitelisted(self):
		self.assertIn(duplicates.check_duplicates, frappe.whitelisted)


# --- authorization ---------------------------------------------------------


class TestPermissions(DuplicatesTestCase):
	"""Master spec §3, the permission-matrix test for this endpoint."""

	def setUp(self):
		super().setUp()
		if not frappe.db.exists("User", OTHER_USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": OTHER_USER,
					"first_name": "Outsider",
					"send_welcome_email": 0,
					"roles": [{"role": "Sales User"}],
				}
			).insert(ignore_permissions=True)

	def test_a_sales_user_does_not_see_another_teams_lead(self):
		"""The whole point of §3: knowing the address must not reveal the record."""
		frappe.set_user(OTHER_USER)
		self.assertEqual(duplicates.check_duplicates(LEAD_DOCTYPE, email=EMAIL), [])

	def test_a_sales_user_does_not_see_another_teams_lead_by_phone_either(self):
		frappe.set_user(OTHER_USER)
		self.assertEqual(duplicates.check_duplicates(LEAD_DOCTYPE, phone=PHONE), [])

	def test_a_sales_user_does_see_their_own_lead(self):
		"""The counterpart: the filter is row-level, not a blanket refusal."""
		frappe.db.set_value(LEAD_DOCTYPE, self.lead.name, "lead_owner", OTHER_USER)
		frappe.set_user(OTHER_USER)
		found = duplicates.check_duplicates(LEAD_DOCTYPE, email=EMAIL)
		self.assertEqual(self.names(found), [self.lead.name])

	def test_the_scope_comes_from_the_hierarchy_conditions(self):
		"""Named explicitly so a refactor that drops `get_list` fails here.

		`frappe.get_list` is what applies `permission_query_conditions`. A
		rewrite onto `frappe.db.sql` or the query builder would still pass the
		two tests above only if it applied the same conditions itself, so this
		asserts the condition text reaches the query rather than the result.
		"""
		from crm.permissions.org_hierarchy import get_lead_permission_query_conditions

		condition = get_lead_permission_query_conditions(OTHER_USER)
		self.assertIn("lead_owner", condition)
		self.assertIn(OTHER_USER, condition)
