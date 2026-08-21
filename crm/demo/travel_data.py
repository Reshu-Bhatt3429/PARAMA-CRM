"""Demo travel data: itineraries, invoices and Today-queue tasks.

Run with:

    bench --site <site> execute crm.demo.travel_data.seed

Idempotent and offline: every step first checks what already exists, no AI
call and no network request is made, and re-running the seeder creates no
duplicate. It builds on the records left by `crm.demo.api.create_demo_data`
and by `crm.demo.whatsapp_demo.seed`, but it does not require either.

NEVER run this on a real production site: it writes demo values into the
CRM Company Profile fields that are still empty.
"""

from datetime import date, timedelta

import frappe
from frappe.utils import add_days, flt, nowdate

SEED_KEY = "crm_travel_demo_seeded"

# --- itinerary day templates (cycled when a trip is longer) -----------------

DAY_TEMPLATES = {
	"thailand": [
		{
			"title": "Arrival in Krabi & Ao Nang sunset",
			"summary": "Land at Krabi International, private transfer to the resort, evening at Ao Nang beachfront.",
			"highlights": ["Private airport transfer", "Welcome drink on arrival", "Ao Nang night market"],
			"accommodation": "Centara Ao Nang Beach Resort — Deluxe Ocean View",
		},
		{
			"title": "Four Islands speedboat tour",
			"summary": "Full-day speedboat across Phra Nang Cave Beach, Chicken Island, Tup Island and Poda Island.",
			"highlights": ["Snorkelling at Chicken Island", "Picnic lunch on Poda Island", "Railay viewpoint"],
			"accommodation": "Centara Ao Nang Beach Resort — Deluxe Ocean View",
		},
		{
			"title": "Ferry to Phi Phi & island walk",
			"summary": "Morning ferry to Phi Phi Don, check-in, afternoon walk to the classic twin-bay viewpoint.",
			"highlights": ["Phi Phi viewpoint hike", "Tonsai village lanes", "Long-tail boats at sunset"],
			"accommodation": "Phi Phi Island Village Beach Resort — Garden Bungalow",
		},
		{
			"title": "Maya Bay & Pileh Lagoon",
			"summary": "Early long-tail charter to Maya Bay before the crowds, swim stop in Pileh Lagoon.",
			"highlights": ["Maya Bay at first light", "Pileh Lagoon swim", "Monkey Beach pass-by"],
			"accommodation": "Phi Phi Island Village Beach Resort — Garden Bungalow",
		},
		{
			"title": "Free day & Thai cooking class",
			"summary": "Morning at leisure, optional dive intro, sunset Thai cooking class with a market visit.",
			"highlights": ["Hands-on cooking class", "Local market tour", "Beach time"],
			"accommodation": "Phi Phi Island Village Beach Resort — Garden Bungalow",
		},
	],
	"bali": [
		{
			"title": "Arrival & Seminyak evening",
			"summary": "Land at Ngurah Rai, private transfer to Seminyak, sunset at Double Six Beach.",
			"highlights": ["Private transfer", "Double Six sunset", "Seminyak dinner strip"],
			"accommodation": "The Seminyak Beach Resort — Deluxe Room",
		},
		{
			"title": "Uluwatu temple & Kecak dance",
			"summary": "South-coast day: Padang Padang beach, Uluwatu cliff temple, Kecak fire dance at dusk.",
			"highlights": ["Uluwatu cliff walk", "Kecak fire dance", "Jimbaran seafood dinner"],
			"accommodation": "The Seminyak Beach Resort — Deluxe Room",
		},
		{
			"title": "Move to Ubud via Tegalalang",
			"summary": "Drive inland with stops at the Tegalalang rice terraces and a family coffee plantation.",
			"highlights": ["Tegalalang rice terraces", "Luwak coffee tasting", "Ubud Palace evening"],
			"accommodation": "Alaya Resort Ubud — Deluxe Room",
		},
		{
			"title": "Sacred Monkey Forest & spa",
			"summary": "Morning in the Monkey Forest sanctuary, afternoon Balinese massage and pool time.",
			"highlights": ["Monkey Forest sanctuary", "Traditional Balinese spa", "Campuhan ridge walk"],
			"accommodation": "Alaya Resort Ubud — Deluxe Room",
		},
	],
	"goa": [
		{
			"title": "Arrival & Baga sundowner",
			"summary": "Airport pickup, check-in near Baga, first evening on the beach shacks strip.",
			"highlights": ["Beachfront check-in", "Baga shacks", "Tito's lane"],
			"accommodation": "Resort Rio, Arpora — Premium Room",
		},
		{
			"title": "Old Goa & Panjim heritage",
			"summary": "Basilica of Bom Jesus, Se Cathedral, then the Latin Quarter lanes of Fontainhas.",
			"highlights": ["Basilica of Bom Jesus", "Fontainhas walk", "Mandovi river cruise"],
			"accommodation": "Resort Rio, Arpora — Premium Room",
		},
		{
			"title": "North beaches & Chapora fort",
			"summary": "Morgim, Vagator and Anjuna by scooter or cab, sunset from Chapora fort.",
			"highlights": ["Vagator cliffs", "Anjuna flea market", "Chapora fort sunset"],
			"accommodation": "Resort Rio, Arpora — Premium Room",
		},
	],
	"kenya": [
		{
			"title": "Nairobi arrival & briefing",
			"summary": "Meet-and-greet at JKIA, city hotel check-in, evening safari briefing with the guide.",
			"highlights": ["Dedicated safari guide", "Giraffe Centre visit", "Carnivore dinner"],
			"accommodation": "Nairobi Serena Hotel — Standard Room",
		},
		{
			"title": "Fly to the Masai Mara",
			"summary": "Morning bush flight to the Mara, afternoon game drive on arrival.",
			"highlights": ["Bush flight over the Rift", "First game drive", "Sundowner in the savannah"],
			"accommodation": "Mara Serena Safari Lodge — Full Board",
		},
		{
			"title": "Full-day game drive",
			"summary": "Dawn-to-dusk drive tracking the Big Five, picnic lunch by the Mara river crossing points.",
			"highlights": ["Big Five tracking", "Mara river hippos", "Maasai village visit"],
			"accommodation": "Mara Serena Safari Lodge — Full Board",
		},
		{
			"title": "Balloon safari & leisure",
			"summary": "Optional sunrise balloon flight with champagne breakfast, afternoon at the lodge pool.",
			"highlights": ["Sunrise balloon safari", "Champagne bush breakfast", "Lodge pool with a view"],
			"accommodation": "Mara Serena Safari Lodge — Full Board",
		},
	],
	"default": [
		{
			"title": "Arrival & orientation",
			"summary": "Airport pickup, hotel check-in and a relaxed first-evening walk near the hotel.",
			"highlights": ["Private transfer", "Welcome briefing", "Neighbourhood walk"],
			"accommodation": "4-star city hotel — Deluxe Room",
		},
		{
			"title": "City highlights tour",
			"summary": "Guided day across the landmark sights with a local lunch stop.",
			"highlights": ["Guided sightseeing", "Local lunch", "Old town walk"],
			"accommodation": "4-star city hotel — Deluxe Room",
		},
		{
			"title": "Day trip & local experience",
			"summary": "Excursion outside the city with one hands-on local experience.",
			"highlights": ["Countryside excursion", "Local craft workshop", "Scenic viewpoint"],
			"accommodation": "4-star city hotel — Deluxe Room",
		},
	],
}

