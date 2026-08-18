"""The composer's send path: send now, send later, and the consent ledger.

Why this file exists
--------------------
The composer used to call `frappe.core.doctype.communication.email.make`
directly. That is the framework's own endpoint and it knows nothing about the
Stage-1A suppression ledger, so an agent could type an address that had already
opted out and the mail would go. The master spec's must-not-do list has no send
path without a suppression check, and Forward (item 6) rides this same path, so
the check goes in here rather than in the Forward button.

What it does NOT change: a send to addresses that are not suppressed produces
exactly the Communication `make` would have produced, from the same arguments,
with the same permission check -- `make` still does `frappe.has_permission(...,
ptype="email", throw=True)` on the referenced record. This wrapper adds a
filter in front and a report of what it removed. Addresses are passed through
BY VALUE: the ledger is consulted with each address as typed, and the surviving
strings are handed on unaltered, so display names and casing reach the queue
exactly as before.

Send later (item 5)
-------------------
A scheduled send is a `CRM Outbound Job` in state `Scheduled` carrying the whole
rendered message as its payload, plus one `CRM Outbound Recipient` for the
primary address. When the hourly sweep finds the job due it claims it, commits,
and calls `email_adapter`, which calls `send_email` above -- so a scheduled mail
takes exactly the same suppression check, the same permission check and the same
`make` as a mail sent by hand. Nothing about "later" is a second send path.

Three things a reader should know before changing this:

* **The job carries ONE recipient row, not one per address.** An email with
  three people in To is one message, not three. The row's address is the first
  To address and it is the idempotency key; the other addresses ride in the
  payload and are re-checked against the ledger by `send_email` at send time.
* **The sweep is HOURLY.** "Send at 18:00" therefore means "on the first sweep
  at or after 18:00". The UI says so; do not promise the minute.
* **The authoritative timezone is the sender's**, read from their User record
  and stored on the job (`sender_timezone`). Every preset is computed in that
  timezone on the SERVER. The browser's clock is never trusted, because a laptop
  with a wrong timezone would silently send at the wrong hour.

The engine is behind `outbound_engine_enabled`, default OFF. Scheduling still
works with the flag off; the sweep that delivers does not run. Deployment has to
turn it on -- see `demo-package/specs/stage3a-notes.md`.

Endpoint authorization (master spec §3), stated here and in
`crm/tests/test_email_compose.py` / `crm/tests/test_send_later.py`:

* `send_email` -- POST only, any signed-in user. Row-level scope is derived
  SERVER-side by `make`, which checks the `email` permission on the named
  reference record (and so runs the org-hierarchy `has_permission` hook for
  CRM Lead and CRM Deal). Nothing about the recipient list widens that scope.
* `schedule_email` -- POST only, any signed-in user. Same `email` permission on
  the same reference record, checked BEFORE the job row exists, plus a fixed
  doctype allowlist. `owner_user` is taken from `frappe.session.user` and never
  from the request.
* `send_scheduled_email_now` / `cancel_scheduled_email` -- POST only. The caller
  must be the job's `owner_user` or a manager, AND still hold the `email`
  permission on the referenced record. Both take the job's row lock through the
  state machine, so a sweep and a click cannot both act on one job.
* `get_scheduled_emails` -- the caller must hold `read` on the referenced
  record; the scope is that one record and nothing else.
"""

from datetime import timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe import _

from crm import outbound
from crm.suppression import CHANNEL_EMAIL, is_suppressed

# The composer runs on exactly these two records. `make` would accept any
# doctype; a scheduled job is a durable object with a reference on it, so the
# doctype it may point at is pinned rather than inherited from the request.
SCHEDULABLE_DOCTYPES = ("CRM Lead", "CRM Deal")

JOB_TYPE_SEND_LATER = "Send Later"

# Presets, as (key, hour, minute). The labels live in the frontend; the times
# live here, because the time is the part that must not depend on the browser.
PRESET_LATER_TODAY = "later_today"
PRESET_TOMORROW_MORNING = "tomorrow_morning"
PRESET_NEXT_MONDAY = "next_monday"

