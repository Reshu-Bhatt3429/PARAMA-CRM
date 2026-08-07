import json

import frappe
from crm.api.doc import get_assigned_users
from crm.fcrm.doctype.crm_notification.crm_notification import notify_user
from crm.integrations.api import get_contact_lead_or_deal_from_number
from crm.utils import parse_phone_number
from frappe import _
from frappe.desk.form.assign_to import _add as assign
from frappe.permissions import add_permission, update_permission_property
from frappe.query_builder import Case
from frappe.query_builder.functions import Coalesce, Count, Max, Sum

ALLOWED_WHATSAPP_ROLES = ["System Manager", "Sales Manager", "Sales User"]
WHATSAPP_LEAD_SOURCE = "WhatsApp"

# Roles allowed to read conversations they are neither assigned to nor own.
INBOX_MANAGER_ROLES = ("System Manager", "Sales Manager")

# Role a user must hold to receive round-robin WhatsApp lead assignments.
ASSIGNMENT_ROLE = "Sales User"

# System accounts that must never be handed a lead.
NON_ASSIGNABLE_USERS = ("Administrator", "Guest")

# compute_priority() thresholds.
HOT_UNANSWERED_HOURS = 24
HOT_MESSAGE_COUNT_7D = 5
WARM_ACTIVITY_DAYS = 3
ACTIVITY_WINDOW_DAYS = 7

# CRM doctypes a WhatsApp Message can be linked to (see crm.api.whatsapp.validate).
CONVERSATION_DOCTYPES = ("CRM Lead", "CRM Deal")

# (icon, untranslated label) used to preview messages that carry no readable text.
MEDIA_PREVIEW_LABELS = {
	"image": ("📷", "Image"),
	"video": ("🎥", "Video"),
	"audio": ("🎤", "Audio"),
	"document": ("📄", "Document"),
	"location": ("📍", "Location"),
	"contact": ("👤", "Contact"),
	"order": ("🛒", "Order"),
	"interactive": ("🔘", "Interactive message"),
	"button": ("🔘", "Button reply"),
	"flow": ("🧩", "Form"),
	"reaction": ("❤️", "Reaction"),
}

PREVIEW_MAX_LENGTH = 120

# Most recent conversations returned by get_whatsapp_conversations by default.
CONVERSATION_LIMIT = 200


def validate_access(reference_doctype=None, reference_name=None, permtype="read"):
	if not any(role in ALLOWED_WHATSAPP_ROLES for role in frappe.get_roles()):
		frappe.throw(_("Only sales users can access WhatsApp features."), frappe.PermissionError)

	if reference_doctype and reference_name:
		if not frappe.db.exists(reference_doctype, reference_name):
			frappe.throw(
				_("Reference document {0} {1} does not exist.").format(reference_doctype, reference_name),
				frappe.DoesNotExistError,
			)
		reference_doc = frappe.get_doc(reference_doctype, reference_name)
		if not reference_doc.has_permission(permtype):
			frappe.throw(
				_("Not permitted to access reference document {0} {1}.").format(
					reference_doctype, reference_name
				),
				frappe.PermissionError,
			)
		return reference_doc

	return None


def validate(doc, method):
	phone_number = doc.get("from") if doc.type == "Incoming" else doc.get("to")
	if phone_number:
		try:
			phone_number = normalize_whatsapp_number(phone_number)
			name, doctype = get_contact_lead_or_deal_from_number(phone_number)
			if not name and doc.type == "Incoming":
				name, doctype = create_lead_from_whatsapp_message(doc, phone_number)
			if doctype and name is not None:
				doc.reference_doctype = doctype
				doc.reference_name = name
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				"CRM WhatsApp: failed to resolve contact or create lead",
			)


def normalize_whatsapp_number(phone_number: str) -> str:
	"""Return the E.164 form of a WhatsApp sender number when it is valid.

	Meta sends sender IDs as E.164 digits without the leading plus sign. Adding
	the plus before parsing prevents the site's default country from being
	applied to an already international number.
	"""
	raw_number = frappe.utils.cstr(phone_number).strip()
	digits = "".join(character for character in raw_number if character.isdigit())
	if not digits:
		return raw_number

	parsed_number = parse_phone_number(f"+{digits}")
	if parsed_number.get("is_valid"):
		return parsed_number["formats"]["E164"]

	return raw_number


