# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the consent and suppression ledger (spec F1).

Nothing here reaches a provider. The ledger is a pure database object, and the
one path that touches the follow-up engine -- the WhatsApp opt-out write-through
-- is exercised through `handle_incoming` with a `frappe._dict` message, exactly
as `crm/tests/test_followup_engine.py` does. `frappe_whatsapp` is not installed
in CI, so no WhatsApp Message row is ever read or written.

Endpoint authorization (master spec §3): this module adds NO whitelisted
endpoint. `crm.suppression` is called from server-side send paths and from the
follow-up engine only. Its two writers take `ignore_permissions=True` on purpose
-- a scheduler worker recording a customer's opt-out has no session user to
check -- and the doctype's own role permissions cover the desk views.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm import suppression
from crm.api import followup_engine as engine


class TestNormalization(FrappeTestCase):
	def test_email_is_lowercased_and_trimmed(self):
		self.assertEqual(
			suppression.normalize_address("Email", "  Ann.Lee@Example.COM "), "ann.lee@example.com"
		)

	def test_display_name_is_unwrapped(self):
		self.assertEqual(
			suppression.normalize_address("Email", "Ann Lee <ann@example.com>"), "ann@example.com"
		)

	def test_unparsable_email_normalises_to_nothing(self):
		self.assertEqual(suppression.normalize_address("Email", "not-an-address"), "")

	def test_phone_becomes_e164(self):
		self.assertEqual(suppression.normalize_address("WhatsApp", "919876543210"), "+919876543210")
		self.assertEqual(suppression.normalize_address("WhatsApp", "+91 98765 43210"), "+919876543210")

	def test_unparsable_phone_normalises_to_nothing(self):
		self.assertEqual(suppression.normalize_address("WhatsApp", "12"), "")

	def test_unknown_channel_normalises_to_nothing(self):
		self.assertEqual(suppression.normalize_address("Carrier Pigeon", "ann@example.com"), "")


