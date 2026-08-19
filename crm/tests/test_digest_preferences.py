# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""The daily digest's quiet hours and per-user switch, and the store behind it.

Stage 3B recorded two gaps against master spec §5 item 22: the digest ignored
quiet hours, and there was no per-user opt-out because the app had no per-user
preference store at all. Stage 4 closes both. This module holds the tests for
each half and for the store itself.

Time is always passed in explicitly. `test_quiet_hours_defer_instead_of_cancel`
in `crm/tests/test_followup_engine.py` was flaky for exactly the reason a
wall-clock-dependent quiet-hours test is: it passed at 22:53 and failed at
23:00. Nothing here reads the clock.
"""

from datetime import datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.api import whatsapp_followups as digest
from crm.fcrm.doctype.crm_user_preference import crm_user_preference as prefs

PREFERENCE_DOCTYPE = "CRM User Preference"
DIGEST_USER = "digest-manager@example.com"
OTHER_USER = "digest-other@example.com"


def quiet_settings(start="21:00:00", end="09:00:00"):
	return frappe._dict({"quiet_hours_start": start, "quiet_hours_end": end})


class PreferenceTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		for email, first_name in ((DIGEST_USER, "Digest"), (OTHER_USER, "Other")):
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": first_name,
						"send_welcome_email": 0,
						"roles": [{"role": "Sales User"}],
					}
				).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()


# --- the store -------------------------------------------------------------


class TestUserPreferenceStore(PreferenceTestCase):
	def test_a_user_with_no_row_gets_the_registered_default(self):
		# Absence means default, which is why adding a preference needs no
		# backfill patch and removing one leaves nothing to clean up.
		self.assertTrue(prefs.is_on(DIGEST_USER, "daily_digest"))

	def test_switching_it_off_is_read_back(self):
		prefs.set_preference(DIGEST_USER, "daily_digest", 0)
		self.assertFalse(prefs.is_on(DIGEST_USER, "daily_digest"))

	def test_switching_it_on_again_reuses_the_same_row(self):
		prefs.set_preference(DIGEST_USER, "daily_digest", 0)
		prefs.set_preference(DIGEST_USER, "daily_digest", 1)

		self.assertTrue(prefs.is_on(DIGEST_USER, "daily_digest"))
		self.assertEqual(frappe.db.count(PREFERENCE_DOCTYPE, {"user": DIGEST_USER}), 1)

	def test_one_users_choice_does_not_move_anothers(self):
		prefs.set_preference(DIGEST_USER, "daily_digest", 0)
		self.assertTrue(prefs.is_on(OTHER_USER, "daily_digest"))

	def test_an_unregistered_key_is_refused(self):
		# The registry is what keeps this from becoming a place to park arbitrary
		# client data under a user's name.
		with self.assertRaises(frappe.ValidationError):
			prefs.set_preference(DIGEST_USER, "not_a_preference", 1)

	def test_an_unregistered_key_cannot_be_inserted_directly_either(self):
		doc = frappe.new_doc(PREFERENCE_DOCTYPE)
		doc.user = DIGEST_USER
		doc.preference_key = "not_a_preference"
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_the_same_user_and_key_cannot_be_stored_twice(self):
		prefs.set_preference(DIGEST_USER, "daily_digest", 1)

		duplicate = frappe.new_doc(PREFERENCE_DOCTYPE)
		duplicate.user = DIGEST_USER
		duplicate.preference_key = "daily_digest"
		duplicate.preference_value = "0"
		with self.assertRaises(frappe.UniqueValidationError):
			duplicate.insert(ignore_permissions=True)

	def test_the_endpoint_reads_and_writes_only_the_session_users_row(self):
		frappe.set_user(DIGEST_USER)
		prefs.set_my_preference("daily_digest", 0)

		answer = prefs.get_my_preferences()
		self.assertFalse(answer["values"]["daily_digest"])
		self.assertIn("daily_digest", answer["registry"])

		frappe.set_user("Administrator")
		self.assertEqual(
			frappe.db.get_value(PREFERENCE_DOCTYPE, {"user": DIGEST_USER}, "preference_value"), "0"
		)

	def test_the_endpoint_refuses_an_unregistered_key(self):
		frappe.set_user(DIGEST_USER)
		with self.assertRaises(frappe.ValidationError):
			prefs.set_my_preference("something_else", 1)

	def test_a_sales_user_can_only_see_their_own_rows(self):
		condition = prefs.get_permission_query_conditions(DIGEST_USER)
		self.assertIn(DIGEST_USER, condition)
		self.assertIn("`tabCRM User Preference`.`user`", condition)

	def test_a_system_manager_sees_every_row(self):
		self.assertEqual(prefs.get_permission_query_conditions("Administrator"), "")

	def test_the_row_level_check_refuses_another_users_row(self):
		prefs.set_preference(OTHER_USER, "daily_digest", 0)
		doc = frappe.get_doc(PREFERENCE_DOCTYPE, {"user": OTHER_USER, "preference_key": "daily_digest"})

		self.assertFalse(prefs.has_permission(doc, "read", DIGEST_USER))
		self.assertTrue(prefs.has_permission(doc, "read", OTHER_USER))
		self.assertTrue(prefs.has_permission(doc, "read", "Administrator"))


# --- quiet hours -----------------------------------------------------------


class TestDigestQuietHours(PreferenceTestCase):
	def check(self, moment, settings=None):
		with patch("crm.api.followup_engine.get_settings", return_value=settings or quiet_settings()):
			return digest.in_digest_quiet_hours(moment)

	def test_the_middle_of_the_night_is_quiet(self):
		# This is the hour the old `daily` schedule fired in, which is what made
		# the digest arrive inside quiet hours in the first place.
		self.assertTrue(self.check(datetime(2026, 8, 19, 0, 30)))

	def test_late_evening_is_quiet(self):
		self.assertTrue(self.check(datetime(2026, 8, 19, 22, 0)))

	def test_the_first_hour_after_the_window_closes_is_not_quiet(self):
		self.assertFalse(self.check(datetime(2026, 8, 19, 9, 0)))

	def test_the_middle_of_the_working_day_is_not_quiet(self):
		self.assertFalse(self.check(datetime(2026, 8, 19, 14, 0)))

	def test_an_unset_window_is_never_quiet(self):
		self.assertFalse(self.check(datetime(2026, 8, 19, 3, 0), quiet_settings(start=None, end=None)))

	def test_an_unreadable_settings_row_fails_open(self):
		# A digest costs nothing to deliver -- no message, no budget, no
		# customer. Never arriving again is the worse silent failure.
		with patch("crm.api.followup_engine.get_settings", side_effect=Exception("gone")):
			self.assertFalse(digest.in_digest_quiet_hours(datetime(2026, 8, 19, 3, 0)))

	def test_the_digest_sends_nothing_inside_quiet_hours(self):
		with (
			patch("crm.api.whatsapp_followups.in_digest_quiet_hours", return_value=True),
			patch("crm.api.whatsapp_followups.get_digest_recipients") as recipients,
		):
			self.assertEqual(digest.send_daily_digest(datetime(2026, 8, 19, 3, 0)), 0)

		# Not even the recipient list is read: the job returns before it works.
		recipients.assert_not_called()


# --- who gets one, and how often -------------------------------------------


class DigestDeliveryTestCase(PreferenceTestCase):
	"""A digest with something in it, delivered to one named manager.

	`self.now` is derived from the real clock rather than pinned to a literal
	date, because "has this user had today's digest?" is answered against the
	notification row's own `creation`, which the database writes. A fixed
	literal would pass on the day it was written and fail the next morning --
	which is precisely how `test_quiet_hours_defer_instead_of_cancel` became
	flaky in Stage 1A.
	"""

	def setUp(self):
		super().setUp()
		self.summary = {
			"new_leads": 2,
			"needs_reply": 1,
			"overdue": 1,
			"reference_doctype": None,
			"reference_name": None,
			"flagged_deals": [],
		}
		self.today = frappe.utils.now_datetime()
		self.yesterday = frappe.utils.add_to_date(self.today, days=-1)
		self.now = self.today

	def run_digest(self, recipients=(DIGEST_USER,)):
		# Both summary builders are stubbed, because which one runs depends on
		# whether `frappe_whatsapp` is installed on the site under test and this
		# module is about delivery, not about counting.
		with (
			patch("crm.api.whatsapp_followups.in_digest_quiet_hours", return_value=False),
			patch("crm.api.whatsapp_followups.get_digest_recipients", return_value=list(recipients)),
			patch("crm.api.whatsapp_followups.build_digest_summary", return_value=dict(self.summary)),
			patch("crm.api.whatsapp_followups.empty_digest_summary", return_value=dict(self.summary)),
			patch("crm.api.whatsapp_followups.get_flagged_deals", return_value=[]),
		):
			return digest.send_daily_digest(self.now)

	def notifications_for(self, user):
		return frappe.get_all(
			"CRM Notification",
			filters={"to_user": user, "type": "WhatsApp"},
			fields=["name", "message"],
		)


class TestDigestDelivery(DigestDeliveryTestCase):
	def test_a_manager_who_has_not_opted_out_gets_one(self):
		self.assertEqual(self.run_digest(), 1)
		self.assertEqual(len(self.notifications_for(DIGEST_USER)), 1)

	def test_a_manager_who_switched_it_off_gets_none(self):
		prefs.set_preference(DIGEST_USER, "daily_digest", 0)

		self.assertEqual(self.run_digest(), 0)
		self.assertEqual(self.notifications_for(DIGEST_USER), [])

	def test_one_manager_opting_out_does_not_silence_another(self):
		prefs.set_preference(DIGEST_USER, "daily_digest", 0)

		self.assertEqual(self.run_digest(recipients=(DIGEST_USER, OTHER_USER)), 1)
		self.assertEqual(len(self.notifications_for(OTHER_USER)), 1)

	def test_twenty_four_hourly_ticks_produce_one_digest(self):
		# The job is on the hourly schedule so it can shift out of quiet hours.
		# Without this, that would mean twenty-four digests a day.
		self.assertEqual(self.run_digest(), 1)
		for _tick in range(5):
			self.assertEqual(self.run_digest(), 0)

		self.assertEqual(len(self.notifications_for(DIGEST_USER)), 1)

	def test_the_next_day_produces_another_one(self):
		self.now = self.yesterday
		self.assertEqual(self.run_digest(), 1)

		# Age yesterday's digest the way the clock would. `creation` is written
		# by the database, so this is the only way to cross a day boundary
		# inside one test.
		for row in self.notifications_for(DIGEST_USER):
			frappe.db.set_value(
				"CRM Notification", row["name"], "creation", self.yesterday, update_modified=False
			)

		self.now = self.today
		self.assertEqual(self.run_digest(), 1)
		self.assertEqual(len(self.notifications_for(DIGEST_USER)), 2)

	def test_a_pending_followup_nudge_does_not_count_as_todays_digest(self):
		# Both are WhatsApp notifications on the same user. Matching on the
		# digest's own leading words is what keeps them apart -- the same problem
		# `has_unread_followup` solves in the other direction.
		lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Ravi",
				"status": frappe.db.get_value("CRM Lead Status", {"type": ["!=", "Lost"]}, "name"),
			}
		).insert(ignore_permissions=True)

		digest.create_followup_notification(
			{
				"reference_doctype": "CRM Lead",
				"reference_name": lead.name,
				"display_name": "Ravi",
				"waiting_since": self.now,
			},
			DIGEST_USER,
		)

		self.assertEqual(self.run_digest(), 1)

	def test_nothing_is_sent_when_there_is_nothing_to_report(self):
		self.summary.update({"new_leads": 0, "needs_reply": 0, "overdue": 0})
		self.assertEqual(self.run_digest(), 0)


class TestDigestDueCheck(DigestDeliveryTestCase):
	def test_due_is_false_for_an_opted_out_user(self):
		prefs.set_preference(DIGEST_USER, "daily_digest", 0)
		self.assertFalse(digest.digest_is_due(DIGEST_USER, self.now))

	def test_due_is_true_for_a_user_with_no_digest_today(self):
		self.assertTrue(digest.digest_is_due(DIGEST_USER, self.now))

	def test_the_prefix_matches_the_message_that_is_actually_written(self):
		self.run_digest()
		message = self.notifications_for(DIGEST_USER)[0]["message"]
		self.assertTrue(message.startswith(digest.digest_message_prefix()))
