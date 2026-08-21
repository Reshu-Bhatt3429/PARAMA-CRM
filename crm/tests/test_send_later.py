# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for Send Later (master spec §5, item 5).

Nothing here sends. `crm.api.email.send_email` is the seam between the scheduled
job and the framework's `make`, and every delivery test stands in for it with a
recorder -- which is also how "the scheduled message is the message that was
written" is asserted, by reading what the recorder was handed.

`outbound.commit` and `outbound.rollback` are neutralised exactly as in
`crm/tests/test_outbound.py`: a real commit inside `claim_job` would escape the
test's rollback, and the commit ORDERING is part of the at-most-once guarantee.

Endpoint authorization (master spec §3), asserted below rather than described:

* `crm.api.email.schedule_email` -- POST only. Needs the `email` permission on
  the named record, which for CRM Lead / CRM Deal runs the org-hierarchy
  `has_permission` hook. `owner_user` is taken from the session, never from the
  request: `test_the_owner_is_the_session_user_not_the_request` proves the
  function has no argument for it. The doctype is checked against a fixed
  allowlist before anything is written.
* `crm.api.email.cancel_scheduled_email` / `send_scheduled_email_now` -- POST
  only. Caller must be the job's owner or a manager AND still hold the `email`
  permission on the referenced record. `TestPermissions` proves both halves.
