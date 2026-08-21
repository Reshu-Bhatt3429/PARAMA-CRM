# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the incremental sweep pattern and the contact-key plumbing (spec F7).

Nothing here sends. A sweep reads rows and writes two derived columns with
`frappe.db.set_value(..., update_modified=False)`, so no document is loaded, no
`validate`, `on_update` or notification hook runs, and no send path is reachable.
That is asserted directly, not assumed:
`test_the_backfill_loads_no_swept_document` fails if the sweep ever starts going
through the document layer.

The resumability claim is tested the only way that means anything -- by running
the backfill twice and comparing the result to running it once.

Endpoint authorization (master spec §3): this module adds NO whitelisted
endpoint. `crm.sweeps` reads with the query builder and no permission
conditions, which is correct for a system job whose results never reach a
request. Nothing in it is callable from a client.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm import contact_keys, sweeps
from crm.patches.v1_0 import backfill_parama_contact_keys as backfill


class TestWatermark(FrappeTestCase):
	def setUp(self):
		self.job = f"test:{frappe.generate_hash(length=8)}"

	def tearDown(self):
		sweeps.reset_watermark(self.job)
		frappe.db.rollback()

	def test_a_new_job_starts_at_the_beginning(self):
		self.assertEqual(sweeps.get_watermark(self.job), ("", ""))

	def test_the_cursor_round_trips(self):
		sweeps.set_watermark(self.job, "2026-08-18 10:00:00", "CRM-LEAD-2026-00001")
		self.assertEqual(sweeps.get_watermark(self.job), ("2026-08-18 10:00:00", "CRM-LEAD-2026-00001"))

	def test_reset_returns_to_the_beginning(self):
		sweeps.set_watermark(self.job, "2026-08-18 10:00:00", "X")
		sweeps.reset_watermark(self.job)
		self.assertEqual(sweeps.get_watermark(self.job), ("", ""))


class TestSweepLock(FrappeTestCase):
	def setUp(self):
		self.job = f"test:{frappe.generate_hash(length=8)}"

	def test_a_second_holder_is_refused_immediately(self):
		"""Non-blocking: the second copy exits rather than doing the work twice."""
		with sweeps.sweep_lock(self.job) as first:
			self.assertTrue(first)
			with sweeps.sweep_lock(self.job) as second:
				self.assertFalse(second)

	def test_the_lock_is_released_afterwards(self):
		with sweeps.sweep_lock(self.job) as acquired:
			self.assertTrue(acquired)
		with sweeps.sweep_lock(self.job) as again:
			self.assertTrue(again)

	def test_a_locked_sweep_does_no_work(self):
		seen = []
		with sweeps.sweep_lock(self.job):
			stats = sweeps.run_sweep(
				self.job, "CRM Lead", lambda row: seen.append(row.name), fields=["email"]
			)

		self.assertFalse(stats["locked"])
		self.assertEqual(stats["read"], 0)
		self.assertEqual(seen, [])


class SweepDataTestCase(FrappeTestCase):
	"""Leads with known keys, and a job name unique to the test."""

	def setUp(self):
		self.job = f"test:{frappe.generate_hash(length=8)}"
		self.leads = [
			self.make_lead("Ann", "Ann.Lee@Example.COM", "919876543210"),
			self.make_lead("Bob", "BOB@example.com", "+91 98765 43211"),
			# Passes the framework's own phone-field check, fails an E.164 parse.
			self.make_lead("Cat", "cat@example.com", "12345"),
		]

	def tearDown(self):
		sweeps.reset_watermark(self.job)
		frappe.db.rollback()

	def make_lead(self, first_name, email, mobile_no):
		return frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": first_name,
				"email": email,
				"mobile_no": mobile_no,
				"lead_owner": "Administrator",
			}
		).insert(ignore_permissions=True)

	def keys_of(self, lead):
		return frappe.db.get_value(
			"CRM Lead",
			lead.name,
			[contact_keys.EMAIL_FIELD, contact_keys.PHONE_FIELD],
			as_dict=True,
		)


