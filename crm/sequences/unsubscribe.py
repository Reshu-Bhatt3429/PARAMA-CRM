"""The unsubscribe link every sequence email carries, and the page it lands on.

Why a signed token and not a row
--------------------------------
An unsubscribe link is handed to the open internet. Two properties decide
whether it is safe: it must be impossible to GUESS one for somebody else's
address (no enumeration), and it must be impossible to FORGE one for an address
we never wrote to (no denial of service by suppressing a competitor's mailbox).

Both are the same property -- authenticity -- so the token is an HMAC over the
address, signed with the site's own encryption key. Nothing is stored when a
link is minted, so there is no table to enumerate, no row to expire, and no
extra write on the send path. The address rides inside the token because the
token only ever travels to that address: the link in Ann's mail is Ann's link,
and anyone holding it already holds her mail.

What the link does NOT do: it never reveals whether an address is known to the
CRM. An unknown-but-correctly-signed address is suppressed exactly like a known
one, and the page says the same thing either way.

The ledger is the effect
------------------------
A click writes `crm.suppression` with channel Email and source `unsubscribe_link`
(master spec F1). That single row is what every other send path already reads --
the composer, Send Later, the web-form auto-response, and the sequence itself at
claim time and again at send time. Nothing here touches the sequence row; the
next due step simply finds a suppressed address and parks with a stated reason.

The List-Unsubscribe header
---------------------------
Frappe v15 has no unsubscribe-header machinery of its own (there is no
`List-Unsubscribe` anywhere in the framework), and `crm.api.email.send_email`
goes through `frappe.core.doctype.communication.email.make`, which takes no
header argument. So the header is added where the message actually becomes MIME:
one `before_insert` hook on Email Queue, armed for the length of one adapter call
by `crm.outbound.deliver_recipient` when the job's payload carries an
`unsubscribe_url`. A message that is not a sequence send never sees the hook do
anything, because the flag is not set.

RFC 8058 one-click (`List-Unsubscribe-Post`) is deliberately NOT advertised: it
requires the URL to accept POST, and this route answers GET. See
`demo-package/specs/stage5-1-notes.md`.

Error contract: `handle` is reached by an unauthenticated GET and never raises.
`add_list_unsubscribe_header` runs inside an Email Queue insert and never raises;
a missing header must not cost the agency the email.
"""

import base64
import hashlib
import hmac
import json
from urllib.parse import quote

import frappe
from frappe import _

from crm import suppression
from crm.normalization import normalize_email

# Set by `crm.outbound.deliver_recipient` for the length of one adapter call, and
# read by `add_list_unsubscribe_header` below. Imported rather than repeated: two
# copies of a flag name is a bug that shows up only as a silently missing
# compliance header.
from crm.outbound import UNSUBSCRIBE_FLAG as FLAG_UNSUBSCRIBE_URL

# Bumped only if the payload shape changes. A token minted under an older
# version stops verifying, which is the correct outcome: the shape it describes
# is no longer the shape we read.
TOKEN_VERSION = "u1"

# Truncated to 16 bytes. That is 128 bits of forgery resistance, which is far
# beyond what a link in an email needs, and it keeps the URL short enough to
# survive a mail client's line wrapping.
SIGNATURE_BYTES = 16

# The source line written into the suppression ledger. Master spec F1 wants the
# ledger to say WHERE consent was withdrawn; this exact string is what the
# acceptance criteria and `crm/tests/test_email_sequences.py` assert.
UNSUBSCRIBE_SOURCE = "unsubscribe_link"

ROUTE = "/unsubscribe"

# One IP may try this many tokens in this many seconds. The token is already
# unguessable, so this is depth rather than the defence itself: it stops a
# scripted client from turning the route into free CPU.
RATE_LIMIT = 10
RATE_WINDOW_SECONDS = 300

# Outcomes the page renders. Kept as constants because the template branches on
# them and a typo in a template is a silently blank page.
RESULT_DONE = "done"
RESULT_ALREADY = "already"
RESULT_INVALID = "invalid"
RESULT_RATE_LIMITED = "rate_limited"


# --- the token -------------------------------------------------------------


def _secret() -> bytes:
	"""The site's own key. Generated and stored by the framework on first use."""
	from frappe.utils.password import get_encryption_key

	return frappe.utils.cstr(get_encryption_key()).encode()


def _b64(raw: bytes) -> str:
	return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
	return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(body: str) -> str:
	return _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest()[:SIGNATURE_BYTES])


def make_token(address, reference_doctype: str | None = None, reference_name: str | None = None) -> str:
	"""Mint one unsubscribe token, or "" when the address cannot be normalised.

	An address that does not normalise has nothing to suppress later, so a token
	for it would be a link that fails when the customer clicks it. Better to have
	no footer than a broken one.
	"""
	normalized = normalize_email(address)
	if not normalized:
		return ""

	payload = json.dumps(
		{
			"v": TOKEN_VERSION,
			"a": normalized,
			"dt": reference_doctype or "",
			"dn": reference_name or "",
		},
		separators=(",", ":"),
		sort_keys=True,
	)
	body = _b64(payload.encode())
	return f"{body}.{_sign(body)}"