INCLUSIONS = (
	"Accommodation with daily breakfast\n"
	"All airport and inter-city transfers in a private vehicle\n"
	"Sightseeing and entry tickets as listed per day\n"
	"English-speaking local guide on tour days\n"
	"All applicable taxes"
)

EXCLUSIONS = (
	"International and domestic airfare unless stated\n"
	"Visa fees and travel insurance\n"
	"Lunches and dinners not listed\n"
	"Personal expenses, tips and porterage\n"
	"Anything not named under inclusions"
)

TERMS = (
	"A 30% advance confirms the booking; the balance is due 21 days before departure.\n"
	"Rates are subject to availability at the time of confirmation.\n"
	"Cancellation: 100% refund 45+ days out, 50% at 30-44 days, none under 30 days."
)

COMPANY_PROFILE_DEMO = {
	"legal_name": "PARAMA Travel Private Limited",
	"address": "12, MG Road, Bengaluru, Karnataka 560001",
	"state_code": "29",
	"gstin": "29ABCDE1234F1Z5",
}


def template_for(destination: str) -> list[dict]:
	text = (destination or "").lower()
	for key, days in DAY_TEMPLATES.items():
		if key != "default" and key in text:
			return days
	return DAY_TEMPLATES["default"]


def build_days(destination: str, num_days: int) -> list[dict]:
	from crm.fcrm.doctype.crm_itinerary.crm_itinerary import empty_day

	template = template_for(destination)
	days = []
	for number in range(1, max(num_days, 1) + 1):
		spec = template[(number - 1) % len(template)]
		day = empty_day(number, title=spec["title"], summary=spec["summary"])
		day["highlights"] = list(spec["highlights"])
		day["accommodation"] = spec["accommodation"]
		day["description"] = spec["summary"]
		day["meals"] = {"breakfast": True, "lunch": number % 2 == 0, "dinner": number % 3 == 0}
		days.append(day)
	return days