def create_lead_from_whatsapp_message(doc, phone_number: str) -> tuple[str | None, str | None]:
	"""Create one CRM Lead for an unknown incoming WhatsApp sender.

	The lookup is repeated while holding a per-number Redis lock so concurrent
	messages from the same new sender reuse the first Lead.
	"""
	if not parse_phone_number(phone_number).get("is_valid"):
		return None, None

	lock_key = f"crm:whatsapp-lead:{phone_number}"
	lock = frappe.cache.lock(lock_key, timeout=60, blocking_timeout=15)
	lock.acquire(blocking=True)
	release_immediately = True

	try:
		name, doctype = get_contact_lead_or_deal_from_number(phone_number)
		if name:
			return name, doctype

		ensure_whatsapp_lead_source()
		profile_name = frappe.utils.cstr(doc.get("profile_name")).strip()[:140]
		first_name = profile_name or f"WhatsApp Lead {phone_number[-4:]}"

		lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": first_name,
				"mobile_no": phone_number,
				"source": WHATSAPP_LEAD_SOURCE,
			}
		).insert(ignore_permissions=True)

		try:
			assign_whatsapp_lead(lead.name)
		except Exception:
			# A lead nobody owns is still better than a message we failed to store.
			frappe.log_error(
				frappe.get_traceback(),
				"CRM WhatsApp: failed to auto-assign lead",
			)

		def release_lock_after_transaction():
			if lock.owned():
				lock.release()

		frappe.db.after_commit.add(release_lock_after_transaction)
		frappe.db.after_rollback.add(release_lock_after_transaction)
		release_immediately = False
		return lead.name, lead.doctype
	finally:
		if release_immediately and lock.owned():
			lock.release()


def ensure_whatsapp_lead_source():
	if frappe.db.exists("CRM Lead Source", WHATSAPP_LEAD_SOURCE):
		return

	frappe.get_doc(
		{
			"doctype": "CRM Lead Source",
			"source_name": WHATSAPP_LEAD_SOURCE,
		}
	).insert(ignore_permissions=True, ignore_if_duplicate=True)


def assign_whatsapp_lead(lead_name: str) -> str | None:
	"""Hand a WhatsApp-sourced lead to the least loaded sales user.

	Assignment goes through `frappe.desk.form.assign_to`, the mechanism the rest
	of the CRM uses (see `CRMLead.assign_agent`): it writes the ToDo that backs
	the assignee avatar, and `crm.api.todo.after_insert` turns that ToDo into the
	`lead_owner` value **and** the "assigned a lead to you" CRM Notification. No
	notification is raised here on purpose -- a second one would double-notify
	the same person about the same assignment.

	Idempotent: a lead that already carries an open assignment is left alone.
	Returns the assignee, or None when nothing was assigned.
	"""
	if get_assigned_users("CRM Lead", lead_name):
		return None

	candidates = get_sales_user_candidates()
	if not candidates:
		return None

	assignee = choose_round_robin_assignee(candidates, get_open_whatsapp_lead_counts(candidates))
	if not assignee:
		return None

	assign(
		{"assign_to": [assignee], "doctype": "CRM Lead", "name": lead_name},
		ignore_permissions=True,
	)
	return assignee


def get_sales_user_candidates() -> list[str]:
	"""Enabled system users holding the Sales User role, minus the system accounts."""
	role_holders = frappe.get_all(
		"Has Role",
		filters={"parenttype": "User", "role": ASSIGNMENT_ROLE},
		pluck="parent",
		distinct=True,
	)
	candidates = [user for user in role_holders if user not in NON_ASSIGNABLE_USERS]
	if not candidates:
		return []

	return sorted(
		frappe.get_all(
			"User",
			filters={"name": ["in", candidates], "enabled": 1, "user_type": "System User"},
			pluck="name",
		)
	)


def get_open_whatsapp_lead_counts(candidates: list[str]) -> dict[str, int]:
	"""Open assignments each candidate already holds on unconverted WhatsApp leads.

	This is the round-robin state: derived from the ToDo table on every call, so
	there is no counter to keep in sync and no drift when leads are reassigned.
	"""
	if not candidates:
		return {}

	ToDo = frappe.qb.DocType("ToDo")
	Lead = frappe.qb.DocType("CRM Lead")
	rows = (
		frappe.qb.from_(ToDo)
		.join(Lead)
		.on(ToDo.reference_name == Lead.name)
		.select(ToDo.allocated_to, Count(ToDo.name).as_("open_leads"))
		.where(
			(ToDo.reference_type == "CRM Lead")
			& ToDo.status.notin(["Closed", "Cancelled"])
			& ToDo.allocated_to.isin(candidates)
			& (Lead.source == WHATSAPP_LEAD_SOURCE)
			& (Lead.converted == 0)
		)
		.groupby(ToDo.allocated_to)
		.run(as_dict=True)
	)

	return {row["allocated_to"]: frappe.utils.cint(row["open_leads"]) for row in rows}