PRESETS = {
	PRESET_LATER_TODAY: (18, 0),
	PRESET_TOMORROW_MORNING: (9, 0),
	PRESET_NEXT_MONDAY: (9, 0),
}

# A scheduled send further out than this is almost always a typo in a year.
MAX_SCHEDULE_DAYS = 365

MANAGER_ROLES = ("Sales Manager", "System Manager")


def split_addresses(value) -> list[str]:
	"""The composer sends "a@x.com, b@y.com". Split it, keep the strings intact."""
	if not value:
		return []
	if isinstance(value, list | tuple):
		items = value
	else:
		items = str(value).split(",")

	return [str(item).strip() for item in items if str(item).strip()]


def drop_suppressed(addresses: list[str]) -> tuple[list[str], list[str]]:
	"""Split a recipient list into (allowed, suppressed), preserving each string.

	`crm.suppression.filter_suppressed` is the batch tool and it returns
	NORMALISED addresses. That is right for a mass send and wrong here: this list
	is a handful of addresses an agent typed, and rewriting `Ann Lee
	<ann@x.com>` into `ann@x.com` on its way to the queue would be a behaviour
	change for sends that have nothing to do with suppression.
	"""
	allowed, blocked = [], []
	for address in addresses:
		if is_suppressed(CHANNEL_EMAIL, address):
			blocked.append(address)
		else:
			allowed.append(address)
	return allowed, blocked


@frappe.whitelist(methods=["POST"])
def send_email(
	doctype: str,
	name: str,
	recipients: str,
	subject: str | None = None,
	content: str | None = None,
	cc: str | None = None,
	bcc: str | None = None,
	# `str` is allowed because a form-encoded POST arrives with the list as a
	# JSON string. `frappe.parse_json` below turns it back into a list; the
	# framework's own type validation would refuse it before this body runs.
	attachments: list | str | None = None,
	sender: str | None = None,
	sender_full_name: str | None = None,
	read_receipt: int = 0,
	send_me_a_copy: int = 0,
	in_reply_to: str | None = None,
) -> dict:
	"""Send one composed email. Returns `make`'s result plus what was held back.

	Raises when EVERY recipient is suppressed: an agent who is told nothing would
	assume the mail went.
	"""
	from frappe.core.doctype.communication.email import make

	to, to_blocked = drop_suppressed(split_addresses(recipients))
	cc_list, cc_blocked = drop_suppressed(split_addresses(cc))
	bcc_list, bcc_blocked = drop_suppressed(split_addresses(bcc))

	if not to:
		if to_blocked:
			frappe.throw(
				_("{0} has opted out of email. Nothing was sent.").format(", ".join(to_blocked)),
				title=_("Recipient opted out"),
			)
		frappe.throw(_("An email needs at least one recipient."))

	result = make(
		doctype=doctype,
		name=name,
		content=content,
		subject=subject,
		sender=sender,
		sender_full_name=sender_full_name,
		recipients=", ".join(to),
		cc=", ".join(cc_list),
		bcc=", ".join(bcc_list),
		attachments=frappe.parse_json(attachments) if isinstance(attachments, str) else attachments,
		send_email=1,
		read_receipt=read_receipt,
		send_me_a_copy=send_me_a_copy,
		in_reply_to=in_reply_to,
	)

	result = dict(result or {})
	result["suppressed"] = to_blocked + cc_blocked + bcc_blocked
	return result


# --- timezone --------------------------------------------------------------


def user_timezone(user: str | None = None) -> str:
	"""The sender's own timezone, falling back to the site's.

	`User.time_zone` is optional and frequently blank on a small self-hosted
	site. Falling back to the system timezone is right: it is the timezone the
	agency actually works in, and it is what every other date in the app already
	means.
	"""
	user = user or frappe.session.user
	stored = frappe.db.get_value("User", user, "time_zone") if user else None
	return stored or frappe.utils.get_system_timezone()


def zone(name: str):
	"""A ZoneInfo, or the system zone when the stored name is unusable."""
	try:
		return ZoneInfo(name)
	except Exception:
		return ZoneInfo(frappe.utils.get_system_timezone())


