"""Consent and suppression ledger -- the one thing every outbound path asks.

Why it exists
-------------
A person who says "stop" says it once, in one place, and expects every channel
we own to obey. Before this ledger the only record of a WhatsApp opt-out was
the `Opted Out` state on that lead's follow-up row: invisible to the composer,
to mass email, to the web-form auto-response, and gone entirely for an address
with no lead. This module is the single source of truth those paths query.

Shape
-----
One row per (channel, normalised address), keyed by a unique `suppression_key`.
Suppressing an address that is already suppressed updates the existing row; it
never inserts a second one. A reversal does not delete the row, it clears
`active` and records who released it and why, so the history survives.

Addresses are normalised on the way in and on the way out (`crm.normalization`).
An address that cannot be normalised is never stored and never matches, so
`is_suppressed` answering False for garbage input is a deliberate outcome, not
an oversight -- a send path that cannot name its recipient has nothing to check.

Reversal discipline
-------------------
`unsuppress` mirrors `crm.api.followup_engine.reopen_optout`: a reason is
mandatory and an audit Comment is written. That is not ceremony. The WhatsApp
webhook is unauthenticated upstream, so an opt-out can be forged by anyone who
reaches the endpoint; suppression therefore has to be reversible, and a
reversible consent record is only safe when every reversal is attributable.

Error contract: `is_suppressed` is called from send paths that must not be taken
down by a ledger problem. It fails closed on an unusable answer -- see the
docstring there -- and never raises.
"""

import frappe
from frappe import _

from crm.normalization import normalize_email, normalize_phone

SUPPRESSION_DOCTYPE = "CRM Suppression"

CHANNEL_EMAIL = "Email"
CHANNEL_WHATSAPP = "WhatsApp"
CHANNELS = (CHANNEL_EMAIL, CHANNEL_WHATSAPP)

STATE_OPTED_OUT = "Opted Out"
STATE_BOUNCED = "Bounced"
STATE_COMPLAINED = "Complained"
STATES = (STATE_OPTED_OUT, STATE_BOUNCED, STATE_COMPLAINED)

# A source line is an audit trail, not a payload. Long enough to identify the
# message or report it came from, short enough that a forged inbound message
# cannot fill the table.
MAX_SOURCE_LENGTH = 200


def normalize_address(channel: str, address) -> str:
	"""The stored form of an address on a channel, or an empty string."""
	if channel == CHANNEL_EMAIL:
		return normalize_email(address)
	if channel == CHANNEL_WHATSAPP:
		return normalize_phone(address)
	return ""


def suppression_key(channel: str, address: str) -> str:
	"""The unique key. Built from the NORMALISED address only."""
	return f"{channel}:{address}"