class TestContactKeysOnSave(SweepDataTestCase):
	def test_the_columns_are_written_on_save(self):
		keys = self.keys_of(self.leads[0])
		self.assertEqual(keys[contact_keys.EMAIL_FIELD], "ann.lee@example.com")
		self.assertEqual(keys[contact_keys.PHONE_FIELD], "+919876543210")

	def test_an_unparsable_number_leaves_the_column_empty(self):
		keys = self.keys_of(self.leads[2])
		self.assertEqual(keys[contact_keys.EMAIL_FIELD], "cat@example.com")
		self.assertIsNone(keys[contact_keys.PHONE_FIELD])

	def test_editing_the_source_field_updates_the_column(self):
		lead = self.leads[0]
		lead.email = "NEW@example.com"
		lead.save(ignore_permissions=True)

		self.assertEqual(self.keys_of(lead)[contact_keys.EMAIL_FIELD], "new@example.com")

	def test_the_hook_never_breaks_a_save(self):
		lead = self.leads[0]
		with patch.object(contact_keys, "compute_keys", side_effect=RuntimeError("boom")):
			lead.email = "still.saves@example.com"
			lead.save(ignore_permissions=True)

		self.assertEqual(frappe.db.get_value("CRM Lead", lead.name, "email"), "still.saves@example.com")

	def test_the_mobile_wins_over_the_landline(self):
		lead = self.make_lead("Dee", "dee@example.com", "+919876543212")
		lead.phone = "+919876543299"
		lead.save(ignore_permissions=True)

		self.assertEqual(self.keys_of(lead)[contact_keys.PHONE_FIELD], "+919876543212")


class TestResumableBackfill(SweepDataTestCase):
	def clear_keys(self):
		for lead in self.leads:
			frappe.db.set_value(
				"CRM Lead",
				lead.name,
				{contact_keys.EMAIL_FIELD: None, contact_keys.PHONE_FIELD: None},
				update_modified=False,
			)

	def run_backfill(self, **kwargs):
		return sweeps.run_sweep(
			job_name=self.job,
			doctype="CRM Lead",
			handler=lambda row: contact_keys.backfill_row("CRM Lead", row),
			fields=[
				*contact_keys.SOURCE_FIELDS["CRM Lead"],
				contact_keys.EMAIL_FIELD,
				contact_keys.PHONE_FIELD,
			],
			commit_between_batches=False,
			**kwargs,
		)

	def snapshot(self):
		return {lead.name: dict(self.keys_of(lead)) for lead in self.leads}

	def test_the_backfill_fills_the_columns(self):
		self.clear_keys()
		stats = self.run_backfill(batch_size=2)

		self.assertTrue(stats["finished"])
		self.assertEqual(self.keys_of(self.leads[0])[contact_keys.EMAIL_FIELD], "ann.lee@example.com")
		self.assertEqual(self.keys_of(self.leads[1])[contact_keys.PHONE_FIELD], "+919876543211")

	def test_running_it_twice_gives_the_same_result(self):
		self.clear_keys()
		self.run_backfill(batch_size=2)
		once = self.snapshot()

		sweeps.reset_watermark(self.job)
		self.run_backfill(batch_size=2)

		self.assertEqual(self.snapshot(), once)

	def test_a_second_run_changes_nothing_it_already_did(self):
		"""Idempotent handler: the rows are already correct, so nothing is written."""
		self.clear_keys()
		self.run_backfill(batch_size=2)

		sweeps.reset_watermark(self.job)
		second = self.run_backfill(batch_size=2)

		self.assertGreater(second["read"], 0)
		self.assertEqual(second["changed"], 0)

	def test_an_interrupted_run_resumes_where_it_stopped(self):
		self.clear_keys()

		first = self.run_backfill(batch_size=1, max_batches=1)
		self.assertFalse(first["finished"])
		self.assertEqual(first["read"], 1)
		cursor_after_first = sweeps.get_watermark(self.job)

		second = self.run_backfill(batch_size=1, max_batches=1)
		self.assertEqual(second["read"], 1)
		self.assertNotEqual(sweeps.get_watermark(self.job), cursor_after_first)

	def test_a_completed_sweep_re_reads_nothing(self):
		self.clear_keys()
		self.run_backfill(batch_size=50)
		again = self.run_backfill(batch_size=50)

		self.assertEqual(again["read"], 0)
		self.assertTrue(again["finished"])

	def test_the_backfill_loads_no_swept_document(self):
		"""Silent by construction: loading a CRM Lead would fire hooks and could send.

		The sweep's own bookkeeping does load a document -- `frappe.db.set_default`
		writes a DefaultValue row -- so this refuses loads of the SWEPT doctype
		specifically rather than banning `get_doc` outright.
		"""
		self.clear_keys()
		real_get_doc = frappe.get_doc

		def guarded(*args, **kwargs):
			if args and args[0] == "CRM Lead":
				raise AssertionError("the sweep must not load a CRM Lead document")
			return real_get_doc(*args, **kwargs)

		with patch.object(frappe, "get_doc", side_effect=guarded):
			self.run_backfill(batch_size=50)

		self.assertEqual(self.keys_of(self.leads[0])[contact_keys.EMAIL_FIELD], "ann.lee@example.com")

	def test_the_backfill_does_not_touch_modified(self):
		"""A sweep that moved `modified` would keep re-finding its own rows."""
		before = frappe.db.get_value("CRM Lead", self.leads[0].name, "modified")
		self.clear_keys()
		self.run_backfill(batch_size=50)

		self.assertEqual(frappe.db.get_value("CRM Lead", self.leads[0].name, "modified"), before)


