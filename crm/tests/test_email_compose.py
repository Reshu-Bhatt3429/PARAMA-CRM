# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the composer's suppression-checked send path (master spec item 6).

Endpoint authorization (master spec §3), asserted below rather than described:

* `crm.api.email.send_email` -- POST only, any signed-in user. Row-level scope
  is derived SERVER-side by `frappe.core.doctype.communication.email.make`,
  which checks the `email` permission on the named reference record.
  `TestAuthorization` asserts the POST-only whitelisting and that the wrapper
  hands the reference doctype and name straight to `make` rather than deciding
  access itself.

Nothing here sends anything. `make` is stubbed in every test that reaches it,
so no Communication is created and no Email Queue row is written.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm import suppression
from crm.api import email as compose

BLOCKED = "opted.out@example.com"
CLEAN = "ann@example.com"
SECOND = "bob@example.com"


class ComposeTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def block(self, address=BLOCKED):
		suppression.suppress(
			suppression.CHANNEL_EMAIL,
			address,
			suppression.STATE_OPTED_OUT,
			source="test",
		)

	def send(self, **overrides):
		values = {
			"doctype": "CRM Lead",
			"name": "LEAD-TEST-0001",
			"recipients": CLEAN,
			"subject": "Fwd: Your itinerary",
			"content": "<p>See below</p>",
		}
		values.update(overrides)
		with patch("frappe.core.doctype.communication.email.make") as make:
			make.return_value = {"name": "COMM-0001"}
			result = compose.send_email(**values)
		return result, make


# --- parsing ---------------------------------------------------------------


class TestSplitAddresses(ComposeTestCase):
	def test_a_comma_list_is_split_and_trimmed(self):
		self.assertEqual(
			compose.split_addresses(" ann@example.com , bob@example.com "),
			["ann@example.com", "bob@example.com"],
		)

	def test_a_list_is_taken_as_it_comes(self):
		self.assertEqual(compose.split_addresses(["ann@example.com"]), ["ann@example.com"])

	def test_nothing_splits_to_nothing(self):
		self.assertEqual(compose.split_addresses(None), [])
		self.assertEqual(compose.split_addresses(""), [])
		self.assertEqual(compose.split_addresses(" , , "), [])


# --- the check itself ------------------------------------------------------


class TestDropSuppressed(ComposeTestCase):
	def test_a_clean_address_survives(self):
		allowed, blocked = compose.drop_suppressed([CLEAN])
		self.assertEqual((allowed, blocked), ([CLEAN], []))

	def test_a_suppressed_address_is_dropped(self):
		self.block()
		allowed, blocked = compose.drop_suppressed([CLEAN, BLOCKED])
		self.assertEqual((allowed, blocked), ([CLEAN], [BLOCKED]))

	def test_the_original_string_is_preserved(self):
		"""Not normalised. Rewriting a display name would change a clean send."""
		typed = "Ann Lee <ANN@Example.com>"
		allowed, _ = compose.drop_suppressed([typed])
		self.assertEqual(allowed, [typed])

	def test_a_display_name_around_a_suppressed_address_is_still_caught(self):
		self.block()
		allowed, blocked = compose.drop_suppressed([f"Opted Out <{BLOCKED.upper()}>"])
		self.assertEqual(allowed, [])
		self.assertEqual(len(blocked), 1)


# --- the endpoint ----------------------------------------------------------


class TestSendEmail(ComposeTestCase):
	def test_a_clean_send_reaches_make_unchanged(self):
		result, make = self.send(cc=SECOND)

		make.assert_called_once()
		kwargs = make.call_args.kwargs
		self.assertEqual(kwargs["recipients"], CLEAN)
		self.assertEqual(kwargs["cc"], SECOND)
		self.assertEqual(kwargs["subject"], "Fwd: Your itinerary")
		self.assertEqual(kwargs["send_email"], 1)
		self.assertEqual(result["suppressed"], [])

	def test_the_reference_record_is_handed_to_make(self):
		"""`make` does the permission check; this wrapper must not shortcut it."""
		_, make = self.send()
		kwargs = make.call_args.kwargs
		self.assertEqual(kwargs["doctype"], "CRM Lead")
		self.assertEqual(kwargs["name"], "LEAD-TEST-0001")

	def test_a_suppressed_recipient_is_dropped_and_named(self):
		self.block()
		result, make = self.send(recipients=f"{CLEAN}, {BLOCKED}")

		self.assertEqual(make.call_args.kwargs["recipients"], CLEAN)
		self.assertEqual(result["suppressed"], [BLOCKED])

	def test_a_suppressed_cc_is_dropped_and_the_mail_still_goes(self):
		self.block()
		result, make = self.send(cc=f"{SECOND}, {BLOCKED}")

		self.assertEqual(make.call_args.kwargs["cc"], SECOND)
		self.assertEqual(make.call_args.kwargs["recipients"], CLEAN)
		self.assertEqual(result["suppressed"], [BLOCKED])

	def test_a_suppressed_bcc_is_dropped(self):
		self.block()
		result, make = self.send(bcc=BLOCKED)

		self.assertEqual(make.call_args.kwargs["bcc"], "")
		self.assertEqual(result["suppressed"], [BLOCKED])

	def test_nothing_is_sent_when_every_recipient_opted_out(self):
		self.block()
		with patch("frappe.core.doctype.communication.email.make") as make:
			with self.assertRaises(frappe.ValidationError):
				compose.send_email(
					doctype="CRM Lead",
					name="LEAD-TEST-0001",
					recipients=BLOCKED,
					subject="Hello",
					content="<p>hi</p>",
				)
		make.assert_not_called()

	def test_an_empty_recipient_list_is_refused(self):
		with patch("frappe.core.doctype.communication.email.make") as make:
			with self.assertRaises(frappe.ValidationError):
				compose.send_email(
					doctype="CRM Lead",
					name="LEAD-TEST-0001",
					recipients="",
					subject="Hello",
					content="<p>hi</p>",
				)
		make.assert_not_called()

	def test_attachments_are_passed_through(self):
		"""A forward carries the original message's File rows by name."""
		_, make = self.send(attachments=["FILE-0001", "FILE-0002"])
		self.assertEqual(make.call_args.kwargs["attachments"], ["FILE-0001", "FILE-0002"])

	def test_a_json_attachment_list_is_parsed(self):
		_, make = self.send(attachments='["FILE-0001"]')
		self.assertEqual(make.call_args.kwargs["attachments"], ["FILE-0001"])


class TestAuthorization(ComposeTestCase):
	def test_the_endpoint_is_post_only(self):
		"""A GET-able sender is a CSRF target."""
		self.assertIn(compose.send_email, frappe.whitelisted)
		self.assertEqual(
			tuple(frappe.allowed_http_methods_for_whitelisted_func[compose.send_email]),
			("POST",),
		)

	def test_the_dotted_path_resolves(self):
		self.assertTrue(callable(frappe.get_attr("crm.api.email.send_email")))
