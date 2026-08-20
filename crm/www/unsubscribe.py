"""`/unsubscribe?token=...` -- the page a customer lands on, as Guest.

Why a www page and not a whitelisted method
-------------------------------------------
The person clicking has no account and never will. A whitelisted `allow_guest`
method answers with JSON, and a customer who withdraws consent is owed a page
that says so in words. A www page is also the surface Frappe already serves to
Guest without an API key, a CSRF token or a session.

Authorization (master spec §3): the route is PUBLIC by design and the token IS
the authorization. It is an HMAC over the address, signed with the site's own
key, so it cannot be guessed for somebody else's address or forged for an
address we never wrote to. Nothing about the caller widens what the route does:
a valid token suppresses exactly one address on exactly one channel, and an
invalid one is answered identically whether or not the address exists.

Rate limited per IP (`crm.sequences.unsubscribe.over_rate_limit`). That is depth
rather than the defence -- the token is already unguessable -- but it stops a
script from turning a public route into free work.

Known limitation, recorded rather than hidden: this is a GET that writes. A mail
client or link scanner that prefetches links will therefore unsubscribe the
customer on their behalf. The failure is in the safe direction (a message not
sent, never a message sent), and RFC 8058 one-click POST is not advertised
because this route does not accept POST. See
`demo-package/specs/stage5-1-notes.md`.
"""

import frappe

from crm.sequences import unsubscribe

no_cache = 1


def get_context(context):
	"""Answer one click. Never raises: an unauthenticated visitor gets a page."""
	context.no_cache = 1

	token = frappe.form_dict.get("token")
	outcome = unsubscribe.handle(token)

	context.result = outcome["result"]
	context.address = outcome["address"]
	context.update(unsubscribe.page_text(outcome["result"], outcome["address"]))

	if outcome["result"] in (unsubscribe.RESULT_DONE, unsubscribe.RESULT_ALREADY):
		# The framework rolls a GET back unless it is told otherwise, and the
		# ledger row is the entire point of the route.
		frappe.local.flags.commit = True
	elif outcome["result"] == unsubscribe.RESULT_RATE_LIMITED:
		context.http_status_code = 429
		frappe.local.response["http_status_code"] = 429

	return context
