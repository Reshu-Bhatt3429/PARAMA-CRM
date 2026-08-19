"""The outbound-job state machine: one queue every scheduled send rides on.

Why one engine
--------------
Send Later, mass email, sequences and scheduled reports all have the same three
problems, and every one of them is a way to send a customer the same message
twice: a job that is due while a worker is already running it, a cancel that
races the send, and a crash between "we decided to send" and "we sent". Solving
that once, here, is cheaper and safer than solving it four times.

The guarantee: at most once
---------------------------
Delivery is at-most-once, deliberately, for the same reason the WhatsApp
follow-up engine chose it (`crm.api.followup_engine`): a paid, customer-visible
message is worse to duplicate than to lose. Two mechanisms carry it.

1. **The unique idempotency key.** One `CRM Outbound Recipient` row per (job,
   channel, normalised address). The row is inserted before anything leaves the
   site, so a second attempt collides on the index instead of sending.
2. **Claim, then commit, then send.** A recipient is moved to `Claimed` and the
   transaction is committed BEFORE the adapter is called. A crash after that
   point leaves the key spent, so the recipient is never retried. A crash before
   it leaves the row `Pending`, so the send simply has not happened yet.

Every state change is made while holding `SELECT ... FOR UPDATE` on the row, so
a scheduler sweep and a user's Cancel cannot both act on the same job.

What "sent" means
-----------------
`Sent` means the framework's Email Queue row reports sent -- never that the
engine handed the message over. The adapter returns the queue row's name, the
recipient stores it, and `refresh_delivery_states` reads the real status back.
Until then the recipient is `Queued`, and the UI must say queued.

Replies are matched on `Message-ID` / `In-Reply-To`, never on the subject line.
A subject match cancels the wrong send the first time two customers reply to two
different campaigns with the same "Re:" line.

Adapters
--------
This module never talks to a provider. A channel adapter is registered by the
stage that owns it and is handed one recipient at a time. Stage 1 registers
none, so `process_scheduled_jobs` cannot send anything even if the flag is
turned on: a job with no adapter fails with a stated reason. That is what makes
the foundation provably send-free.

The flag: `outbound_engine_enabled`, default OFF (`crm.feature_flags`).

Error contract: `process_scheduled_jobs` runs unattended and never raises.
"""

import importlib
import json
import re

import frappe
from frappe import _

from crm import suppression
from crm.feature_flags import is_enabled
from crm.normalization import normalize_email, normalize_phone

JOB_DOCTYPE = "CRM Outbound Job"
RECIPIENT_DOCTYPE = "CRM Outbound Recipient"
EMAIL_QUEUE_DOCTYPE = "Email Queue"

FLAG_OUTBOUND_ENGINE = "outbound_engine_enabled"

CHANNEL_EMAIL = suppression.CHANNEL_EMAIL
CHANNEL_WHATSAPP = suppression.CHANNEL_WHATSAPP

# --- job states ---
JOB_DRAFT = "Draft"
JOB_SCHEDULED = "Scheduled"
JOB_CLAIMED = "Claimed"
JOB_QUEUED = "Queued"
JOB_SENT = "Sent"
JOB_FAILED = "Failed"
JOB_CANCELLED = "Cancelled"

# Cancel is reachable from Draft and Scheduled ONLY. Past Claimed the recipients
# may already be with a provider, and a state that said Cancelled would be a lie
# told to the person who clicked it. That is the cancellation cutoff the spec
# names for Send Later.
JOB_TRANSITIONS = {
	JOB_DRAFT: {JOB_SCHEDULED, JOB_CLAIMED, JOB_CANCELLED},
	JOB_SCHEDULED: {JOB_CLAIMED, JOB_CANCELLED},
	JOB_CLAIMED: {JOB_QUEUED, JOB_FAILED},
	JOB_QUEUED: {JOB_SENT, JOB_FAILED},
	JOB_SENT: set(),
	JOB_FAILED: set(),
	JOB_CANCELLED: set(),
}

