# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for task due-date reminders (master spec item 1, on the F5 ledger).

Endpoint authorization (master spec §3): this module adds NO whitelisted
endpoint. `crm.reminders.send_task_reminders` is reached from the scheduler
only, and `TestScheduling` asserts it is registered on exactly one schedule --
the whole point of the feature, because the event-reminder path it deliberately
does not copy is registered on two and double-fires.

Nothing here reaches a provider. `frappe.sendmail` is stubbed everywhere the
email leg is exercised, and the default state of the feature is OFF.
"""

from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm import reminders

TASK_DOCTYPE = "CRM Task"
LOG_DOCTYPE = "CRM Reminder Log"
ASSIGNEE = "crm.user1@example.com"
OTHER = "crm.user2@example.com"


class ReminderTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		# Pin the org-wide offset. The stored value on a live site is whatever the
		# agency chose, and a suite that inherited it would pass or fail by
		# configuration.
		frappe.db.set_single_value("FCRM Settings", "task_reminder_offset_minutes", 60)
		self.now = frappe.utils.now_datetime()
		# 30 minutes out, so the default 60-minute offset makes it due NOW.
		self.due = self.now + timedelta(minutes=30)

		# `journal` records every commit and rollback, in order, and keeps a real
		# commit from escaping the test's rollback. Same seam as
		# `crm/tests/test_outbound.py`.
		self.journal = []
		self.patches = [
			patch.object(reminders, "commit", side_effect=lambda: self.journal.append("commit")),
			patch.object(reminders, "rollback", side_effect=lambda: self.journal.append("rollback")),
		]
		for entry in self.patches:
			entry.start()

	def tearDown(self):
		for entry in self.patches:
			entry.stop()
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def make_task(self, **overrides):
		values = {
			"doctype": TASK_DOCTYPE,
			"title": "Call the customer back",
			"status": "Todo",
			"priority": "Medium",
			"due_date": self.due,
			"assigned_to": ASSIGNEE,
		}
		values.update(overrides)
		return frappe.get_doc(values).insert(ignore_permissions=True)

	def logs_for(self, task) -> list[dict]:
		return frappe.get_all(
			LOG_DOCTYPE,
			filters={"task": task.name},
			fields=["name", "channel", "status", "recipient", "offset_minutes"],
		)

	def notifications_for(self, task) -> list[dict]:
		"""Only the REMINDER notifications.

		Inserting a task with an assignee already produces one CRM Notification
		("assigned a new task ... to you") through `crm.api.todo.after_insert`.
		Counting that as a reminder would make every count here one too high.
		"""
		return frappe.get_all(
			"CRM Notification",
			filters={
				"notification_type_doctype": TASK_DOCTYPE,
				"notification_type_doc": task.name,
				"notification_text": ["like", "%is due%"],
			},
			fields=["name", "to_user", "notification_text"],
		)

	def sweep(self, **kwargs):
		"""Run the whole scheduler entry with the flag forced on."""
		with (
			patch.object(reminders, "is_enabled", return_value=True),
			patch.object(reminders, "email_enabled", return_value=kwargs.get("with_email", False)),
		):
			return reminders.send_task_reminders()


# --- the window ------------------------------------------------------------


class TestWindow(ReminderTestCase):
	def test_window_ends_one_offset_ahead_of_now(self):
		start, end = reminders.due_window(self.now, 60)
		self.assertEqual(end, frappe.utils.get_datetime_str(self.now + timedelta(minutes=60)))
		self.assertEqual(
			start,
			frappe.utils.get_datetime_str(
				self.now + timedelta(minutes=60) - timedelta(minutes=reminders.LOOKBACK_MINUTES)
			),
		)

	def test_a_zero_offset_window_ends_now(self):
		_, end = reminders.due_window(self.now, 0)
		self.assertEqual(end, frappe.utils.get_datetime_str(self.now))

	def test_a_task_inside_the_window_is_found(self):
		task = self.make_task()
		names = [row["name"] for row in reminders.find_due_tasks(now=self.now, offset_minutes=60)]
		self.assertIn(task.name, names)

	def test_a_task_beyond_the_offset_is_not_found(self):
		task = self.make_task(due_date=self.now + timedelta(hours=5))
		names = [row["name"] for row in reminders.find_due_tasks(now=self.now, offset_minutes=60)]
		self.assertNotIn(task.name, names)

	def test_a_task_older_than_the_lookback_is_not_found(self):
		task = self.make_task(due_date=self.now - timedelta(days=3))
		names = [row["name"] for row in reminders.find_due_tasks(now=self.now, offset_minutes=60)]
		self.assertNotIn(task.name, names)

	def test_a_done_task_is_suppressed(self):
		task = self.make_task(status="Done")
		names = [row["name"] for row in reminders.find_due_tasks(now=self.now, offset_minutes=60)]
		self.assertNotIn(task.name, names)

	def test_a_cancelled_task_is_suppressed(self):
		task = self.make_task(status="Canceled")
		names = [row["name"] for row in reminders.find_due_tasks(now=self.now, offset_minutes=60)]
		self.assertNotIn(task.name, names)

	def test_a_task_with_no_due_date_is_not_found(self):
		task = self.make_task(due_date=None)
		names = [row["name"] for row in reminders.find_due_tasks(now=self.now, offset_minutes=60)]
		self.assertNotIn(task.name, names)


# --- the settings ----------------------------------------------------------


class TestSettings(ReminderTestCase):
	def test_the_default_offset_is_one_hour(self):
		"""An unwritten column reads as None, and None means the documented default.

		A stored `0` is NOT unset -- it means "remind at the due time itself" --
		which is why `crm.patches.v1_0.set_task_reminder_offset_default` settles
		the value once instead of the reader guessing every call.
		"""
		with patch.object(frappe.db, "get_single_value", return_value=None):
			self.assertEqual(reminders.reminder_offset_minutes(), 60)

	def test_a_configured_offset_is_used(self):
		frappe.db.set_single_value("FCRM Settings", "task_reminder_offset_minutes", 15)
		self.assertEqual(reminders.reminder_offset_minutes(), 15)

	def test_zero_is_a_real_offset_not_a_missing_one(self):
		frappe.db.set_single_value("FCRM Settings", "task_reminder_offset_minutes", 0)
		self.assertEqual(reminders.reminder_offset_minutes(), 0)

	def test_a_negative_offset_falls_back_to_the_default(self):
		frappe.db.set_single_value("FCRM Settings", "task_reminder_offset_minutes", -30)
		self.assertEqual(reminders.reminder_offset_minutes(), 60)

	def test_an_absurd_offset_is_capped(self):
		frappe.db.set_single_value("FCRM Settings", "task_reminder_offset_minutes", 10**9)
		self.assertEqual(reminders.reminder_offset_minutes(), reminders.MAX_OFFSET_MINUTES)

	def test_an_unreadable_setting_falls_back_and_is_logged(self):
		with (
			patch.object(frappe.db, "get_single_value", side_effect=RuntimeError("boom")),
			patch.object(frappe, "log_error") as log_mock,
		):
			self.assertEqual(reminders.reminder_offset_minutes(), 60)
		log_mock.assert_called_once()

	def test_the_email_leg_is_off_by_default(self):
		frappe.db.set_single_value("FCRM Settings", "task_reminder_email", 0)
		self.assertFalse(reminders.email_enabled())


# --- idempotency, the whole point ------------------------------------------


class TestIdempotency(ReminderTestCase):
	def test_two_runs_produce_one_notification(self):
		task = self.make_task()

		self.assertEqual(self.sweep(), 1)
		self.assertEqual(self.sweep(), 0)

		self.assertEqual(len(self.logs_for(task)), 1)
		self.assertEqual(len(self.notifications_for(task)), 1)

	def test_a_simulated_double_fire_claims_once(self):
		"""Two schedulers calling the same task in the same instant."""
		task = self.make_task()
		row = reminders.find_due_tasks(now=self.now, offset_minutes=60)
		row = next(entry for entry in row if entry["name"] == task.name)

		first = reminders.remind_about(row, 60, with_email=False)
		second = reminders.remind_about(row, 60, with_email=False)

		self.assertEqual((first, second), (1, 0))
		self.assertEqual(len(self.logs_for(task)), 1)
		self.assertEqual(len(self.notifications_for(task)), 1)

	def test_the_claim_is_committed_before_the_notification(self):
		"""The commit is what makes a racing worker's insert collide."""
		task = self.make_task()
		row = next(
			entry
			for entry in reminders.find_due_tasks(now=self.now, offset_minutes=60)
			if entry["name"] == task.name
		)

		with patch.object(reminders, "deliver_notification") as deliver:
			deliver.side_effect = lambda *a, **k: self.journal.append("notify")
			reminders.remind_about(row, 60, with_email=False)

		self.assertIn("commit", self.journal[: self.journal.index("notify")])

	def test_moving_the_due_date_earns_a_new_reminder(self):
		task = self.make_task()
		self.assertEqual(self.sweep(), 1)

		# The assignment hook already touched the row; reload before rescheduling.
		task.reload()
		task.due_date = self.due + timedelta(minutes=10)
		task.save(ignore_permissions=True)

		self.assertEqual(self.sweep(), 1)
		self.assertEqual(len(self.logs_for(task)), 2)

	def test_a_different_offset_earns_a_new_reminder(self):
		task = self.make_task()
		row = next(
			entry
			for entry in reminders.find_due_tasks(now=self.now, offset_minutes=60)
			if entry["name"] == task.name
		)

		reminders.remind_about(row, 60, with_email=False)
		reminders.remind_about(row, 30, with_email=False)

		self.assertEqual(len(self.logs_for(task)), 2)

	def test_the_dedup_key_is_the_ledger_shape(self):
		task = self.make_task()
		self.sweep()
		log = frappe.get_doc(LOG_DOCTYPE, self.logs_for(task)[0]["name"])
		self.assertEqual(
			log.dedup_key,
			f"{task.name}:{ASSIGNEE}:60:{frappe.utils.get_datetime_str(task.due_date)}:Notification",
		)