def choose_round_robin_assignee(candidates: list[str], open_counts: dict[str, int]) -> str | None:
	"""Least loaded candidate; ties break on user id so the choice is reproducible."""
	if not candidates:
		return None

	return min(candidates, key=lambda user: (frappe.utils.cint(open_counts.get(user)), user))


def backfill_unlinked_whatsapp_messages() -> dict:
	"""Link stored incoming messages, creating Leads for unknown senders."""
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return {"processed": 0, "linked": 0, "leads": []}

	messages = frappe.get_all(
		"WhatsApp Message",
		filters={
			"type": "Incoming",
			"reference_name": ["is", "not set"],
		},
		pluck="name",
	)
	linked = 0
	leads = set()

	for message_name in messages:
		message = frappe.get_doc("WhatsApp Message", message_name)
		message.save(ignore_permissions=True)
		if message.reference_doctype and message.reference_name:
			linked += 1
			if message.reference_doctype == "CRM Lead":
				leads.add(message.reference_name)

	return {
		"processed": len(messages),
		"linked": linked,
		"leads": sorted(leads),
	}


def on_update(doc, method):
	frappe.publish_realtime(
		"whatsapp_message",
		{
			"reference_doctype": doc.reference_doctype,
			"reference_name": doc.reference_name,
		},
	)

	notify_agent(doc)


def notify_agent(doc):
	if doc.type == "Incoming":
		if not doc.reference_doctype or not doc.reference_name:
			return
		doctype = doc.reference_doctype
		if doctype and doctype.startswith("CRM "):
			doctype = doctype[4:].lower()
		safe_reference_name = frappe.utils.escape_html(doc.reference_name)
		notification_text = f"""
            <div class="mb-2 leading-5 text-ink-gray-5">
                <span class="font-medium text-ink-gray-9">{_("You")}</span>
                <span>{_("received a whatsapp message in {0}").format(doctype)}</span>
                <span class="font-medium text-ink-gray-9">{safe_reference_name}</span>
            </div>
        """
		assigned_users = get_assigned_users(doc.reference_doctype, doc.reference_name)
		for user in assigned_users:
			notify_user(
				{
					"owner": doc.owner,
					"assigned_to": user,
					"notification_type": "WhatsApp",
					"message": doc.message,
					"notification_text": notification_text,
					"reference_doctype": "WhatsApp Message",
					"reference_docname": doc.name,
					"redirect_to_doctype": doc.reference_doctype,
					"redirect_to_docname": doc.reference_name,
				}
			)


@frappe.whitelist()
def is_whatsapp_enabled():
	if not frappe.db.exists("DocType", "WhatsApp Settings"):
		return False
	default_outgoing = frappe.get_cached_value(
		"WhatsApp Settings", "WhatsApp Settings", "default_outgoing_account"
	)
	if not default_outgoing:
		return False
	status = frappe.get_cached_value("WhatsApp Account", default_outgoing, "status")
	return status == "Active"


@frappe.whitelist()
def is_whatsapp_installed():
	if not frappe.db.exists("DocType", "WhatsApp Settings"):
		return False
	return True


@frappe.whitelist()
def get_connected_whatsapp_account():
	"""The WhatsApp Business account the inbox sends and receives through.

	Returns the default outgoing account's name and status so the inbox can
	show what is connected, or None when nothing is configured.
	"""
	validate_access()
	if not frappe.db.exists("DocType", "WhatsApp Settings"):
		return None
	default_outgoing = frappe.get_cached_value(
		"WhatsApp Settings", "WhatsApp Settings", "default_outgoing_account"
	)
	if not default_outgoing:
		return None
	return frappe.db.get_value("WhatsApp Account", default_outgoing, ["name", "status"], as_dict=True)