def to_system_datetime(local_value, timezone: str):
	"""A naive datetime read in `timezone`, returned naive in the system timezone.

	Frappe stores every Datetime in the system timezone, so a time the user chose
	in their own timezone has to be converted before it is written. Doing it here,
	once, is what makes `scheduled_at` comparable with the sweep's `now`.
	"""
	naive = frappe.utils.get_datetime(local_value)
	if naive is None:
		frappe.throw(_("That is not a valid date and time."))
	aware = naive.replace(tzinfo=zone(timezone))
	return aware.astimezone(zone(frappe.utils.get_system_timezone())).replace(tzinfo=None)


def now_in_timezone(timezone: str):
	"""The wall-clock time the sender sees right now, as a naive datetime."""
	system_now = frappe.utils.now_datetime().replace(tzinfo=zone(frappe.utils.get_system_timezone()))
	return system_now.astimezone(zone(timezone)).replace(tzinfo=None)


def preset_datetime(preset: str, timezone: str):
	"""The local wall-clock time a preset names. Raises for an unknown preset."""
	if preset not in PRESETS:
		frappe.throw(_("Unknown send-later option {0}.").format(preset))

	hour, minute = PRESETS[preset]
	local_now = now_in_timezone(timezone)
	target = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)

	if preset == PRESET_TOMORROW_MORNING:
		target = target + timedelta(days=1)
	elif preset == PRESET_NEXT_MONDAY:
		# Strictly the NEXT Monday. On a Monday morning "next Monday" means the
		# one in seven days, not two hours ago.
		ahead = (7 - local_now.weekday()) or 7
		target = target + timedelta(days=ahead)

	return target


# --- send later ------------------------------------------------------------


def schedulable_doctype(doctype: str) -> str:
	if doctype not in SCHEDULABLE_DOCTYPES:
		frappe.throw(_("Emails can only be scheduled from: {0}").format(", ".join(SCHEDULABLE_DOCTYPES)))
	return doctype


@frappe.whitelist(methods=["POST"])
def schedule_email(
	doctype: str,
	name: str,
	recipients: str,
	subject: str | None = None,
	content: str | None = None,
	cc: str | None = None,
	bcc: str | None = None,
	attachments: list | str | None = None,
	sender: str | None = None,
	sender_full_name: str | None = None,
	preset: str | None = None,
	send_at: str | None = None,
) -> dict:
	"""Park a composed email until a time the sender chose. Returns the job.

	`preset` and `send_at` are alternatives; `preset` wins when both arrive.
	`send_at` is a naive local date-time in the SENDER's timezone, which is the
	timezone stored on the job and the one every later reading uses.

	Nothing is sent here and nothing is rendered later: the message is stored
	exactly as the composer produced it. A scheduled mail is the message the
	agent wrote, not the message their record happened to justify an hour later.
	"""
	schedulable_doctype(doctype)
	# The same check `make` performs, performed BEFORE a durable row exists.
	frappe.has_permission(doctype, doc=name, ptype="email", throw=True)

	user = frappe.session.user
	timezone = user_timezone(user)

	if preset:
		local_when = preset_datetime(preset, timezone)
	elif send_at:
		local_when = frappe.utils.get_datetime(send_at)
	else:
		frappe.throw(_("Choose when this email should go out."))

	local_now = now_in_timezone(timezone)
	if local_when <= local_now:
		frappe.throw(_("That time has already passed. Pick a later one."))
	if (local_when - local_now).days > MAX_SCHEDULE_DAYS:
		frappe.throw(_("An email cannot be scheduled more than {0} days ahead.").format(MAX_SCHEDULE_DAYS))

	to = split_addresses(recipients)
	if not to:
		frappe.throw(_("An email needs at least one recipient."))

	primary = outbound.normalize_for_channel(outbound.CHANNEL_EMAIL, to[0])
	if not primary:
		frappe.throw(_("{0} is not a valid email address.").format(to[0]))

	scheduled_at = to_system_datetime(local_when, timezone)

	payload = {
		"doctype": doctype,
		"name": name,
		"recipients": to,
		"cc": split_addresses(cc),
		"bcc": split_addresses(bcc),
		"subject": subject,
		"content": content,
		"attachments": frappe.parse_json(attachments)
		if isinstance(attachments, str)
		else (attachments or []),
		"sender": sender,
		"sender_full_name": sender_full_name,
	}

	job = outbound.create_job(
		job_type=JOB_TYPE_SEND_LATER,
		channel=outbound.CHANNEL_EMAIL,
		# A fresh key per scheduling action. Two deliberate "send later" clicks on
		# the same record ARE two different emails; only a retry of one click must
		# collapse, and a retry carries the same key because the browser does not
		# get a second one.
		idempotency_key=f"send-later:{frappe.generate_hash(length=20)}",
		recipients=[{"address": to[0], "reference_doctype": doctype, "reference_name": name}],
		scheduled_at=scheduled_at,
		subject=subject,
		payload=payload,
		owner_user=user,
		sender_timezone=timezone,
		reference_doctype=doctype,
		reference_name=name,
	)

	outbound.schedule_job(job.name)

	return describe_job(job.name)


