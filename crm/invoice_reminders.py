"""Payment reminders, one ladder per schedule row (master spec §5 item 29).

The shape of the feature
------------------------
A travel invoice is usually not one payment. It is a deposit now and a balance
before departure, and the two are chased separately: a customer who has paid the
deposit must stop hearing about the deposit and must still hear about the
balance. So the ladder belongs to the SCHEDULE ROW, not to the invoice, and
`CRM Invoice.settle_schedule_rows` marks the row a payment named. That single
flag is what makes acceptance criterion 5 true.

Each row fires at its due date, then seven days later, then fourteen -- the
default the design note takes from HubSpot. Every step is a separate
`CRM Outbound Job` with its own idempotency key, so a step is sent at most once
whatever the scheduler does, and a step that has already gone cannot be resent by
running the sweep again.

Nothing here sends. It creates jobs on the Stage 1 outbound machine and lets
`crm.outbound` deliver them, which is what buys the claim-commit-send guarantee,
the row locks and the delivery-state read-back for free.

Two flags, and what each one stops
----------------------------------
| Flag | Off means |
| --- | --- |
| `invoice_reminders_enabled` | This sweep returns without reading one invoice row. No ladder exists. |
| `invoices_enabled` | The module is off entirely; reminding about invoices it will not let anybody open would be absurd, so this sweep stops too. |
| `outbound_engine_enabled` | A job that exists is never claimed, so nothing is delivered. |

All three are OFF by default and all three are needed before a customer is
reminded of anything.

The catch-up window
-------------------
A step is fired only when its moment fell inside the last `LOOKBACK_DAYS`. That
is what stops the day the flag is switched on from replaying a year of history
into a customer's inbox -- the same reasoning, and the same failure, as
`crm.reminders.LOOKBACK_MINUTES`.

Authorization: no whitelisted endpoint. Reached from the scheduler only. It reads
with `frappe.get_all` (no permission conditions) because nothing it reads reaches
a request: the only thing it does with an invoice is email the customer whose
invoice it already is. `crm.outbound.execute_job` re-checks `owner_user` AT
EXECUTION TIME and `crm.outbound.deliver_recipient` re-checks suppression inside
the recipient's row lock.

Error contract: `send_invoice_reminders` runs unattended and never raises.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from crm import invoicing, outbound, suppression
from crm.feature_flags import is_enabled

FLAG_INVOICE_REMINDERS = "invoice_reminders_enabled"
FLAG_INVOICES = "invoices_enabled"

JOB_TYPE = "Invoice Reminder"

# Due date, then a week, then a fortnight. The design note's default ladder.
OFFSET_DAYS = (0, 7, 14)

# How far back a switched-on sweep will reach. Long enough to survive a weekend
# of scheduler downtime, short enough that turning the feature on does not mail a
# customer about an instalment they settled in March.
LOOKBACK_DAYS = 7

# The most invoices one run will look at. Ordered by due date, so the most urgent
# are always the ones that fit.
SWEEP_LIMIT = 200

FOLLOWUP_SETTINGS = "CRM Followup Settings"


def reminder_key(invoice: str, schedule_row: str, offset_days: int) -> str:
	"""The identity of one step of one row's ladder.

	Carries the row, not just the invoice: two schedule rows on one invoice are
	two ladders, and a key without the row would let the deposit reminder spend
	the balance reminder's slot.
	"""
	return f"invoice-{invoice}-row-{schedule_row}-day-{cint(offset_days)}"


def quiet_settings():
	"""The quiet-hours window. Shared with the follow-up engine, on purpose.

	An agency configures "do not message customers at night" once. A second
	window that only invoices obeyed would be a setting nobody knew they had.
	"""
	return frappe.get_cached_doc(FOLLOWUP_SETTINGS)


def send_at(now):
	"""When a reminder decided on now may actually go out.

	Inside quiet hours the job is SCHEDULED for the moment the window closes
	rather than skipped. Skipping would mean the step is never sent at all: its
	moment has passed and the next sweep would find it outside the catch-up
	window.
	"""
	from crm.sequences.core import in_quiet_hours, quiet_hours_end_after

	settings = quiet_settings()
	if in_quiet_hours(now, settings):
		return quiet_hours_end_after(now, settings)
	return now


def due_steps(row, today) -> list[int]:
	"""Which steps of one row's ladder have come due, inside the catch-up window."""
	due_date = row.get("due_date")
	if not due_date:
		return []

	today = frappe.utils.getdate(today)
	due_date = frappe.utils.getdate(due_date)

	steps = []
	for offset in OFFSET_DAYS:
		moment = frappe.utils.add_days(due_date, offset)
		if moment > today:
			continue
		if frappe.utils.date_diff(today, moment) > LOOKBACK_DAYS:
			continue
		steps.append(offset)
	return steps