# --- delivery --------------------------------------------------------------


class TestDelivery(ReminderTestCase):
	def test_the_notification_reaches_the_assignee(self):
		task = self.make_task()
		self.sweep()

		notifications = self.notifications_for(task)
		self.assertEqual(len(notifications), 1)
		self.assertEqual(notifications[0]["to_user"], ASSIGNEE)
		self.assertIn("Call the customer back", notifications[0]["notification_text"])

	def test_a_self_assigned_task_still_notifies(self):
		"""`notify_user` skips a notification whose owner IS the recipient.

		A task somebody made for themselves is the commonest case there is, so
		the reminder carries no owner at all -- it has no human sender.
		"""
		frappe.set_user(ASSIGNEE)
		task = frappe.get_doc(
			{
				"doctype": TASK_DOCTYPE,
				"title": "My own task",
				"status": "Todo",
				"priority": "Medium",
				"due_date": self.due,
				"assigned_to": ASSIGNEE,
			}
		).insert(ignore_permissions=True)
		frappe.set_user("Administrator")

		self.assertEqual(task.owner, ASSIGNEE)
		self.sweep()
		self.assertEqual(len(self.notifications_for(task)), 1)

	def test_an_unassigned_task_reminds_its_creator(self):
		task = self.make_task(assigned_to=None)
		self.sweep()
		notifications = self.notifications_for(task)
		self.assertEqual(len(notifications), 1)
		self.assertEqual(notifications[0]["to_user"], task.owner)

	def test_a_disabled_recipient_is_never_notified(self):
		task = self.make_task()
		with patch.object(reminders, "recipient_is_reachable", return_value=False):
			self.assertEqual(self.sweep(), 0)
		self.assertEqual(self.logs_for(task), [])
		self.assertEqual(self.notifications_for(task), [])

	def test_guest_is_not_a_person(self):
		self.assertFalse(reminders.recipient_is_reachable("Guest"))
		self.assertFalse(reminders.recipient_is_reachable(None))

	def test_a_failed_notification_marks_the_row_failed_and_does_not_retry(self):
		task = self.make_task()
		with (
			patch.object(reminders, "deliver_notification", side_effect=RuntimeError("boom")),
			patch.object(frappe, "log_error") as log_mock,
		):
			self.assertEqual(self.sweep(), 0)

		log_mock.assert_called()
		logs = self.logs_for(task)
		self.assertEqual(len(logs), 1)
		self.assertEqual(logs[0]["status"], "Failed")

		# The key is spent. A failure is not a licence to try again forever.
		self.assertEqual(self.sweep(), 0)
		self.assertEqual(len(self.logs_for(task)), 1)