def describe_job(job_name: str) -> dict:
	"""One scheduled job, in the shape the timeline renders."""
	row = frappe.db.get_value(
		outbound.JOB_DOCTYPE,
		job_name,
		[
			"name",
			"state",
			"subject",
			"scheduled_at",
			"sender_timezone",
			"owner_user",
			"reference_doctype",
			"reference_name",
			"payload",
			"last_error",
		],
		as_dict=True,
	)
	if not row:
		return {}

	payload = frappe.parse_json(row.payload or "{}") or {}
	return {
		"name": row.name,
		"state": row.state,
		"subject": row.subject,
		"scheduled_at": row.scheduled_at,
		"sender_timezone": row.sender_timezone,
		"owner_user": row.owner_user,
		"reference_doctype": row.reference_doctype,
		"reference_name": row.reference_name,
		"recipients": ", ".join(payload.get("recipients") or []),
		"cc": ", ".join(payload.get("cc") or []),
		"bcc": ", ".join(payload.get("bcc") or []),
		"content": payload.get("content"),
		"attachment_count": len(payload.get("attachments") or []),
		"can_cancel": row.state in (outbound.JOB_DRAFT, outbound.JOB_SCHEDULED),
		"last_error": row.last_error,
	}


def pending_jobs(doctype: str, name: str) -> list[str]:
	"""Names of the not-yet-sent Send Later jobs on one record, soonest first.

	Permission note: reads with `frappe.get_all`, which applies no permission
	conditions. Safe only because every caller has already checked the caller's
	access to `name`, and the query is scoped to that one record.
	"""
	return frappe.get_all(
		outbound.JOB_DOCTYPE,
		filters={
			"job_type": JOB_TYPE_SEND_LATER,
			"reference_doctype": doctype,
			"reference_name": name,
			"state": ["in", (outbound.JOB_DRAFT, outbound.JOB_SCHEDULED, outbound.JOB_CLAIMED)],
		},
		pluck="name",
		order_by="scheduled_at asc",
		limit_page_length=50,
	)


@frappe.whitelist()
def get_scheduled_emails(doctype: str, name: str) -> list[dict]:
	"""The pending scheduled emails on one record."""
	schedulable_doctype(doctype)
	if not frappe.has_permission(doctype, "read", name):
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	return [describe_job(job) for job in pending_jobs(doctype, name)]


