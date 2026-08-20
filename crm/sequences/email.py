"""The email channel adapter: Email Queue semantics, as the sequence core sees them.

What this adapter actually does
-------------------------------
It does NOT send. It creates one `CRM Outbound Job` per due stage and hands it to
the F2 outbound machine, which delivers it on the hourly sweep through
`crm.api.email.send_email` -- the same suppression-checked, permission-checked
path the composer uses. There is deliberately no second send path for sequence
mail: a message the engine writes and a message an agent types leave the site
through one function.

The claim, and why it is the job row
------------------------------------
`crm.sequences.core.deliver` claims a key, commits, and only then calls the
outside world. Here the key is the job's `idempotency_key`,
`{lead}-cycle-{n}-stage-{n}-email`, which carries a unique index. The job is
inserted DIRECTLY rather than through `crm.outbound.create_job`, because that
helper is forgiving by design -- it hands back an existing job for a repeated key
-- and a claim must be able to tell "I created this" from "somebody else already
did". A collision on the index is `AlreadyClaimed`, and the core answers it by
advancing the stage instead of retrying it for ever.

The job leaves `claim` in **Draft**, which the sweep never picks up. `send` moves
it to Scheduled. So a crash between the two loses one message and can never
repeat one, which is the same trade the WhatsApp channel makes for the same
reason: a customer-visible message is worse duplicated than lost.

Two flags, both default OFF
---------------------------
`email_sequences_enabled` decides whether an email stage may be built at all --
with it off, `resolve_content` refuses and the core parks the row with a stated
reason, so nothing is claimed and no job exists. `outbound_engine_enabled`
decides whether a job that does exist is ever delivered. Either one off means no
mail.

Consent
-------
`resolve_destination` returns "" for a suppressed address, so the core parks the
row and the stage is skipped with a reason a human can read. The ledger is then
checked AGAIN by `crm.outbound.deliver_recipient` at delivery, and a third time
by `crm.api.email.send_email` inside it. Three checks, because the window between
claiming a stage and sending it is an hour long and consent can change inside it.

Debt, recorded on purpose
-------------------------
The enrolment row is still `CRM WhatsApp Followup`. The core treats it as generic
state and every field this adapter reads (`lead`, `cycle`, `current_stage`,
`state`) is channel-neutral, so the rename is churn rather than correctness. It
should happen when a second non-WhatsApp channel arrives, not before.

Error contract: nothing here swallows an exception the core does not expect.
`handle_inbound_reply` is the exception -- it runs inside the insert of a
customer's email and must never cost us that email.
"""

import frappe
from frappe import _

from crm import outbound, suppression
from crm.feature_flags import is_enabled
from crm.normalization import normalize_email
from crm.sequences import core, unsubscribe

FLAG_EMAIL_SEQUENCES = "email_sequences_enabled"

CHANNEL_EMAIL = suppression.CHANNEL_EMAIL
CHANNEL_WHATSAPP = suppression.CHANNEL_WHATSAPP

EMAIL_TEMPLATE_DOCTYPE = "Email Template"
LEAD_DOCTYPE = "CRM Lead"
DEAL_DOCTYPE = "CRM Deal"

# `crm.api.email.describe_job` and the timeline both read this to tell a sequence
# send from a Send Later one.
JOB_TYPE_SEQUENCE = "Email Sequence"

# The suffix that keeps an email stage's key distinct from the WhatsApp stage of
# the same number, so a mixed sequence cannot collide with itself.
KEY_SUFFIX = "email"

# Lead fields a template may interpolate. The same discipline as
# `crm.api.followup_engine.AI_LEAD_FIELDS`: travel context, never an address, a
# phone number or an owner.
TEMPLATE_FIELDS = (
	"lead_name",
	"first_name",
	"last_name",
	"destination",
	"travel_start_date",
	"travel_end_date",
	"group_size",
	"budget",
)

MAX_SUBJECT_LENGTH = 200


def email_stage(stage) -> bool:
	"""True when this configured stage sends on the email channel."""
	return (stage.get("channel") or CHANNEL_WHATSAPP) == CHANNEL_EMAIL


def sequence_enabled() -> bool:
	return is_enabled(FLAG_EMAIL_SEQUENCES)