# --- the email leg ---------------------------------------------------------


class TestEmailLeg(ReminderTestCase):
	def test_no_email_row_while_the_email_switch_is_off(self):
		task = self.make_task()
		with patch.object(frappe, "sendmail") as sendmail:
			self.sweep(with_email=False)
		sendmail.assert_not_called()
		self.assertEqual([row["channel"] for row in self.logs_for(task)], ["Notification"])

	def test_the_email_leg_queues_and_is_logged_separately(self):
		task = self.make_task()
		with patch.object(frappe, "sendmail") as sendmail:
			self.assertEqual(self.sweep(with_email=True), 2)

		sendmail.assert_called_once()
		self.assertEqual(sorted(row["channel"] for row in self.logs_for(task)), ["Email", "Notification"])

	def test_a_suppressed_recipient_is_never_mailed(self):
		"""No send path without a suppression check (master spec §9)."""
		task = self.make_task()
		with (
			patch.object(reminders, "is_suppressed", return_value=True) as suppressed,
			patch.object(frappe, "sendmail") as sendmail,
		):
			self.sweep(with_email=True)

		suppressed.assert_called_once()
		sendmail.assert_not_called()
		email_rows = [row for row in self.logs_for(task) if row["channel"] == "Email"]
		self.assertEqual(email_rows[0]["status"], "Suppressed")

	def test_the_suppression_check_asks_about_the_email_channel(self):
		task = self.make_task()
		with (
			patch.object(reminders, "is_suppressed", return_value=False) as suppressed,
			patch.object(frappe, "sendmail"),
		):
			self.sweep(with_email=True)

		channel, address = suppressed.call_args[0]
		self.assertEqual(channel, "Email")
		self.assertEqual(address, ASSIGNEE)
		self.assertTrue(task.name)