def load_job_for_action(job_name: str):
	"""Read one Send Later job and prove the caller may act on it.

	Two checks, both server-side. The caller has to own the job or be a manager,
	AND still hold the `email` permission on the record the job points at -- an
	agent who lost access to a deal must not be able to fire a message from it
	just because they scheduled the message while they still could.
	"""
	job = frappe.db.get_value(
		outbound.JOB_DOCTYPE,
		job_name,
		["name", "state", "job_type", "owner_user", "reference_doctype", "reference_name"],
		as_dict=True,
	)
	if not job or job.job_type != JOB_TYPE_SEND_LATER:
		frappe.throw(_("This scheduled email no longer exists."), frappe.DoesNotExistError)

	is_manager = bool(set(frappe.get_roles()) & set(MANAGER_ROLES))
	if job.owner_user != frappe.session.user and not is_manager:
		frappe.throw(_("Not permitted"), frappe.PermissionError)

	if job.reference_doctype and job.reference_name:
		frappe.has_permission(job.reference_doctype, doc=job.reference_name, ptype="email", throw=True)

	return job


@frappe.whitelist(methods=["POST"])
def cancel_scheduled_email(job: str, reason: str = "") -> dict:
	"""Stop a scheduled email that has not been claimed yet.

	Past `Claimed` this refuses, and the refusal is the feature: the recipients
	may already be with the provider, and telling somebody their send was stopped
	after it left is worse than telling them it was too late.
	"""
	row = load_job_for_action(job)
	try:
		outbound.cancel_job(row.name, reason or _("Cancelled by {0}").format(frappe.session.user))
	except outbound.InvalidTransition:
		frappe.throw(
			_("This email is already on its way and can no longer be cancelled."),
			title=_("Too late to cancel"),
		)

	return describe_job(row.name)


@frappe.whitelist(methods=["POST"])
def send_scheduled_email_now(job: str) -> dict:
	"""Claim a scheduled email and deliver it in this request.

	It goes through `claim_job` and `execute_job` exactly as the sweep would, so
	a click and a sweep that land together serialise on the same row lock and
	only one of them sends.
	"""
	row = load_job_for_action(job)

	register_adapters()

	if row.state == outbound.JOB_DRAFT:
		outbound.schedule_job(row.name)

	if not outbound.claim_job(row.name):
		frappe.throw(
			_("This email is already being sent."),
			title=_("Already on its way"),
		)

	outbound.execute_job(row.name)
	return describe_job(row.name)


# --- the email channel adapter ---------------------------------------------


def email_adapter(job, recipient) -> dict:
	"""Deliver one scheduled job. Registered on the Email channel at import.

	Runs AS the job's owner. `crm.outbound.execute_job` already refused a
	disabled owner, but it does not change the session user, and a background
	worker's session user is whoever the runner happens to be. Sending as that
	user would put the wrong name on the Communication and would check the wrong
	person's permissions.
	"""
	payload = frappe.parse_json(job.payload or "{}") or {}
	owner = job.owner_user or frappe.session.user
	previous_user = frappe.session.user

	if owner != previous_user:
		frappe.set_user(owner)
	try:
		result = send_email(
			doctype=payload.get("doctype"),
			name=payload.get("name"),
			recipients=", ".join(payload.get("recipients") or []),
			subject=payload.get("subject"),
			content=payload.get("content"),
			cc=", ".join(payload.get("cc") or []),
			bcc=", ".join(payload.get("bcc") or []),
			attachments=payload.get("attachments") or [],
			sender=payload.get("sender"),
			sender_full_name=payload.get("sender_full_name"),
		)
	finally:
		if owner != previous_user:
			frappe.set_user(previous_user)

	communication = (result or {}).get("name")
	message_id = None
	queue_name = None

	if communication:
		message_id = frappe.db.get_value("Communication", communication, "message_id")
		queue_name = frappe.db.get_value("Email Queue", {"communication": communication}, "name")

	return {
		"communication": communication,
		"message_id": message_id,
		"email_queue": queue_name,
	}


def register_adapters() -> None:
	"""Bind the Email channel to `email_adapter`.

	Called by `crm.outbound.load_adapter_modules` (the hourly sweep) and by
	Send-now. Deliberately NOT an import-time side effect: importing this module
	for any other reason would then silently arm the outbound engine, and
	`crm/tests/test_outbound.py` asserts the honest opposite -- a job run with no
	adapter registered fails with "no adapter" rather than sending.

	It also does not OVERWRITE an adapter somebody else already bound to the
	channel. This is the default for the Email channel, not a claim on it, and a
	test that installed a recorder must keep it.
	"""
	if not outbound.get_adapter(outbound.CHANNEL_EMAIL):
		outbound.register_adapter(outbound.CHANNEL_EMAIL, email_adapter)


