"""Seed a demonstrable WhatsApp team inbox without any Meta credentials.

Run inside a bench::

	bench --site <site> execute crm.demo.whatsapp_demo.seed

It creates two sales users, four travel CRM Leads, seventeen WhatsApp Messages
spread over the last ten days, and an Active WhatsApp Account so
`crm.api.whatsapp.is_whatsapp_enabled` turns the UI on. The leads are shared
round-robin between the two sales users, so each of them logs in to their own
scoped inbox while Administrator keeps the manager view.

The message scripts are tuned so the inbox shows one hot, one warm and one cold
conversation, and one conversation that has been waiting on a reply for more
than two hours -- which is what `crm.api.whatsapp_followups` nudges about.

Everything is idempotent: re-running only fills in what is missing. No network
calls. See `insert_demo_message` for why that needs care.
"""

import os

import frappe
from frappe import _
from frappe.desk.form.assign_to import _add as assign
from frappe.utils import add_to_date, now_datetime

from crm.api.doc import get_assigned_users
from crm.api.whatsapp import (
	WHATSAPP_LEAD_SOURCE,
	ensure_whatsapp_lead_source,
)
from crm.install import add_default_lead_statuses

# The single business number every demo conversation is held with. Outgoing
# rows leave `from` empty exactly like frappe_whatsapp does, so this number
# lives on the demo WhatsApp Account rather than on each message.
DEMO_BUSINESS_NUMBER = "+15550100200"

DEMO_ACCOUNT_NAME = "Demo WhatsApp Account"

# Deterministic docnames make the seeder re-runnable: an existing row is skipped
# instead of duplicated. WhatsApp Message has no autoname, so we set our own.
DEMO_MESSAGE_PREFIX = "demo-whatsapp-"

# The salespeople the demo hands conversations to. Administrator stays the
# manager: it is the account that sees every conversation and the daily digest.
DEMO_SALES_USERS = [
	{
		"email": "priya@demo.crm",
		"first_name": "Priya",
		"last_name": "Sharma",
		"mobile_no": "+15550100301",
	},
	{
		"email": "rahul@demo.crm",
		"first_name": "Rahul",
		"last_name": "Menon",
		"mobile_no": "+15550100302",
	},
]

# Demo-only credential. Never a real password: these accounts exist so you can
# log in as two different salespeople on a throwaway site. Set CRM_DEMO_PASSWORD
# in the environment to override it before seeding a box anyone else can reach.
DEMO_USER_PASSWORD = os.environ.get("CRM_DEMO_PASSWORD") or "demo1234"

DEMO_LEADS = [
	{
		"first_name": "Amara",
		"last_name": "Okafor",
		"email": "amara.okafor@example.com",
		"mobile_no": "+15551230101",
		"organization": "Lumen Analytics",
		"job_title": "Head of Growth",
		"status": "Proposal Sent",
		"destination": "Kenya — Masai Mara",
		"travel_start_in_days": 45,
		"travel_days": 8,
		"group_size": 4,
		"budget": 9500,
	},
	{
		"first_name": "Diego",
		"last_name": "Ferreira",
		"email": "diego.ferreira@example.com",
		"mobile_no": "+15551230102",
		"organization": "Northwind Logistics",
		"job_title": "Operations Director",
		"status": "Negotiation",
		"destination": "Portugal — Douro Valley",
		"travel_start_in_days": 30,
		"travel_days": 6,
		"group_size": 12,
		"budget": 18000,
	},
	{
		"first_name": "Priya",
		"last_name": "Raman",
		"email": "priya.raman@example.com",
		"mobile_no": "+15551230103",
		"organization": "Kestrel Health",
		"job_title": "Procurement Lead",
		"status": "Booked",
		"destination": "Japan — Kyoto & Hakone",
		"travel_start_in_days": 90,
		"travel_days": 11,
		"group_size": 2,
		"budget": 12400,
	},
	{
		"first_name": "Jonas",
		"last_name": "Weber",
		"email": "jonas.weber@example.com",
		"mobile_no": "+15551230104",
		"organization": "Halden Manufacturing",
		"job_title": "Plant Manager",
		"status": "Contacted",
		"destination": "Norway — Lofoten",
		"travel_start_in_days": 150,
		"travel_days": 5,
		"group_size": 6,
		"budget": 7200,
	},
]

