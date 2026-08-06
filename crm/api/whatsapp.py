import json

import frappe
from frappe import _
from frappe.permissions import add_permission, update_permission_property
from frappe.query_builder.functions import Coalesce, Count, Max

from crm.api.doc import get_assigned_users
from crm.fcrm.doctype.crm_notification.crm_notification import notify_user
from crm.integrations.api import get_contact_lead_or_deal_from_number
from crm.utils import parse_phone_number

ALLOWED_WHATSAPP_ROLES = ["System Manager", "Sales Manager", "Sales User"]
WHATSAPP_LEAD_SOURCE = "WhatsApp"

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
def get_whatsapp_conversations(limit: int = CONVERSATION_LIMIT):
	"""Return one row per WhatsApp conversation across every CRM Lead and Deal.

	Powers the shared team inbox. Three bounded queries regardless of how many
	messages exist: one GROUP BY for the counts/last timestamps, one to fetch
	the last message of each conversation, and one per reference doctype to
	resolve display names. Conversations whose reference document the current
	user cannot read are dropped, since the reference lookup is permission
	aware.
	"""
	validate_access()

	# twilio integration app is not compatible with crm app
	if "twilio_integration" in frappe.get_installed_apps():
		return []
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		return []

	aggregates = get_conversation_aggregates()
	if not aggregates:
		return []

	# Trim before the follow-up lookups so their `in (...)` lists stay bounded.
	aggregates.sort(key=lambda row: row["last_at"], reverse=True)
	limit = frappe.utils.cint(limit) or CONVERSATION_LIMIT
	aggregates = aggregates[:limit]

	last_messages = get_last_conversation_messages(aggregates)
	references = get_conversation_references(aggregates)

	conversations = []
	for row in aggregates:
		key = (row["reference_doctype"], row["reference_name"])
		reference = references.get(key)
		if not reference:
			# Reference document was deleted, or is not readable by this user.
			continue

		last_message = last_messages.get(key) or {}
		conversations.append(
			{
				"reference_doctype": row["reference_doctype"],
				"reference_name": row["reference_name"],
				"display_name": reference["display_name"],
				"phone": get_counterpart_number(last_message) or reference["phone"],
				"last_message": whatsapp_message_preview(last_message),
				"last_message_type": last_message.get("type"),
				"last_at": row["last_at"],
				"message_count": row["message_count"],
			}
		)

	conversations.sort(key=lambda conversation: conversation["last_at"], reverse=True)
	return conversations


def get_conversation_aggregates() -> list[dict]:
	"""One GROUP BY over WhatsApp Message: message count and last timestamp per conversation.

	Reactions are excluded because the thread view hides them too, so counting
	them would make the inbox disagree with the conversation it opens.
	"""
	Message = frappe.qb.DocType("WhatsApp Message")
	return (
		frappe.qb.from_(Message)
		.select(
			Message.reference_doctype,
			Message.reference_name,
			Count(Message.name).as_("message_count"),
			Max(Message.creation).as_("last_at"),
		)
		.where(
			Message.reference_doctype.isin(list(CONVERSATION_DOCTYPES))
			& (Coalesce(Message.reference_name, "") != "")
			& (Coalesce(Message.content_type, "text") != "reaction")
		)
		.groupby(Message.reference_doctype, Message.reference_name)
		.run(as_dict=True)
	)


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
	"""Resolve display name and phone for each linked Lead/Deal, honouring read permissions."""
	names = {doctype: [] for doctype in CONVERSATION_DOCTYPES}
	for row in aggregates:
		if row["reference_doctype"] in names and row["reference_name"]:
			names[row["reference_doctype"]].append(row["reference_name"])

	references = {}

	if names["CRM Lead"]:
		leads = frappe.get_list(
			"CRM Lead",
			filters={"name": ["in", names["CRM Lead"]]},
			fields=["name", "lead_name", "first_name", "last_name", "organization", "mobile_no"],
			limit_page_length=0,
		)
		for lead in leads:
			full_name = " ".join(part for part in [lead.first_name, lead.last_name] if part)
			references[("CRM Lead", lead.name)] = {
				"display_name": lead.lead_name or full_name or lead.organization or lead.name,
				"phone": lead.mobile_no or "",
			}

	if names["CRM Deal"]:
		deals = frappe.get_list(
			"CRM Deal",
			filters={"name": ["in", names["CRM Deal"]]},
			fields=["name", "organization", "lead_name", "mobile_no"],
			limit_page_length=0,
		)
		for deal in deals:
			references[("CRM Deal", deal.name)] = {
				"display_name": deal.organization or deal.lead_name or deal.name,
				"phone": deal.mobile_no or "",
			}

	return references


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