def read_token(token) -> dict | None:
	"""The payload of a token this site signed, or None. Never raises.

	Every failure -- wrong shape, wrong signature, wrong version, an address that
	no longer normalises -- returns None and is indistinguishable from the others.
	A route that answered differently for a well-formed-but-unsigned token would
	tell an attacker their guessing was getting warmer.
	"""
	try:
		body, _sep, signature = frappe.utils.cstr(token).strip().partition(".")
		if not body or not signature:
			return None

		if not hmac.compare_digest(signature, _sign(body)):
			return None

		payload = json.loads(_unb64(body).decode())
		if not isinstance(payload, dict) or payload.get("v") != TOKEN_VERSION:
			return None

		address = normalize_email(payload.get("a"))
		if not address:
			return None

		return {
			"address": address,
			"reference_doctype": payload.get("dt") or None,
			"reference_name": payload.get("dn") or None,
		}
	except Exception:
		return None


def unsubscribe_url(token: str) -> str:
	"""The absolute link that goes in the footer and in the header."""
	if not token:
		return ""
	return f"{frappe.utils.get_url(ROUTE)}?token={quote(token, safe='')}"


def link_for(address, reference_doctype: str | None = None, reference_name: str | None = None) -> str:
	"""One call for the send path: address in, absolute URL out ("" when none)."""
	return unsubscribe_url(make_token(address, reference_doctype, reference_name))


# --- rate limiting ---------------------------------------------------------


def request_ip() -> str:
	"""The caller's address, or "" outside a request."""
	return frappe.utils.cstr(getattr(frappe.local, "request_ip", "") or "")


def over_rate_limit(ip: str) -> bool:
	"""True when this IP has spent its window. Fails OPEN, deliberately.

	A cache that is down must not stop a customer withdrawing consent -- refusing
	a genuine unsubscribe is a compliance failure, while letting a few extra
	requests through is a load problem. The counter is `incrby`, so it is atomic
	even with several workers answering at once.
	"""
	if not ip:
		return False

	try:
		key = f"crm-unsubscribe-hits:{ip}"
		hits = frappe.cache.incrby(key, 1)
		if hits == 1:
			frappe.cache.expire(key, RATE_WINDOW_SECONDS)
		return frappe.utils.cint(hits) > RATE_LIMIT
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM unsubscribe: rate limiter unavailable")
		return False


# --- the click -------------------------------------------------------------


def handle(token, ip: str | None = None) -> dict:
	"""Answer one click. Returns what the page renders. Never raises.

	The suppression write is idempotent (`crm.suppression.suppress` folds a repeat
	into the existing row and keeps the original `suppressed_at`), so a customer
	who clicks twice, or whose mail client follows the link once on its own, gets
	the same row and the same page.
	"""
	ip = request_ip() if ip is None else ip

	if over_rate_limit(ip):
		return {"result": RESULT_RATE_LIMITED, "address": ""}

	payload = read_token(token)
	if not payload:
		return {"result": RESULT_INVALID, "address": ""}

	address = payload["address"]

	try:
		already = bool(suppression.get_suppression(suppression.CHANNEL_EMAIL, address))
		suppression.suppress(
			suppression.CHANNEL_EMAIL,
			address,
			state=suppression.STATE_OPTED_OUT,
			source=UNSUBSCRIBE_SOURCE,
			reference_doctype=payload.get("reference_doctype"),
			reference_name=payload.get("reference_name"),
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM unsubscribe: ledger write failed")
		return {"result": RESULT_INVALID, "address": ""}

	return {"result": RESULT_ALREADY if already else RESULT_DONE, "address": address}


def page_text(result: str, address: str) -> dict:
	"""The two lines the confirmation page shows, per outcome."""
	if result == RESULT_DONE:
		return {
			"heading": _("You have been unsubscribed"),
			"message": _("We will not send any more email to {0}.").format(address),
		}
	if result == RESULT_ALREADY:
		return {
			"heading": _("You are already unsubscribed"),
			"message": _("{0} was already removed from our email list.").format(address),
		}
	if result == RESULT_RATE_LIMITED:
		return {
			"heading": _("Too many requests"),
			"message": _("Please wait a few minutes and open the link again."),
		}
	return {
		"heading": _("This link is not valid"),
		"message": _(
			"The link may be incomplete or out of date. Reply to any of our emails and we will remove you."
		),
	}


# --- the List-Unsubscribe header -------------------------------------------

HEADER = "List-Unsubscribe"


def add_list_unsubscribe_header(doc, method=None) -> None:
	"""`before_insert` on Email Queue: add the header to a sequence send.

	The queue row already carries the built MIME string, and a header line may sit
	anywhere in the header block (RFC 5322 §3.6), so prepending one is valid and
	leaves the rest of the message byte-identical.

	Never raises. A missing compliance header is a problem; an exception here
	would lose the whole email, which is a worse one.
	"""
	try:
		url = frappe.utils.cstr(frappe.local.flags.get(FLAG_UNSUBSCRIBE_URL) or "")
		if not url:
			return

		message = frappe.utils.cstr(doc.get("message") or "")
		if not message or f"{HEADER}:" in message[:4000]:
			return

		doc.message = f"{HEADER}: <{url}>\r\n{message}"
	except Exception:
		frappe.log_error(frappe.get_traceback(), "CRM unsubscribe: List-Unsubscribe header failed")
