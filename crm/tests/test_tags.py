# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the tag endpoints (master spec §5, item 2).

Endpoint authorization (master spec §3), asserted below rather than described:

* `crm.api.tags.get_tags` -- needs READ on the named record. `TestPermissions`
  proves a Sales User who neither owns nor is assigned the lead is refused, with
  no patching: the real `crm.permissions.org_hierarchy` rule is what does it.
* `crm.api.tags.add_tag` / `remove_tag` -- POST only, need WRITE on the record.
  Same class, plus `test_state_changing_endpoints_are_post_only`.
* `crm.api.tags.search_tags` -- needs doctype-level READ, and refuses a doctype
  outside the allowlist.

Nothing here sends anything or reaches a provider. The whole feature is one
column, one master row and one link row.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.api import tags

LEAD_DOCTYPE = "CRM Lead"
OTHER_USER = "tags-outsider@example.com"


def lead_status() -> str:
	"""A live lead status. A "Lost" one would demand a lost reason."""
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


class TagsTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.lead = frappe.get_doc(
			{
				"doctype": LEAD_DOCTYPE,
				"first_name": "Tagged",
				"last_name": "Customer",
				"status": lead_status(),
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()


# --- validation ------------------------------------------------------------


class TestCleanTag(FrappeTestCase):
	def test_whitespace_is_trimmed(self):
		self.assertEqual(tags.clean_tag("  VIP  "), "VIP")

	def test_case_is_preserved(self):
		self.assertEqual(tags.clean_tag("Honeymoon"), "Honeymoon")

	def test_empty_is_refused(self):
		self.assertRaises(frappe.ValidationError, tags.clean_tag, "   ")
		self.assertRaises(frappe.ValidationError, tags.clean_tag, None)

	def test_a_comma_is_refused(self):
		"""`_user_tags` is comma-joined: one such tag would come back as two."""
		self.assertRaises(frappe.ValidationError, tags.clean_tag, "Bali, Honeymoon")

	def test_a_line_break_is_refused(self):
		self.assertRaises(frappe.ValidationError, tags.clean_tag, "Bali\nHoneymoon")

	def test_an_over_long_tag_is_refused(self):
		self.assertRaises(frappe.ValidationError, tags.clean_tag, "x" * (tags.MAX_TAG_LENGTH + 1))
		self.assertEqual(tags.clean_tag("x" * tags.MAX_TAG_LENGTH), "x" * tags.MAX_TAG_LENGTH)


class TestSplitTags(FrappeTestCase):
	def test_the_stored_leading_comma_is_dropped(self):
		self.assertEqual(tags.split_tags(",VIP,Honeymoon"), ["VIP", "Honeymoon"])

	def test_an_empty_column_is_an_empty_list(self):
		self.assertEqual(tags.split_tags(""), [])
		self.assertEqual(tags.split_tags(None), [])


class TestEscapeLike(FrappeTestCase):
	def test_wildcards_are_neutralised(self):
		self.assertEqual(tags.escape_like("50%"), "50\\%")
		self.assertEqual(tags.escape_like("a_b"), "a\\_b")

	def test_the_escape_character_itself_is_escaped_first(self):
		self.assertEqual(tags.escape_like("a\\%"), "a\\\\\\%")


# --- the round trip --------------------------------------------------------


class TestTagWrites(TagsTestCase):
	def test_a_new_record_has_no_tags(self):
		self.assertEqual(tags.get_tags(LEAD_DOCTYPE, self.lead.name), [])

	def test_add_then_read(self):
		self.assertEqual(tags.add_tag(LEAD_DOCTYPE, self.lead.name, "VIP"), ["VIP"])
		self.assertEqual(tags.get_tags(LEAD_DOCTYPE, self.lead.name), ["VIP"])

	def test_add_returns_the_whole_list(self):
		tags.add_tag(LEAD_DOCTYPE, self.lead.name, "VIP")
		self.assertEqual(tags.add_tag(LEAD_DOCTYPE, self.lead.name, "Honeymoon"), ["VIP", "Honeymoon"])

	def test_adding_the_same_tag_twice_is_a_no_op(self):
		tags.add_tag(LEAD_DOCTYPE, self.lead.name, "VIP")
		self.assertEqual(tags.add_tag(LEAD_DOCTYPE, self.lead.name, "VIP"), ["VIP"])

	def test_adding_differs_only_by_case_is_a_no_op(self):
		"""The core remover is case-insensitive, so two casings would strand one."""
		tags.add_tag(LEAD_DOCTYPE, self.lead.name, "VIP")
		self.assertEqual(tags.add_tag(LEAD_DOCTYPE, self.lead.name, "vip"), ["VIP"])

	def test_remove(self):
		tags.add_tag(LEAD_DOCTYPE, self.lead.name, "VIP")
		tags.add_tag(LEAD_DOCTYPE, self.lead.name, "Honeymoon")
		self.assertEqual(tags.remove_tag(LEAD_DOCTYPE, self.lead.name, "VIP"), ["Honeymoon"])

	def test_remove_is_case_insensitive(self):
		tags.add_tag(LEAD_DOCTYPE, self.lead.name, "VIP")
		self.assertEqual(tags.remove_tag(LEAD_DOCTYPE, self.lead.name, "vip"), [])

	def test_removing_a_tag_that_is_not_there_is_a_no_op(self):
		tags.add_tag(LEAD_DOCTYPE, self.lead.name, "VIP")
		self.assertEqual(tags.remove_tag(LEAD_DOCTYPE, self.lead.name, "Nothing"), ["VIP"])

	def test_the_master_row_is_created(self):
		tags.add_tag(LEAD_DOCTYPE, self.lead.name, "Stage2A Probe")
		self.assertTrue(frappe.db.exists("Tag", "Stage2A Probe"))

	def test_a_record_cannot_carry_more_than_the_cap(self):
		for index in range(tags.MAX_TAGS_PER_RECORD):
			tags.add_tag(LEAD_DOCTYPE, self.lead.name, f"tag-{index}")
		self.assertRaises(frappe.ValidationError, tags.add_tag, LEAD_DOCTYPE, self.lead.name, "one-too-many")

	def test_the_column_holds_the_frappe_format(self):
		tags.add_tag(LEAD_DOCTYPE, self.lead.name, "VIP")
		stored = frappe.db.get_value(LEAD_DOCTYPE, self.lead.name, "_user_tags")
		self.assertEqual(stored, ",VIP")


# --- the allowlist ---------------------------------------------------------


class TestAllowlist(TagsTestCase):
	def test_an_unlisted_doctype_is_refused(self):
		self.assertRaises(frappe.PermissionError, tags.get_tags, "CRM Task", "anything")
		self.assertRaises(frappe.PermissionError, tags.add_tag, "CRM Task", "anything", "VIP")
		self.assertRaises(frappe.PermissionError, tags.remove_tag, "CRM Task", "anything", "VIP")

	def test_search_refuses_an_unlisted_doctype(self):
		self.assertRaises(frappe.PermissionError, tags.search_tags, "CRM Task")

	def test_a_missing_record_is_refused_like_a_forbidden_one(self):
		"""Otherwise the endpoint is an existence oracle over other teams' leads."""
		self.assertRaises(frappe.PermissionError, tags.get_tags, LEAD_DOCTYPE, "CRM-LEAD-does-not-exist")

	def test_an_empty_name_is_refused(self):
		self.assertRaises(frappe.PermissionError, tags.get_tags, LEAD_DOCTYPE, "")


# --- the vocabulary --------------------------------------------------------


class TestSearchTags(TagsTestCase):
	def setUp(self):
		super().setUp()
		for name in ("Bali", "Bali Deluxe", "Honeymoon"):
			if not frappe.db.exists("Tag", name):
				frappe.get_doc({"doctype": "Tag", "name": name}).insert(ignore_permissions=True)

	def test_an_empty_query_lists_the_vocabulary(self):
		found = tags.search_tags(LEAD_DOCTYPE)
		self.assertIn("Bali", found)
		self.assertIn("Honeymoon", found)

	def test_a_query_narrows_it(self):
		found = tags.search_tags(LEAD_DOCTYPE, "bali")
		self.assertIn("Bali", found)
		self.assertIn("Bali Deluxe", found)
		self.assertNotIn("Honeymoon", found)

	def test_a_wildcard_in_the_query_is_not_a_wildcard(self):
		"""Regression: "%" must match a literal "%", not every tag on the site."""
		self.assertEqual(tags.search_tags(LEAD_DOCTYPE, "%"), [])

	def test_the_limit_is_clamped(self):
		self.assertLessEqual(len(tags.search_tags(LEAD_DOCTYPE, "", limit=10_000)), tags.MAX_SEARCH_RESULTS)


# --- authorization ---------------------------------------------------------


class TestPermissions(TagsTestCase):
	"""Master spec §3. No patching: the real lead rule is what protects a tag."""

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

	def test_a_sales_user_without_lead_access_cannot_read_its_tags(self):
		tags.add_tag(LEAD_DOCTYPE, self.lead.name, "VIP")
		frappe.set_user(OTHER_USER)
		self.assertRaises(frappe.PermissionError, tags.get_tags, LEAD_DOCTYPE, self.lead.name)

	def test_a_sales_user_without_lead_access_cannot_add_a_tag(self):
		frappe.set_user(OTHER_USER)
		self.assertRaises(frappe.PermissionError, tags.add_tag, LEAD_DOCTYPE, self.lead.name, "VIP")

	def test_a_sales_user_without_lead_access_cannot_remove_a_tag(self):
		tags.add_tag(LEAD_DOCTYPE, self.lead.name, "VIP")
		frappe.set_user(OTHER_USER)
		self.assertRaises(frappe.PermissionError, tags.remove_tag, LEAD_DOCTYPE, self.lead.name, "VIP")

	def test_the_refused_write_did_not_happen(self):
		"""The core writer sets the column BEFORE it checks. Ours checks first."""
		tags.add_tag(LEAD_DOCTYPE, self.lead.name, "VIP")
		frappe.set_user(OTHER_USER)
		try:
			tags.add_tag(LEAD_DOCTYPE, self.lead.name, "Stolen")
		except frappe.PermissionError:
			pass
		frappe.set_user("Administrator")
		self.assertEqual(tags.get_tags(LEAD_DOCTYPE, self.lead.name), ["VIP"])

	def test_the_owner_of_the_lead_can_tag_it(self):
		frappe.db.set_value(LEAD_DOCTYPE, self.lead.name, "lead_owner", OTHER_USER)
		frappe.set_user(OTHER_USER)
		self.assertEqual(tags.add_tag(LEAD_DOCTYPE, self.lead.name, "Mine"), ["Mine"])
		self.assertEqual(tags.get_tags(LEAD_DOCTYPE, self.lead.name), ["Mine"])

	def test_state_changing_endpoints_are_post_only(self):
		"""A GET-able writer is a CSRF target (the M-csrf precedent)."""
		for method in (tags.add_tag, tags.remove_tag):
			self.assertIn(method, frappe.whitelisted, msg=f"{method.__name__} must be whitelisted")
			self.assertEqual(
				frappe.allowed_http_methods_for_whitelisted_func[method],
				["POST"],
				msg=f"{method.__name__} must be POST only",
			)

	def test_the_read_endpoints_are_whitelisted(self):
		for method in (tags.get_tags, tags.search_tags):
			self.assertIn(method, frappe.whitelisted)