# --- recipient states ---
RECIPIENT_PENDING = "Pending"
RECIPIENT_CLAIMED = "Claimed"
RECIPIENT_QUEUED = "Queued"
RECIPIENT_SENT = "Sent"
RECIPIENT_FAILED = "Failed"
RECIPIENT_SUPPRESSED = "Suppressed"
RECIPIENT_CANCELLED = "Cancelled"

RECIPIENT_TRANSITIONS = {
	RECIPIENT_PENDING: {RECIPIENT_CLAIMED, RECIPIENT_SUPPRESSED, RECIPIENT_CANCELLED},
	RECIPIENT_CLAIMED: {RECIPIENT_QUEUED, RECIPIENT_FAILED},
	RECIPIENT_QUEUED: {RECIPIENT_SENT, RECIPIENT_FAILED},
	RECIPIENT_SENT: set(),
	RECIPIENT_FAILED: set(),
	RECIPIENT_SUPPRESSED: set(),
	RECIPIENT_CANCELLED: set(),
}

TERMINAL_RECIPIENT_STATES = (
	RECIPIENT_SENT,
	RECIPIENT_FAILED,
	RECIPIENT_SUPPRESSED,
	RECIPIENT_CANCELLED,
)

# How many due jobs one sweep will take. A sweep that tried to drain an unbounded
# backlog would hold a worker for the whole scheduler interval.
SWEEP_LIMIT = 20

# Message-ID tokens as RFC 5322 writes them: <local@domain>, possibly several.
MESSAGE_ID_PATTERN = re.compile(r"<([^<>@\s]+@[^<>\s]+)>")

MAX_ERROR_LENGTH = 200

# Set for the length of one adapter call when the job's payload asks for an
# unsubscribe link. Read by `crm.sequences.unsubscribe.add_list_unsubscribe_header`
# on Email Queue `before_insert`.
UNSUBSCRIBE_FLAG = "crm_unsubscribe_url"


class InvalidTransition(frappe.ValidationError):
	"""A state change the machine does not allow. Never caught to retry."""


# --- transaction seams -----------------------------------------------------
# Every commit and rollback goes through these two. The scheduler needs real
# commit boundaries; the tests need to neutralise them and to assert their
# ordering, exactly as in crm.api.followup_engine.


def commit():
	frappe.db.commit()


def rollback():
	frappe.db.rollback()


def lock_job(name: str):
	"""Read one job row with `SELECT ... FOR UPDATE`."""
	return frappe.get_doc(JOB_DOCTYPE, name, for_update=True)


def lock_recipient(name: str):
	"""Read one recipient row with `SELECT ... FOR UPDATE`."""
	return frappe.get_doc(RECIPIENT_DOCTYPE, name, for_update=True)


# --- channel adapters ------------------------------------------------------
# An adapter is `fn(job, recipient) -> dict`. The dict may carry `email_queue`,
# `message_id` and `communication`; anything else is ignored. Raising means the
# send failed, and the recipient is marked Failed with the message.

_ADAPTERS: dict[str, object] = {}


def register_adapter(channel: str, handler) -> None:
	"""Bind a channel to the code that actually sends on it."""
	_ADAPTERS[channel] = handler


def unregister_adapter(channel: str) -> None:
	_ADAPTERS.pop(channel, None)


def get_adapter(channel: str):
	return _ADAPTERS.get(channel)


# An adapter registers itself when its module is imported, and a scheduler worker
# imports `crm.outbound` alone -- nothing drags `crm.api.email` in behind it. The
# sweep therefore imports the owning modules before it claims anything.
#
# Deliberately NOT done inside `get_adapter` or `execute_job`: a job executed
# directly with no adapter registered must still fail with "no adapter", which is
# the behaviour Stage 1 shipped and `crm/tests/test_outbound.py` asserts. The
# import belongs to the unattended entry point, not to the lookup.
#
# A registrar is named rather than relied on as an import side effect, so that
# importing `crm.api.email` for any other reason does not silently arm the
# engine.
ADAPTER_REGISTRARS = ("crm.api.email:register_adapters",)