class EmailSequenceAdapter(core.ChannelAdapter):
	"""Drives the email stages of a follow-up row through the sequence core.

	Only the channel-specific half of the interface is implemented here. Row
	state -- locking, due dates, parking, advancing -- belongs to the row's own
	doctype and stays with the WhatsApp adapter; `crm.sequences.router` routes
	each call to whichever of the two owns it.
	"""

	log_prefix = "CRM email sequence"
	row_label = "Follow-up"
	provider_label = "the outbound engine"

	def __init__(self, engine):
		#: The `crm.api.followup_engine` module, read at call time. Same reason as
		#: `crm.sequences.whatsapp`: no import cycle, and the seams stay patchable.
		self.engine = engine
		#: Why `resolve_destination` last returned "". The core asks for the reason
		#: in a separate call that carries no arguments, so it has to be remembered
		#: between the two. One row is decided at a time, inside one transaction,
		#: and the value is rewritten at the start of every `resolve_destination`.
		self.destination_problem = ""
		#: The message `build_payload` produced for the stage now being delivered.
		#: `crm.sequences.core.deliver` hands the payload to `send` but not to
		#: `claim`, and the claim is the row that carries the message, so it has to
		#: be remembered across those two calls. The whole sequence
		#: build_payload -> claim -> send runs inside one call stack for one row,
		#: so the value can never belong to a different row; `claim` clears it, and
		#: claiming without one is an error rather than an empty email.
		self.pending_payload = None

	@property
	def channel(self) -> str:
		return CHANNEL_EMAIL

	# --- transaction seam ---
	# Shared with the WhatsApp channel on purpose: the follow-up row is the same
	# row whatever the stage sends on, so it has one set of commit boundaries.

	def commit(self) -> None:
		self.engine.commit()

	def rollback(self) -> None:
		self.engine.rollback()

	def lock(self, name: str):
		return self.engine.lock_followup(name)

	def row_name(self, row) -> str:
		return row.name

	# --- content ---

	def resolve_content(self, stage):
		"""The stage's Email Template, or the reason it cannot be sent.

		The flag is checked HERE rather than higher up so that a configured email
		stage on a site with the feature off parks the row with a sentence a
		manager can read, instead of silently falling through to the WhatsApp
		rules and complaining about a missing WhatsApp template.
		"""
		if not sequence_enabled():
			return None, _("Email sequences are turned off.")

		name = frappe.utils.cstr(stage.get("email_template") or "").strip()
		if not name:
			return None, _("No email template is set for this stage.")

		template = frappe.db.get_value(
			EMAIL_TEMPLATE_DOCTYPE,
			name,
			["name", "subject", "response", "response_html", "use_html", "enabled"],
			as_dict=True,
		)
		if not template:
			return None, _("Email template {0} does not exist.").format(name)

		if not template.enabled:
			return None, _("Email template {0} is disabled.").format(name)

		subject = frappe.utils.cstr(stage.get("email_subject_override") or template.subject or "").strip()
		if not subject:
			return None, _("Email template {0} has no subject.").format(name)

		body = template_body(template)
		if not body.strip():
			return None, _("Email template {0} has no body.").format(name)

		# Rendered once against an EMPTY context, purely to find a broken template
		# now rather than an hour of hourly tracebacks later. Frappe's Jinja leaves
		# an unknown variable empty, so only a syntax error fails here.
		try:
			frappe.render_template(subject, {})
			frappe.render_template(body, {})
		except Exception as error:
			return None, _("Email template {0} could not be rendered: {1}").format(
				name, frappe.utils.cstr(error)[:120]
			)

		return frappe._dict({"name": template.name, "subject": subject, "body": body}), None

	def resolve_destination(self, row) -> str:
		"""The lead's email address, re-derived and re-checked at send time.

		Returns "" for a lead that has no address and for one that has withdrawn
		consent, and remembers which of the two it was so the row can be parked
		with the truth on it.
		"""
		self.destination_problem = _("The lead has no email address.")

		if not frappe.db.exists(LEAD_DOCTYPE, row.lead):
			self.destination_problem = _("The lead no longer exists.")
			return ""

		address = normalize_email(frappe.db.get_value(LEAD_DOCTYPE, row.lead, "email"))
		if not address:
			return ""

		if suppression.is_suppressed(CHANNEL_EMAIL, address):
			self.destination_problem = _("{0} has opted out of email.").format(address)
			return ""

		self.destination_problem = ""
		return address

	def no_destination_reason(self) -> str:
		return self.destination_problem or _("The lead has no email address.")

	def build_payload(self, row, stage, content) -> dict:
		"""The rendered message, plus everything the outbound job needs to send it."""
		address = self.resolve_destination(row)
		context = lead_context(row.lead)
		link = unsubscribe.link_for(address, LEAD_DOCTYPE, row.lead)

		subject = one_line(frappe.render_template(content.subject, context))[:MAX_SUBJECT_LENGTH]
		body = frappe.render_template(content.body, context)

		return self.remember(
			{
				"doctype": LEAD_DOCTYPE,
				"name": row.lead,
				"recipients": [address] if address else [],
				"cc": [],
				"bcc": [],
				"subject": subject,
				"content": with_unsubscribe_footer(body, link),
				"attachments": [],
				"sender": None,
				"sender_full_name": None,
				# Read by `crm.outbound.deliver_recipient`, which arms the
				# List-Unsubscribe header for the length of one adapter call.
				"unsubscribe_url": link,
				# Read by the timeline chip and by anyone debugging a job.
				"sequence": {
					"followup": row.name,
					"lead": row.lead,
					"cycle": frappe.utils.cint(row.cycle) or 1,
					"stage": frappe.utils.cint(row.current_stage) + 1,
					"template": content.name,
				},
			}
		)

	def remember(self, payload: dict) -> dict:
		"""Hold the message for `claim`. See `self.pending_payload`."""
		self.pending_payload = payload
		return payload

	# --- draft mode ---

	def is_draft_mode(self, settings) -> bool:
		return settings.send_mode == self.engine.SEND_MODE_DRAFT

	def hold_for_approval(self, row, stage_number: int, payload: dict) -> None:
		"""Exactly the WhatsApp behaviour: park the row, notify the lead's agent."""
		self.engine.hold_for_approval(row, stage_number, payload)

	# --- the at-most-once send ---

	def claim_key(self, row, stage_number: int) -> str:
		return f"{core.sequence_key(row.lead, row.cycle, stage_number)}-{KEY_SUFFIX}"

	def claim(self, row, stage_number: int, key: str, now):
		"""Insert the Draft outbound job whose unique key is the claim.

		Inserted directly rather than through `crm.outbound.create_job`: that
		helper returns an EXISTING job for a repeated key, which is right for a
		user clicking Send Later twice and wrong for an outbox guard, because it
		cannot say whether this worker or another one wrote the row.
		"""
		payload = self.pending_payload
		self.pending_payload = None
		if not payload:
			# Never guess. An empty payload here would mean an email with no body
			# going to a customer, which is worse than a logged failure.
			frappe.throw(_("This email stage has no message to send."))

		address = (payload.get("recipients") or [""])[0]
		if not address:
			frappe.throw(_("This email stage has no recipient."))

		job = frappe.new_doc(outbound.JOB_DOCTYPE)
		job.update(
			{
				"job_type": JOB_TYPE_SEQUENCE,
				"channel": CHANNEL_EMAIL,
				"idempotency_key": key,
				"state": outbound.JOB_DRAFT,
				"scheduled_at": now,
				"subject": payload.get("subject"),
				"payload": frappe.as_json(payload),
				"owner_user": sending_user(self.engine, row.lead),
				"reference_doctype": LEAD_DOCTYPE,
				"reference_name": row.lead,
			}
		)

		try:
			job.insert(ignore_permissions=True)
		except (frappe.UniqueValidationError, frappe.DuplicateEntryError) as error:
			raise core.AlreadyClaimed(key) from error

		count = outbound.add_recipients(
			job,
			[{"address": address, "reference_doctype": LEAD_DOCTYPE, "reference_name": row.lead}],
		)
		frappe.db.set_value(outbound.JOB_DOCTYPE, job.name, "recipient_count", count, update_modified=False)
		return job

	def send(self, row, content, payload: dict, destination: str):
		"""Hand the claimed job to the outbound sweep. Draft -> Scheduled.

		The job is found by its key rather than carried in an attribute: the core
		gives `send` no claim and no stage number, and a value remembered on the
		adapter between two calls is a bug waiting for the first worker that
		interleaves two rows. `current_stage` has not been advanced yet at this
		point -- the core advances it after `record_success` -- so the pending
		stage is always `current_stage + 1`.
		"""
		stage_number = frappe.utils.cint(row.current_stage) + 1
		key = self.claim_key(row, stage_number)

		job = frappe.db.get_value(
			outbound.JOB_DOCTYPE, {"idempotency_key": key}, ["name", "state"], as_dict=True
		)
		if not job:
			frappe.throw(_("The outbound job for this stage is missing."))
		if job.state != outbound.JOB_DRAFT:
			frappe.throw(_("The outbound job for this stage is already {0}.").format(job.state))

		outbound.schedule_job(job.name)
		return frappe._dict({"name": job.name})

	def record_success(self, claim, reference) -> None:
		"""Nothing to write: the job IS the record.

		The WhatsApp channel stores the provider's message id on its Send Log row
		here. The email channel's outcome lives on the job and on its recipient,
		and `crm.outbound.refresh_delivery_states` writes the real delivery state
		back from the Email Queue. Inventing a second copy here would give the app
		two answers to "was it sent" and no rule for which one wins.
		"""

	def record_failure(self, claim) -> None:
		"""Cancel the job the handover never completed.

		Without this a Draft job with a spent key would sit in the table for ever:
		invisible to the sweep, un-retryable because the key is gone, and confusing
		to whoever reads the table next.
		"""
		try:
			outbound.cancel_job(claim.name, _("The sequence could not schedule this stage."))
		except Exception:
			frappe.log_error(frappe.get_traceback(), "CRM email sequence: could not cancel a failed claim")

	# --- daily budget ---

	def count_sent_today(self) -> int:
		"""Email stages claimed today. A claim counts whether or not it delivered."""
		start_of_day = frappe.utils.get_datetime_str(
			frappe.utils.get_datetime(frappe.utils.today())
		)
		return frappe.db.count(
			outbound.JOB_DOCTYPE,
			{
				"job_type": JOB_TYPE_SEQUENCE,
				"creation": [">=", start_of_day],
				"state": ["!=", outbound.JOB_CANCELLED],
			},
		)


