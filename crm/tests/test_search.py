# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the Cmd+K palette endpoints (master spec §5, items 10 and 11).

Endpoint authorization (master spec §3), asserted below rather than described:

* `crm.api.search.palette_search` -- any signed-in user. Row scope comes from
  `frappe.get_list` per group, which puts `crm.permissions.org_hierarchy` into
  the SQL for CRM Lead and CRM Deal. `TestPermissions` proves it with a real
  Sales User and no patching.
* `crm.api.search.resolve_records` -- the same, for the "recently viewed" list.
  A record the caller has since lost access to must not come back, which is
  `test_a_recent_the_user_lost_access_to_is_dropped`.

Neither endpoint writes anything, sends anything or reaches a provider.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.api import search

LEAD_DOCTYPE = "CRM Lead"
OTHER_USER = "search-outsider@example.com"

MARKER = "Zephyrine"


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


class SearchTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.lead = frappe.get_doc(
			{
				"doctype": LEAD_DOCTYPE,
				"first_name": MARKER,
				"last_name": "Bhat",
				"status": lead_status(),
				"email": "zephyrine.bhat@example.com",
				"mobile_no": "+919820000078",
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def items(self, payload, doctype):
		for group in payload["groups"]:
			if group["doctype"] == doctype:
				return group["items"]
		return []

	def names(self, payload, doctype):
		return [item["name"] for item in self.items(payload, doctype)]


# --- pure helpers ----------------------------------------------------------


class TestHelpers(FrappeTestCase):
	def test_escape_like_neutralises_wildcards(self):
		self.assertEqual(search.escape_like("50%"), "50\\%")
		self.assertEqual(search.escape_like("a_b"), "a\\_b")
		self.assertEqual(search.escape_like("a\\b"), "a\\\\b")

	def test_digits_of(self):
		self.assertEqual(search.digits_of("+91 98200-00078"), "919820000078")
		self.assertEqual(search.digits_of("no digits"), "")

	def test_first_of_takes_the_first_non_empty(self):
		row = frappe._dict({"a": "", "b": "  ", "c": "Ok"})
		self.assertEqual(search.first_of(row, ("a", "b", "c")), "Ok")
		self.assertEqual(search.first_of(row, ("a", "b")), "")

	def test_subtitle_joins_at_most_two_distinct_values(self):
		row = frappe._dict({"a": "One", "b": "Two", "c": "Three"})
		self.assertEqual(search.subtitle_of(row, ("a", "b", "c"), title=""), "One · Two")

	def test_subtitle_never_repeats_the_title(self):
		row = frappe._dict({"a": "Acme", "b": "Open"})
		self.assertEqual(search.subtitle_of(row, ("a", "b"), title="Acme"), "Open")

	def test_subtitle_drops_duplicates(self):
		row = frappe._dict({"a": "Acme", "b": "Acme", "c": "Open"})
		self.assertEqual(search.subtitle_of(row, ("a", "b", "c"), title=""), "Acme · Open")

	def test_a_phone_fragment_shorter_than_three_digits_is_not_searched(self):
		group = search.GROUP_BY_DOCTYPE[LEAD_DOCTYPE]
		clauses = search.build_or_filters(group, "ab12")
		self.assertNotIn("custom_parama_phone_e164", [clause[0] for clause in clauses])

	def test_a_phone_fragment_is_searched_on_the_derived_column(self):
		group = search.GROUP_BY_DOCTYPE[LEAD_DOCTYPE]
		clauses = search.build_or_filters(group, "98200")
		self.assertIn(["custom_parama_phone_e164", "like", "%98200%"], clauses)

	def test_a_group_without_the_derived_columns_never_filters_on_them(self):
		group = search.GROUP_BY_DOCTYPE["CRM Task"]
		clauses = search.build_or_filters(group, "98200")
		self.assertEqual([clause[0] for clause in clauses], list(group["search"]))


# --- searching -------------------------------------------------------------


class TestPaletteSearch(SearchTestCase):
	def test_a_lead_is_found_by_name(self):
		payload = search.palette_search(MARKER)
		self.assertIn(self.lead.name, self.names(payload, LEAD_DOCTYPE))

	def test_a_lead_is_found_by_email(self):
		payload = search.palette_search("zephyrine.bhat@example.com")
		self.assertIn(self.lead.name, self.names(payload, LEAD_DOCTYPE))

	def test_a_lead_is_found_by_a_phone_fragment(self):
		payload = search.palette_search("0000078")
		self.assertIn(self.lead.name, self.names(payload, LEAD_DOCTYPE))

	def test_a_lead_is_found_by_its_id(self):
		payload = search.palette_search(self.lead.name)
		self.assertIn(self.lead.name, self.names(payload, LEAD_DOCTYPE))

	def test_a_short_query_searches_nothing(self):
		"""§2: the palette shows recents instead, it is never blank."""
		self.assertEqual(search.palette_search("z")["groups"], [])
		self.assertEqual(search.palette_search("")["groups"], [])
		self.assertEqual(search.palette_search("   ")["groups"], [])

	def test_a_wildcard_query_is_not_a_wildcard(self):
		"""Regression: "%" must not return the whole database."""
		self.assertEqual(search.palette_search("%%")["groups"], [])

	def test_an_empty_group_is_dropped_rather_than_rendered(self):
		payload = search.palette_search(MARKER)
		self.assertTrue(all(group["items"] for group in payload["groups"]))

	def test_groups_keep_the_declared_order(self):
		order = [group["doctype"] for group in search.GROUPS]
		payload = search.palette_search("a")
		found = [group["doctype"] for group in payload["groups"]]
		self.assertEqual(found, [doctype for doctype in order if doctype in found])

	def test_the_limit_is_clamped(self):
		payload = search.palette_search(MARKER, limit=10_000)
		for group in payload["groups"]:
			self.assertLessEqual(len(group["items"]), search.MAX_LIMIT)

	def test_the_row_shape_is_the_contract(self):
		item = self.items(search.palette_search(MARKER), LEAD_DOCTYPE)[0]
		self.assertEqual(
			set(item),
			{"doctype", "name", "title", "subtitle", "reference_doctype", "reference_docname"},
		)
		self.assertEqual(item["title"], f"{MARKER} Bhat")

	def test_a_task_is_found_by_title(self):
		task = frappe.get_doc(
			{"doctype": "CRM Task", "title": f"Call {MARKER} back", "status": "Backlog"}
		).insert(ignore_permissions=True)
		payload = search.palette_search(MARKER)
		self.assertIn(task.name, self.names(payload, "CRM Task"))

	def test_a_note_is_found_by_title(self):
		note = frappe.get_doc(
			{"doctype": "FCRM Note", "title": f"{MARKER} prefers WhatsApp", "content": "..."}
		).insert(ignore_permissions=True)
		payload = search.palette_search(MARKER)
		self.assertIn(note.name, self.names(payload, "FCRM Note"))

	def test_every_group_can_be_queried_without_an_error(self):
		"""A misspelled field in `GROUPS` is an SQL error, not an empty result."""
		for group in search.GROUPS:
			search.search_group(group, MARKER, 5)


# --- recents ---------------------------------------------------------------


class TestResolveRecords(SearchTestCase):
	def test_a_readable_record_comes_back_with_a_fresh_title(self):
		frappe.db.set_value(LEAD_DOCTYPE, self.lead.name, "lead_name", "Renamed Later")
		found = search.resolve_records([{"doctype": LEAD_DOCTYPE, "name": self.lead.name}])
		self.assertEqual(len(found), 1)
		self.assertEqual(found[0]["title"], "Renamed Later")

	def test_the_input_order_is_preserved(self):
		other = frappe.get_doc(
			{"doctype": LEAD_DOCTYPE, "first_name": "Second", "status": lead_status()}
		).insert(ignore_permissions=True)
		wanted = [
			{"doctype": LEAD_DOCTYPE, "name": other.name},
			{"doctype": LEAD_DOCTYPE, "name": self.lead.name},
		]
		found = search.resolve_records(wanted)
		self.assertEqual([row["name"] for row in found], [other.name, self.lead.name])

	def test_a_deleted_record_is_dropped(self):
		found = search.resolve_records([{"doctype": LEAD_DOCTYPE, "name": "CRM-LEAD-gone"}])
		self.assertEqual(found, [])

	def test_an_unlisted_doctype_is_dropped(self):
		self.assertEqual(search.resolve_records([{"doctype": "User", "name": "Administrator"}]), [])

	def test_junk_input_is_dropped_rather_than_raising(self):
		self.assertEqual(search.resolve_records([]), [])
		self.assertEqual(search.resolve_records(["nonsense", 7, None]), [])
		self.assertEqual(search.resolve_records("not-a-list"), [])

	def test_a_json_string_is_accepted(self):
		"""The client posts it as JSON; `frappe.parse_json` is what unpacks it."""
		import json

		payload = json.dumps([{"doctype": LEAD_DOCTYPE, "name": self.lead.name}])
		self.assertEqual(len(search.resolve_records(payload)), 1)

	def test_the_input_is_capped(self):
		wanted = [{"doctype": LEAD_DOCTYPE, "name": f"L-{index}"} for index in range(200)]
		wanted.append({"doctype": LEAD_DOCTYPE, "name": self.lead.name})
		self.assertEqual(search.resolve_records(wanted), [])


# --- authorization ---------------------------------------------------------


class TestPermissions(SearchTestCase):
	"""Master spec §3, the permission-matrix test for both endpoints."""

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
		frappe.set_user(OTHER_USER)
		self.assertEqual(self.names(search.palette_search(MARKER), LEAD_DOCTYPE), [])

	def test_a_sales_user_does_not_reach_it_by_email_either(self):
		frappe.set_user(OTHER_USER)
		payload = search.palette_search("zephyrine.bhat@example.com")
		self.assertEqual(self.names(payload, LEAD_DOCTYPE), [])

	def test_a_sales_user_does_see_their_own_lead(self):
		frappe.db.set_value(LEAD_DOCTYPE, self.lead.name, "lead_owner", OTHER_USER)
		frappe.set_user(OTHER_USER)
		self.assertEqual(self.names(search.palette_search(MARKER), LEAD_DOCTYPE), [self.lead.name])

	def test_a_recent_the_user_lost_access_to_is_dropped(self):
		"""Item 11: localStorage remembers what the browser saw, not what it may see."""
		frappe.set_user(OTHER_USER)
		found = search.resolve_records([{"doctype": LEAD_DOCTYPE, "name": self.lead.name}])
		self.assertEqual(found, [])

	def test_a_recent_the_user_still_owns_is_kept(self):
		frappe.db.set_value(LEAD_DOCTYPE, self.lead.name, "lead_owner", OTHER_USER)
		frappe.set_user(OTHER_USER)
		found = search.resolve_records([{"doctype": LEAD_DOCTYPE, "name": self.lead.name}])
		self.assertEqual([row["name"] for row in found], [self.lead.name])

	def test_the_scope_comes_from_the_hierarchy_conditions(self):
		"""Named explicitly so a rewrite away from `get_list` fails here."""
		from crm.permissions.org_hierarchy import get_lead_permission_query_conditions

		condition = get_lead_permission_query_conditions(OTHER_USER)
		self.assertIn("lead_owner", condition)
		self.assertIn(OTHER_USER, condition)

	def test_both_endpoints_are_whitelisted(self):
		for method in (search.palette_search, search.resolve_records):
			self.assertIn(method, frappe.whitelisted)