# Lead fields the seeder derives rather than copies straight from DEMO_LEADS.
DEMO_LEAD_DERIVED_FIELDS = ("travel_start_in_days", "travel_days")

# One script per lead, ordered oldest first. `minutes_ago` is measured from the
# moment the seeder runs, so the inbox always looks freshly active.
#
# The offsets are the demo: script 1 is hot and answered (five messages inside a
# week), script 2 is hot and overdue (an incoming message unanswered for three
# hours, which is what the follow-up nudge fires on), script 3 is warm (a short
# thread, last touched two days ago) and script 4 is cold (nothing for nine
# days). See `crm.api.whatsapp.compute_priority`.
#
# `status` on an Outgoing row uses the same vocabulary the live send path writes
# ("Success" / "Failed"), not Meta's delivery words ("sent"/"delivered"/"read"),
# so a seeded row and a really-sent row read the same. frappe_whatsapp is not
# vendored in this repo, so the exact option list could not be verified here --
# check WhatsApp Message's `status` field if the demo ever shows a blank status.
DEMO_CONVERSATIONS = [
	[
		{
			"minutes_ago": 190,
			"type": "Incoming",
			"message": "Hi! We are 4 friends looking at a Kenya safari in the autumn.",
		},
		{
			"minutes_ago": 178,
			"type": "Outgoing",
			"message": "Hi Amara! The Mara is at its best then. Are you flexible on the dates?",
			"status": "Success",
		},
		{
			"minutes_ago": 165,
			"type": "Outgoing",
			"content_type": "image",
			"attach": "/assets/crm/images/desk.png",
			"message": "This is the camp most of our groups start from.",
			"status": "Success",
		},
		{
			"minutes_ago": 40,
			"type": "Incoming",
			"message": "That looks great. Can you send the full itinerary and price?",
		},
		{
			"minutes_ago": 12,
			"type": "Outgoing",
			"message": "Sending the proposal over now — happy to walk through it on a call.",
			"status": "Success",
		},
	],
	[
		{
			"minutes_ago": 1180,
			"type": "Outgoing",
			"message": "Hi Diego, following up on the Douro trip for the leadership offsite.",
			"status": "Success",
		},
		{
			"minutes_ago": 1140,
			"type": "Incoming",
			"message": "Thanks for the nudge. Finance asked for the signed quote first.",
		},
		{
			"minutes_ago": 1120,
			"type": "Outgoing",
			"content_type": "document",
			"attach": "/assets/crm/images/logo.svg",
			"message": "",
			"status": "Success",
		},
		{
			"minutes_ago": 300,
			"type": "Outgoing",
			"message": "Quote attached. It holds the river-view rooms until Friday.",
			"status": "Success",
		},
		{
			"minutes_ago": 190,
			"type": "Incoming",
			"message": "Got it. Can you also price a two day Lisbon extension for 12?",
		},
	],
	[
		{
			"minutes_ago": 2900,
			"type": "Incoming",
			"message": "We booked Kyoto — could you add the Hakone leg for the two of us?",
		},
		{
			"minutes_ago": 2880,
			"type": "Outgoing",
			"message": "Done, Priya. Two nights at the ryokan with the private onsen.",
			"status": "Success",
		},
		{
			"minutes_ago": 2870,
			"type": "Outgoing",
			"message": "Vouchers are in your inbox. Safe travels!",
			"status": "Success",
		},
	],
	[
		{
			"minutes_ago": 13000,
			"type": "Incoming",
			"message": "Hello, we met at the travel fair — six of us are eyeing Lofoten.",
		},
		{
			"minutes_ago": 12980,
			"type": "Outgoing",
			"message": "Hi Jonas! Good to hear from you. Summer or northern lights season?",
			"status": "Success",
		},
		{
			"minutes_ago": 12950,
			"type": "Incoming",
			"message": "Lights, most likely. The group has not settled on a budget yet.",
		},
		{
			"minutes_ago": 12900,
			"type": "Outgoing",
			"message": "No rush — ping me once they have, and I will hold the cabins.",
			"status": "Success",
		},
	],
]