def load_adapter_modules() -> None:
	"""Run every registrar that owns a channel adapter. Never raises.

	A registrar that fails is logged and skipped. Its channel's jobs then fail
	with "no adapter", which is the honest outcome -- far better than a sweep
	that dies and takes every other channel down with it.
	"""
	for path in ADAPTER_REGISTRARS:
		module_path, _sep, attribute = path.partition(":")
		try:
			getattr(importlib.import_module(module_path), attribute)()
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"CRM outbound: adapter registrar {path} failed")


# --- enqueue ---------------------------------------------------------------


def normalize_for_channel(channel: str, address) -> str:
	if channel == CHANNEL_EMAIL:
		return normalize_email(address)
	if channel == CHANNEL_WHATSAPP:
		return normalize_phone(address)
	return ""


def recipient_key(job_name: str, channel: str, address: str) -> str:
	"""The outbox guard. Built from the NORMALISED address only."""
	return f"{job_name}:{channel}:{address}"


def create_job(
	job_type: str,
	channel: str,
	idempotency_key: str,
	recipients,
	scheduled_at=None,
	subject: str | None = None,
	payload=None,
	owner_user: str | None = None,
	sender_timezone: str | None = None,
	reference_doctype: str | None = None,
	reference_name: str | None = None,
):
	"""Create one Draft job and its recipient rows. Idempotent on the job key.

	Returns the job document. A caller that repeats the same `idempotency_key`
	gets the EXISTING job back untouched -- a browser double-click, a retried
	worker and a re-run report must not each produce their own copy.

	Recipients are normalised and de-duplicated here, so two spellings of one
	address become one row and one send. Nothing is sent, scheduled or enqueued:
	the job leaves this function in `Draft`.
	"""
	if channel not in (CHANNEL_EMAIL, CHANNEL_WHATSAPP):
		frappe.throw(_("Unknown outbound channel {0}.").format(channel))

	existing = frappe.db.get_value(JOB_DOCTYPE, {"idempotency_key": idempotency_key}, "name")
	if existing:
		return frappe.get_doc(JOB_DOCTYPE, existing)

	job = frappe.new_doc(JOB_DOCTYPE)
	job.update(
		{
			"job_type": job_type,
			"channel": channel,
			"idempotency_key": idempotency_key,
			"state": JOB_DRAFT,
			"scheduled_at": scheduled_at,
			"subject": subject,
			"payload": payload if isinstance(payload, str) else json.dumps(payload or {}),
			"owner_user": owner_user or frappe.session.user,
			"sender_timezone": sender_timezone,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
		}
	)

	try:
		job.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		# Another caller claimed the key between the read and the insert.
		existing = frappe.db.get_value(JOB_DOCTYPE, {"idempotency_key": idempotency_key}, "name")
		if not existing:
			raise
		return frappe.get_doc(JOB_DOCTYPE, existing)

	count = add_recipients(job, recipients)
	frappe.db.set_value(JOB_DOCTYPE, job.name, "recipient_count", count, update_modified=False)
	job.recipient_count = count
	return job


def add_recipients(job, recipients) -> int:
	"""Insert one row per distinct normalised address. Returns how many landed."""
	seen = set()
	created = 0

	for entry in recipients or []:
		if isinstance(entry, str):
			entry = {"address": entry}

		address = normalize_for_channel(job.channel, entry.get("address"))
		if not address or address in seen:
			# An address that cannot be normalised has no key to claim, and a
			# duplicate would be a second copy for the same person.
			continue
		seen.add(address)

		row = frappe.new_doc(RECIPIENT_DOCTYPE)
		row.update(
			{
				"job": job.name,
				"channel": job.channel,
				"address": address,
				"state": RECIPIENT_PENDING,
				"idempotency_key": recipient_key(job.name, job.channel, address),
				"reference_doctype": entry.get("reference_doctype"),
				"reference_name": entry.get("reference_name"),
			}
		)

		try:
			row.insert(ignore_permissions=True)
		except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
			# The key is already claimed for this job. Someone else added it.
			continue

		created += 1

	return created