def open_invoices(limit: int = SWEEP_LIMIT) -> list[dict]:
	"""Invoices that still owe money and are not paused. Most urgent first."""
	return frappe.get_all(
		invoicing.INVOICE_DOCTYPE,
		filters={
			"status": ["in", list(invoicing.OPEN_STATUSES)],
			"reminders_paused": 0,
		},
		fields=["name", "invoice_number", "deal", "customer_name", "customer_email", "currency", "owner"],
		order_by="due_date asc",
		limit_page_length=limit,
	)


def schedule_rows(invoice: str) -> list[dict]:
	"""The unsettled, unpaused schedule rows of one invoice."""
	return frappe.get_all(
		"CRM Invoice Schedule",
		filters={"parenttype": invoicing.INVOICE_DOCTYPE, "parent": invoice, "settled": 0, "reminders_paused": 0},
		fields=["name", "label", "due_date", "amount"],
		order_by="due_date asc",
		limit_page_length=0,
	)


def owner_for(invoice: dict) -> str:
	"""Who the reminder is sent on behalf of. Re-checked at execution time.

	The deal's owner, because that is the agent whose name the customer knows.
	The invoice's own creator is the fallback for a deal with no owner.
	"""
	if invoice.get("deal"):
		deal_owner = frappe.db.get_value(invoicing.DEAL_DOCTYPE, invoice["deal"], "deal_owner")
		if deal_owner:
			return deal_owner
	return invoice.get("owner") or "Administrator"


def live_link_url(invoice: str) -> str:
	"""The tokenised URL a SEND already minted for this invoice, or "".

	Strictly read-only. This never mints a link and never renders a PDF -- see the
	contract in `message_for`. It answers "is there already a live door the
	customer can walk through", and nothing else.

	A link counts as live only when all four hold: it is this invoice's, it has
	the Invoice purpose, it is still `active` (a re-send retires the previous
	token, and `expire_links` retires an expired one), and the private File it
	points at still exists. The last check matters because `expire_links` clears
	`file` before it deletes the document, so a row can be active-looking for the
	length of one sweep.
	"""
	from crm import document_links
	from crm.api.invoices import PURPOSE

	rows = frappe.get_all(
		document_links.LINK_DOCTYPE,
		filters={
			"reference_doctype": invoicing.INVOICE_DOCTYPE,
			"reference_name": invoice,
			"purpose": PURPOSE,
			"active": 1,
		},
		fields=["token", "expires_at", "file"],
		order_by="creation desc",
		limit_page_length=1,
	)
	if not rows:
		return ""

	link = rows[0]
	if not link.get("file") or not frappe.db.exists("File", link["file"]):
		return ""
	if link.get("expires_at") and frappe.utils.get_datetime(link["expires_at"]) < frappe.utils.now_datetime():
		return ""

	return document_links.public_url(link["token"], method="crm.api.invoices.view")


