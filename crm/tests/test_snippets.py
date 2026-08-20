# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for snippets (master spec item 23).

Endpoint authorization (master spec §3), asserted below rather than described:

* `crm.api.snippets.get_snippets` -- any signed-in CRM user. Row-level scope is
  `frappe.get_list` plus `get_snippet_permission_query_conditions`.
  `TestVisibility` proves another user's PRIVATE snippet is invisible, with no
  patching: the real hook is what hides it.
* `crm.api.snippets.render` -- needs READ on the snippet AND READ on the record
  named in the call. `TestRenderPermissions` proves a Sales User cannot merge
  against a lead they may not read, and that a doctype outside the allowlist is
  refused outright.

Nothing here sends anything or reaches a provider. A snippet is a row and a
string substitution.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.api import snippets

DOCTYPE = "CRM Snippet"
LEAD_DOCTYPE = "CRM Lead"
OWNER = "crm.user1@example.com"
OTHER = "crm.user2@example.com"
MANAGER = "crm.manager@example.com"


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


class SnippetTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def make_snippet(self, user=OWNER, **overrides):
		values = {
			"doctype": DOCTYPE,
			"title": "Booking confirmation",
			"shortcut": "booking",
			"body": "<p>Hello {{ first_name }}</p>",
			"shared": 0,
			"enabled": 1,
		}
		values.update(overrides)

		current = frappe.session.user
		frappe.set_user(user)
		try:
			doc = frappe.get_doc(values).insert(ignore_permissions=True)
		finally:
			frappe.set_user(current)
		return doc

	def make_lead(self, owner="Administrator", **overrides):
		# `lead_owner`, not the framework `owner`: that is the column
		# `crm/permissions/org_hierarchy.py` scopes a Sales User's reads by.
		values = {
			"doctype": LEAD_DOCTYPE,
			"first_name": "Ann",
			"last_name": "Lee",
			"status": lead_status(),
			"lead_owner": owner,
		}
		values.update(overrides)

		current = frappe.session.user
		frappe.set_user(owner)
		try:
			doc = frappe.get_doc(values).insert(ignore_permissions=True)
		finally:
			frappe.set_user(current)
		return doc


# --- the doctype's own rules -----------------------------------------------


class TestValidation(SnippetTestCase):
	def test_a_shortcut_is_folded_to_one_spelling(self):
		snippet = self.make_snippet(shortcut="  Booking Ref  ")
		self.assertEqual(snippet.shortcut, "booking-ref")

	def test_a_shortcut_of_punctuation_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_snippet(shortcut="!!!")

	def test_an_empty_shortcut_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_snippet(shortcut="   ")

	def test_a_shortcut_may_not_start_with_a_hyphen(self):
		with self.assertRaises(frappe.ValidationError):
			self.make_snippet(shortcut="-nope")

	def test_one_user_cannot_reuse_their_own_shortcut(self):
		self.make_snippet()
		with self.assertRaises(frappe.ValidationError):
			self.make_snippet(title="Another")

	def test_two_users_may_hold_the_same_private_shortcut(self):
		"""Neither of them ever sees both, so neither of them can be confused."""
		self.make_snippet(user=OWNER)
		other = self.make_snippet(user=OTHER, title="Theirs")
		self.assertEqual(other.shortcut, "booking")

	def test_a_shared_shortcut_collides_with_everything(self):
		self.make_snippet(user=MANAGER, shared=1)
		with self.assertRaises(frappe.ValidationError):
			self.make_snippet(user=OTHER, title="Theirs")

	def test_editing_a_snippet_does_not_collide_with_itself(self):
		snippet = self.make_snippet()
		snippet.title = "Renamed"
		snippet.save(ignore_permissions=True)
		self.assertEqual(snippet.title, "Renamed")


class TestSharing(SnippetTestCase):
	def test_a_sales_user_cannot_share(self):
		frappe.set_user(OWNER)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(
				{
					"doctype": DOCTYPE,
					"title": "Mine",
					"shortcut": "mine",
					"body": "<p>hi</p>",
					"shared": 1,
				}
			).insert(ignore_permissions=True)

	def test_a_manager_can_share(self):
		snippet = self.make_snippet(user=MANAGER, shared=1)
		self.assertTrue(snippet.shared)

	def test_a_sales_user_cannot_unshare(self):
		snippet = self.make_snippet(user=MANAGER, shared=1)
		frappe.set_user(OWNER)
		snippet.reload()
		snippet.shared = 0
		with self.assertRaises(frappe.PermissionError):
			snippet.save(ignore_permissions=True)

	def test_an_ordinary_save_of_a_shared_snippet_is_not_a_transition(self):
		snippet = self.make_snippet(user=MANAGER, shared=1)
		frappe.set_user(MANAGER)
		snippet.reload()
		snippet.title = "Renamed"
		snippet.save(ignore_permissions=True)
		self.assertTrue(snippet.shared)