# --- state machine ---------------------------------------------------------


def transition_job(name: str, target: str, **updates) -> bool:
	"""Move one job to `target` while holding its row lock.

	Returns True when the row moved. Returns False when it was ALREADY in the
	target state -- a repeated sweep is not an error. Raises `InvalidTransition`
	for a move the machine forbids, which is how cancel-after-claim is refused.
	"""
	job = lock_job(name)
	current = job.state

	if current == target:
		return False

	if target not in JOB_TRANSITIONS.get(current, set()):
		frappe.throw(
			_("An outbound job cannot go from {0} to {1}.").format(current, target),
			InvalidTransition,
		)

	job.db_set({"state": target, **updates}, update_modified=False)
	return True


def transition_recipient(name: str, target: str, **updates) -> bool:
	"""Move one recipient to `target` while holding its row lock.

	This is the claim gate. Two workers that both reach a `Pending` recipient
	serialise on the lock; the first writes `Claimed`, the second re-reads
	`Claimed` and gets False back, so only one of them ever calls the adapter.
	"""
	row = lock_recipient(name)
	current = row.state

	if current == target:
		return False

	if target not in RECIPIENT_TRANSITIONS.get(current, set()):
		frappe.throw(
			_("An outbound recipient cannot go from {0} to {1}.").format(current, target),
			InvalidTransition,
		)

	row.db_set({"state": target, **updates}, update_modified=False)
	return True


def schedule_job(name: str, when=None) -> bool:
	"""Draft → Scheduled. `when` defaults to the job's own `scheduled_at`."""
	updates = {}
	if when:
		updates["scheduled_at"] = when
	return transition_job(name, JOB_SCHEDULED, **updates)


def cancel_job(name: str, reason: str = "") -> bool:
	"""Cancel a job that has not been claimed yet.

	Raises `InvalidTransition` once the job is Claimed or past it. That refusal
	is the feature: the cutoff has to be a hard edge, or a user is told their
	send was stopped after it left.
	"""
	moved = transition_job(
		name,
		JOB_CANCELLED,
		cancelled_reason=frappe.utils.strip_html(frappe.utils.cstr(reason))[:MAX_ERROR_LENGTH],
		completed_at=frappe.utils.now_datetime(),
	)
	if moved:
		cancel_pending_recipients(name)
	return moved


def cancel_pending_recipients(job_name: str) -> int:
	"""Close every recipient that has not been claimed. Returns how many."""
	pending = frappe.get_all(
		RECIPIENT_DOCTYPE, filters={"job": job_name, "state": RECIPIENT_PENDING}, pluck="name"
	)
	cancelled = 0
	for name in pending:
		try:
			if transition_recipient(name, RECIPIENT_CANCELLED):
				cancelled += 1
		except InvalidTransition:
			# It was claimed between the read and the lock. Leave it alone: it is
			# already past the cutoff.
			continue
	return cancelled


def claim_job(name: str, now=None) -> bool:
	"""Take exclusive ownership of a due job. True only for the winner.

	The commit is the point of the function. It ends the transaction that holds
	the row lock, so the loser's `SELECT ... FOR UPDATE` unblocks and reads the
	state the winner wrote.
	"""
	now = now or frappe.utils.now_datetime()
	try:
		moved = transition_job(name, JOB_CLAIMED, claimed_at=now)
	except InvalidTransition:
		# Cancelled, or already past Claimed. Not this worker's job.
		rollback()
		return False

	commit()
	return moved