@frappe.whitelist()
def get_whatsapp_messages(reference_doctype: str, reference_name: str):
	reference_doc = validate_access(reference_doctype, reference_name)
	# twilio integration app is not compatible with crm app
	# crm has its own twilio integration in built
	if "twilio_integration" in frappe.get_installed_apps():
		return []
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return []
	messages = []

	if reference_doctype == "CRM Deal":
		lead = reference_doc.get("lead")
		if lead:
			validate_access("CRM Lead", lead)
			messages = frappe.get_all(
				"WhatsApp Message",
				filters={
					"reference_doctype": "CRM Lead",
					"reference_name": lead,
				},
				fields=[
					"name",
					"type",
					"to",
					"from",
					"content_type",
					"message_type",
					"attach",
					"template",
					"use_template",
					"message_id",
					"is_reply",
					"reply_to_message_id",
					"creation",
					"message",
					"status",
					"reference_doctype",
					"reference_name",
					"template_parameters",
					"template_header_parameters",
				],
			)

	messages += frappe.get_all(
		"WhatsApp Message",
		filters={
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
		},
		fields=[
			"name",
			"type",
			"to",
			"from",
			"content_type",
			"message_type",
			"attach",
			"template",
			"use_template",
			"message_id",
			"is_reply",
			"reply_to_message_id",
			"creation",
			"message",
			"status",
			"reference_doctype",
			"reference_name",
			"template_parameters",
			"template_header_parameters",
		],
	)

	# Filter messages to get only Template messages
	template_messages = [message for message in messages if message["message_type"] == "Template"]

	# Iterate through template messages
	for template_message in template_messages:
		# Find the template that this message is using
		if not frappe.db.exists("WhatsApp Templates", template_message["template"]):
			continue
		template = frappe.get_doc("WhatsApp Templates", template_message["template"])

		if template:
			template_message["template_name"] = template.template_name
			if template_message["template_parameters"]:
				parameters = json.loads(template_message["template_parameters"])
				template.template = parse_template_parameters(template.template, parameters)

			template_message["template"] = template.template
			if template_message["template_header_parameters"]:
				header_parameters = json.loads(template_message["template_header_parameters"])
				template.header = parse_template_parameters(template.header, header_parameters)
			template_message["header"] = template.header
			template_message["footer"] = template.footer

	# Filter messages to get only reaction messages
	reaction_messages = [message for message in messages if message["content_type"] == "reaction"]
	reaction_messages.reverse()

	# Iterate through reaction messages
	for reaction_message in reaction_messages:
		# Find the message that this reaction is reacting to
		reacted_message = next(
			(m for m in messages if m["message_id"] == reaction_message["reply_to_message_id"]),
			None,
		)

		# If the reacted message is found, add the reaction to it
		if reacted_message:
			reacted_message["reaction"] = reaction_message["message"]

	for message in messages:
		from_name = get_from_name(message) if message["from"] else _("You")
		message["from_name"] = from_name
	# Filter messages to get only replies
	reply_messages = [message for message in messages if message["is_reply"]]

	# Iterate through reply messages
	for reply_message in reply_messages:
		# Find the message that this message is replying to
		replied_message = next(
			(m for m in messages if m["message_id"] == reply_message["reply_to_message_id"]),
			None,
		)

		# If the replied message is found, add the reply details to the reply message
		if replied_message:
			from_name = get_from_name(reply_message) if replied_message["from"] else _("You")
			message = replied_message["message"]
			if replied_message["message_type"] == "Template":
				message = replied_message["template"]
			reply_message["reply_message"] = message
			reply_message["header"] = replied_message.get("header") or ""
			reply_message["footer"] = replied_message.get("footer") or ""
			reply_message["reply_to"] = replied_message["name"]
			reply_message["reply_to_type"] = replied_message["type"]
			reply_message["reply_to_from"] = from_name

	return [message for message in messages if message["content_type"] != "reaction"]