# --- row-level scope --------------------------------------------------------


class TestVisibility(SnippetTestCase):
	def test_another_users_private_snippet_is_invisible(self):
		private = self.make_snippet(user=OWNER, title="Private")

		frappe.set_user(OTHER)
		names = [row["name"] for row in snippets.get_snippets()]
		self.assertNotIn(private.name, names)

	def test_a_shared_snippet_is_visible_to_everybody(self):
		shared = self.make_snippet(user=MANAGER, shared=1, title="Shared")

		frappe.set_user(OTHER)
		names = [row["name"] for row in snippets.get_snippets()]
		self.assertIn(shared.name, names)

	def test_a_user_sees_their_own(self):
		mine = self.make_snippet(user=OWNER)

		frappe.set_user(OWNER)
		names = [row["name"] for row in snippets.get_snippets()]
		self.assertIn(mine.name, names)

	def test_a_manager_sees_the_whole_library(self):
		private = self.make_snippet(user=OWNER, title="Private")

		frappe.set_user(MANAGER)
		names = [row["name"] for row in snippets.get_snippets()]
		self.assertIn(private.name, names)

	def test_a_disabled_snippet_is_not_offered(self):
		off = self.make_snippet(user=OWNER, enabled=0)

		frappe.set_user(OWNER)
		names = [row["name"] for row in snippets.get_snippets()]
		self.assertNotIn(off.name, names)

	def test_search_narrows_and_cannot_widen(self):
		private = self.make_snippet(user=OWNER, title="Private")

		frappe.set_user(OTHER)
		names = [row["name"] for row in snippets.get_snippets(search="Private")]
		self.assertNotIn(private.name, names)

	def test_search_matches_title_and_shortcut(self):
		snippet = self.make_snippet(user=OWNER, title="Booking confirmation", shortcut="bk")

		frappe.set_user(OWNER)
		self.assertIn(snippet.name, [row["name"] for row in snippets.get_snippets(search="booking")])
		self.assertIn(snippet.name, [row["name"] for row in snippets.get_snippets(search="bk")])
		self.assertNotIn(snippet.name, [row["name"] for row in snippets.get_snippets(search="nothing")])

	def test_the_query_condition_scopes_a_plain_get_list(self):
		private = self.make_snippet(user=OWNER, title="Private")

		frappe.set_user(OTHER)
		names = frappe.get_list(DOCTYPE, pluck="name")
		self.assertNotIn(private.name, names)

	def test_the_query_condition_is_empty_for_a_manager(self):
		self.assertEqual(snippets.get_snippet_permission_query_conditions(MANAGER), "")

	def test_the_query_condition_names_the_user_for_everybody_else(self):
		condition = snippets.get_snippet_permission_query_conditions(OWNER)
		self.assertIn(OWNER, condition)
		self.assertIn("shared", condition)


class TestDocumentPermission(SnippetTestCase):
	def test_a_user_may_not_read_another_users_private_snippet(self):
		private = self.make_snippet(user=OWNER)

		frappe.set_user(OTHER)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(DOCTYPE, private.name).check_permission("read")

	def test_a_user_may_read_a_shared_snippet(self):
		shared = self.make_snippet(user=MANAGER, shared=1)

		frappe.set_user(OTHER)
		frappe.get_doc(DOCTYPE, shared.name).check_permission("read")

	def test_a_user_may_not_write_a_shared_snippet(self):
		shared = self.make_snippet(user=MANAGER, shared=1)

		frappe.set_user(OTHER)
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(DOCTYPE, shared.name).check_permission("write")

	def test_a_user_may_write_their_own(self):
		mine = self.make_snippet(user=OWNER)

		frappe.set_user(OWNER)
		frappe.get_doc(DOCTYPE, mine.name).check_permission("write")


# --- the merge --------------------------------------------------------------