# --- enqueue after commit --------------------------------------------------


def enqueue_job_execution(job_name: str, queue: str = "long") -> None:
	"""Hand a claimed job to a background worker, AFTER this transaction commits.

	`enqueue_after_commit` is not a nicety. Enqueue during the transaction and a
	worker can pick the job up before the row that describes it is visible -- or
	after the transaction rolls back, in which case the worker sends a message
	for a job that does not exist.
	"""
	frappe.enqueue(
		"crm.outbound.execute_job",
		queue=queue,
		job_name=job_name,
		enqueue_after_commit=True,
	)


# --- scheduler -------------------------------------------------------------


def process_scheduled_jobs() -> int:
	"""Scheduler entry point. Claim and run every job whose time has come.

	Returns the number of jobs claimed. Never raises: a scheduler job that throws
	takes the rest of its queue down with it.

	Gated on `outbound_engine_enabled`, which is OFF by default. While it is off
	this function does not read a single job row.
	"""
	try:
		if not is_enabled(FLAG_OUTBOUND_ENGINE):
			return 0

		load_adapter_modules()

		now = frappe.utils.now_datetime()
		due = frappe.get_all(
			JOB_DOCTYPE,
			filters={
				"state": JOB_SCHEDULED,
				"scheduled_at": ["<=", frappe.utils.get_datetime_str(now)],
			},
			pluck="name",
			order_by="scheduled_at asc",
			limit_page_length=SWEEP_LIMIT,
		)

		claimed = 0
		for name in due:
			try:
				if claim_job(name, now):
					claimed += 1
					execute_job(name)
			except Exception:
				rollback()
				frappe.log_error(frappe.get_traceback(), f"CRM outbound: job {name} failed")

		return claimed
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM outbound: scheduled sweep failed")
		return 0


def execute_job(job_name: str) -> dict:
	"""Send every pending recipient of a claimed job.

	Runs as the job's `owner_user`, not as whoever enqueued it, and re-reads that
	user at EXECUTION time. A user disabled between scheduling and sending must
	not still be able to send through a job they left behind.
	"""
	job = frappe.get_doc(JOB_DOCTYPE, job_name)
	if job.state != JOB_CLAIMED:
		return {"sent": 0, "failed": 0, "suppressed": 0, "skipped": True}

	adapter = get_adapter(job.channel)
	if not adapter:
		transition_job(
			job_name,
			JOB_FAILED,
			last_error=_("No send adapter is registered for channel {0}.").format(job.channel)[
				:MAX_ERROR_LENGTH
			],
			completed_at=frappe.utils.now_datetime(),
		)
		commit()
		return {"sent": 0, "failed": 0, "suppressed": 0, "no_adapter": True}

	if not sender_is_active(job):
		transition_job(
			job_name,
			JOB_FAILED,
			last_error=_("The user this job sends on behalf of is disabled.")[:MAX_ERROR_LENGTH],
			completed_at=frappe.utils.now_datetime(),
		)
		commit()
		return {"sent": 0, "failed": 0, "suppressed": 0, "sender_disabled": True}

	transition_job(job_name, JOB_QUEUED)
	commit()

	counts = {"sent": 0, "failed": 0, "suppressed": 0}
	pending = frappe.get_all(
		RECIPIENT_DOCTYPE, filters={"job": job_name, "state": RECIPIENT_PENDING}, pluck="name"
	)

	for name in pending:
		outcome = deliver_recipient(job, name, adapter)
		if outcome in counts:
			counts[outcome] += 1

	finish_job(job_name, counts)
	return counts


def sender_is_active(job) -> bool:
	"""True when the job may still act for its user. Checked at run time."""
	if not job.owner_user:
		return True
	enabled = frappe.db.get_value("User", job.owner_user, "enabled")
	return bool(enabled)