# --- rendering -------------------------------------------------------------


def template_body(template) -> str:
	"""The HTML body of an Email Template, whichever field it was authored in."""
	if template.get("use_html"):
		return frappe.utils.cstr(template.get("response_html") or "")
	return frappe.utils.cstr(template.get("response") or "")


def lead_context(lead: str) -> dict:
	"""Whitelisted lead fields, HTML-escaped, for the template to interpolate.

	Escaped because the body is sent as HTML and these values were typed by hand:
	a customer called `A & B Travel` must not break the markup of their own
	follow-up. Empty fields become empty strings rather than `None`, so a template
	that names one renders a gap instead of the word "None".
	"""
	row = frappe.db.get_value(LEAD_DOCTYPE, lead, list(TEMPLATE_FIELDS), as_dict=True) or {}
	context = {
		key: frappe.utils.escape_html(frappe.utils.cstr(value)) if value not in (None, "") else ""
		for key, value in row.items()
	}
	context["first_name"] = context.get("first_name") or context.get("lead_name") or _("there")
	context["destination"] = context.get("destination") or _("your trip")
	return context


def one_line(value) -> str:
	return " ".join(frappe.utils.cstr(value).split())


def with_unsubscribe_footer(body: str, link: str) -> str:
	"""Append the tokenised unsubscribe line. Every sequence email carries one."""
	if not link:
		return body

	footer = (
		'<p style="margin-top:24px;font-size:12px;color:#8d8d8d">'
		f'<a href="{frappe.utils.escape_html(link)}">{_("Unsubscribe")}</a>'
		f" &middot; {_('You are receiving this because you enquired with us.')}"
		"</p>"
	)
	return f"{body}{footer}"