class TestSuppressionLedger(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_unknown_address_is_not_suppressed(self):
		self.assertFalse(suppression.is_suppressed("Email", "nobody@example.com"))

	def test_suppress_then_check(self):
		suppression.suppress("Email", "Ann@Example.com", source="manual")
		self.assertTrue(suppression.is_suppressed("Email", "ann@example.com"))

	def test_check_is_insensitive_to_the_spelling_used(self):
		"""The whole point of normalising: the check must not depend on the caller."""
		suppression.suppress("WhatsApp", "+91 98765 43210", source="manual")
		self.assertTrue(suppression.is_suppressed("WhatsApp", "919876543210"))
		self.assertTrue(suppression.is_suppressed("WhatsApp", "+919876543210"))

	def test_suppression_is_per_channel(self):
		suppression.suppress("WhatsApp", "+919876543210", source="manual")
		self.assertFalse(suppression.is_suppressed("Email", "ann@example.com"))

	def test_suppress_is_idempotent(self):
		first = suppression.suppress("Email", "ann@example.com", source="one")
		second = suppression.suppress("Email", "ANN@example.com", source="two")
		self.assertEqual(first, second)
		self.assertEqual(frappe.db.count(suppression.SUPPRESSION_DOCTYPE, {"name": first}), 1)

	def test_repeat_suppression_keeps_the_original_date(self):
		"""When consent was withdrawn is the interesting date, not the last bounce."""
		name = suppression.suppress("Email", "ann@example.com", source="one")
		original = frappe.db.get_value(suppression.SUPPRESSION_DOCTYPE, name, "suppressed_at")

		suppression.suppress("Email", "ann@example.com", state=suppression.STATE_BOUNCED, source="two")
		self.assertEqual(
			frappe.db.get_value(suppression.SUPPRESSION_DOCTYPE, name, "suppressed_at"), original
		)
		self.assertEqual(
			frappe.db.get_value(suppression.SUPPRESSION_DOCTYPE, name, "state"), suppression.STATE_BOUNCED
		)

	def test_unnormalisable_address_writes_no_row(self):
		before = frappe.db.count(suppression.SUPPRESSION_DOCTYPE)
		self.assertIsNone(suppression.suppress("Email", "not-an-address"))
		self.assertIsNone(suppression.suppress("WhatsApp", "12"))
		self.assertEqual(frappe.db.count(suppression.SUPPRESSION_DOCTYPE), before)

	def test_unknown_channel_is_refused(self):
		self.assertRaises(frappe.ValidationError, suppression.suppress, "Carrier Pigeon", "ann@example.com")

	def test_unknown_state_is_refused(self):
		self.assertRaises(frappe.ValidationError, suppression.suppress, "Email", "ann@example.com", "Sulking")

	def test_source_is_trimmed_and_stripped_of_html(self):
		name = suppression.suppress("Email", "ann@example.com", source="<b>x</b>" + "y" * 500)
		stored = frappe.db.get_value(suppression.SUPPRESSION_DOCTYPE, name, "source")
		self.assertNotIn("<b>", stored)
		self.assertLessEqual(len(stored), suppression.MAX_SOURCE_LENGTH)

	def test_get_suppression_returns_the_row(self):
		suppression.suppress("Email", "ann@example.com", source="manual")
		row = suppression.get_suppression("Email", "ANN@EXAMPLE.COM")
		self.assertEqual(row.address, "ann@example.com")
		self.assertEqual(row.state, suppression.STATE_OPTED_OUT)

	def test_ledger_read_failure_fails_closed(self):
		"""An unreadable ledger must hold a message back, never let it through."""
		with patch.object(suppression, "suppression_key", side_effect=RuntimeError("boom")):
			self.assertTrue(suppression.is_suppressed("Email", "ann@example.com"))


class TestReversal(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_reversal_needs_a_reason(self):
		suppression.suppress("Email", "ann@example.com", source="manual")
		self.assertRaises(frappe.ValidationError, suppression.unsuppress, "Email", "ann@example.com", "")
		self.assertRaises(frappe.ValidationError, suppression.unsuppress, "Email", "ann@example.com", "   ")
		self.assertTrue(suppression.is_suppressed("Email", "ann@example.com"))

	def test_reversal_clears_the_check(self):
		suppression.suppress("Email", "ann@example.com", source="manual")
		suppression.unsuppress("Email", "ann@example.com", "customer asked us to resume")
		self.assertFalse(suppression.is_suppressed("Email", "ann@example.com"))

	def test_reversal_keeps_the_row_and_records_who_and_why(self):
		name = suppression.suppress("Email", "ann@example.com", source="WhatsApp message WM-1: STOP")
		suppression.unsuppress("Email", "ann@example.com", "customer asked us to resume")

		row = frappe.db.get_value(
			suppression.SUPPRESSION_DOCTYPE,
			name,
			["active", "released_by", "release_reason", "released_at"],
			as_dict=True,
		)
		self.assertEqual(row.active, 0)
		self.assertEqual(row.released_by, frappe.session.user)
		self.assertEqual(row.release_reason, "customer asked us to resume")
		self.assertIsNotNone(row.released_at)

	def test_reversal_writes_an_audit_comment(self):
		name = suppression.suppress("Email", "ann@example.com", source="WhatsApp message WM-1: STOP")
		suppression.unsuppress("Email", "ann@example.com", "customer asked us to resume")

		comments = frappe.get_all(
			"Comment",
			filters={"reference_doctype": suppression.SUPPRESSION_DOCTYPE, "reference_name": name},
			pluck="content",
		)
		self.assertEqual(len(comments), 1)
		self.assertIn("customer asked us to resume", comments[0])
		self.assertIn("WhatsApp message WM-1", comments[0])

	def test_reversing_an_unsuppressed_address_is_a_no_op(self):
		self.assertIsNone(suppression.unsuppress("Email", "nobody@example.com", "why not"))

	def test_re_suppression_after_a_reversal_reopens_the_same_row(self):
		name = suppression.suppress("Email", "ann@example.com", source="one")
		suppression.unsuppress("Email", "ann@example.com", "resume")
		again = suppression.suppress("Email", "ann@example.com", source="two")

		self.assertEqual(name, again)
		self.assertTrue(suppression.is_suppressed("Email", "ann@example.com"))
		row = frappe.db.get_value(
			suppression.SUPPRESSION_DOCTYPE, name, ["released_at", "release_reason"], as_dict=True
		)
		self.assertIsNone(row.released_at)
		self.assertIsNone(row.release_reason)


class TestBulkFilter(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_split_allowed_from_suppressed(self):
		suppression.suppress("Email", "blocked@example.com", source="manual")
		allowed, blocked = suppression.filter_suppressed(
			"Email", ["Allowed@example.com", "BLOCKED@example.com", "junk"]
		)
		self.assertEqual(allowed, ["allowed@example.com"])
		self.assertEqual(blocked, ["blocked@example.com"])

	def test_duplicates_collapse(self):
		allowed, blocked = suppression.filter_suppressed(
			"Email", ["ann@example.com", "ANN@example.com", " ann@example.com "]
		)
		self.assertEqual(allowed, ["ann@example.com"])
		self.assertEqual(blocked, [])

	def test_empty_input(self):
		self.assertEqual(suppression.filter_suppressed("Email", []), ([], []))


class TestWhatsappWriteThrough(FrappeTestCase):
	"""The existing WhatsApp opt-out must ALSO land in the ledger (spec F1).

	These assert the write-through only. The follow-up engine's own behaviour is
	covered by `crm/tests/test_followup_engine.py` and is unchanged.
	"""

	def setUp(self):
		self.patches = [
			patch.object(engine, "commit"),
			patch.object(engine, "rollback"),
		]
		for patcher in self.patches:
			patcher.start()
		frappe.db.set_single_value(engine.SETTINGS_DOCTYPE, "stop_keywords", "stop\nunsubscribe")

	def tearDown(self):
		for patcher in self.patches:
			patcher.stop()
		frappe.db.rollback()
		frappe.clear_document_cache(engine.SETTINGS_DOCTYPE, engine.SETTINGS_DOCTYPE)

	def make_lead(self, mobile_no="+919876543210"):
		return frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Ann",
				"mobile_no": mobile_no,
				"lead_owner": "Administrator",
			}
		).insert(ignore_permissions=True)

	def make_followup(self, lead, **fields):
		followup = frappe.get_doc(
			{
				"doctype": engine.FOLLOWUP_DOCTYPE,
				"lead": lead.name,
				"phone": lead.mobile_no,
				"state": engine.STATE_ACTIVE,
				"current_stage": 0,
				"cycle": 1,
			}
		)
		followup.update(fields)
		return followup.insert(ignore_permissions=True)

	def incoming(self, lead, text):
		return frappe._dict(
			{
				"name": "WM-TEST-0001",
				"type": "Incoming",
				"message": text,
				"reference_doctype": "CRM Lead",
				"reference_name": lead.name,
				"from": lead.mobile_no,
				"to": "+14155550000",
				"creation": frappe.utils.now_datetime(),
				"flags": frappe._dict(),
			}
		)

	def test_optout_on_an_enrolled_lead_writes_the_ledger(self):
		lead = self.make_lead()
		followup = self.make_followup(lead)

		engine.handle_incoming(self.incoming(lead, "STOP"))

		# The engine's own state is unchanged in shape.
		self.assertEqual(
			frappe.db.get_value(engine.FOLLOWUP_DOCTYPE, followup.name, "state"), engine.STATE_OPTED_OUT
		)
		# And the ledger now knows too.
		self.assertTrue(suppression.is_suppressed("WhatsApp", lead.mobile_no))
		row = suppression.get_suppression("WhatsApp", lead.mobile_no)
		self.assertEqual(row.state, suppression.STATE_OPTED_OUT)
		self.assertIn("WM-TEST-0001", row.source)

	def test_optout_without_a_followup_row_still_writes_the_ledger(self):
		lead = self.make_lead(mobile_no="+919876543211")

		engine.handle_incoming(self.incoming(lead, "unsubscribe please"))

		self.assertTrue(suppression.is_suppressed("WhatsApp", "+919876543211"))

	def test_a_plain_reply_writes_nothing_to_the_ledger(self):
		lead = self.make_lead()
		self.make_followup(lead)

		engine.handle_incoming(self.incoming(lead, "sounds good, send the quote"))

		self.assertFalse(suppression.is_suppressed("WhatsApp", lead.mobile_no))

	def test_reopen_optout_lifts_the_ledger_row(self):
		lead = self.make_lead()
		followup = self.make_followup(lead)
		engine.handle_incoming(self.incoming(lead, "STOP"))
		self.assertTrue(suppression.is_suppressed("WhatsApp", lead.mobile_no))

		engine.reopen_optout(followup.name, "customer called and asked us to continue")

		self.assertFalse(suppression.is_suppressed("WhatsApp", lead.mobile_no))

	def test_a_ledger_failure_never_breaks_the_optout(self):
		"""The mirror is best-effort. The engine's own opt-out is not."""
		lead = self.make_lead()
		followup = self.make_followup(lead)

		with patch.object(engine, "suppress_address", side_effect=RuntimeError("ledger down")):
			engine.handle_incoming(self.incoming(lead, "STOP"))

		self.assertEqual(
			frappe.db.get_value(engine.FOLLOWUP_DOCTYPE, followup.name, "state"), engine.STATE_OPTED_OUT
		)