class TestMerge(SnippetTestCase):
	def test_a_known_token_is_replaced(self):
		self.assertEqual(snippets.merge("Hi {{ first_name }}", {"first_name": "Ann"}), "Hi Ann")

	def test_whitespace_inside_the_braces_does_not_matter(self):
		values = {"first_name": "Ann"}
		self.assertEqual(snippets.merge("{{first_name}}", values), "Ann")
		self.assertEqual(snippets.merge("{{   first_name   }}", values), "Ann")

	def test_an_unknown_token_is_left_as_typed(self):
		"""A misspelt token must be found by the agent, not by the customer."""
		self.assertEqual(snippets.merge("Hi {{ frist_name }}", {"first_name": "Ann"}), "Hi {{ frist_name }}")

	def test_a_known_token_with_no_value_becomes_empty(self):
		self.assertEqual(snippets.merge("Hi {{ first_name }}!", {"first_name": ""}), "Hi !")

	def test_a_value_is_html_escaped(self):
		merged = snippets.merge("{{ first_name }}", {"first_name": "<script>x</script>"})
		self.assertNotIn("<script>", merged)
		self.assertIn("&lt;script&gt;", merged)

	def test_an_empty_body_merges_to_an_empty_string(self):
		self.assertEqual(snippets.merge(None, {}), "")

	def test_jinja_is_not_evaluated(self):
		"""A snippet is written by a Sales User, so its body is never a template."""
		body = "{{ 7 * 6 }}"
		self.assertEqual(snippets.merge(body, {}), body)


class TestTokenValues(SnippetTestCase):
	def test_the_session_user_is_always_available(self):
		frappe.set_user(OWNER)
		values = snippets.token_values(None, None)
		self.assertEqual(values["user.email"], OWNER)
		self.assertTrue(values["user.full_name"])

	def test_a_record_field_is_available(self):
		lead = self.make_lead()
		values = snippets.token_values(LEAD_DOCTYPE, lead.name)
		self.assertEqual(values["first_name"], "Ann")
		self.assertEqual(values["name"], lead.name)

	def test_a_doctype_outside_the_allowlist_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			snippets.token_values("User", OWNER)


class TestRender(SnippetTestCase):
	def test_render_merges_against_the_record(self):
		lead = self.make_lead(owner=OWNER)
		snippet = self.make_snippet(user=OWNER, body="<p>Hello {{ first_name }}</p>")

		frappe.set_user(OWNER)
		result = snippets.render(snippet.name, LEAD_DOCTYPE, lead.name)
		self.assertEqual(result["body"], "<p>Hello Ann</p>")
		self.assertEqual(result["shortcut"], "booking")

	def test_render_works_without_a_record(self):
		snippet = self.make_snippet(user=OWNER, body="<p>{{ user.email }}</p>")

		frappe.set_user(OWNER)
		self.assertEqual(snippets.render(snippet.name)["body"], f"<p>{OWNER}</p>")

	def test_a_disabled_snippet_is_refused(self):
		snippet = self.make_snippet(user=OWNER, enabled=0)

		frappe.set_user(OWNER)
		with self.assertRaises(frappe.ValidationError):
			snippets.render(snippet.name)


class TestRenderPermissions(SnippetTestCase):
	def test_another_users_private_snippet_cannot_be_rendered(self):
		private = self.make_snippet(user=OWNER)

		frappe.set_user(OTHER)
		with self.assertRaises(frappe.PermissionError):
			snippets.render(private.name)

	def test_a_record_the_caller_cannot_read_is_refused(self):
		"""Naming a lead in the request does not grant access to it."""
		lead = self.make_lead(owner=OWNER)
		shared = self.make_snippet(user=MANAGER, shared=1, body="<p>{{ first_name }}</p>")

		frappe.set_user(OTHER)
		with self.assertRaises(frappe.PermissionError):
			snippets.render(shared.name, LEAD_DOCTYPE, lead.name)

	def test_a_doctype_outside_the_allowlist_is_refused(self):
		shared = self.make_snippet(user=MANAGER, shared=1)

		frappe.set_user(OTHER)
		with self.assertRaises(frappe.ValidationError):
			snippets.render(shared.name, "User", OTHER)


class TestWhitelisting(SnippetTestCase):
	def test_the_read_endpoints_are_whitelisted(self):
		for method in (snippets.get_snippets, snippets.render):
			self.assertIn(method, frappe.whitelisted, msg=f"{method.__name__} must be whitelisted")

	def test_the_permission_hooks_are_registered(self):
		from crm import hooks

		self.assertEqual(
			hooks.permission_query_conditions[DOCTYPE],
			"crm.api.snippets.get_snippet_permission_query_conditions",
		)
		self.assertEqual(hooks.has_permission[DOCTYPE], "crm.api.snippets.has_snippet_permission")

	def test_the_hook_paths_resolve(self):
		self.assertTrue(callable(frappe.get_attr("crm.api.snippets.get_snippet_permission_query_conditions")))
		self.assertTrue(callable(frappe.get_attr("crm.api.snippets.has_snippet_permission")))