def sending_user(engine, lead: str) -> str:
	"""Who the job sends as. Re-checked at execution time by the outbound engine.

	The lead's own assignee, falling back to its owner and then to Administrator.
	`crm.api.email.email_adapter` switches to this user before it calls
	`send_email`, so the permission check that reaches `make` is the check on the
	person the agency would have had send it by hand.
	"""
	for user in engine.get_lead_assignees(lead) or []:
		if user and frappe.db.get_value("User", user, "enabled"):
			return user
	return "Administrator"


# --- reply stop ------------------------------------------------------------


def handle_inbound_reply(doc, method=None) -> str | None:
	"""`after_insert` on Communication: a customer's email stops their sequence.

	Two matches, in order, because neither one alone is enough.

	1. **The header.** `crm.outbound.match_reply` finds the recipient row this
	   reply answers by Message-ID / In-Reply-To, never by subject -- two
	   campaigns produce the same "Re:" line and the first collision stops the
	   wrong customer's sequence.
	2. **The address.** Mail clients strip and rewrite headers, and a customer who
	   starts a NEW thread instead of replying answers us just as clearly. So any
	   inbound mail from the lead's normalised address stops the sequence too.
	   `custom_parama_email_normalized` (F7) makes that an indexed equality
	   lookup rather than a scan.

	Gated on `email_sequences_enabled`: with the flag off this returns before
	reading a single row, so a site that has not switched sequences on behaves
	exactly as it did before this stage.

	Never raises. It runs inside the insert of the customer's own email, and an
	exception here would lose that email.
	"""
	try:
		if not sequence_enabled():
			return None

		if doc.get("sent_or_received") != "Received":
			return None

		lead = lead_from_reply(doc)
		if not lead:
			return None

		return stop_sequence(lead, doc)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM email sequence: reply stop failed")
		return None