# --- the flag and the blast radius -----------------------------------------


class TestFlag(ReminderTestCase):
	def test_the_sweep_reads_nothing_while_the_flag_is_off(self):
		self.make_task()
		with (
			patch.object(reminders, "is_enabled", return_value=False) as flag,
			patch.object(reminders, "find_due_tasks") as find,
		):
			self.assertEqual(reminders.send_task_reminders(), 0)

		flag.assert_called_once_with(reminders.FLAG_TASK_REMINDERS)
		find.assert_not_called()

	def test_the_flag_is_registered(self):
		from crm.feature_flags import FLAGS

		self.assertIn(reminders.FLAG_TASK_REMINDERS, FLAGS)

	def test_the_offset_default_patch_is_registered(self):
		"""The Single stores 0 for a new Int column; the patch makes it 60 once."""
		with open(frappe.get_app_path("crm", "patches.txt")) as handle:
			registered = handle.read()
		self.assertIn("crm.patches.v1_0.set_task_reminder_offset_default", registered)

	def test_the_flag_has_its_settings_field(self):
		"""The registry is the contract; the field is the UI. Both or neither."""
		meta = frappe.get_meta("FCRM Settings")
		self.assertTrue(meta.get_field(reminders.FLAG_TASK_REMINDERS))
		self.assertTrue(meta.get_field("task_reminder_offset_minutes"))
		self.assertTrue(meta.get_field("task_reminder_email"))

	def test_the_sweep_never_raises(self):
		with (
			patch.object(reminders, "is_enabled", side_effect=RuntimeError("boom")),
			patch.object(frappe, "log_error") as log_mock,
		):
			self.assertEqual(reminders.send_task_reminders(), 0)
		log_mock.assert_called_once()

	def test_one_broken_task_does_not_stop_the_others(self):
		good = self.make_task(title="Good one")
		bad = self.make_task(title="Bad one")

		original = reminders.remind_about

		def explode(task, offset, with_email):
			if task["name"] == bad.name:
				raise RuntimeError("boom")
			return original(task, offset, with_email)

		with (
			patch.object(reminders, "remind_about", side_effect=explode),
			patch.object(frappe, "log_error"),
		):
			self.assertEqual(self.sweep(), 1)

		self.assertEqual(len(self.logs_for(good)), 1)


# --- scheduling ------------------------------------------------------------


class TestScheduling(FrappeTestCase):
	def test_registered_on_exactly_one_schedule(self):
		"""The event-reminder path is on two, and that is why it double-fires."""
		from crm import hooks

		dotted = "crm.reminders.send_task_reminders"
		events = hooks.scheduler_events

		appearances = 0
		for key, value in events.items():
			if key == "cron":
				for entries in value.values():
					appearances += entries.count(dotted)
			else:
				appearances += value.count(dotted)

		self.assertEqual(appearances, 1)

	def test_registered_on_the_fifteen_minute_cron(self):
		from crm import hooks

		self.assertIn(
			"crm.reminders.send_task_reminders",
			hooks.scheduler_events["cron"]["*/15 * * * *"],
		)

	def test_the_dotted_path_resolves(self):
		self.assertTrue(callable(frappe.get_attr("crm.reminders.send_task_reminders")))