def is_suppressed(channel: str, address) -> bool:
	"""True when this address must not be contacted on this channel.

	Called on every recipient of every send, so it never raises: a ledger that
	errors must not turn into an unsent campaign or a crashed queue worker. It
	fails CLOSED -- an unreadable ledger returns True -- because the cost of one
	held-back message is far below the cost of one message to somebody who opted
	out.
	"""
	try:
		normalized = normalize_address(channel, address)
		if not normalized:
			# Nothing to check against. A send path that cannot name its recipient
			# fails on its own; this is not the place to guess.
			return False

		return bool(
			frappe.db.get_value(
				SUPPRESSION_DOCTYPE,
				{"suppression_key": suppression_key(channel, normalized), "active": 1},
				"name",
			)
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM suppression: ledger read failed")
		return True


def get_suppression(channel: str, address) -> dict | None:
	"""The active suppression row for an address, or None."""
	normalized = normalize_address(channel, address)
	if not normalized:
		return None

	return frappe.db.get_value(
		SUPPRESSION_DOCTYPE,
		{"suppression_key": suppression_key(channel, normalized), "active": 1},
		["name", "channel", "address", "state", "source", "suppressed_at"],
		as_dict=True,
	)


def suppress(
	channel: str,
	address,
	state: str = STATE_OPTED_OUT,
	source: str = "",
	reference_doctype: str | None = None,
	reference_name: str | None = None,
	when=None,
) -> str | None:
	"""Record that an address must not be contacted. Idempotent.

	Returns the row name, or None when the address could not be normalised --
	there is nothing to key a row on, and a row keyed on a raw value would never
	be found by a send path.

	An address suppressed again while already suppressed keeps its original
	`suppressed_at`: the date consent was withdrawn is the interesting one, and a
	repeated bounce should not make an old opt-out look recent.
	"""
	if channel not in CHANNELS:
		frappe.throw(_("Unknown suppression channel {0}.").format(channel))
	if state not in STATES:
		frappe.throw(_("Unknown suppression state {0}.").format(state))

	normalized = normalize_address(channel, address)
	if not normalized:
		return None

	key = suppression_key(channel, normalized)
	stamp = when or frappe.utils.now_datetime()
	trimmed_source = frappe.utils.strip_html(frappe.utils.cstr(source))[:MAX_SOURCE_LENGTH]

	existing = frappe.db.get_value(SUPPRESSION_DOCTYPE, {"suppression_key": key}, "name")
	if existing:
		return _reapply(existing, state, trimmed_source, stamp, reference_doctype, reference_name)

	doc = frappe.new_doc(SUPPRESSION_DOCTYPE)
	doc.update(
		{
			"channel": channel,
			"address": normalized,
			"suppression_key": key,
			"state": state,
			"active": 1,
			"source": trimmed_source,
			"suppressed_at": stamp,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
		}
	)

	try:
		doc.insert(ignore_permissions=True)
	except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
		# Another worker claimed the key between the read and the insert. Its row
		# is as good as ours, so fold this call into it rather than failing the
		# opt-out that triggered us.
		existing = frappe.db.get_value(SUPPRESSION_DOCTYPE, {"suppression_key": key}, "name")
		if not existing:
			raise
		return _reapply(existing, state, trimmed_source, stamp, reference_doctype, reference_name)

	return doc.name


def _reapply(
	name: str,
	state: str,
	source: str,
	stamp,
	reference_doctype: str | None,
	reference_name: str | None,
) -> str:
	"""Re-arm an existing row without losing when consent was first withdrawn."""
	row = frappe.db.get_value(SUPPRESSION_DOCTYPE, name, ["active", "suppressed_at"], as_dict=True)

	update = {"active": 1, "state": state}
	if source:
		update["source"] = source
	if reference_doctype and reference_name:
		update["reference_doctype"] = reference_doctype
		update["reference_name"] = reference_name
	if not row or not row.active or not row.suppressed_at:
		# Newly re-suppressed after a release, or a legacy row with no stamp.
		update["suppressed_at"] = stamp
		update["released_at"] = None
		update["released_by"] = None
		update["release_reason"] = None

	frappe.db.set_value(SUPPRESSION_DOCTYPE, name, update, update_modified=False)
	return name


def unsuppress(channel: str, address, reason: str) -> str | None:
	"""Reverse a suppression. Needs a reason, and writes an audit comment.

	Returns the row name, or None when the address is not suppressed. Reversing
	does not resume any sequence by itself -- it only stops the ledger from
	blocking a send a human decides to make.
	"""
	if not (reason or "").strip():
		frappe.throw(_("Give a reason for lifting this suppression."))

	normalized = normalize_address(channel, address)
	if not normalized:
		return None

	name = frappe.db.get_value(
		SUPPRESSION_DOCTYPE, {"suppression_key": suppression_key(channel, normalized), "active": 1}, "name"
	)
	if not name:
		return None

	previous = frappe.db.get_value(SUPPRESSION_DOCTYPE, name, "source")
	clean_reason = frappe.utils.strip_html(frappe.utils.cstr(reason))[:MAX_SOURCE_LENGTH]

	frappe.db.set_value(
		SUPPRESSION_DOCTYPE,
		name,
		{
			"active": 0,
			"released_at": frappe.utils.now_datetime(),
			"released_by": frappe.session.user,
			"release_reason": clean_reason,
		},
		update_modified=False,
	)

	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": SUPPRESSION_DOCTYPE,
			"reference_name": name,
			"content": _("Suppression lifted by {0}. Reason: {1}. Previous source: {2}").format(
				frappe.session.user, clean_reason, previous or _("unknown")
			),
		}
	).insert(ignore_permissions=True)

	return name


def filter_suppressed(channel: str, addresses) -> tuple[list[str], list[str]]:
	"""Split addresses into (allowed, suppressed), both normalised and de-duped.

	One query for the whole list. A mass send that checked row by row would issue
	one query per recipient, and the batch sizes this has to serve make that the
	difference between a job and an outage.
	"""
	normalized = []
	seen = set()
	for address in addresses or []:
		value = normalize_address(channel, address)
		if value and value not in seen:
			seen.add(value)
			normalized.append(value)

	if not normalized:
		return [], []

	blocked = set(
		frappe.get_all(
			SUPPRESSION_DOCTYPE,
			filters={
				"channel": channel,
				"active": 1,
				"address": ["in", normalized],
			},
			pluck="address",
		)
	)

	return [a for a in normalized if a not in blocked], [a for a in normalized if a in blocked]
