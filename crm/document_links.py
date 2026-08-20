"""Customer-facing documents: render them, attach them, and hand out a link.

Why this module exists
----------------------
The itinerary feature grew a working but narrow version of all of this: render a
print format to a PDF, write it as a File on the record, and -- for a WhatsApp
send -- write a SECOND copy that is public so the messaging platform can fetch
it, then sweep that copy away an hour or two later. Item 25 (quote PDF) needs the
same machinery, and the master spec says plainly that the public-File approach is
not sufficient for it: a quote has to be reachable through a token-gated route
that logs who read it and when.

So the machinery is extracted here and upgraded here, and both features use it.
The itinerary's own behaviour is deliberately unchanged by the extraction -- the
functions it used still do exactly what they did, byte for byte, and its 112
tests are the check on that.

The two ways a customer receives a document
-------------------------------------------
1. **A public File copy** (`attach_pdf(is_private=0)` + `cleanup_public_pdfs`).
   Cheap, and readable by anyone who learns the URL for as long as the file
   exists. This is what the itinerary send does today. Nothing new uses it.

2. **A tokenised link** (`create_link` + `record_view`). The file stays PRIVATE
   and a whitelisted route streams it after checking the token. The link expires,
   can be revoked, and every fetch is written down. This is what item 25 uses,
   and what any future customer-facing document should use.

Reading a view log honestly
---------------------------
A messaging platform fetches the media itself, from its own servers, seconds
after the send. If that fetch were counted the agent would be told the customer
opened the quote before the message was even delivered, and would stop believing
the signal entirely. `classify_fetch` therefore flags a fetch as the platform's
when its user agent looks like a platform crawler AND it is the FIRST fetch on
the link. Both halves matter: the user agent alone would let a customer who
happens to preview the link in a chat app be written off as a bot for ever, and
"first fetch" alone would discard the customer's own first read whenever the
platform never came.

Error contract: `cleanup_public_pdfs` and `record_view` run where a raised
exception is worse than a lost row. Neither raises.
"""

import contextlib
import json
import re

import frappe
from frappe import _
from frappe.utils import cint

LINK_DOCTYPE = "CRM Document Link"
VIEW_DOCTYPE = "CRM Document Link View"

# The random suffix a send copy carries. Long enough that the URL cannot be
# guessed from the customer's name, and the marker the sweep looks for.
PUBLIC_TOKEN_LENGTH = 24

# The token in a public link. Longer than the file token because it is the ONLY
# thing standing between the internet and the document.
LINK_TOKEN_LENGTH = 32

# How long a tokenised quote link stays readable. Long enough that a customer who
# opens the message the next morning still sees the document, short enough that a
# forwarded chat does not become a permanent public URL.
LINK_TTL_HOURS = 24 * 14

# How long the temporary public PDF stays reachable after a WhatsApp send.
# Unchanged from the itinerary's own constant -- see `crm.api.itinerary`.
PUBLIC_PDF_TTL_HOURS = 2

# `-v<version>-<token>.pdf`. The trailing `,` in the repetition is not slack for
# its own sake: when a file of the same name already exists, Frappe appends six
# hex characters of the content hash, and a fixed-length match would then miss
# the very file it wrote.
SEND_COPY_PATTERN = re.compile(rf"-v\d+-[0-9a-f]{{{PUBLIC_TOKEN_LENGTH},}}\.pdf$", re.IGNORECASE)

# User agents that mean "a platform is prefetching this", not "a person opened
# it". Matched case-insensitively as substrings; the list is short on purpose,
# because a wrong entry here silently hides a real customer open.
PLATFORM_AGENTS = (
	"facebookexternalhit",
	"whatsapp",
	"facebot",
	"twitterbot",
	"telegrambot",
	"slackbot",
	"linkedinbot",
	"skypeuripreview",
	"discordbot",
	"bingbot",
	"googlebot",
)

MAX_USER_AGENT_LENGTH = 400


def log_quietly(message: str, title: str):
	"""Write an error log, and give up silently if even that fails.

	`frappe.log_error` reads and writes the database, so whatever broke the
	caller can break the logging too. A function promising never to raise cannot
	make that promise if its own error handler can throw.
	"""
	with contextlib.suppress(Exception):
		frappe.log_error(message, title)