@frappe.whitelist()
def get_whatsapp_conversations(limit: int = CONVERSATION_LIMIT, scope: str = "mine"):
	"""Return one row per WhatsApp conversation across every CRM Lead and Deal.

	Powers the team inbox. Four bounded queries regardless of how many messages
	exist: one GROUP BY for the counts, last timestamps and 7-day activity, one
	to fetch the last message of each conversation, one per reference doctype to
	resolve display names and assignment, and one for the start of the current
	unanswered run. Conversations whose reference document the current user
	cannot read are dropped, since the reference lookup is permission aware.

	`scope` is "mine" (default) -- conversations assigned to or owned by the
	session user -- or "all", which needs a manager role and silently degrades
	to "mine" without one.
	"""
	validate_access()

	# twilio integration app is not compatible with crm app
	if "twilio_integration" in frappe.get_installed_apps():
		return []
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return []

	scope = resolve_conversation_scope(scope)
	aggregates = get_conversation_aggregates()
	if not aggregates:
		return []

	# Trim before the follow-up lookups so their `in (...)` lists stay bounded.
	# Scope filtering happens after the trim, so "mine" is the session user's
	# share of the most recent `limit` conversations rather than an unbounded scan.
	aggregates.sort(key=lambda row: row["last_at"], reverse=True)
	limit = frappe.utils.cint(limit) or CONVERSATION_LIMIT
	aggregates = aggregates[:limit]

	last_messages = get_last_conversation_messages(aggregates)
	references = get_conversation_references(aggregates)
	unanswered_since = get_unanswered_since(aggregates)
	session_user = frappe.session.user

	conversations = []
	for row in aggregates:
		key = (row["reference_doctype"], row["reference_name"])
		reference = references.get(key)
		if not reference:
			# Reference document was deleted, or is not readable by this user.
			continue
		if scope == "mine" and not conversation_belongs_to(reference, session_user):
			continue

		last_message = last_messages.get(key) or {}
		assignee = get_conversation_assignee(reference)
		conversations.append(
			{
				"reference_doctype": row["reference_doctype"],
				"reference_name": row["reference_name"],
				"display_name": reference["display_name"],
				"phone": get_counterpart_number(last_message) or reference["phone"],
				# Pipeline stage of the linked Lead/Deal, so the inbox can show a status
				# pill without a second round trip. Free: it rides along on the reference
				# query that already runs.
				"status": reference.get("status") or "",
				"last_message": whatsapp_message_preview(last_message),
				"last_message_type": last_message.get("type"),
				"last_at": row["last_at"],
				"message_count": row["message_count"],
				"assigned_to": {"user": assignee, "full_name": assignee} if assignee else None,
				"needs_reply": is_unanswered(row),
				"waiting_since": unanswered_since.get(key),
				"priority": compute_priority(
					row.get("last_incoming_at"),
					row.get("last_outgoing_at"),
					row.get("message_count_7d"),
				),
			}
		)

	add_assignee_full_names(conversations)
	conversations.sort(key=lambda conversation: conversation["last_at"], reverse=True)
	return conversations


def resolve_conversation_scope(scope: str) -> str:
	"""Only managers may widen the inbox; everyone else silently gets their own."""
	if frappe.utils.cstr(scope).lower() == "all" and can_view_all_conversations():
		return "all"

	return "mine"


def can_view_all_conversations(user: str | None = None) -> bool:
	roles = frappe.get_roles(user) if user else frappe.get_roles()
	return any(role in INBOX_MANAGER_ROLES for role in roles)


def conversation_belongs_to(reference: dict, user: str) -> bool:
	"""A conversation is yours when you are assigned to its Lead/Deal, or own it."""
	if user in (reference.get("assigned_users") or []):
		return True

	return bool(reference.get("owner_user")) and reference["owner_user"] == user


def get_conversation_assignee(reference: dict) -> str:
	"""The one user the inbox shows an avatar for: first assignee, else the owner."""
	assigned_users = reference.get("assigned_users") or []
	if assigned_users:
		return assigned_users[0]

	return reference.get("owner_user") or ""


def add_assignee_full_names(conversations: list[dict]):
	"""Resolve every assignee's full name in one query instead of one per row."""
	assignees = {
		conversation["assigned_to"]["user"] for conversation in conversations if conversation["assigned_to"]
	}
	if not assignees:
		return

	full_names = {
		row["name"]: row["full_name"]
		for row in frappe.get_all(
			"User", filters={"name": ["in", sorted(assignees)]}, fields=["name", "full_name"]
		)
	}
	for conversation in conversations:
		assignee = conversation["assigned_to"]
		if assignee:
			assignee["full_name"] = full_names.get(assignee["user"]) or assignee["user"]


def is_unanswered(row: dict) -> bool:
	"""True when the newest message of a conversation is one we have not replied to."""
	last_incoming_at = row.get("last_incoming_at")
	if not last_incoming_at:
		return False

	last_outgoing_at = row.get("last_outgoing_at")
	return not last_outgoing_at or last_outgoing_at < last_incoming_at