def lead_from_reply(doc) -> str | None:
	"""The lead a received email belongs to, by header first, then by address."""
	lead = lead_from_header(doc)
	if lead:
		return lead

	return lead_from_address(doc.get("sender"))


def lead_from_header(doc) -> str | None:
	"""The lead behind the message this reply answers, or None.

	`crm.api.email.reply_message_ids` is reused rather than copied: it already
	knows that the framework stores a Communication's `message_id` WITHOUT the
	angle brackets while `crm.outbound` matches the bracketed form.
	"""
	from crm.api.email import reply_message_ids

	row = None
	parent = doc.get("in_reply_to")
	if parent:
		matches = frappe.get_all(
			outbound.RECIPIENT_DOCTYPE,
			filters={"communication": parent},
			fields=["name", "job"],
			order_by="creation desc",
			limit_page_length=1,
		)
		row = matches[0] if matches else None

	if not row:
		row = outbound.match_reply(in_reply_to=" ".join(reply_message_ids(doc)))

	if not row:
		return None

	job = frappe.db.get_value(
		outbound.JOB_DOCTYPE, row["job"], ["job_type", "reference_doctype", "reference_name"], as_dict=True
	)
	if not job or job.job_type != JOB_TYPE_SEQUENCE or job.reference_doctype != LEAD_DOCTYPE:
		return None

	outbound.record_reply(row["name"], frappe.utils.cstr(doc.get("in_reply_to") or ""))
	return job.reference_name


def lead_from_address(sender) -> str | None:
	"""The lead whose normalised email matches this sender, or None."""
	from crm.contact_keys import EMAIL_FIELD

	address = normalize_email(sender)
	if not address:
		return None

	rows = frappe.get_all(
		LEAD_DOCTYPE,
		filters={EMAIL_FIELD: address},
		pluck="name",
		order_by="modified desc",
		limit_page_length=1,
	)
	return rows[0] if rows else None


def stop_sequence(lead: str, doc) -> str | None:
	"""Move the lead's follow-up row to Replied. The same state machine as WhatsApp.

	An opted-out row is left alone: consent withdrawn is not downgraded by a later
	message, which is the rule `crm.api.followup_engine.handle_incoming` already
	applies on the other channel.
	"""
	from crm.api import followup_engine as engine

	name = frappe.db.get_value(engine.FOLLOWUP_DOCTYPE, {"lead": lead}, "name")
	if not name:
		return None

	row = engine.lock_followup(name)
	if row.state in engine.TERMINAL_STATES:
		return None

	stamp = doc.get("communication_date") or doc.get("creation") or frappe.utils.now_datetime()
	if row.state == engine.STATE_REPLIED:
		row.db_set("last_customer_message", stamp, update_modified=False)
		return row.name

	row.db_set(
		{
			"state": engine.STATE_REPLIED,
			"last_customer_message": stamp,
			"next_due": None,
			"pending_stage": 0,
			"pending_params": None,
		},
		update_modified=False,
	)
	return row.name