# --- rendering -------------------------------------------------------------


def ensure_print_format(print_format: str, installer) -> None:
	"""Install a print format on first use.

	The `after_migrate` hook is the normal installer. This is the fallback for a
	site that has not migrated since the format was added, so the first PDF
	renders instead of failing with "no such print format".
	"""
	if not frappe.db.exists("Print Format", print_format):
		installer()


def render_print_pdf(doctype: str, name: str, print_format: str, doc=None) -> bytes:
	"""One print format, one document, one PDF. No side effects.

	`doc` is passed through so a caller can render a document it has decorated in
	memory -- the quote sets its terms on the deal object it renders and does not
	write them to the record, because a quote's terms are a property of the quote
	and not of the deal.
	"""
	return frappe.get_print(
		doctype,
		name,
		print_format=print_format,
		as_pdf=True,
		no_letterhead=1,
		doc=doc,
	)


def pdf_file_name(stem: str, version: int = 1, token: str = "") -> str:
	"""The customer-facing file name, optionally with an unguessable suffix.

	A private attachment keeps the readable name: it lives behind a login and the
	agent has to recognise it. A copy that will be reachable without a login takes
	a random token, because a name built from the customer's own name would let
	anyone walk the /files/ directory and read other customers' documents.
	"""
	stem = frappe.utils.cstr(stem).strip()
	# Anything that is not a plain word character becomes a dash, so the name is
	# safe in a URL, on disk and in a WhatsApp document card.
	stem = re.sub(r"[^\w\-]+", "-", stem).strip("-")[:60]
	suffix = f"-{token}" if token else ""
	return f"{stem}-v{cint(version) or 1}{suffix}.pdf"


def attach_pdf(doctype: str, name: str, file_name: str, content: bytes, is_private: int):
	"""Write a PDF as a File on a record, replacing a file of the same name.

	Regenerating the same version twice must leave one attachment, not two, so a
	file with the same name on the same document is dropped first.
	"""
	stale = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": doctype,
			"attached_to_name": name,
			"file_name": file_name,
			"is_private": cint(is_private),
		},
		pluck="name",
	)
	for stale_name in stale:
		frappe.delete_doc("File", stale_name, ignore_permissions=True, delete_permanently=True)

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"attached_to_doctype": doctype,
			"attached_to_name": name,
			"is_private": cint(is_private),
			"content": content,
		}
	)
	file_doc.insert(ignore_permissions=True)
	return file_doc


def is_send_copy_name(file_name: str) -> bool:
	"""True only for a file this module named for a send."""
	return bool(SEND_COPY_PATTERN.search(frappe.utils.cstr(file_name)))


def cleanup_public_pdfs(doctype: str, older_than_hours: int = PUBLIC_PDF_TTL_HOURS) -> int:
	"""Hourly: sweep away the temporary public PDFs a send leaves behind.

	A send cannot delete its own file. The messaging platform accepts the POST and
	returns a message id, then fetches the media while it processes the message.
	Deleting in the same request races that fetch and the customer receives
	nothing. So the file stays live for a couple of hours and a sweep removes it
	afterwards.

	A sweep is also what survives a worker that dies mid-send: an in-request timer
	or an enqueued job tied to the send would leak the file forever, while the
	next hourly run picks it up regardless.

	Three conditions must all hold before a file is touched. It has to be public,
	attached to the named doctype, and carry the random token this module puts in
	the name. That last one is what keeps the sweep off a public file somebody
	attached to a record by hand.

	Never raises: a scheduler job that throws takes the rest of its queue down.
	"""
	removed = 0
	try:
		cutoff = frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-cint(older_than_hours))
		candidates = frappe.get_all(
			"File",
			filters={
				"attached_to_doctype": doctype,
				"is_private": 0,
				"creation": ["<", cutoff],
			},
			fields=["name", "file_name"],
			limit=500,
		)

		for row in candidates:
			if not is_send_copy_name(row.file_name):
				continue
			try:
				frappe.delete_doc("File", row.name, ignore_permissions=True, delete_permanently=True)
				removed += 1
			except Exception:
				log_quietly(
					f"could not remove public PDF {row.file_name}",
					f"CRM document links: cleanup failed for {doctype}",
				)

		# No explicit commit. The background job runner commits when the method
		# returns, and committing here would also commit whatever the caller had
		# open -- which silently defeats the rollback a test relies on.
	except Exception:
		log_quietly(frappe.get_traceback(), f"CRM document links: public PDF sweep failed for {doctype}")

	return removed