def compute_priority(last_incoming_at, last_outgoing_at, message_count_7d, now=None) -> str:
	"""Deterministic temperature of one conversation. No model, no stored state.

	hot -- an incoming message from the last day is still unanswered, or the
	conversation carried at least five messages in the last week.
	warm -- any activity in the last three days.
	cold -- everything else.
	"""
	now = frappe.utils.get_datetime(now) if now else frappe.utils.now_datetime()
	last_incoming_at = frappe.utils.get_datetime(last_incoming_at) if last_incoming_at else None
	last_outgoing_at = frappe.utils.get_datetime(last_outgoing_at) if last_outgoing_at else None

	unanswered = bool(last_incoming_at) and (not last_outgoing_at or last_outgoing_at < last_incoming_at)
	if unanswered and last_incoming_at >= frappe.utils.add_to_date(now, hours=-HOT_UNANSWERED_HOURS):
		return "hot"

	if frappe.utils.cint(message_count_7d) >= HOT_MESSAGE_COUNT_7D:
		return "hot"

	last_activity = max([stamp for stamp in (last_incoming_at, last_outgoing_at) if stamp], default=None)
	if last_activity and last_activity >= frappe.utils.add_to_date(now, days=-WARM_ACTIVITY_DAYS):
		return "warm"

	return "cold"


def get_conversation_aggregates() -> list[dict]:
	"""One GROUP BY over WhatsApp Message: counts and last timestamps per conversation.

	Reactions are excluded because the thread view hides them too, so counting
	them would make the inbox disagree with the conversation it opens.

	The per-direction maxima and the 7-day count are aggregated here as well, so
	`compute_priority` and the unanswered-run lookup cost no extra query.
	"""
	Message = frappe.qb.DocType("WhatsApp Message")
	# Rendered as a string: a datetime object reaches SQL in ISO form, with a `T`
	# between date and time.
	window_start = frappe.utils.get_datetime_str(
		frappe.utils.add_to_date(frappe.utils.now_datetime(), days=-ACTIVITY_WINDOW_DAYS)
	)
	return (
		frappe.qb.from_(Message)
		.select(
			Message.reference_doctype,
			Message.reference_name,
			Count(Message.name).as_("message_count"),
			Max(Message.creation).as_("last_at"),
			Max(Case().when(Message.type == "Incoming", Message.creation)).as_("last_incoming_at"),
			Max(Case().when(Message.type == "Outgoing", Message.creation)).as_("last_outgoing_at"),
			Sum(Case().when(Message.creation >= window_start, 1).else_(0)).as_("message_count_7d"),
		)
		.where(
			Message.reference_doctype.isin(list(CONVERSATION_DOCTYPES))
			& (Coalesce(Message.reference_name, "") != "")
			& (Coalesce(Message.content_type, "text") != "reaction")
		)
		.groupby(Message.reference_doctype, Message.reference_name)
		.run(as_dict=True)
	)


def get_unanswered_since(aggregates: list[dict]) -> dict[tuple, object]:
	"""Start of each conversation's current unanswered run.

	That is the oldest incoming message newer than our last reply. Only
	conversations whose newest message is incoming can have such a run, and every
	message of interest is newer than the oldest reply among them, so one bounded
	query covers the whole page of conversations.
	"""
	pending = [row for row in aggregates if is_unanswered(row)]
	if not pending:
		return {}

	replied_at = [row["last_outgoing_at"] for row in pending if row.get("last_outgoing_at")]
	# A conversation we never replied to is unanswered from its very first
	# message, so only bound the scan when every pending conversation has a reply.
	lower_bound = min(replied_at) if len(replied_at) == len(pending) else None

	Message = frappe.qb.DocType("WhatsApp Message")
	condition = (
		(Message.type == "Incoming")
		& Message.reference_doctype.isin(list(CONVERSATION_DOCTYPES))
		& Message.reference_name.isin(sorted({row["reference_name"] for row in pending}))
		& (Coalesce(Message.content_type, "text") != "reaction")
	)
	if lower_bound:
		condition = condition & (Message.creation > frappe.utils.get_datetime_str(lower_bound))

	rows = (
		frappe.qb.from_(Message)
		.select(Message.reference_doctype, Message.reference_name, Message.creation)
		.where(condition)
		.orderby(Message.creation)
		.run(as_dict=True)
	)

	bounds = {
		(row["reference_doctype"], row["reference_name"]): row.get("last_outgoing_at") for row in pending
	}
	unanswered_since = {}
	for row in rows:
		key = (row["reference_doctype"], row["reference_name"])
		if key not in bounds or key in unanswered_since:
			continue

		last_outgoing_at = bounds[key]
		if last_outgoing_at and row["creation"] <= last_outgoing_at:
			continue

		unanswered_since[key] = row["creation"]

	return unanswered_since


