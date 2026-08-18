"""The composer's send path, with the consent ledger in front of it.

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

Endpoint authorization (master spec §3), stated here and in
`crm/tests/test_email_compose.py`:

* `send_email` -- POST only, any signed-in user. Row-level scope is derived
  SERVER-side by `make`, which checks the `email` permission on the named
  reference record (and so runs the org-hierarchy `has_permission` hook for
  CRM Lead and CRM Deal). Nothing about the recipient list widens that scope.
"""

import frappe
from frappe import _

from crm.suppression import CHANNEL_EMAIL, is_suppressed


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
