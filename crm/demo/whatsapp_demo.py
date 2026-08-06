"""Seed a demonstrable WhatsApp team inbox without any Meta credentials.

Run inside a bench::

	bench --site <site> execute crm.demo.whatsapp_demo.seed

It creates four CRM Leads, twenty WhatsApp Messages spread over the last three
days, and an Active WhatsApp Account so `crm.api.whatsapp.is_whatsapp_enabled`
turns the UI on. Everything is idempotent: re-running only fills in what is
missing.

No network calls. See `insert_demo_message` for why that needs care.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, now_datetime

from crm.api.whatsapp import WHATSAPP_LEAD_SOURCE, ensure_whatsapp_lead_source

# The single business number every demo conversation is held with. Outgoing
# rows leave `from` empty exactly like frappe_whatsapp does, so this number
# lives on the demo WhatsApp Account rather than on each message.
DEMO_BUSINESS_NUMBER = "+15550100200"

DEMO_ACCOUNT_NAME = "Demo WhatsApp Account"

# Deterministic docnames make the seeder re-runnable: an existing row is skipped
# instead of duplicated. WhatsApp Message has no autoname, so we set our own.
DEMO_MESSAGE_PREFIX = "demo-whatsapp-"

DEMO_LEADS = [
	{
		"first_name": "Amara",
		"last_name": "Okafor",
		"email": "amara.okafor@example.com",
		"mobile_no": "+15551230101",
		"organization": "Lumen Analytics",
		"job_title": "Head of Growth",
		"status": "Qualified",
	},
	{
		"first_name": "Diego",
		"last_name": "Ferreira",
		"email": "diego.ferreira@example.com",
		"mobile_no": "+15551230102",
		"organization": "Northwind Logistics",
		"job_title": "Operations Director",
		"status": "Contacted",
	},
	{
		"first_name": "Priya",
		"last_name": "Raman",
		"email": "priya.raman@example.com",
		"mobile_no": "+15551230103",
		"organization": "Kestrel Health",
		"job_title": "Procurement Lead",
		"status": "Nurture",
	},
	{
		"first_name": "Jonas",
		"last_name": "Weber",
		"email": "jonas.weber@example.com",
		"mobile_no": "+15551230104",
		"organization": "Halden Manufacturing",
		"job_title": "Plant Manager",
		"status": "Contacted",
	},
]

# One script per lead, ordered oldest first. `minutes_ago` is measured from the
# moment the seeder runs, so the inbox always looks freshly active.
DEMO_CONVERSATIONS = [
	[
		{
			"minutes_ago": 190,
			"type": "Incoming",
			"message": "Hi! We saw your pricing page — do you support multi-region reporting?",
		},
		{
			"minutes_ago": 178,
			"type": "Outgoing",
			"message": "Hi Amara, we do. Regions roll up into a single dashboard by default.",
			"status": "read",
		},
		{
			"minutes_ago": 165,
			"type": "Outgoing",
			"content_type": "image",
			"attach": "/assets/crm/images/desk.png",
			"message": "Here is the roll-up view our customers start with.",
			"status": "read",
		},
		{
			"minutes_ago": 40,
			"type": "Incoming",
			"message": "That looks great. Can you send the enterprise plan details?",
		},
		{
			"minutes_ago": 12,
			"type": "Outgoing",
			"message": "Sending it over now — happy to walk through it on a call this week.",
			"status": "delivered",
		},
	],
	[
		{
			"minutes_ago": 1180,
			"type": "Outgoing",
			"message": "Hi Diego, following up on the fleet tracking pilot we discussed.",
			"status": "read",
		},
		{
			"minutes_ago": 1140,
			"type": "Incoming",
			"message": "Thanks for the nudge. Finance asked for the signed scope first.",
		},
		{
			"minutes_ago": 1120,
			"type": "Outgoing",
			"content_type": "document",
			"attach": "/assets/crm/images/logo.svg",
			"message": "",
			"status": "read",
		},
		{
			"minutes_ago": 480,
			"type": "Incoming",
			"message": "Got it, forwarding internally today.",
		},
		{
			"minutes_ago": 305,
			"type": "Outgoing",
			"message": "Perfect. Ping me when procurement has questions.",
			"status": "sent",
		},
	],
	[
		{
			"minutes_ago": 2600,
			"type": "Incoming",
			"message": "Is the compliance module included in the base subscription?",
		},
		{
			"minutes_ago": 2570,
			"type": "Outgoing",
			"message": "It is, Priya. Audit trails and retention policies are all standard.",
			"status": "read",
		},
		{
			"minutes_ago": 2540,
			"type": "Incoming",
			"message": "And can we export the audit log monthly?",
		},
		{
			"minutes_ago": 2510,
			"type": "Outgoing",
			"message": "Yes — scheduled CSV exports, or the API if you prefer.",
			"status": "read",
		},
		{
			"minutes_ago": 1520,
			"type": "Incoming",
			"message": "Great, I will bring this to our review on Thursday.",
		},
	],
	[
		{
			"minutes_ago": 4200,
			"type": "Incoming",
			"message": "Hello, we met at the manufacturing expo last month.",
		},
		{
			"minutes_ago": 4180,
			"type": "Outgoing",
			"message": "Hi Jonas! Good to hear from you. How is the line upgrade going?",
			"status": "read",
		},
		{
			"minutes_ago": 4150,
			"type": "Incoming",
			"message": "Slower than planned. We need better downtime reporting.",
		},
		{
			"minutes_ago": 4120,
			"type": "Outgoing",
			"message": "That is exactly what the shop-floor dashboard covers. Shall I book a demo?",
			"status": "read",
		},
		{
			"minutes_ago": 3600,
			"type": "Incoming",
			"message": "Please do — next Tuesday morning works for us.",
		},
	],
]


def seed():
	"""Create (or top up) the demo WhatsApp inbox. Safe to run repeatedly."""
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		frappe.throw(_("Install the frappe_whatsapp app before seeding demo WhatsApp data."))

	account_name = ensure_demo_whatsapp_account()
	ensure_demo_whatsapp_settings(account_name)
	leads = ensure_demo_leads()
	messages_created = seed_demo_messages(leads, account_name)

	# `bench execute` prints whatever the method returns.
	return {
		"account": account_name,
		"business_number": DEMO_BUSINESS_NUMBER,
		"leads": [lead["name"] for lead in leads],
		"messages_created": messages_created,
	}


def ensure_demo_whatsapp_account() -> str:
	"""Create an Active WhatsApp Account with placeholder credentials.

	Nothing here is ever transmitted: the seeder never inserts a message through
	the document lifecycle, so `WhatsAppMessage.notify()` is never reached. The
	account exists purely so `is_whatsapp_enabled` returns True and so every
	message row has a valid `whatsapp_account` link.
	"""
	exists = bool(frappe.db.exists("WhatsApp Account", DEMO_ACCOUNT_NAME))
	if exists:
		account = frappe.get_doc("WhatsApp Account", DEMO_ACCOUNT_NAME)
	else:
		account = frappe.get_doc(
			{
				"doctype": "WhatsApp Account",
				"account_name": DEMO_ACCOUNT_NAME,
				"token": "demo-token-not-a-real-credential",
				"url": "https://graph.facebook.com",
				"version": "v19.0",
				"phone_id": "000000000000000",
				"business_id": "000000000000000",
				"app_id": "000000000000000",
				"webhook_verify_token": "demo-verify-token",
			}
		)

	account.status = "Active"

	# Only claim the default flags when no other account holds them, so a real
	# configuration on the same site is never hijacked.
	for fieldname in ("is_default_incoming", "is_default_outgoing"):
		current_default = frappe.db.get_value("WhatsApp Account", {fieldname: 1}, "name")
		if current_default in (None, DEMO_ACCOUNT_NAME):
			account.set(fieldname, 1)

	if exists:
		account.save(ignore_permissions=True)
	else:
		account.insert(ignore_permissions=True)

	return account.name


def ensure_demo_whatsapp_settings(account_name: str):
	"""Point WhatsApp Settings at the demo account unless a working one is set."""
	settings = frappe.get_single("WhatsApp Settings")
	changed = False

	for fieldname in ("default_incoming_account", "default_outgoing_account"):
		current = settings.get(fieldname)
		if current and frappe.db.get_value("WhatsApp Account", current, "status") == "Active":
			continue
		settings.set(fieldname, account_name)
		changed = True

	if changed:
		settings.save(ignore_permissions=True)


def ensure_demo_leads() -> list[dict]:
	"""Create the demo leads, matching on mobile number so re-runs reuse them."""
	ensure_whatsapp_lead_source()

	leads = []
	for data in DEMO_LEADS:
		name = frappe.db.get_value("CRM Lead", {"mobile_no": data["mobile_no"]}, "name")
		if not name:
			lead = frappe.get_doc(
				{
					"doctype": "CRM Lead",
					"source": WHATSAPP_LEAD_SOURCE,
					**data,
				}
			).insert(ignore_permissions=True)
			name = lead.name
		leads.append({**data, "name": name})

	return leads


def seed_demo_messages(leads: list[dict], account_name: str) -> int:
	"""Insert every scripted message that does not exist yet. Returns how many were added."""
	reference_time = now_datetime()
	created = 0

	for lead_index, lead in enumerate(leads):
		script = DEMO_CONVERSATIONS[lead_index]
		for message_index, message in enumerate(script):
			docname = f"{DEMO_MESSAGE_PREFIX}{lead_index + 1}-{message_index + 1:02d}"
			if frappe.db.exists("WhatsApp Message", docname):
				continue

			timestamp = add_to_date(reference_time, minutes=-message["minutes_ago"])
			insert_demo_message(docname, lead, message, timestamp, account_name)
			created += 1

	return created


def insert_demo_message(docname: str, lead: dict, message: dict, timestamp, account_name: str):
	"""Write one WhatsApp Message row directly, bypassing the document lifecycle.

	This is the whole reason the seeder exists as its own module.
	`WhatsAppMessage.before_insert` (frappe_whatsapp) calls `send_outgoing()`,
	which POSTs every Outgoing message to the Meta Graph API and raises when the
	token is fake. `db_insert()` runs a plain INSERT with no `before_insert`,
	`validate` or `on_update`, so there is no HTTP request, no realtime event and
	no agent notification -- exactly what a demo seeder needs.

	Because no hook runs, every column the framework would normally fill in
	(name, owner, timestamps, docstatus, idx) is set explicitly here.
	"""
	is_incoming = message["type"] == "Incoming"
	full_name = f"{lead['first_name']} {lead['last_name']}"

	doc = frappe.new_doc("WhatsApp Message")
	doc.update(
		{
			"type": message["type"],
			# Meta delivers sender ids as E.164 digits without the leading plus,
			# and leaves `from` empty on messages we sent ourselves.
			"from": lead["mobile_no"].lstrip("+") if is_incoming else "",
			"to": "" if is_incoming else lead["mobile_no"],
			"message": message.get("message") or "",
			"content_type": message.get("content_type") or "text",
			"attach": message.get("attach") or "",
			"message_type": "Manual",
			"message_id": f"wamid.demo.{docname}",
			"status": "" if is_incoming else (message.get("status") or "sent"),
			"profile_name": full_name if is_incoming else "",
			"whatsapp_account": account_name,
			"reference_doctype": "CRM Lead",
			"reference_name": lead["name"],
		}
	)

	doc.name = docname
	doc.owner = doc.modified_by = frappe.session.user
	doc.creation = doc.modified = timestamp
	doc.docstatus = 0
	doc.idx = 0
	doc.db_insert()