def get_last_conversation_messages(aggregates: list[dict]) -> dict[tuple, dict]:
	"""Fetch the newest message of every conversation in a single query."""
	reference_names = sorted({row["reference_name"] for row in aggregates if row["reference_name"]})
	if not reference_names:
		return {}

	rows = frappe.get_all(
		"WhatsApp Message",
		filters={
			"reference_doctype": ["in", list(CONVERSATION_DOCTYPES)],
			"reference_name": ["in", reference_names],
			"creation": ["in", [row["last_at"] for row in aggregates]],
		},
		fields=[
			"reference_doctype",
			"reference_name",
			"type",
			"message",
			"message_type",
			"content_type",
			"attach",
			"creation",
			"from",
			"to",
		],
		order_by="creation asc",
	)

	# The `creation in (...)` filter can match a sibling conversation that happens
	# to share a timestamp, so keep only rows on their own conversation's last_at.
	wanted = {(row["reference_doctype"], row["reference_name"]): row["last_at"] for row in aggregates}
	last_messages = {}
	for row in rows:
		key = (row["reference_doctype"], row["reference_name"])
		if wanted.get(key) == row["creation"]:
			last_messages[key] = row

	return last_messages


def get_conversation_references(aggregates: list[dict]) -> dict[tuple, dict]:
	"""Resolve display name, phone, pipeline status and assignment for each linked Lead/Deal.

	`frappe.get_list` is permission aware, so a conversation the session user
	cannot read simply never comes back. `_assign` and the owner field are what
	the "mine" scope filters on.
	"""
	names = {doctype: [] for doctype in CONVERSATION_DOCTYPES}
	for row in aggregates:
		if row["reference_doctype"] in names and row["reference_name"]:
			names[row["reference_doctype"]].append(row["reference_name"])

	references = {}

	if names["CRM Lead"]:
		leads = frappe.get_list(
			"CRM Lead",
			filters={"name": ["in", names["CRM Lead"]]},
			fields=[
				"name",
				"lead_name",
				"first_name",
				"last_name",
				"organization",
				"mobile_no",
				"status",
				"lead_owner",
				"_assign",
			],
			limit_page_length=0,
		)
		for lead in leads:
			full_name = " ".join(part for part in [lead.first_name, lead.last_name] if part)
			references[("CRM Lead", lead.name)] = {
				"display_name": lead.lead_name or full_name or lead.organization or lead.name,
				"phone": lead.mobile_no or "",
				"status": lead.get("status") or "",
				"owner_user": lead.get("lead_owner") or "",
				"assigned_users": parse_assigned_users(lead.get("_assign")),
			}

	if names["CRM Deal"]:
		deals = frappe.get_list(
			"CRM Deal",
			filters={"name": ["in", names["CRM Deal"]]},
			fields=["name", "organization", "lead_name", "mobile_no", "status", "deal_owner", "_assign"],
			limit_page_length=0,
		)
		for deal in deals:
			references[("CRM Deal", deal.name)] = {
				"display_name": deal.organization or deal.lead_name or deal.name,
				"phone": deal.mobile_no or "",
				"status": deal.get("status") or "",
				"owner_user": deal.get("deal_owner") or "",
				"assigned_users": parse_assigned_users(deal.get("_assign")),
			}

	return references


def parse_assigned_users(value) -> list[str]:
	"""`_assign` is stored as a JSON list; anything unreadable counts as unassigned."""
	if not value:
		return []

	try:
		users = frappe.parse_json(value)
	except (ValueError, TypeError):
		return []

	if isinstance(users, str):
		users = [users]
	if not isinstance(users, list):
		return []

	return [user for user in users if user]


def get_counterpart_number(message: dict) -> str:
	"""Return the customer side of a message: the sender when incoming, the recipient when outgoing."""
	if not message:
		return ""

	raw_number = message.get("from") if message.get("type") == "Incoming" else message.get("to")
	if not raw_number:
		return ""

	return normalize_whatsapp_number(raw_number)


def whatsapp_message_preview(message: dict) -> str:
	"""Build a single-line, type-aware preview of a message for the conversation list."""
	if not message:
		return ""

	text = frappe.utils.strip_html(frappe.utils.cstr(message.get("message"))).strip()
	# Media messages without a caption store the file path in `message`.
	if text and (text == frappe.utils.cstr(message.get("attach")) or text.startswith("/files/")):
		text = ""

	if frappe.utils.cstr(message.get("message_type")) == "Template":
		return truncate_preview(f"📋 {_('Template message')}")

	content_type = frappe.utils.cstr(message.get("content_type") or "text").lower()
	label = MEDIA_PREVIEW_LABELS.get(content_type)
	if not label:
		return truncate_preview(text)

	icon, name = label
	prefix = f"{icon} {_(name)}"
	return truncate_preview(f"{prefix} · {text}" if text else prefix)