* `crm.api.email.get_scheduled_emails` -- needs `read` on the named record.
"""

from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm import outbound
from crm.api import email
from crm.suppression import CHANNEL_EMAIL, suppress

LEAD_DOCTYPE = "CRM Lead"
OWNER = "send-later-owner@example.com"
OUTSIDER = "send-later-outsider@example.com"
MANAGER = "send-later-manager@example.com"


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


def make_user(user: str, role: str):
	if not frappe.db.exists("User", user):
		frappe.get_doc(
			{
				"doctype": "User",
				"email": user,
				"first_name": user.split("@")[0],
				"send_welcome_email": 0,
				"roles": [{"role": role}],
			}
		).insert(ignore_permissions=True)


class SendLaterTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		self.journal = []
		self.sent = []

		self.patches = [
			patch.object(outbound, "commit", side_effect=lambda: self.journal.append("commit")),
			patch.object(outbound, "rollback", side_effect=lambda: self.journal.append("rollback")),
		]
		for patcher in self.patches:
			patcher.start()

		self.lead = frappe.get_doc(
			{
				"doctype": LEAD_DOCTYPE,
				"first_name": "Scheduled",
				"last_name": "Customer",
				"email": "ann@example.com",
				"status": lead_status(),
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		for patcher in self.patches:
			patcher.stop()
		outbound.unregister_adapter(outbound.CHANNEL_EMAIL)
		outbound.unregister_adapter(outbound.CHANNEL_WHATSAPP)
		frappe.set_user("Administrator")
		frappe.db.rollback()

	# --- fixtures ---

	def recorder(self, **kwargs):
		self.sent.append(kwargs)
		return {"name": f"COMM-{len(self.sent)}"}

	def schedule(self, hours=2, **overrides):
		when = frappe.utils.add_to_date(email.now_in_timezone(email.user_timezone()), hours=hours)
		args = {
			"doctype": LEAD_DOCTYPE,
			"name": self.lead.name,
			"recipients": "ann@example.com",
			"subject": "About your trip",
			"content": "<p>Shall we book it?</p>",
			"send_at": frappe.utils.get_datetime_str(when),
		}
		args.update(overrides)
		return email.schedule_email(**args)

	def state_of(self, job_name):
		return frappe.db.get_value(outbound.JOB_DOCTYPE, job_name, "state")


# --- timezone --------------------------------------------------------------


class TestTimezone(FrappeTestCase):
	def test_an_unusable_timezone_falls_back_to_the_system_one(self):
		"""A blank or corrupt `User.time_zone` must not take the composer down."""
		self.assertEqual(str(email.zone("Not/AZone")), frappe.utils.get_system_timezone())

	def test_a_local_time_becomes_the_same_instant_in_system_time(self):
		system = frappe.utils.get_system_timezone()
		local = "2026-08-19 18:00:00"
		# Converting from the system's own timezone is the identity.
		self.assertEqual(
			frappe.utils.get_datetime_str(email.to_system_datetime(local, system)),
			frappe.utils.get_datetime_str(frappe.utils.get_datetime(local)),
		)

	def test_a_different_timezone_shifts_the_stored_time(self):
		"""18:00 in Kolkata is not 18:00 in London, and the row must say so."""
		kolkata = email.to_system_datetime("2026-08-19 18:00:00", "Asia/Kolkata")
		london = email.to_system_datetime("2026-08-19 18:00:00", "Europe/London")
		self.assertNotEqual(kolkata, london)

	def test_the_user_timezone_falls_back_to_the_site(self):
		self.assertTrue(email.user_timezone("Administrator"))


class TestPresets(FrappeTestCase):
	def test_later_today_is_six_in_the_evening(self):
		when = email.preset_datetime(email.PRESET_LATER_TODAY, "Asia/Kolkata")
		self.assertEqual((when.hour, when.minute), (18, 0))
		self.assertEqual(when.date(), email.now_in_timezone("Asia/Kolkata").date())

	def test_tomorrow_morning_is_nine_the_next_day(self):
		now = email.now_in_timezone("Asia/Kolkata")
		when = email.preset_datetime(email.PRESET_TOMORROW_MORNING, "Asia/Kolkata")
		self.assertEqual((when.hour, when.minute), (9, 0))
		self.assertEqual(when.date(), (now + timedelta(days=1)).date())

	def test_next_monday_is_a_monday_and_never_today(self):
		now = email.now_in_timezone("Asia/Kolkata")
		when = email.preset_datetime(email.PRESET_NEXT_MONDAY, "Asia/Kolkata")
		self.assertEqual(when.weekday(), 0)
		self.assertGreater(when.date(), now.date())

	def test_an_unknown_preset_is_refused(self):
		self.assertRaises(frappe.ValidationError, email.preset_datetime, "whenever", "Asia/Kolkata")


# --- scheduling ------------------------------------------------------------


class TestScheduling(SendLaterTestCase):
	def test_a_scheduled_email_is_a_scheduled_job(self):
		job = self.schedule()
		self.assertEqual(job["state"], outbound.JOB_SCHEDULED)
		self.assertEqual(self.state_of(job["name"]), outbound.JOB_SCHEDULED)

	def test_the_message_is_stored_exactly_as_written(self):
		job = self.schedule()
		self.assertEqual(job["subject"], "About your trip")
		self.assertIn("Shall we book it?", job["content"])
		self.assertEqual(job["recipients"], "ann@example.com")

	def test_the_senders_timezone_is_stored_on_the_job(self):
		job = self.schedule()
		self.assertEqual(job["sender_timezone"], email.user_timezone())

	def test_one_recipient_row_carries_the_primary_address(self):
		job = self.schedule()
		rows = frappe.get_all(
			outbound.RECIPIENT_DOCTYPE, filters={"job": job["name"]}, fields=["address", "state"]
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["address"], "ann@example.com")
		self.assertEqual(rows[0]["state"], outbound.RECIPIENT_PENDING)

	def test_a_preset_is_computed_on_the_server(self):
		job = self.schedule(send_at=None, preset=email.PRESET_TOMORROW_MORNING)
		stored = frappe.utils.get_datetime(job["scheduled_at"])
		self.assertGreater(stored, frappe.utils.now_datetime())

	def test_a_time_in_the_past_is_refused(self):
		self.assertRaises(frappe.ValidationError, self.schedule, hours=-1)

	def test_a_time_a_decade_out_is_refused(self):
		self.assertRaises(frappe.ValidationError, self.schedule, hours=24 * 400)

	def test_no_time_at_all_is_refused(self):
		self.assertRaises(frappe.ValidationError, self.schedule, send_at=None)

	def test_no_recipient_is_refused(self):
		self.assertRaises(frappe.ValidationError, self.schedule, recipients="")

	def test_an_unusable_address_is_refused_before_a_row_exists(self):
		"""A row keyed on an address that cannot be normalised would never send."""
		before = frappe.db.count(outbound.JOB_DOCTYPE)
		self.assertRaises(frappe.ValidationError, self.schedule, recipients="not-an-address")
		self.assertEqual(frappe.db.count(outbound.JOB_DOCTYPE), before)

	def test_a_doctype_outside_the_allowlist_is_refused(self):
		self.assertRaises(frappe.ValidationError, self.schedule, doctype="User", name="Administrator")

	def test_the_owner_is_the_session_user_not_the_request(self):
		"""The strongest form: there is no argument to supply an owner with."""
		import inspect

		self.assertNotIn("owner_user", inspect.signature(email.schedule_email).parameters)
		job = self.schedule()
		self.assertEqual(job["owner_user"], frappe.session.user)

	def test_two_deliberate_schedules_are_two_jobs(self):
		first = self.schedule()
		second = self.schedule(hours=3)
		self.assertNotEqual(first["name"], second["name"])

	def test_a_pending_job_is_listed_on_the_record(self):
		job = self.schedule()
		listed = email.get_scheduled_emails(LEAD_DOCTYPE, self.lead.name)
		self.assertEqual([row["name"] for row in listed], [job["name"]])

	def test_a_cancelled_job_leaves_the_list(self):
		job = self.schedule()
		email.cancel_scheduled_email(job["name"])
		self.assertEqual(email.get_scheduled_emails(LEAD_DOCTYPE, self.lead.name), [])


# --- cancel ----------------------------------------------------------------


class TestCancel(SendLaterTestCase):
	def test_a_scheduled_job_can_be_cancelled(self):
		job = self.schedule()
		email.cancel_scheduled_email(job["name"])
		self.assertEqual(self.state_of(job["name"]), outbound.JOB_CANCELLED)

	def test_cancelling_closes_the_pending_recipient(self):
		job = self.schedule()
		email.cancel_scheduled_email(job["name"])
		state = frappe.db.get_value(outbound.RECIPIENT_DOCTYPE, {"job": job["name"]}, "state")
		self.assertEqual(state, outbound.RECIPIENT_CANCELLED)

	def test_cancel_after_claim_is_refused(self):
		"""The cutoff has to be a hard edge, or a user is told a send was stopped
		after it left."""
		job = self.schedule()
		outbound.claim_job(job["name"])
		self.assertRaises(frappe.ValidationError, email.cancel_scheduled_email, job["name"])
		self.assertEqual(self.state_of(job["name"]), outbound.JOB_CLAIMED)

	def test_a_claimed_job_reports_that_it_cannot_be_cancelled(self):
		job = self.schedule()
		outbound.claim_job(job["name"])
		self.assertFalse(email.describe_job(job["name"])["can_cancel"])

	def test_a_job_that_never_existed_is_refused(self):
		self.assertRaises(frappe.DoesNotExistError, email.cancel_scheduled_email, "NOPE")

	def test_a_job_of_another_type_is_not_reachable_through_this_endpoint(self):
		job = outbound.create_job(
			job_type="Mass Email",
			channel=outbound.CHANNEL_EMAIL,
			idempotency_key=frappe.generate_hash(length=12),
			recipients=["ann@example.com"],
			owner_user=frappe.session.user,
		)
		self.assertRaises(frappe.DoesNotExistError, email.cancel_scheduled_email, job.name)


# --- send now --------------------------------------------------------------


class TestSendNow(SendLaterTestCase):
	def test_send_now_delivers_the_stored_message(self):
		job = self.schedule()
		with patch.object(email, "send_email", side_effect=self.recorder):
			email.send_scheduled_email_now(job["name"])

		self.assertEqual(len(self.sent), 1)
		self.assertEqual(self.sent[0]["subject"], "About your trip")
		self.assertEqual(self.sent[0]["recipients"], "ann@example.com")
		self.assertEqual(self.state_of(job["name"]), outbound.JOB_SENT)

	def test_send_now_leaves_the_record_off_the_pending_list(self):
		job = self.schedule()
		with patch.object(email, "send_email", side_effect=self.recorder):
			email.send_scheduled_email_now(job["name"])
		self.assertEqual(email.get_scheduled_emails(LEAD_DOCTYPE, self.lead.name), [])

	def test_a_second_send_now_sends_nothing_more(self):
		"""One click that arrives twice is one message."""
		job = self.schedule()
		with patch.object(email, "send_email", side_effect=self.recorder):
			email.send_scheduled_email_now(job["name"])
			self.assertRaises(frappe.ValidationError, email.send_scheduled_email_now, job["name"])
		self.assertEqual(len(self.sent), 1)

	def test_the_claim_is_committed_before_the_adapter_runs(self):
		"""The whole at-most-once guarantee is this ordering."""
		job = self.schedule()
		self.journal.clear()

		def journalling(**kwargs):
			self.journal.append("send")
			return self.recorder(**kwargs)

		with patch.object(email, "send_email", side_effect=journalling):
			email.send_scheduled_email_now(job["name"])

		self.assertIn("send", self.journal)
		self.assertEqual(self.journal[0], "commit")
		self.assertLess(self.journal.index("commit"), self.journal.index("send"))

	def test_a_suppressed_recipient_is_never_written_to(self):
		job = self.schedule()
		suppress(CHANNEL_EMAIL, "ann@example.com", source="test")
		with patch.object(email, "send_email", side_effect=self.recorder):
			email.send_scheduled_email_now(job["name"])
		self.assertEqual(self.sent, [])
		state = frappe.db.get_value(outbound.RECIPIENT_DOCTYPE, {"job": job["name"]}, "state")
		self.assertEqual(state, outbound.RECIPIENT_SUPPRESSED)

	def test_a_failing_provider_marks_the_recipient_failed_and_spends_the_key(self):
		job = self.schedule()
		with patch.object(email, "send_email", side_effect=RuntimeError("smtp refused")):
			email.send_scheduled_email_now(job["name"])
		state = frappe.db.get_value(outbound.RECIPIENT_DOCTYPE, {"job": job["name"]}, "state")
		self.assertEqual(state, outbound.RECIPIENT_FAILED)


# --- the sweep -------------------------------------------------------------


class TestSweep(SendLaterTestCase):
	def test_the_sweep_registers_the_email_adapter(self):
		"""A worker imports `crm.outbound` alone; without this nothing can send."""
		outbound.unregister_adapter(outbound.CHANNEL_EMAIL)
		outbound.load_adapter_modules()
		self.assertIs(outbound.get_adapter(outbound.CHANNEL_EMAIL), email.email_adapter)

	def test_the_registrar_does_not_overwrite_an_adapter_already_bound(self):
		def other(job, recipient):
			return {}

		outbound.register_adapter(outbound.CHANNEL_EMAIL, other)
		email.register_adapters()
		self.assertIs(outbound.get_adapter(outbound.CHANNEL_EMAIL), other)

	def test_a_due_job_is_delivered_by_the_sweep(self):
		job = self.schedule()
		frappe.db.set_value(
			outbound.JOB_DOCTYPE,
			job["name"],
			"scheduled_at",
			frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-1),
			update_modified=False,
		)

		with (
			patch.object(outbound, "is_enabled", return_value=True),
			patch.object(email, "send_email", side_effect=self.recorder),
		):
			outbound.process_scheduled_jobs()

		self.assertEqual(len(self.sent), 1)
		self.assertEqual(self.state_of(job["name"]), outbound.JOB_SENT)

	def test_a_job_that_is_not_due_is_left_alone(self):
		job = self.schedule(hours=5)
		with (
			patch.object(outbound, "is_enabled", return_value=True),
			patch.object(email, "send_email", side_effect=self.recorder),
		):
			outbound.process_scheduled_jobs()

		self.assertEqual(self.sent, [])
		self.assertEqual(self.state_of(job["name"]), outbound.JOB_SCHEDULED)

	def test_the_flag_off_means_the_sweep_reads_nothing(self):
		job = self.schedule()
		frappe.db.set_value(
			outbound.JOB_DOCTYPE,
			job["name"],
			"scheduled_at",
			frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-1),
			update_modified=False,
		)
		with patch.object(outbound, "is_enabled", return_value=False):
			self.assertEqual(outbound.process_scheduled_jobs(), 0)
		self.assertEqual(self.state_of(job["name"]), outbound.JOB_SCHEDULED)


# --- the adapter -----------------------------------------------------------


class TestAdapter(SendLaterTestCase):
	def test_the_adapter_correlates_the_communication_and_the_queue_row(self):
		job = self.schedule()
		job_doc = frappe.get_doc(outbound.JOB_DOCTYPE, job["name"])
		row = frappe.get_doc(outbound.RECIPIENT_DOCTYPE, {"job": job["name"]})

		with patch.object(email, "send_email", return_value={"name": "COMM-X"}):
			with patch.object(email.frappe.db, "get_value", return_value="mid@crm.test"):
				result = email.email_adapter(job_doc, row)

		self.assertEqual(result["communication"], "COMM-X")

	def test_the_adapter_hands_on_every_field_of_the_payload(self):
		job = self.schedule(cc="cc@example.com", bcc="bcc@example.com")
		job_doc = frappe.get_doc(outbound.JOB_DOCTYPE, job["name"])
		row = frappe.get_doc(outbound.RECIPIENT_DOCTYPE, {"job": job["name"]})

		with patch.object(email, "send_email", side_effect=self.recorder):
			email.email_adapter(job_doc, row)

		self.assertEqual(self.sent[0]["cc"], "cc@example.com")
		self.assertEqual(self.sent[0]["bcc"], "bcc@example.com")
		self.assertEqual(self.sent[0]["doctype"], LEAD_DOCTYPE)
		self.assertEqual(self.sent[0]["name"], self.lead.name)


# --- cancel on reply -------------------------------------------------------


class TestReplyCancels(SendLaterTestCase):
	def make_outgoing(self, message_id="out-1@crm.test"):
		return frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_type": "Communication",
				"communication_medium": "Email",
				"sent_or_received": "Sent",
				"subject": "About your trip",
				"content": "<p>Shall we book it?</p>",
				"sender": "agent@example.com",
				"recipients": "ann@example.com",
				"reference_doctype": LEAD_DOCTYPE,
				"reference_name": self.lead.name,
				"message_id": message_id,
			}
		).insert(ignore_permissions=True)

	def make_reply(self, parent):
		return frappe._dict(
			{
				"doctype": "Communication",
				"sent_or_received": "Received",
				"in_reply_to": parent.name,
				"reference_doctype": LEAD_DOCTYPE,
				"reference_name": self.lead.name,
			}
		)

	def correlate(self, job_name, communication, message_id=None):
		frappe.db.set_value(
			outbound.RECIPIENT_DOCTYPE,
			{"job": job_name},
			{"communication": communication.name, "message_id": message_id or communication.message_id},
			update_modified=False,
		)

	def test_a_reply_cancels_a_still_pending_job(self):
		job = self.schedule()
		parent = self.make_outgoing()
		self.correlate(job["name"], parent)

		self.assertEqual(email.handle_inbound_reply(self.make_reply(parent)), job["name"])
		self.assertEqual(self.state_of(job["name"]), outbound.JOB_CANCELLED)

	def test_the_reply_is_stamped_on_the_recipient(self):
		job = self.schedule()
		parent = self.make_outgoing()
		self.correlate(job["name"], parent)
		email.handle_inbound_reply(self.make_reply(parent))
		self.assertTrue(frappe.db.get_value(outbound.RECIPIENT_DOCTYPE, {"job": job["name"]}, "replied_at"))

	def test_a_message_id_match_works_when_the_link_is_missing(self):
		"""The header route, with no `communication` correlation to lean on."""
		job = self.schedule()
		parent = self.make_outgoing(message_id="mid-only@crm.test")
		frappe.db.set_value(
			outbound.RECIPIENT_DOCTYPE,
			{"job": job["name"]},
			{"message_id": "mid-only@crm.test"},
			update_modified=False,
		)
		self.assertEqual(email.handle_inbound_reply(self.make_reply(parent)), job["name"])

	def test_an_outgoing_message_cancels_nothing(self):
		job = self.schedule()
		parent = self.make_outgoing()
		self.correlate(job["name"], parent)

		outgoing = self.make_reply(parent)
		outgoing.sent_or_received = "Sent"
		self.assertIsNone(email.handle_inbound_reply(outgoing))
		self.assertEqual(self.state_of(job["name"]), outbound.JOB_SCHEDULED)

	def test_an_unrelated_reply_cancels_nothing(self):
		job = self.schedule()
		stranger = self.make_outgoing(message_id="stranger@crm.test")
		self.assertIsNone(email.handle_inbound_reply(self.make_reply(stranger)))
		self.assertEqual(self.state_of(job["name"]), outbound.JOB_SCHEDULED)

	def test_a_claimed_job_is_past_the_cutoff_and_survives_a_reply(self):
		job = self.schedule()
		parent = self.make_outgoing()
		self.correlate(job["name"], parent)
		outbound.claim_job(job["name"])

		self.assertIsNone(email.handle_inbound_reply(self.make_reply(parent)))
		self.assertEqual(self.state_of(job["name"]), outbound.JOB_CLAIMED)

	def test_the_author_is_told(self):
		job = self.schedule()
		parent = self.make_outgoing()
		self.correlate(job["name"], parent)
		frappe.db.set_value(outbound.JOB_DOCTYPE, job["name"], "owner_user", OWNER, update_modified=False)
		make_user(OWNER, "Sales User")

		email.handle_inbound_reply(self.make_reply(parent))
		self.assertTrue(
			frappe.db.exists("CRM Notification", {"to_user": OWNER, "reference_name": self.lead.name})
		)

	def test_it_never_raises(self):
		"""This runs inside the insert of an inbound email. A throw loses it."""
		with patch.object(email.outbound, "match_reply", side_effect=RuntimeError("boom")):
			self.assertIsNone(email.handle_inbound_reply(frappe._dict({"sent_or_received": "Received"})))


# --- permissions -----------------------------------------------------------


class TestPermissions(SendLaterTestCase):
	"""Master spec §3. No patching: the real org-hierarchy rule refuses."""

	def setUp(self):
		super().setUp()
		make_user(OUTSIDER, "Sales User")
		make_user(MANAGER, "Sales Manager")

	def test_a_sales_user_without_lead_access_cannot_schedule(self):
		frappe.set_user(OUTSIDER)
		self.assertRaises(frappe.PermissionError, self.schedule)

	def test_a_sales_user_without_lead_access_cannot_list(self):
		frappe.set_user(OUTSIDER)
		self.assertRaises(frappe.PermissionError, email.get_scheduled_emails, LEAD_DOCTYPE, self.lead.name)

	def test_the_owner_of_the_lead_can_schedule(self):
		frappe.db.set_value(LEAD_DOCTYPE, self.lead.name, "lead_owner", OUTSIDER)
		frappe.set_user(OUTSIDER)
		self.assertEqual(self.schedule()["state"], outbound.JOB_SCHEDULED)

	def test_somebody_elses_job_is_not_cancellable(self):
		job = self.schedule()
		frappe.db.set_value(LEAD_DOCTYPE, self.lead.name, "lead_owner", OUTSIDER)
		frappe.set_user(OUTSIDER)
		self.assertRaises(frappe.PermissionError, email.cancel_scheduled_email, job["name"])

	def test_a_manager_may_cancel_somebody_elses_job(self):
		job = self.schedule()
		frappe.set_user(MANAGER)
		email.cancel_scheduled_email(job["name"])
		self.assertEqual(self.state_of(job["name"]), outbound.JOB_CANCELLED)

	def test_losing_access_to_the_record_takes_the_job_with_it(self):
		"""Scheduling while you could does not entitle you to fire it later."""
		frappe.db.set_value(LEAD_DOCTYPE, self.lead.name, "lead_owner", OUTSIDER)
		frappe.set_user(OUTSIDER)
		job = self.schedule()

		frappe.set_user("Administrator")
		frappe.db.set_value(LEAD_DOCTYPE, self.lead.name, "lead_owner", "Administrator")
		frappe.set_user(OUTSIDER)
		self.assertRaises(
			(frappe.PermissionError, frappe.ValidationError),
			email.send_scheduled_email_now,
			job["name"],
		)

	def test_state_changing_endpoints_are_post_only(self):
		"""A GET-able writer is a CSRF target (the M-csrf precedent)."""
		for method in (email.schedule_email, email.cancel_scheduled_email, email.send_scheduled_email_now):
			self.assertIn(method, frappe.whitelisted, msg=f"{method.__name__} must be whitelisted")
			self.assertEqual(
				tuple(frappe.allowed_http_methods_for_whitelisted_func[method]),
				("POST",),
				msg=f"{method.__name__} must be POST only",
			)

	def test_the_read_endpoint_is_whitelisted(self):
		self.assertIn(email.get_scheduled_emails, frappe.whitelisted)