def ensure_company_profile():
	"""Fill ONLY the still-empty Rule-46 fields, so a finalize cannot block."""
	from crm.invoicing import COMPANY_PROFILE_DOCTYPE

	profile = frappe.get_doc(COMPANY_PROFILE_DOCTYPE)
	changed = False
	for field, value in COMPANY_PROFILE_DEMO.items():
		if not (profile.get(field) or "").strip():
			profile.set(field, value)
			changed = True
	if changed:
		profile.save(ignore_permissions=True)
	return changed


def fill_itinerary(doc, statuses):
	"""Give one itinerary a full set of days, price tiers and terms."""
	from crm.fcrm.doctype.crm_itinerary.crm_itinerary import dump_days, parse_days

	if parse_days(doc.days_json):
		return False  # already has content; leave the agent's work alone

	num_days = int(doc.num_days or 0) or len(template_for(doc.destination))
	doc.num_days = num_days
	doc.days_json = dump_days(build_days(doc.destination, num_days))
	base = flt(doc.budget) or 45000
	if not doc.get("price_tiers"):
		doc.append("price_tiers", {"tier_label": "Standard", "price_per_person": base})
		doc.append("price_tiers", {"tier_label": "Deluxe", "price_per_person": round(base * 1.35)})
		doc.append("price_tiers", {"tier_label": "Luxury", "price_per_person": round(base * 1.8)})
	doc.inclusions = doc.inclusions or INCLUSIONS
	doc.exclusions = doc.exclusions or EXCLUSIONS
	doc.terms = doc.terms or TERMS
	doc.trip_vibe = doc.trip_vibe or "Leisure"
	if doc.status == "Draft" and statuses:
		doc.status = statuses.pop(0)
	doc.save(ignore_permissions=True)
	return True


def seed_itineraries(limit_new: int = 4) -> dict:
	from crm.api.itinerary import create_from_lead

	filled = 0
	created = 0
	# Cycle so the list page shows every status, not one repeated.
	statuses = ["Sent", "Revised", "Sent"]

	for doc_name in frappe.get_all("CRM Itinerary", pluck="name"):
		doc = frappe.get_doc("CRM Itinerary", doc_name)
		if fill_itinerary(doc, statuses):
			filled += 1

	with_itinerary = {row.lead for row in frappe.get_all("CRM Itinerary", fields=["lead"])}
	leads = frappe.get_all(
		"CRM Lead",
		filters={"destination": ("is", "set"), "converted": 0},
		fields=["name"],
		order_by="modified desc",
		limit_page_length=20,
	)
	for lead in leads:
		if created >= limit_new:
			break
		if lead.name in with_itinerary:
			continue
		doc = create_from_lead(lead.name)
		fill_itinerary(frappe.get_doc("CRM Itinerary", doc.name), statuses)
		created += 1

	return {"filled": filled, "created": created}


def demo_invoice_items(deal) -> list[dict]:
	"""Three believable tour-package lines sized off the deal's value."""
	total = flt(deal.get("deal_value")) or 90000
	return [
		{
			"description": "Tour package — accommodation and transfers",
			"sac": "998552",
			"qty": 1,
			"rate": round(total * 0.6),
			"tax_rate": 5,
		},
		{
			"description": "Sightseeing, activities and entry tickets",
			"sac": "998552",
			"qty": 1,
			"rate": round(total * 0.3),
			"tax_rate": 5,
		},
		{
			"description": "Travel insurance and documentation",
			"sac": "998552",
			"qty": 1,
			"rate": round(total * 0.1),
			"tax_rate": 5,
		},
	]


# GST Rule 46: an unregistered-customer invoice of ₹50,000+ needs the
# recipient's name, address and state code, so every demo invoice carries them.
DEMO_ADDRESSES = [
	("14, Residency Road, Bengaluru, Karnataka 560025", "Karnataka", "29", ""),
	("B-402, Lokhandwala Complex, Andheri West, Mumbai 400053", "Maharashtra", "27", "27AAACD5678E1Z3"),
	("22, Anna Salai, Teynampet, Chennai 600018", "Tamil Nadu", "33", ""),
	("H-77, Sector 18, Noida, Uttar Pradesh 201301", "Uttar Pradesh", "09", "09AABCU9603R1ZM"),
	("5th Cross, Jubilee Hills, Hyderabad, Telangana 500033", "Telangana", "36", ""),
]