def message_for(invoice: dict, row: dict, offset_days: int) -> tuple[str, str]:
	"""The subject and body of one step. Plain, factual, and it names the row.

	The link contract
	-----------------
	This sweep NEVER mints a link and NEVER renders a PDF. Rendering a document
	inside an unattended hourly job would put wkhtmltopdf on the scheduler's
	critical path for every open invoice, every hour, for ever.

	What it does instead is REUSE a door that is already open: when the invoice
	carries a live tokenised share link -- one an agent minted at send time, still
	active, not expired, and still backed by its private file -- the body gains one
	line carrying that URL. When there is no such link the body is exactly what it
	was before: a reminder with the amount, the instalment and the due date, and no
	way to open the document. That is the weaker case, and it is the honest one --
	an invoice the agency never sent from the app has no link to give.

	So the reminder is as useful as the send that preceded it, and the sweep still
	costs one indexed read per invoice.
	"""
	currency = invoice.get("currency") or "INR"
	amount = frappe.utils.fmt_money(flt(row.get("amount")), currency=currency)
	number = invoice.get("invoice_number") or invoice.get("name")
	label = frappe.utils.cstr(row.get("label") or _("payment"))
	due = frappe.utils.formatdate(row.get("due_date"), "d MMM yyyy") if row.get("due_date") else ""

	if offset_days == 0:
		subject = _("{0} for invoice {1} is due today").format(label, number)
	else:
		subject = _("{0} for invoice {1} is {2} days overdue").format(label, number, offset_days)

	greeting = (
		_("Hi {0},").format(invoice["customer_name"]) if invoice.get("customer_name") else _("Hello,")
	)
	lines = [
		frappe.utils.escape_html(greeting),
		frappe.utils.escape_html(
			_("This is a reminder about the {0} of {1} on invoice {2}, due {3}.").format(
				label, amount, number, due
			)
		),
	]

	url = live_link_url(invoice["name"])
	if url:
		# The anchor is the only markup this function writes; the URL itself is
		# escaped on both sides of it, so a token can never break out of the
		# attribute or the text.
		safe = frappe.utils.escape_html(url)
		lines.append(
			_("You can open the invoice here: {0}").format(f'<a href="{safe}">{safe}</a>')
		)

	lines.append(frappe.utils.escape_html(_("If you have already paid, please ignore this message.")))

	return subject, "<br>".join(lines)


def queue_step(invoice: dict, row: dict, offset_days: int, now) -> str | None:
	"""Create and schedule one reminder job. Returns the job name, or None.

	`crm.outbound.create_job` is idempotent on the key, so a repeated sweep hands
	back the job that already exists and `schedule_job` then finds it past Draft
	and returns False. A step is therefore sent at most once, which is the same
	guarantee every other outbound path in this app makes.
	"""
	address = frappe.utils.cstr(invoice.get("customer_email") or "").strip()
	if not address:
		return None

	# Checked here as well as inside the delivery lock. Creating a job for an
	# address that has opted out would leave a row that looks like a pending send
	# for ever, and the master spec has no send path without a suppression check.
	if suppression.is_suppressed(outbound.CHANNEL_EMAIL, address):
		return None

	key = reminder_key(invoice["name"], row["name"], offset_days)
	existing = frappe.db.get_value(outbound.JOB_DOCTYPE, {"idempotency_key": key}, "name")
	if existing:
		return None

	subject, body = message_for(invoice, row, offset_days)

	job = outbound.create_job(
		job_type=JOB_TYPE,
		channel=outbound.CHANNEL_EMAIL,
		idempotency_key=key,
		recipients=[address],
		scheduled_at=send_at(now),
		subject=subject,
		payload={
			"doctype": invoicing.INVOICE_DOCTYPE,
			"name": invoice["name"],
			"recipients": [address],
			"subject": subject,
			"content": body,
		},
		owner_user=owner_for(invoice),
		reference_doctype=invoicing.INVOICE_DOCTYPE,
		reference_name=invoice["name"],
	)

	outbound.schedule_job(job.name)
	return job.name


def remind_about(invoice: dict, now) -> int:
	"""Queue every step that has come due on every live row of one invoice."""
	today = frappe.utils.getdate(now)
	queued = 0

	for row in schedule_rows(invoice["name"]):
		for offset in due_steps(row, today):
			if queue_step(invoice, row, offset, now):
				queued += 1

	return queued


def send_invoice_reminders() -> int:
	"""Scheduler entry point, hourly. Returns how many jobs were queued.

	Never raises: a scheduler job that throws takes the rest of its queue down
	with it. Both flags are read before anything else, and while either is off
	this function does not read a single invoice row.
	"""
	try:
		if not is_enabled(FLAG_INVOICE_REMINDERS) or not is_enabled(FLAG_INVOICES):
			return 0

		now = frappe.utils.now_datetime()
		queued = 0

		for invoice in open_invoices():
			try:
				queued += remind_about(invoice, now)
			except Exception:
				frappe.log_error(
					frappe.get_traceback(),
					f"CRM invoice reminders: invoice {invoice.get('name')} failed",
				)

		return queued
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM invoice reminders: sweep failed")
		return 0