def truncate_preview(text: str, length: int = PREVIEW_MAX_LENGTH) -> str:
	"""Collapse whitespace and clip a preview to a single readable line."""
	text = " ".join(frappe.utils.cstr(text).split())
	if len(text) <= length:
		return text
	return text[: length - 1].rstrip() + "…"


@frappe.whitelist()
def create_whatsapp_message(
	reference_doctype: str,
	reference_name: str,
	message: str,
	to: str,
	attach: str,
	reply_to: str,
	content_type: str = "text",
):
	validate_access(reference_doctype, reference_name)
	doc = frappe.new_doc("WhatsApp Message")

	if reply_to:
		if not frappe.db.exists("WhatsApp Message", reply_to):
			frappe.throw(_("Referenced WhatsApp message does not exist."), frappe.DoesNotExistError)
		reply_doc = frappe.get_doc("WhatsApp Message", reply_to)
		if not reply_doc.has_permission("read"):
			frappe.throw(
				_("Not permitted to access the referenced WhatsApp message."), frappe.PermissionError
			)
		validate_access(reply_doc.reference_doctype, reply_doc.reference_name)
		doc.update(
			{
				"is_reply": True,
				"reply_to_message_id": reply_doc.message_id,
			}
		)

	doc.update(
		{
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"message": message or attach,
			"to": to,
			"attach": attach,
			"content_type": content_type,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


@frappe.whitelist()
def send_whatsapp_template(reference_doctype: str, reference_name: str, template: str, to: str):
	validate_access(reference_doctype, reference_name)
	doc = frappe.new_doc("WhatsApp Message")
	doc.update(
		{
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"message_type": "Template",
			"message": "Template message",
			"content_type": "text",
			"use_template": True,
			"template": template,
			"to": to,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


@frappe.whitelist()
def react_on_whatsapp_message(emoji: str, reply_to_name: str):
	validate_access()
	if not frappe.db.exists("WhatsApp Message", reply_to_name):
		frappe.throw(_("Referenced WhatsApp message does not exist."), frappe.DoesNotExistError)
	reply_to_doc = frappe.get_doc("WhatsApp Message", reply_to_name)

	if not reply_to_doc.has_permission("read"):
		frappe.throw(_("Not permitted to access the referenced WhatsApp message."), frappe.PermissionError)

	validate_access(reply_to_doc.reference_doctype, reply_to_doc.reference_name)

	to = (reply_to_doc.type == "Incoming" and reply_to_doc.get("from")) or reply_to_doc.to
	doc = frappe.new_doc("WhatsApp Message")
	doc.update(
		{
			"reference_doctype": reply_to_doc.reference_doctype,
			"reference_name": reply_to_doc.reference_name,
			"message": emoji,
			"to": to,
			"reply_to_message_id": reply_to_doc.message_id,
			"content_type": "reaction",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def parse_template_parameters(string, parameters):
	for i, parameter in enumerate(parameters, start=1):
		placeholder = "{{" + str(i) + "}}"
		string = string.replace(placeholder, str(parameter))

	return string


def get_from_name(message):
	doc = frappe.get_doc(message["reference_doctype"], message["reference_name"])
	from_name = ""
	if message["reference_doctype"] == "CRM Deal":
		if doc.get("contacts"):
			for c in doc.get("contacts"):
				if c.is_primary:
					from_name = c.full_name or c.mobile_no
					break
		else:
			from_name = doc.get("lead_name")
	else:
		from_name = " ".join(name for name in [doc.get("first_name"), doc.get("last_name")] if name)
	return from_name


def add_roles():
	if "frappe_whatsapp" not in frappe.get_installed_apps():
		return

	role_list = ["Sales Manager", "Sales User"]
	doctypes = ["WhatsApp Message", "WhatsApp Templates", "WhatsApp Settings"]
	for doctype in doctypes:
		for role in role_list:
			if frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role}):
				continue
			add_permission(doctype, role, 0, "write")
			update_permission_property(doctype, role, 0, "create", 1)
			update_permission_property(doctype, role, 0, "delete", 1)
			update_permission_property(doctype, role, 0, "share", 1)
			update_permission_property(doctype, role, 0, "email", 1)
			update_permission_property(doctype, role, 0, "print", 1)
			update_permission_property(doctype, role, 0, "report", 1)
			update_permission_property(doctype, role, 0, "export", 1)