def seed_invoices(limit_new: int = 5) -> dict:
	"""A mixed book: Draft, Sent, Paid, Partially Paid and one overdue."""
	from crm.api.invoices import convert_deal

	invoiced_deals = {row.deal for row in frappe.get_all("CRM Invoice", fields=["deal"])}
	deals = frappe.get_all(
		"CRM Deal",
		filters={"status": ("not in", ["Lost"])},
		fields=["name"],
		order_by="modified desc",
		limit_page_length=20,
	)

	# Position in this list decides the invoice's fate, so one run produces
	# every state the tiles and the list page can show.
	plans = ["paid", "partial", "overdue", "sent", "draft"]
	made = []
	position = 0
	for deal in deals:
		if position >= limit_new:
			break
		if deal.name in invoiced_deals:
			continue
		plan = plans[position % len(plans)]
		position += 1

		result = convert_deal(deal.name)
		doc = frappe.get_doc("CRM Invoice", result["name"])
		if not doc.get("items"):
			for row in demo_invoice_items(frappe.get_doc("CRM Deal", deal.name)):
				doc.append("items", row)
		address, state, state_code, gstin = DEMO_ADDRESSES[(position - 1) % len(DEMO_ADDRESSES)]
		doc.customer_name = doc.customer_name or "Walk-in customer"
		doc.customer_address = doc.customer_address or address
		doc.customer_state = doc.customer_state or state
		doc.customer_state_code = doc.customer_state_code or state_code
		doc.customer_gstin = doc.customer_gstin or gstin
		if plan == "overdue":
			# Set while still a draft; finalize locks the document after this.
			doc.invoice_date = add_days(nowdate(), -21)
			doc.due_date = add_days(nowdate(), -14)
		doc.save(ignore_permissions=True)

		if plan != "draft":
			outcome = doc.finalize()
			if outcome.get("blockers"):
				frappe.throw("Invoice finalize blocked: " + "; ".join(outcome["blockers"]))
			doc = frappe.get_doc("CRM Invoice", doc.name)
			if plan == "paid":
				doc.record_payment(mode="UPI", reference="UPI/DEMO/8834")
			elif plan == "partial":
				doc.record_payment(
					amount=round(flt(doc.grand_total) * 0.3),
					mode="Bank Transfer",
					reference="NEFT-DEMO-2211",
					note="30% booking advance",
				)
		made.append({"invoice": doc.name, "plan": plan})

	return {"created": made}


def seed_today_tasks() -> int:
	"""Tasks due today and overdue, so the Today queue has work in it."""
	today = date.today()
	users = ["Administrator"]
	for email in ("priya@demo.crm", "rahul@demo.crm"):
		if frappe.db.exists("User", email):
			users.append(email)

	leads = frappe.get_all("CRM Lead", fields=["name", "lead_name"], order_by="modified desc", limit_page_length=6)
	deals = frappe.get_all("CRM Deal", fields=["name"], order_by="modified desc", limit_page_length=4)

	specs = []
	for index, lead in enumerate(leads[:4]):
		specs.append(
			{
				"title": f"Call {lead.lead_name or 'the customer'} about the itinerary",
				"priority": "High" if index % 2 == 0 else "Medium",
				"due_date": today if index % 2 == 0 else today - timedelta(days=1 + index),
				"reference_doctype": "CRM Lead",
				"reference_docname": lead.name,
			}
		)
	for index, deal in enumerate(deals[:3]):
		specs.append(
			{
				"title": "Send the payment link and confirm the advance",
				"priority": "High",
				"due_date": today if index == 0 else today - timedelta(days=index),
				"reference_doctype": "CRM Deal",
				"reference_docname": deal.name,
			}
		)

	created = 0
	for index, spec in enumerate(specs):
		exists = frappe.db.exists(
			"CRM Task",
			{
				"title": spec["title"],
				"reference_doctype": spec["reference_doctype"],
				"reference_docname": spec["reference_docname"],
			},
		)
		if exists:
			continue
		frappe.get_doc(
			{
				"doctype": "CRM Task",
				"title": spec["title"],
				"status": "Todo",
				"priority": spec["priority"],
				"due_date": spec["due_date"],
				"assigned_to": users[index % len(users)],
				"reference_doctype": spec["reference_doctype"],
				"reference_docname": spec["reference_docname"],
				"description": "Demo task seeded for the Today queue.",
			}
		).insert(ignore_permissions=True)
		created += 1
	return created


def seed():
	"""Fill itineraries, invoices and the Today queue with demo content."""
	frappe.flags.in_demo_seed = True

	profile_changed = ensure_company_profile()
	itineraries = seed_itineraries()
	invoices = seed_invoices()
	tasks = seed_today_tasks()

	frappe.db.set_default(SEED_KEY, "1")
	frappe.db.commit()

	summary = {
		"company_profile_filled": profile_changed,
		"itineraries": itineraries,
		"invoices": invoices,
		"tasks_created": tasks,
	}
	print(summary)
	return summary