# --- cancel on reply -------------------------------------------------------


def reply_message_ids(doc) -> list[str]:
	"""Every Message-ID an inbound Communication says it is answering.

	The framework's receiver resolves the In-Reply-To header into a Communication
	link and does not keep the raw header, so the id is read back off the
	Communication the reply points at. `doc.in_reply_to` is also matched directly
	against the recipient row, which is the exact link and does not depend on a
	header surviving a round trip.
	"""
	ids = []
	parent = doc.get("in_reply_to")
	if parent:
		parent_message_id = frappe.db.get_value("Communication", parent, "message_id")
		if parent_message_id:
			# The framework stores a Communication's `message_id` WITHOUT the angle
			# brackets, and `crm.outbound.extract_message_ids` only recognises the
			# bracketed RFC 5322 form. Put them back or nothing ever matches.
			ids.append(f"<{frappe.utils.cstr(parent_message_id).strip('<>')}>")
	return ids


def handle_inbound_reply(doc) -> str | None:
	"""Cancel a still-pending scheduled email the customer has already answered.

	Called from `crm.utils.on_communication_insert`. Returns the cancelled job's
	name, or None. Never raises: this runs inside the insert of an inbound email,
	and a failure here must not lose the customer's message.

	A reply is matched on Message-ID only, never on the subject line -- two
	campaigns produce the same "Re:" and the first collision cancels the wrong
	customer's send.
	"""
	try:
		if doc.get("sent_or_received") != "Received":
			return None

		row = None
		parent = doc.get("in_reply_to")
		if parent:
			matches = frappe.get_all(
				outbound.RECIPIENT_DOCTYPE,
				filters={"communication": parent},
				fields=["name", "job", "state"],
				order_by="creation desc",
				limit_page_length=1,
			)
			row = matches[0] if matches else None

		if not row:
			row = outbound.match_reply(in_reply_to=" ".join(reply_message_ids(doc)))

		if not row:
			return None

		outbound.record_reply(row["name"], frappe.utils.cstr(parent or ""))

		job = frappe.db.get_value(
			outbound.JOB_DOCTYPE,
			row["job"],
			["name", "state", "job_type", "owner_user", "reference_doctype", "reference_name", "subject"],
			as_dict=True,
		)
		if not job or job.job_type != JOB_TYPE_SEND_LATER:
			return None
		if job.state not in (outbound.JOB_DRAFT, outbound.JOB_SCHEDULED):
			return None

		outbound.cancel_job(job.name, _("The customer replied before it went out."))
		notify_author_of_cancellation(job)
		return job.name
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM send later: reply cancellation failed")
		return None


def notify_author_of_cancellation(job) -> None:
	"""Tell the person who scheduled it that their message was held back.

	Silent cancellation is the failure mode that matters here: the agent believes
	a follow-up is coming, it never comes, and nothing anywhere says why.
	"""
	from crm.fcrm.doctype.crm_notification.crm_notification import notify_user

	if not job.owner_user:
		return

	text = _("Scheduled email cancelled: {0} replied first.").format(job.reference_name or "")
	notify_user(
		{
			"owner": None,
			"assigned_to": job.owner_user,
			"notification_type": "Email",
			"message": text,
			"notification_text": text,
			"reference_doctype": job.reference_doctype,
			"reference_docname": job.reference_name,
			"redirect_to_doctype": job.reference_doctype,
			"redirect_to_docname": job.reference_name,
		}
	)


# --- outgoing account ------------------------------------------------------


def has_outgoing_account() -> bool:
	"""Whether the site can send at all. Read by the web-form auto-response."""
	return bool(
		frappe.db.exists("Email Account", {"enable_outgoing": 1, "default_outgoing": 1})
		or frappe.db.exists("Email Account", {"enable_outgoing": 1})
	)