def seed():
	"""Create (or top up) the demo WhatsApp inbox. Safe to run repeatedly."""
	if not frappe.db.exists("DocType", "WhatsApp Message"):
		frappe.throw(_("Install the frappe_whatsapp app before seeding demo WhatsApp data."))

	account_name = ensure_demo_whatsapp_account()
	ensure_demo_whatsapp_settings(account_name)
	sales_users = ensure_demo_sales_users()
	# The ladder statuses the demo leads sit on ship with the app, but a site
	# seeded before they existed only gets them at the next migrate.
	add_default_lead_statuses()
	leads = ensure_demo_leads()
	assignments = assign_demo_leads(leads)
	messages_created = seed_demo_messages(leads, account_name)

	# `bench execute` prints whatever the method returns.
	return {
		"account": account_name,
		"business_number": DEMO_BUSINESS_NUMBER,
		"sales_users": sales_users,
		"leads": [lead["name"] for lead in leads],
		"assignments": assignments,
		"messages_created": messages_created,
	}


def ensure_demo_sales_users() -> list[str]:
	"""Create the two demo salespeople, both plain Sales Users.

	They exist so the scoped inbox can be shown from two different logins. The
	password is a fixed demo credential and the password policy is bypassed on
	purpose -- this seeder is for throwaway sites only.
	"""
	created = []
	for data in DEMO_SALES_USERS:
		if frappe.db.exists("User", data["email"]):
			created.append(data["email"])
			continue

		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": data["email"],
				"first_name": data["first_name"],
				"last_name": data["last_name"],
				"mobile_no": data["mobile_no"],
				"send_welcome_email": 0,
				"user_type": "System User",
				"new_password": DEMO_USER_PASSWORD,
				"roles": [{"role": "Sales User"}],
			}
		)
		user.flags.ignore_password_policy = True
		user.insert(ignore_permissions=True)
		created.append(user.name)

	return created


def assign_demo_leads(leads: list[dict]) -> dict[str, str]:
	"""Share the demo leads round-robin between the two demo salespeople.

	The live helper (`crm.api.whatsapp.assign_whatsapp_lead`) picks from every
	Sales User on the site, so on a site that already has real -- or
	setup-wizard -- Sales Users the demo conversations land on accounts nobody
	can log in to. The demo needs its two scripted inboxes, so it assigns to
	DEMO_SALES_USERS directly, through the same `assign_to` mechanism.

	Idempotent: a lead that already carries an open assignment is left alone.
	"""
	demo_users = [data["email"] for data in DEMO_SALES_USERS]
	assignments = {}
	for index, lead in enumerate(leads):
		if get_assigned_users("CRM Lead", lead["name"]):
			continue

		assignee = demo_users[index % len(demo_users)]
		assign(
			{"assign_to": [assignee], "doctype": "CRM Lead", "name": lead["name"]},
			ignore_permissions=True,
		)
		assignments[lead["name"]] = assignee

	return assignments


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
		values = demo_lead_values(data)
		name = frappe.db.get_value("CRM Lead", {"mobile_no": data["mobile_no"]}, "name")
		if name:
			backfill_demo_lead(name, values)
		else:
			lead = frappe.get_doc(
				{
					"doctype": "CRM Lead",
					"source": WHATSAPP_LEAD_SOURCE,
					**values,
				}
			).insert(ignore_permissions=True)
			name = lead.name
		leads.append({**data, "name": name})

	return leads


def demo_lead_values(data: dict) -> dict:
	"""Turn one DEMO_LEADS entry into CRM Lead field values.

	Travel dates are stored relative to today so a re-seeded demo never shows a
	trip that has already happened.
	"""
	values = {key: value for key, value in data.items() if key not in DEMO_LEAD_DERIVED_FIELDS}
	start_date = add_to_date(now_datetime(), days=data["travel_start_in_days"]).date()
	values["travel_start_date"] = start_date
	values["travel_end_date"] = add_to_date(start_date, days=data["travel_days"])
	return values


def backfill_demo_lead(name: str, values: dict):
	"""Fill in fields an earlier seeding run did not write, leaving edits alone."""
	lead = frappe.get_doc("CRM Lead", name)
	changed = False
	for fieldname, value in values.items():
		if not lead.get(fieldname):
			lead.set(fieldname, value)
			changed = True

	if changed:
		lead.save(ignore_permissions=True)


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
			# Incoming rows carry no send status; outgoing ones use the live
			# vocabulary ("Success" / "Failed"). See DEMO_CONVERSATIONS.
			"status": "" if is_incoming else (message.get("status") or "Success"),
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