def arm_unsubscribe_header(job) -> None:
	"""Let the Email Queue hook add List-Unsubscribe to THIS job's message.

	A compliance header belongs to the job that asked for one, not to every mail
	the site sends, and the only place it can be added is on the built MIME
	string an Email Queue row carries (Frappe v15 has no unsubscribe machinery of
	its own -- see `crm.sequences.unsubscribe`). The adapter call is the narrowest
	window that certainly contains that insert, so the flag is set here and
	cleared in the caller's `finally`.

	Never raises: an unreadable payload must not fail a send that is already paid
	for by a spent idempotency key.
	"""
	try:
		payload = frappe.parse_json(job.payload or "{}") or {}
		url = frappe.utils.cstr(payload.get("unsubscribe_url") or "")
	except Exception:
		url = ""

	frappe.local.flags[UNSUBSCRIBE_FLAG] = url or None


def disarm_unsubscribe_header() -> None:
	frappe.local.flags[UNSUBSCRIBE_FLAG] = None


def deliver_recipient(job, name: str, adapter) -> str:
	"""Claim, commit, send, record. Returns 'sent', 'failed' or 'suppressed'.

	The order is the whole guarantee -- see the module docstring. The suppression
	check happens INSIDE the lock and before the claim, so an opt-out recorded
	while the job was queued still stops the message.
	"""
	row = lock_recipient(name)
	if row.state != RECIPIENT_PENDING:
		commit()
		return "skipped"

	if suppression.is_suppressed(job.channel, row.address):
		row.db_set({"state": RECIPIENT_SUPPRESSED}, update_modified=False)
		commit()
		return "suppressed"

	row.db_set(
		{"state": RECIPIENT_CLAIMED, "claimed_at": frappe.utils.now_datetime()},
		update_modified=False,
	)

	# The commit boundary. After this the recipient is spent whatever happens
	# next, so a crash can lose a send but can never repeat one.
	commit()

	try:
		arm_unsubscribe_header(job)
		result = adapter(job, row) or {}
	except Exception:
		rollback()
		frappe.db.set_value(
			RECIPIENT_DOCTYPE,
			name,
			{
				"state": RECIPIENT_FAILED,
				"last_error": frappe.utils.cstr(frappe.get_traceback(with_context=False))[-MAX_ERROR_LENGTH:],
			},
			update_modified=False,
		)
		commit()
		frappe.log_error(frappe.get_traceback(), f"CRM outbound: send failed for recipient {name}")
		return "failed"
	finally:
		disarm_unsubscribe_header()

	frappe.db.set_value(
		RECIPIENT_DOCTYPE,
		name,
		{
			"state": RECIPIENT_QUEUED,
			"email_queue": result.get("email_queue"),
			"message_id": result.get("message_id"),
			"communication": result.get("communication"),
			"sent_at": frappe.utils.now_datetime(),
		},
		update_modified=False,
	)
	commit()
	return "sent"


def finish_job(job_name: str, counts: dict) -> None:
	"""Write the tallies and close the job. Queued → Sent, or Failed."""
	target = JOB_FAILED if counts.get("failed") and not counts.get("sent") else JOB_SENT
	transition_job(
		job_name,
		target,
		sent_count=counts.get("sent", 0),
		failed_count=counts.get("failed", 0),
		suppressed_count=counts.get("suppressed", 0),
		completed_at=frappe.utils.now_datetime(),
	)
	commit()


# --- Email Queue correlation ----------------------------------------------


def read_queue_status(queue_name: str) -> str | None:
	"""The framework's own verdict on one queued message.

	A named seam rather than an inline lookup: this is the ONE place the app asks
	whether a message was really sent, so it is also the one place a test can
	stand in for the queue without patching the database layer out from under
	every other caller.
	"""
	return frappe.db.get_value(EMAIL_QUEUE_DOCTYPE, queue_name, "status")