class TestPaging(SweepDataTestCase):
	def test_every_row_is_visited_exactly_once_across_batches(self):
		seen = []
		sweeps.run_sweep(
			job_name=self.job,
			doctype="CRM Lead",
			handler=lambda row: seen.append(row.name),
			fields=["email"],
			batch_size=1,
			commit_between_batches=False,
		)

		self.assertEqual(len(seen), len(set(seen)))
		for lead in self.leads:
			self.assertIn(lead.name, seen)

	def test_rows_sharing_one_timestamp_are_not_skipped(self):
		"""The (modified, name) cursor exists for exactly this case."""
		stamp = "2020-01-01 00:00:00"
		for lead in self.leads:
			frappe.db.sql("update `tabCRM Lead` set modified = %s where name = %s", (stamp, lead.name))

		seen = []
		sweeps.run_sweep(
			job_name=self.job,
			doctype="CRM Lead",
			handler=lambda row: seen.append(row.name),
			fields=["email"],
			batch_size=1,
			commit_between_batches=False,
		)

		for lead in self.leads:
			self.assertIn(lead.name, seen)
		self.assertEqual(len(seen), len(set(seen)))


class TestBackfillPatch(FrappeTestCase):
	"""The patch itself: idempotent, and it must never raise on a fresh site."""

	def tearDown(self):
		for doctype in contact_keys.TARGETS:
			sweeps.reset_watermark(backfill.sweep_name(doctype))
		frappe.db.rollback()

	def test_the_patch_runs_twice_without_error(self):
		backfill.execute()
		backfill.execute()

	def test_the_patch_skips_a_doctype_that_has_no_columns_yet(self):
		"""A partially migrated site must not take the patch down."""
		meta = frappe.get_meta("CRM Lead")
		with (
			patch.object(meta, "has_field", return_value=False),
			patch.object(frappe, "get_meta", return_value=meta),
		):
			backfill.execute()