# --- tokenised links -------------------------------------------------------


def create_link(
	reference_doctype: str,
	reference_name: str,
	file_doc,
	purpose: str = "Quote",
	payload=None,
	ttl_hours: int = LINK_TTL_HOURS,
):
	"""Mint one tokenised URL for a private file. Returns the link document.

	Every call mints a NEW token and retires the record's previous live links for
	the same purpose. Re-sending a quote after the price changed must not leave
	the old figure readable at the URL the customer already has.
	"""
	revoke_links(reference_doctype, reference_name, purpose)

	doc = frappe.new_doc(LINK_DOCTYPE)
	doc.update(
		{
			"token": frappe.generate_hash(length=LINK_TOKEN_LENGTH),
			"purpose": purpose,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"active": 1,
			"file": file_doc.name,
			"file_name": file_doc.file_name,
			"expires_at": frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=cint(ttl_hours)),
			"created_by_user": frappe.session.user,
			"payload": json.dumps(payload or {}, default=str),
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def revoke_links(reference_doctype: str, reference_name: str, purpose: str) -> int:
	"""Deactivate every live link of one purpose on one record. Returns how many."""
	names = frappe.get_all(
		LINK_DOCTYPE,
		filters={
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"purpose": purpose,
			"active": 1,
		},
		pluck="name",
	)
	for name in names:
		frappe.db.set_value(LINK_DOCTYPE, name, "active", 0, update_modified=False)
	return len(names)


def public_url(token: str, method: str = "crm.api.quote.view") -> str:
	"""The absolute URL a customer receives."""
	return f"{frappe.utils.get_url()}/api/method/{method}?token={token}"


def resolve_link(token: str) -> dict | None:
	"""The live link a token names, or None.

	A token that does not exist, a link that was revoked and a link that expired
	all return None. They must be indistinguishable to the caller: three different
	answers would let somebody probe which tokens were ever real.
	"""
	token = frappe.utils.cstr(token).strip()
	if not token or len(token) > 128:
		return None

	row = frappe.db.get_value(
		LINK_DOCTYPE,
		{"token": token, "active": 1},
		[
			"name",
			"purpose",
			"reference_doctype",
			"reference_name",
			"file",
			"file_name",
			"expires_at",
			"view_count",
		],
		as_dict=True,
	)
	if not row:
		return None

	if row.expires_at and frappe.utils.get_datetime(row.expires_at) < frappe.utils.now_datetime():
		return None

	return row


def looks_like_platform_agent(user_agent) -> bool:
	"""True when the user agent names a link-preview crawler rather than a browser."""
	agent = frappe.utils.cstr(user_agent).lower()
	return any(marker in agent for marker in PLATFORM_AGENTS)


def classify_fetch(user_agent, previous_views: int) -> bool:
	"""True when this fetch is the messaging platform's, not the customer's.

	Both halves are required -- see the module docstring.
	"""
	return bool(looks_like_platform_agent(user_agent)) and cint(previous_views) == 0


def request_user_agent() -> str:
	"""The caller's User-Agent, or an empty string off a request.

	Guarded because the view route is also exercised directly by tests and by the
	console, where `frappe.local.request` does not exist. A crash in the logging
	of a fetch would be a customer staring at an error instead of their document.
	"""
	try:
		return frappe.utils.cstr(frappe.get_request_header("User-Agent") or "")
	except Exception:
		return ""


def request_ip() -> str:
	try:
		return frappe.utils.cstr(getattr(frappe.local, "request_ip", "") or "")
	except Exception:
		return ""


def record_view(link: dict, user_agent=None, ip_address=None) -> bool:
	"""Write one fetch of one link. Returns True when it counted as a customer open.

	Never raises. This runs on the request that streams the document to a
	customer, and a logging failure must not turn into a customer staring at an
	error page instead of their quote.
	"""
	try:
		previous = frappe.db.count(VIEW_DOCTYPE, {"document_link": link["name"]})
		is_platform = classify_fetch(user_agent, previous)
		now = frappe.utils.now_datetime()

		row = frappe.new_doc(VIEW_DOCTYPE)
		row.update(
			{
				"document_link": link["name"],
				"reference_doctype": link.get("reference_doctype"),
				"reference_name": link.get("reference_name"),
				"viewed_at": now,
				"is_platform_fetch": 1 if is_platform else 0,
				"user_agent": frappe.utils.cstr(user_agent)[:MAX_USER_AGENT_LENGTH],
				"ip_address": frappe.utils.cstr(ip_address)[:45],
			}
		)
		row.insert(ignore_permissions=True)

		if is_platform:
			frappe.db.set_value(LINK_DOCTYPE, link["name"], "platform_fetch_at", now, update_modified=False)
			return False

		updates = {
			"view_count": cint(link.get("view_count")) + 1,
			"last_viewed_at": now,
		}
		if not frappe.db.get_value(LINK_DOCTYPE, link["name"], "first_viewed_at"):
			updates["first_viewed_at"] = now
		frappe.db.set_value(LINK_DOCTYPE, link["name"], updates, update_modified=False)
		return True
	except Exception:
		log_quietly(frappe.get_traceback(), "CRM document links: view not recorded")
		return False


def customer_views(reference_doctype: str, reference_name: str, purpose: str = "Quote") -> list[dict]:
	"""Customer opens on one record, newest last. Platform prefetches are excluded.

	Permission note (master spec §3): this reads with `frappe.get_all`, which
	applies NO permission conditions. It is safe only because every caller has
	already checked read permission on `reference_name`, and the scope of the
	query is that one record. Never call it with a doctype/name a caller supplied
	without checking first.
	"""
	links = frappe.get_all(
		LINK_DOCTYPE,
		filters={
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"purpose": purpose,
		},
		pluck="name",
	)
	if not links:
		return []

	return frappe.get_all(
		VIEW_DOCTYPE,
		filters={"document_link": ["in", links], "is_platform_fetch": 0},
		fields=["name", "document_link", "viewed_at", "creation"],
		order_by="viewed_at asc",
		limit_page_length=100,
	)


def expire_links(older_than_hours: int = LINK_TTL_HOURS) -> int:
	"""Deactivate links whose time is up, and drop the private file they held.

	Never raises, for the same reason `cleanup_public_pdfs` does not.
	"""
	closed = 0
	try:
		now = frappe.utils.now_datetime()
		rows = frappe.get_all(
			LINK_DOCTYPE,
			filters={"active": 1, "expires_at": ["<", frappe.utils.get_datetime_str(now)]},
			fields=["name", "file"],
			limit=500,
		)
		for row in rows:
			# The link's own `file` column is a Link, so `delete_doc` would refuse
			# the File with a "linked with" error while the row still points at
			# it. Clear the reference first; the link is being retired anyway.
			frappe.db.set_value(LINK_DOCTYPE, row.name, {"active": 0, "file": None}, update_modified=False)
			closed += 1
			if row.file and frappe.db.exists("File", row.file):
				try:
					frappe.delete_doc("File", row.file, ignore_permissions=True, delete_permanently=True)
				except Exception:
					log_quietly(
						f"could not remove expired document file {row.file}",
						"CRM document links: expiry could not delete a file",
					)
	except Exception:
		log_quietly(frappe.get_traceback(), "CRM document links: expiry sweep failed")

	return closed


def stream_file(file_doc, download_name: str | None = None) -> None:
	"""Put a File's bytes on the response as a downloadable attachment.

	Set on `frappe.response` rather than returned: the framework's request
	handler only writes a body for `frappe.response`, and returning bytes from a
	whitelisted method would be JSON-encoded into nonsense.
	"""
	frappe.response.filename = download_name or file_doc.file_name
	frappe.response.filecontent = file_doc.get_content()
	frappe.response.type = "pdf"


def throw_gone() -> None:
	"""The one answer every bad token gets."""
	frappe.throw(
		_("This link has expired or is no longer valid."),
		frappe.PermissionError,
		title=_("Link unavailable"),
	)