def refresh_delivery_states(job_name: str | None = None) -> int:
	"""Read the real status back from the Email Queue rows we correlated.

	"Sent" in this app means the framework says sent. This is the function that
	makes that true: until it runs, a recipient stays `Queued`, and the UI must
	say queued rather than delivered.
	"""
	filters = {"state": RECIPIENT_QUEUED, "email_queue": ["is", "set"]}
	if job_name:
		filters["job"] = job_name

	rows = frappe.get_all(RECIPIENT_DOCTYPE, filters=filters, fields=["name", "email_queue"])
	updated = 0

	for row in rows:
		status = read_queue_status(row.email_queue)
		if status == "Sent":
			transition_recipient(row.name, RECIPIENT_SENT)
			updated += 1
		elif status in ("Error", "Expired"):
			transition_recipient(
				row.name,
				RECIPIENT_FAILED,
				last_error=_("Email Queue reported {0}.").format(status)[:MAX_ERROR_LENGTH],
			)
			updated += 1

	return updated


def sweep_delivery_states() -> int:
	"""Scheduler entry point for `refresh_delivery_states`. Hourly.

	A separate function rather than putting the guards inside
	`refresh_delivery_states`, because the two have different contracts. The
	sweep runs unattended: it is gated on `outbound_engine_enabled` (OFF by
	default, so it reads nothing at all) and it never raises, because a scheduler
	job that throws takes the rest of its queue down with it. The function it
	calls is also used by a request handler, which wants neither guard.

	Without this entry a recipient stays `Queued` for ever and the UI keeps
	saying "queued" about a message the framework delivered an hour ago.
	"""
	try:
		if not is_enabled(FLAG_OUTBOUND_ENGINE):
			return 0

		return refresh_delivery_states()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM outbound: delivery-state sweep failed")
		return 0


# --- reply matching --------------------------------------------------------


def extract_message_ids(value) -> list[str]:
	"""Every RFC 5322 Message-ID token in a header value, without the brackets.

	`References` legitimately holds a whole chain, and a reply's `In-Reply-To`
	sometimes repeats it, so this returns a list rather than one value.
	"""
	return MESSAGE_ID_PATTERN.findall(frappe.utils.cstr(value))


def message_id_candidates(*values) -> list[str]:
	"""Both spellings of every id found: bare, and wrapped in angle brackets.

	The stored `message_id` comes from whichever adapter produced it, and the two
	conventions are both common -- the framework's Email Queue keeps the bare
	form, a raw header carries the brackets. Matching on one spelling only would
	silently never match half the adapters, and a reply-matcher that quietly
	never matches is worse than one that fails loudly.
	"""
	found = []
	for value in values:
		for message_id in extract_message_ids(value):
			found.extend([message_id, f"<{message_id}>"])
	return found


def match_reply(in_reply_to=None, references=None) -> dict | None:
	"""Find the recipient a reply belongs to. Returns the row, or None.

	Matching is on Message-ID only. Subject matching is not a fallback here and
	must not become one: two campaigns produce the same "Re: " line, and the
	first collision cancels the wrong customer's send.
	"""
	candidates = message_id_candidates(in_reply_to, references)
	if not candidates:
		return None

	rows = frappe.get_all(
		RECIPIENT_DOCTYPE,
		filters={"message_id": ["in", candidates]},
		fields=["name", "job", "channel", "address", "state", "message_id"],
		order_by="creation desc",
		limit_page_length=1,
	)
	return rows[0] if rows else None


def record_reply(recipient_name: str, in_reply_to: str, when=None) -> None:
	"""Stamp the reply on a recipient. Does not change a terminal state."""
	state = frappe.db.get_value(RECIPIENT_DOCTYPE, recipient_name, "state")
	if state is None:
		return

	frappe.db.set_value(
		RECIPIENT_DOCTYPE,
		recipient_name,
		{
			"in_reply_to": frappe.utils.cstr(in_reply_to)[:MAX_ERROR_LENGTH],
			"replied_at": when or frappe.utils.now_datetime(),
		},
		update_modified=False,
	)