class TestFeatureFlags(FrappeTestCase):
	def test_every_registered_flag_exists_as_a_settings_field(self):
		from crm.feature_flags import FLAGS

		meta = frappe.get_meta("FCRM Settings")
		for flag in FLAGS:
			self.assertTrue(meta.has_field(flag), f"{flag} is registered but has no settings field")

	def test_every_registered_flag_is_off(self):
		from crm.feature_flags import FLAGS

		meta = frappe.get_meta("FCRM Settings")
		for flag in FLAGS:
			self.assertFalse(frappe.utils.cint(meta.get_field(flag).default), flag)

	def test_an_unknown_flag_reads_as_off(self):
		from crm.feature_flags import is_enabled

		self.assertFalse(is_enabled("no_such_flag"))


class TestReminderLedger(FrappeTestCase):
	"""The F5 ledger. No reminder feature ships in this stage, so nothing sends.

	This lives here rather than in its own file because Stage 1A ships the ledger
	and its index only: there is no reminder engine yet for a test file to be
	about. Move these when the reminder feature is built.
	"""

	def tearDown(self):
		frappe.db.rollback()

	def make_task(self, due_date="2026-09-01 09:00:00"):
		return frappe.get_doc(
			{"doctype": "CRM Task", "title": "Call Ann", "due_date": due_date, "status": "Todo"}
		).insert(ignore_permissions=True)

	def claim(self, task, recipient="Administrator", offset=60, due_date=None, channel="Notification"):
		from crm.fcrm.doctype.crm_reminder_log.crm_reminder_log import reminder_key

		return frappe.get_doc(
			{
				"doctype": "CRM Reminder Log",
				"task": task.name,
				"recipient": recipient,
				"channel": channel,
				"status": "Claimed",
				"offset_minutes": offset,
				"due_date": due_date or task.due_date,
				"dedup_key": reminder_key(task.name, recipient, offset, due_date or task.due_date, channel),
			}
		).insert(ignore_permissions=True)

	def test_the_same_reminder_cannot_be_claimed_twice(self):
		"""The guard that the event-reminder precedent lacks: it double-fires."""
		task = self.make_task()
		self.claim(task)

		self.assertRaises((frappe.UniqueValidationError, frappe.DuplicateEntryError), self.claim, task)

	def test_a_different_offset_is_a_different_reminder(self):
		task = self.make_task()
		self.claim(task, offset=60)
		self.claim(task, offset=1440)
		self.assertEqual(frappe.db.count("CRM Reminder Log", {"task": task.name}), 2)

	def test_a_different_channel_is_a_different_reminder(self):
		task = self.make_task()
		self.claim(task, channel="Notification")
		self.claim(task, channel="Email")
		self.assertEqual(frappe.db.count("CRM Reminder Log", {"task": task.name}), 2)

	def test_rescheduling_the_task_makes_a_new_reminder(self):
		"""The due date is in the key on purpose: a moved task is a new reminder."""
		task = self.make_task()
		self.claim(task, due_date="2026-09-01 09:00:00")
		self.claim(task, due_date="2026-09-08 09:00:00")
		self.assertEqual(frappe.db.count("CRM Reminder Log", {"task": task.name}), 2)

	def test_the_key_does_not_depend_on_how_the_due_date_was_spelled(self):
		"""A datetime from a document and a str from a query must agree."""
		from crm.fcrm.doctype.crm_reminder_log.crm_reminder_log import reminder_key

		as_text = reminder_key("T1", "Administrator", 60, "2026-09-01 09:00:00", "Notification")
		as_datetime = reminder_key(
			"T1",
			"Administrator",
			60,
			frappe.utils.get_datetime("2026-09-01 09:00:00"),
			"Notification",
		)
		self.assertEqual(as_text, as_datetime)

	def test_the_task_index_exists(self):
		rows = frappe.db.sql("SHOW INDEX FROM `tabCRM Task`", as_dict=True)
		self.assertIn("due_date_status_index", {row["Key_name"] for row in rows})
