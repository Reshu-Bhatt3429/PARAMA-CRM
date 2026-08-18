# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the AI-assisted travel itinerary generator.

`crm.ai.client.complete` is patched in every generation test, so the suite never
reaches a provider and never spends a token. `frappe_whatsapp` is not installed
in CI either, so the send path patches `create_whatsapp_message` rather than
inserting a WhatsApp Message.

The PDF tests exercise `frappe.get_print` HTML rather than wkhtmltopdf. The
binary render is verified separately on the demo stack: in CI the site's
`host_name` decides whether wkhtmltopdf can fetch the print stylesheet, which
makes the binary path an environment test, not a code test. The HTML is what
this module owns, and it is where a template regression would show.
"""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.ai.client import AIConfigurationError, AIResponseError
from crm.api import itinerary as api
from crm.fcrm.doctype.crm_itinerary.crm_itinerary import (
	ItinerarySchemaError,
	dump_days,
	empty_day,
	parse_days,
)

LEAD_DOCTYPE = "CRM Lead"
ITINERARY_DOCTYPE = "CRM Itinerary"

OTHER_USER = "itinerary-outsider@example.com"


def lead_status() -> str:
	"""A live lead status.

	Not simply the first one: a status of type "Lost" makes CRM Lead demand a
	lost reason, and this fixture is about a trip that is still being planned.
	"""
	status = frappe.db.get_value("CRM Lead Status", {"type": ["!=", "Lost"]}, "name")
	if not status:
		status = (
			frappe.get_doc(
				{"doctype": "CRM Lead Status", "lead_status": "New", "position": 1, "type": "Open"}
			)
			.insert(ignore_permissions=True)
			.name
		)
	return status


def stub_pdf() -> bytes:
	"""A real, minimal PDF.

	Not a placeholder byte string: the File doctype runs every `.pdf` upload
	through `pdf_contains_js`, which parses it. Anything unparseable is rejected
	before the row is written, so a stub has to be a genuine document.
	"""
	from io import BytesIO

	from pypdf import PdfWriter

	writer = PdfWriter()
	writer.add_blank_page(width=595, height=842)
	buffer = BytesIO()
	writer.write(buffer)
	return buffer.getvalue()


def make_item(title="Snorkelling at the reef", **overrides):
	item = {
		"title": title,
		"description": "Two hours on the house reef with a guide.",
		"place_name": "Coral Bay",
		"duration_hours": 2.0,
		"est_cost": 1500,
		"verified": False,
	}
	item.update(overrides)
	return item


def make_day(day_number=1, items=None):
	day = empty_day(day_number, title=f"Day {day_number}", summary="A full day.")
	day["slots"][0]["items"] = items if items is not None else [make_item()]
	return day


class ItineraryTestCase(FrappeTestCase):
	"""Shared fixture: one travel lead, cleaned up after each test."""

	def setUp(self):
		frappe.set_user("Administrator")
		self.lead = frappe.get_doc(
			{
				"doctype": LEAD_DOCTYPE,
				"first_name": "Ravi",
				"last_name": "Kulkarni",
				"status": lead_status(),
				"mobile_no": "+919820000001",
				"destination": "Maldives",
				"travel_start_date": "2026-11-02",
				"travel_end_date": "2026-11-06",
				"group_size": 4,
				"budget": 250000,
			}
		).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		for name in frappe.get_all(ITINERARY_DOCTYPE, filters={"lead": self.lead.name}, pluck="name"):
			frappe.delete_doc(ITINERARY_DOCTYPE, name, force=True, ignore_permissions=True)
		frappe.delete_doc(LEAD_DOCTYPE, self.lead.name, force=True, ignore_permissions=True)
		frappe.db.rollback()

	def new_itinerary(self, **overrides):
		doc = api.create_from_lead(self.lead.name)
		if overrides:
			doc.update(overrides)
			doc.save()
		return doc


# --- creation --------------------------------------------------------------


class TestCreateFromLead(ItineraryTestCase):
	def test_prefills_every_travel_field_from_the_lead(self):
		doc = api.create_from_lead(self.lead.name)

		self.assertEqual(doc.lead, self.lead.name)
		self.assertEqual(doc.destination, "Maldives")
		self.assertEqual(str(doc.start_date), "2026-11-02")
		# 2 Nov to 6 Nov inclusive is five days, not four.
		self.assertEqual(doc.num_days, 5)
		self.assertEqual(doc.group_size, 4)
		self.assertEqual(doc.budget, 250000)
		self.assertEqual(doc.status, "Draft")
		self.assertEqual(doc.version, 1)
		self.assertEqual(doc.title, "Maldives — Ravi Kulkarni")
		self.assertEqual(parse_days(doc.days_json), [])

	def test_makes_no_ai_call(self):
		with patch("crm.api.itinerary.complete") as mock_complete:
			api.create_from_lead(self.lead.name)
		mock_complete.assert_not_called()

	def test_falls_back_to_one_day_without_travel_dates(self):
		self.lead.db_set("travel_start_date", None)
		self.lead.db_set("travel_end_date", None)
		doc = api.create_from_lead(self.lead.name)
		self.assertEqual(doc.num_days, 1)

	def test_clamps_an_absurd_trip_length(self):
		self.lead.db_set("travel_end_date", "2027-11-06")
		doc = api.create_from_lead(self.lead.name)
		self.assertEqual(doc.num_days, 30)

	def test_title_survives_a_lead_without_a_destination(self):
		self.lead.db_set("destination", "")
		doc = api.create_from_lead(self.lead.name)
		self.assertEqual(doc.title, "Ravi Kulkarni")


# --- skeleton --------------------------------------------------------------


class TestGenerateSkeleton(ItineraryTestCase):
	def test_merges_titles_and_summaries_for_every_day(self):
		doc = self.new_itinerary()
		answer = {
			"days": [{"day_number": n, "title": f"Title {n}", "summary": f"Summary {n}"} for n in range(1, 6)]
		}

		with patch("crm.api.itinerary.complete", return_value=answer):
			result = api.generate_skeleton(doc.name)

		days = result["days"]
		self.assertEqual(len(days), 5)
		self.assertEqual([day["day_number"] for day in days], [1, 2, 3, 4, 5])
		self.assertEqual(days[2]["title"], "Title 3")
		self.assertEqual(days[2]["summary"], "Summary 3")
		# The skeleton is the shape of the trip, so no day carries items yet.
		for day in days:
			self.assertEqual([slot["items"] for slot in day["slots"]], [[], [], []])

	def test_keeps_items_a_day_already_holds(self):
		doc = self.new_itinerary()
		doc.days_json = dump_days([make_day(1), make_day(2)])
		doc.save()

		answer = {"days": [{"day_number": 1, "title": "Rewritten", "summary": "New"}]}
		with patch("crm.api.itinerary.complete", return_value=answer):
			result = api.generate_skeleton(doc.name)

		first = result["days"][0]
		self.assertEqual(first["title"], "Rewritten")
		self.assertEqual(first["slots"][0]["items"][0]["title"], "Snorkelling at the reef")

	def test_ignores_days_outside_the_trip_length(self):
		doc = self.new_itinerary()
		answer = {
			"days": [
				{"day_number": 1, "title": "Real day", "summary": ""},
				{"day_number": 99, "title": "Day that does not exist", "summary": ""},
				{"day_number": 0, "title": "Day zero", "summary": ""},
				"not a day at all",
			]
		}

		with patch("crm.api.itinerary.complete", return_value=answer):
			result = api.generate_skeleton(doc.name)

		self.assertEqual(len(result["days"]), 5)
		self.assertEqual(result["days"][0]["title"], "Real day")

	def test_sends_the_trip_facts_to_the_model(self):
		doc = self.new_itinerary()
		answer = {"days": []}

		with patch("crm.api.itinerary.complete", return_value=answer) as mock_complete:
			api.generate_skeleton(doc.name)

		prompt = mock_complete.call_args.args[0]
		self.assertIn("Maldives", prompt)
		self.assertIn("November", prompt)
		self.assertIn("5 days", prompt)
		self.assertIn("4 travellers", prompt)
		self.assertEqual(mock_complete.call_args.kwargs["system"], api.SKELETON_SYSTEM)
		self.assertEqual(mock_complete.call_args.kwargs["json_schema"], api.SKELETON_SCHEMA)


# --- one day ---------------------------------------------------------------


class TestGenerateDay(ItineraryTestCase):
	def day_answer(self):
		return {
			"slots": [
				{
					"time_of_day": "morning",
					"items": [
						{
							"title": "Seaplane transfer",
							"description": "Twenty minutes over the atolls.",
							"place_name": "Velana International",
							"duration_hours": 0.5,
							"est_cost": 18000,
						}
					],
				},
				{
					"time_of_day": "evening",
					"items": [
						{
							"title": "Sunset dhoni cruise",
							"description": "Look for dolphins on the way out.",
							"place_name": None,
							"duration_hours": 2,
							"est_cost": None,
						}
					],
				},
			]
		}

	def test_writes_only_the_requested_day(self):
		doc = self.new_itinerary()
		doc.days_json = dump_days([make_day(1), make_day(2), make_day(3)])
		doc.save()

		with patch("crm.api.itinerary.complete", return_value=self.day_answer()):
			api.generate_day(doc.name, 2)

		days = parse_days(frappe.db.get_value(ITINERARY_DOCTYPE, doc.name, "days_json"))
		by_number = {day["day_number"]: day for day in days}

		self.assertEqual(by_number[2]["slots"][0]["items"][0]["title"], "Seaplane transfer")
		self.assertEqual(by_number[2]["slots"][2]["items"][0]["title"], "Sunset dhoni cruise")
		# Days 1 and 3 are byte-for-byte what they were.
		for untouched in (1, 3):
			self.assertEqual(by_number[untouched]["slots"][0]["items"][0]["title"], "Snorkelling at the reef")

	def test_every_generated_item_is_unverified(self):
		doc = self.new_itinerary()
		answer = self.day_answer()
		# Even when the model insists it checked, it did not.
		answer["slots"][0]["items"][0]["verified"] = True

		with patch("crm.api.itinerary.complete", return_value=answer):
			result = api.generate_day(doc.name, 1)

		for slot in result["day"]["slots"]:
			for item in slot["items"]:
				self.assertFalse(item["verified"])

	def test_keeps_the_three_slots_in_order(self):
		doc = self.new_itinerary()
		with patch("crm.api.itinerary.complete", return_value=self.day_answer()):
			result = api.generate_day(doc.name, 1)

		self.assertEqual(
			[slot["time_of_day"] for slot in result["day"]["slots"]],
			["morning", "afternoon", "evening"],
		)

	def test_strips_urls_the_model_was_told_not_to_write(self):
		doc = self.new_itinerary()
		answer = {
			"slots": [
				{
					"time_of_day": "morning",
					"items": [
						{
							"title": "Book at https://example.com/resort now",
							"description": "See www.example.com for photos.",
							"place_name": None,
							"duration_hours": None,
							"est_cost": None,
						}
					],
				}
			]
		}

		with patch("crm.api.itinerary.complete", return_value=answer):
			result = api.generate_day(doc.name, 1)

		item = result["day"]["slots"][0]["items"][0]
		self.assertNotIn("http", item["title"])
		self.assertNotIn("example.com", item["description"])

	def test_shows_neighbouring_items_so_days_do_not_repeat(self):
		doc = self.new_itinerary()
		doc.days_json = dump_days(
			[
				make_day(1, items=[make_item("Manta ray snorkel")]),
				empty_day(2, title="Day 2"),
				make_day(3, items=[make_item("Sandbank picnic")]),
				make_day(5, items=[make_item("Far away activity")]),
			]
		)
		doc.save()

		with patch("crm.api.itinerary.complete", return_value=self.day_answer()) as mock_complete:
			api.generate_day(doc.name, 2)

		prompt = mock_complete.call_args.args[0]
		self.assertIn("Manta ray snorkel", prompt)
		self.assertIn("Sandbank picnic", prompt)
		# Day 5 is three days away, outside the dedup window.
		self.assertNotIn("Far away activity", prompt)

	def test_refuses_a_day_outside_the_trip(self):
		doc = self.new_itinerary()
		with patch("crm.api.itinerary.complete") as mock_complete:
			with self.assertRaises(frappe.ValidationError):
				api.generate_day(doc.name, 99)
		mock_complete.assert_not_called()


# --- ai failures -----------------------------------------------------------


class TestAIFailures(ItineraryTestCase):
	def test_configuration_error_reaches_the_caller_unchanged(self):
		doc = self.new_itinerary()
		error = AIConfigurationError("AI is turned off. Enable it in AI & Follow-ups settings.")

		with patch("crm.api.itinerary.complete", side_effect=error):
			with self.assertRaises(AIConfigurationError) as caught:
				api.generate_skeleton(doc.name)

		self.assertIn("AI & Follow-ups", str(caught.exception))

	def test_a_bad_answer_becomes_a_readable_error(self):
		doc = self.new_itinerary()
		with patch("crm.api.itinerary.complete", side_effect=AIResponseError("the answer is not valid JSON")):
			with self.assertRaises(frappe.ValidationError):
				api.generate_day(doc.name, 1)

	def test_a_non_object_answer_is_refused(self):
		doc = self.new_itinerary()
		with patch("crm.api.itinerary.complete", return_value="just some prose"):
			with self.assertRaises(frappe.ValidationError):
				api.generate_skeleton(doc.name)


# --- schema ----------------------------------------------------------------


class TestUpdateDays(ItineraryTestCase):
	def save_days(self, days):
		doc = self.new_itinerary()
		return api.update_days(doc.name, {"days": days})

	def test_accepts_a_well_formed_itinerary(self):
		result = self.save_days([make_day(1), make_day(2)])
		self.assertEqual([day["day_number"] for day in result["days"]], [1, 2])

	def test_accepts_a_json_string_and_a_bare_list(self):
		doc = self.new_itinerary()
		api.update_days(doc.name, json.dumps({"days": [make_day(1)]}))
		self.assertEqual(len(parse_days(frappe.get_doc(ITINERARY_DOCTYPE, doc.name).days_json)), 1)

		api.update_days(doc.name, [make_day(1), make_day(2)])
		self.assertEqual(len(parse_days(frappe.get_doc(ITINERARY_DOCTYPE, doc.name).days_json)), 2)

	def test_rejects_a_missing_day_number(self):
		day = make_day(1)
		del day["day_number"]
		with self.assertRaises(ItinerarySchemaError):
			self.save_days([day])

	def test_rejects_a_day_number_that_is_not_a_number(self):
		with self.assertRaises(ItinerarySchemaError):
			self.save_days([{**make_day(1), "day_number": "one"}])

	def test_rejects_a_duplicate_day_number(self):
		with self.assertRaises(ItinerarySchemaError) as caught:
			self.save_days([make_day(2), make_day(2)])
		self.assertIn("more than once", str(caught.exception))

	def test_rejects_an_unknown_key_on_a_day(self):
		with self.assertRaises(ItinerarySchemaError) as caught:
			self.save_days([{**make_day(1), "hotel": "Paradise Resort"}])
		self.assertIn("hotel", str(caught.exception))

	def test_rejects_an_unknown_key_on_an_item(self):
		day = make_day(1, items=[make_item(booking_reference="ABC123")])
		with self.assertRaises(ItinerarySchemaError) as caught:
			self.save_days([day])
		self.assertIn("booking_reference", str(caught.exception))

	def test_rejects_an_unknown_time_of_day(self):
		day = make_day(1)
		day["slots"][0]["time_of_day"] = "midnight"
		with self.assertRaises(ItinerarySchemaError):
			self.save_days([day])

	def test_rejects_an_item_without_a_title(self):
		with self.assertRaises(ItinerarySchemaError):
			self.save_days([make_day(1, items=[make_item(title="")])])

	def test_rejects_a_cost_that_is_not_a_number(self):
		with self.assertRaises(ItinerarySchemaError):
			self.save_days([make_day(1, items=[make_item(est_cost="a lot")])])

	def test_rejects_a_negative_duration(self):
		with self.assertRaises(ItinerarySchemaError):
			self.save_days([make_day(1, items=[make_item(duration_hours=-3)])])

	def test_rejects_a_day_that_is_not_an_object(self):
		with self.assertRaises(ItinerarySchemaError):
			self.save_days(["day one"])

	def test_rejects_broken_json(self):
		doc = self.new_itinerary()
		with self.assertRaises(ItinerarySchemaError):
			api.update_days(doc.name, "{not json")

	def test_normalises_a_partial_day_into_three_slots(self):
		result = self.save_days([{"day_number": 1, "title": "Arrival", "summary": ""}])
		day = result["days"][0]
		self.assertEqual([slot["time_of_day"] for slot in day["slots"]], ["morning", "afternoon", "evening"])

	def test_a_string_verified_flag_does_not_read_as_verified(self):
		result = self.save_days([make_day(1, items=[make_item(verified="true")])])
		self.assertFalse(result["days"][0]["slots"][0]["items"][0]["verified"])

	def test_the_agent_can_mark_an_item_verified(self):
		result = self.save_days([make_day(1, items=[make_item(verified=True)])])
		self.assertTrue(result["days"][0]["slots"][0]["items"][0]["verified"])

	def test_the_controller_refuses_malformed_json_on_a_direct_save(self):
		doc = self.new_itinerary()
		doc.days_json = json.dumps({"days": [{"day_number": 1, "surprise": True}]})
		with self.assertRaises(ItinerarySchemaError):
			doc.save()


class TestUpdateDetails(ItineraryTestCase):
	def test_saves_the_trip_facts_and_the_price_tiers(self):
		doc = self.new_itinerary()
		payload = api.update_details(
			doc.name,
			{
				"title": "Maldives honeymoon",
				"destination": "Maldives",
				"num_days": 6,
				"group_size": 2,
				"budget": 300000,
				"inclusions": "Seaplane transfers",
				"price_tiers": [
					{"tier_label": "Water Villa", "price_per_person": 185000},
					{"tier_label": "Beach Villa", "price_per_person": 140000},
				],
			},
		)

		self.assertEqual(payload["title"], "Maldives honeymoon")
		self.assertEqual(payload["num_days"], 6)
		self.assertEqual(len(payload["price_tiers"]), 2)
		self.assertEqual(payload["price_tiers"][0]["tier_label"], "Water Villa")
		self.assertEqual(payload["price_tiers"][0]["price_per_person"], 185000)

	def test_replaces_the_price_tiers_rather_than_appending(self):
		doc = self.new_itinerary()
		api.update_details(doc.name, {"price_tiers": [{"tier_label": "A", "price_per_person": 1}]})
		payload = api.update_details(doc.name, {"price_tiers": [{"tier_label": "B", "price_per_person": 2}]})

		self.assertEqual([tier["tier_label"] for tier in payload["price_tiers"]], ["B"])

	def test_drops_a_tier_without_a_label(self):
		doc = self.new_itinerary()
		payload = api.update_details(
			doc.name,
			{"price_tiers": [{"tier_label": "  ", "price_per_person": 5}, "not a row"]},
		)
		self.assertEqual(payload["price_tiers"], [])

	def test_refuses_to_write_the_status_or_the_version(self):
		doc = self.new_itinerary()
		for field in ("status", "version", "lead", "days_json"):
			with self.assertRaises(frappe.ValidationError) as caught:
				api.update_details(doc.name, {field: "anything"})
			self.assertIn(field, str(caught.exception))

		self.assertEqual(frappe.db.get_value(ITINERARY_DOCTYPE, doc.name, "status"), "Draft")

	def test_accepts_a_json_string(self):
		doc = self.new_itinerary()
		payload = api.update_details(doc.name, json.dumps({"destination": "Sri Lanka"}))
		self.assertEqual(payload["destination"], "Sri Lanka")

	def test_clamps_a_day_count_outside_the_bounds(self):
		doc = self.new_itinerary()
		self.assertEqual(api.update_details(doc.name, {"num_days": 400})["num_days"], 30)
		self.assertEqual(api.update_details(doc.name, {"num_days": 0})["num_days"], 1)

	def test_the_editor_payload_carries_the_days_and_the_notes(self):
		doc = self.new_itinerary()
		api.update_days(doc.name, {"days": [make_day(1)]})
		api.update_details(doc.name, {"internal_notes": "Margin is thin"})

		payload = api.get_itinerary_for_editor(doc.name)
		self.assertEqual(payload["name"], doc.name)
		self.assertEqual(len(payload["days"]), 1)
		self.assertEqual(payload["internal_notes"], "Margin is thin")


# --- permissions -----------------------------------------------------------


class TestPermissions(ItineraryTestCase):
	def setUp(self):
		super().setUp()
		if not frappe.db.exists("User", OTHER_USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": OTHER_USER,
					"first_name": "Outsider",
					"send_welcome_email": 0,
					"roles": [{"role": "Sales User"}],
				}
			).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def test_a_user_without_lead_access_cannot_create_an_itinerary(self):
		# No patching: the real CRM Lead permission rule is what protects the
		# itinerary, so the test has to go through it. The lead belongs to
		# Administrator and this sales user is neither its owner nor an assignee.
		frappe.set_user(OTHER_USER)
		with self.assertRaises(frappe.PermissionError):
			api.create_from_lead(self.lead.name)

	def test_a_user_without_lead_access_cannot_read_an_itinerary(self):
		doc = self.new_itinerary()
		frappe.set_user(OTHER_USER)
		with self.assertRaises(frappe.PermissionError):
			api.get_itinerary(doc.name)

	def test_a_user_without_lead_access_cannot_edit_the_days(self):
		doc = self.new_itinerary()
		frappe.set_user(OTHER_USER)
		with self.assertRaises(frappe.PermissionError):
			api.update_days(doc.name, {"days": [make_day(1)]})

	def test_a_user_without_lead_access_cannot_edit_the_details(self):
		doc = self.new_itinerary()
		frappe.set_user(OTHER_USER)
		with self.assertRaises(frappe.PermissionError):
			api.update_details(doc.name, {"title": "Stolen"})

	def test_a_user_without_lead_access_cannot_spend_an_ai_call(self):
		doc = self.new_itinerary()
		frappe.set_user(OTHER_USER)
		with patch("crm.api.itinerary.complete") as mock_complete:
			with self.assertRaises(frappe.PermissionError):
				api.generate_skeleton(doc.name)
		mock_complete.assert_not_called()

	def test_a_missing_itinerary_is_refused_like_a_forbidden_one(self):
		with self.assertRaises(frappe.PermissionError):
			api.get_itinerary("ITN-does-not-exist")

	def test_an_empty_name_is_refused(self):
		with self.assertRaises(frappe.PermissionError):
			api.get_itinerary("")


# --- print -----------------------------------------------------------------


class TestPrintFormat(ItineraryTestCase):
	def full_itinerary(self):
		doc = self.new_itinerary()
		doc.days_json = dump_days([make_day(1), make_day(2)])
		doc.inclusions = "Airport transfers\nDaily breakfast"
		doc.exclusions = "International flights"
		doc.terms = "50% advance to confirm"
		doc.internal_notes = "MARGIN-IS-32-PERCENT"
		doc.append("price_tiers", {"tier_label": "Double Sharing", "price_per_person": 74000})
		doc.save()
		return doc

	def render(self, doc):
		api.install_print_format()
		return frappe.get_print(ITINERARY_DOCTYPE, doc.name, print_format=api.PRINT_FORMAT, no_letterhead=1)

	def test_the_print_format_is_installed_idempotently(self):
		api.install_print_format()
		api.install_print_format()
		self.assertTrue(frappe.db.exists("Print Format", api.PRINT_FORMAT))

	def test_the_customer_document_shows_the_itinerary(self):
		html = self.render(self.full_itinerary())

		self.assertIn("Snorkelling at the reef", html)
		self.assertIn("Double Sharing", html)
		self.assertIn("Airport transfers", html)
		self.assertIn("International flights", html)
		self.assertIn("50% advance to confirm", html)

	def test_internal_notes_never_reach_the_customer(self):
		html = self.render(self.full_itinerary())
		self.assertNotIn("MARGIN-IS-32-PERCENT", html)

	def test_an_unverified_place_is_flagged_for_the_agent(self):
		html = self.render(self.full_itinerary())
		self.assertIn("to confirm", html)

	def test_customer_text_is_escaped_not_executed(self):
		doc = self.new_itinerary()
		doc.days_json = dump_days([make_day(1, items=[make_item("<script>alert(1)</script>")])])
		doc.save()

		html = self.render(doc)
		self.assertNotIn("<script>alert(1)</script>", html)
		self.assertIn("&lt;script&gt;", html)

	def test_an_empty_itinerary_still_renders(self):
		html = self.render(self.new_itinerary())
		self.assertIn("no days yet", html)


# --- whatsapp --------------------------------------------------------------


class TestSendViaWhatsApp(ItineraryTestCase):
	def ready_itinerary(self):
		doc = self.new_itinerary()
		doc.days_json = dump_days([make_day(1)])
		doc.append("price_tiers", {"tier_label": "Double Sharing", "price_per_person": 74000})
		doc.save()
		return doc

	def test_sends_the_pdf_and_marks_the_itinerary_sent(self):
		doc = self.ready_itinerary()
		file_doc = frappe._dict({"file_url": "/files/maldives-v1.pdf"})

		with (
			patch("crm.api.itinerary.attach_pdf", return_value=file_doc),
			patch("crm.api.whatsapp.create_whatsapp_message", return_value="MSG-0001") as mock_send,
		):
			result = api.send_via_whatsapp(doc.name)

		self.assertTrue(result["success"])
		self.assertEqual(result["message"], "MSG-0001")
		self.assertEqual(result["status"], "Sent")
		self.assertEqual(result["version"], 1)

		sent = mock_send.call_args.kwargs
		self.assertEqual(sent["reference_doctype"], LEAD_DOCTYPE)
		self.assertEqual(sent["reference_name"], self.lead.name)
		self.assertEqual(sent["content_type"], "document")
		self.assertEqual(sent["attach"], "/files/maldives-v1.pdf")
		self.assertEqual(sent["to"], "+919820000001")
		self.assertIn("Maldives", sent["message"])
		self.assertIn("Double Sharing", sent["message"])

		self.assertEqual(frappe.db.get_value(ITINERARY_DOCTYPE, doc.name, "status"), "Sent")

	def test_a_rejected_send_returns_the_24_hour_window_hint(self):
		doc = self.ready_itinerary()
		file_doc = frappe._dict({"file_url": "/files/maldives-v1.pdf"})

		with (
			patch("crm.api.itinerary.attach_pdf", return_value=file_doc),
			patch(
				"crm.api.whatsapp.create_whatsapp_message",
				side_effect=Exception("(#131047) Re-engagement message"),
			),
		):
			result = api.send_via_whatsapp(doc.name)

		self.assertFalse(result["success"])
		self.assertEqual(result["reason"], "send_failed")
		self.assertIn("131047", result["error"])
		self.assertIn("24 hours", result["hint"])
		# A failed send must not claim the customer has the itinerary.
		self.assertEqual(frappe.db.get_value(ITINERARY_DOCTYPE, doc.name, "status"), "Draft")

	def test_a_lead_without_a_number_is_refused_before_any_pdf_is_built(self):
		self.lead.db_set("mobile_no", "")
		self.lead.db_set("phone", "")
		doc = self.ready_itinerary()

		with patch("crm.api.itinerary.attach_pdf") as mock_attach:
			result = api.send_via_whatsapp(doc.name)

		self.assertFalse(result["success"])
		self.assertEqual(result["reason"], "no_number")
		mock_attach.assert_not_called()

	def test_the_summary_names_the_trip_and_its_prices(self):
		doc = self.ready_itinerary()
		summary = api.whatsapp_summary(doc)

		self.assertIn("Maldives", summary)
		self.assertIn("5 days", summary)
		self.assertIn("Double Sharing", summary)
		self.assertNotIn("MARGIN", summary)


# --- status and version ----------------------------------------------------


class TestStatusAndVersion(ItineraryTestCase):
	def send(self, doc):
		file_doc = frappe._dict({"file_url": "/files/x.pdf"})
		with (
			patch("crm.api.itinerary.attach_pdf", return_value=file_doc),
			patch("crm.api.whatsapp.create_whatsapp_message", return_value="MSG"),
		):
			return api.send_via_whatsapp(doc.name)

	def test_a_new_itinerary_starts_as_draft_version_one(self):
		doc = self.new_itinerary()
		self.assertEqual((doc.status, doc.version), ("Draft", 1))

	def test_editing_after_a_send_moves_the_itinerary_to_revised(self):
		doc = self.new_itinerary()
		self.send(doc)
		self.assertEqual(frappe.db.get_value(ITINERARY_DOCTYPE, doc.name, "status"), "Sent")

		api.update_days(doc.name, {"days": [make_day(1)]})
		self.assertEqual(frappe.db.get_value(ITINERARY_DOCTYPE, doc.name, "status"), "Revised")

	def test_resending_after_an_edit_bumps_the_version(self):
		doc = self.new_itinerary()
		self.send(doc)
		api.update_days(doc.name, {"days": [make_day(1)]})

		result = self.send(frappe.get_doc(ITINERARY_DOCTYPE, doc.name))
		self.assertEqual(result["status"], "Sent")
		self.assertEqual(result["version"], 2)

	def test_a_save_that_changes_nothing_keeps_the_sent_status(self):
		doc = self.new_itinerary()
		self.send(doc)

		sent = frappe.get_doc(ITINERARY_DOCTYPE, doc.name)
		sent.internal_notes = "Customer asked about upgrades"
		sent.save()

		# Internal notes are not part of what the customer received.
		self.assertEqual(frappe.db.get_value(ITINERARY_DOCTYPE, doc.name, "status"), "Sent")

	def test_changing_a_price_tier_after_a_send_marks_it_revised(self):
		doc = self.new_itinerary()
		self.send(doc)

		sent = frappe.get_doc(ITINERARY_DOCTYPE, doc.name)
		sent.append("price_tiers", {"tier_label": "Triple Sharing", "price_per_person": 68000})
		sent.save()

		self.assertEqual(frappe.db.get_value(ITINERARY_DOCTYPE, doc.name, "status"), "Revised")


# --- security regressions --------------------------------------------------


class TestGenericApiIsolation(ItineraryTestCase):
	"""An itinerary must be exactly as visible as the lead behind it.

	The custom endpoints are not the only door. Frappe's generic API reaches
	every doctype a role can read, and an itinerary carries the customer's name,
	their budget, the quoted prices and the agency's internal notes. These tests
	drive the generic paths as a sales user who cannot see the lead.
	"""

	def setUp(self):
		super().setUp()
		if not frappe.db.exists("User", OTHER_USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": OTHER_USER,
					"first_name": "Outsider",
					"send_welcome_email": 0,
					"roles": [{"role": "Sales User"}],
				}
			).insert(ignore_permissions=True)

		self.itinerary = self.new_itinerary()
		api.update_details(self.itinerary.name, {"internal_notes": "MARGIN-IS-32-PERCENT"})

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def test_the_list_hides_another_agents_itinerary(self):
		frappe.set_user(OTHER_USER)
		rows = frappe.get_list(ITINERARY_DOCTYPE, filters={"name": self.itinerary.name}, pluck="name")
		self.assertEqual(rows, [])

	def test_the_query_condition_names_the_lead_table(self):
		# The filter must go through the lead, not through the itinerary's own
		# owner: an itinerary has no owner-based rule of its own.
		condition = api.get_itinerary_permission_query_conditions(OTHER_USER)
		self.assertIn("`tabCRM Itinerary`.`lead`", condition)
		self.assertIn("`tabCRM Lead`", condition)

	def test_has_permission_refuses_another_agents_itinerary(self):
		self.assertFalse(
			api.has_itinerary_permission(frappe._dict({"lead": self.lead.name}), "read", OTHER_USER)
		)

	def test_has_permission_allows_a_create_with_no_lead_yet(self):
		self.assertTrue(api.has_itinerary_permission(frappe._dict({}), "create", OTHER_USER))

	def test_client_get_refuses_another_agents_itinerary(self):
		from frappe.client import get as client_get

		frappe.set_user(OTHER_USER)
		with self.assertRaises(frappe.PermissionError):
			client_get(ITINERARY_DOCTYPE, self.itinerary.name)

	def test_client_set_value_cannot_flip_the_status(self):
		from frappe.client import set_value as client_set_value

		frappe.set_user(OTHER_USER)
		with self.assertRaises(frappe.PermissionError):
			client_set_value(ITINERARY_DOCTYPE, self.itinerary.name, "status", "Sent")

		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value(ITINERARY_DOCTYPE, self.itinerary.name, "status"), "Draft")

	def test_a_sales_user_may_neither_report_nor_export(self):
		api.add_itinerary_roles()
		perms = frappe.get_all(
			"Custom DocPerm",
			filters={"parent": ITINERARY_DOCTYPE, "role": "Sales User"},
			fields=["read", "write", "create", "delete", "report", "export", "share"],
		)
		self.assertTrue(perms, "the sales user grant is missing")
		for perm in perms:
			self.assertEqual(perm.read, 1)
			self.assertEqual(perm.write, 1)
			self.assertEqual(perm.report, 0)
			self.assertEqual(perm.export, 0)
			self.assertEqual(perm.share, 0)
			self.assertEqual(perm.delete, 0)

	def test_the_grant_leaves_the_system_manager_row_intact(self):
		api.add_itinerary_roles()
		manager = frappe.get_all(
			"Custom DocPerm",
			filters={"parent": ITINERARY_DOCTYPE, "role": "System Manager"},
			fields=["read", "write", "delete"],
		)
		self.assertTrue(manager, "adding a custom perm wiped the standard permissions")
		self.assertEqual(manager[0].delete, 1)

	def test_can_use_itineraries_is_false_without_a_sales_role(self):
		frappe.set_user(OTHER_USER)
		self.assertTrue(api.can_use_itineraries())

		with patch("crm.api.itinerary.frappe.get_roles", return_value=["Guest"]):
			self.assertFalse(api.can_use_itineraries())

	def test_the_ai_probe_needs_a_sales_role(self):
		with patch("crm.api.itinerary.frappe.get_roles", return_value=["Guest"]):
			with self.assertRaises(frappe.PermissionError):
				api.is_ai_configured()


class TestWritePermissionLevel(ItineraryTestCase):
	"""Read access to a lead must not buy write access to its itinerary."""

	def read_only_on_lead(self):
		"""Let the lead be read but not written."""

		def rule(doctype, ptype="read", *args, **kwargs):
			if doctype == LEAD_DOCTYPE:
				return ptype == "read"
			return True

		return patch("crm.api.itinerary.frappe.has_permission", side_effect=rule)

	def test_a_read_only_user_cannot_rewrite_the_days(self):
		doc = self.new_itinerary()
		with self.read_only_on_lead():
			with self.assertRaises(frappe.PermissionError):
				api.update_days(doc.name, {"days": [make_day(1)]})

	def test_a_read_only_user_cannot_rewrite_the_details(self):
		doc = self.new_itinerary()
		with self.read_only_on_lead():
			with self.assertRaises(frappe.PermissionError):
				api.update_details(doc.name, {"title": "Rewritten"})

	def test_a_read_only_user_cannot_spend_an_ai_call(self):
		doc = self.new_itinerary()
		with self.read_only_on_lead():
			with patch("crm.api.itinerary.complete") as mock_complete:
				with self.assertRaises(frappe.PermissionError):
					api.generate_day(doc.name, 1)
		mock_complete.assert_not_called()

	def test_a_read_only_user_may_still_read_and_print(self):
		doc = self.new_itinerary()
		with self.read_only_on_lead():
			payload = api.get_itinerary_for_editor(doc.name)
		self.assertEqual(payload["name"], doc.name)


class TestPostOnlyEndpoints(ItineraryTestCase):
	"""A GET must not send WhatsApp messages or spend AI tokens.

	Frappe only enforces the allowed methods for a request that arrives over
	HTTP, so this asserts the declaration itself.
	"""

	CHANGES_STATE = (
		"create_from_lead",
		"generate_skeleton",
		"generate_day",
		"update_days",
		"update_details",
		"get_pdf",
		"send_via_whatsapp",
	)

	@staticmethod
	def allowed_methods(name):
		"""What `@frappe.whitelist` recorded for this endpoint.

		The decorator does not tag the function. It stores the allowed verbs in
		a module-level registry keyed by the function object, so that registry
		is what the router reads and what this test has to read too.
		"""
		return frappe.allowed_http_methods_for_whitelisted_func.get(getattr(api, name))

	def test_every_state_changing_endpoint_is_post_only(self):
		for name in self.CHANGES_STATE:
			self.assertEqual(self.allowed_methods(name), ["POST"], f"{name} still accepts GET")

	def test_the_read_only_endpoints_stay_readable(self):
		for name in ("get_itinerary_for_editor", "get_draft_for_lead", "can_use_itineraries"):
			self.assertIn("GET", self.allowed_methods(name), f"{name} should stay readable")


# --- correctness regressions -----------------------------------------------


class TestNoOpSaveDoesNotRevise(ItineraryTestCase):
	"""A save that changes nothing must not tell the customer the trip changed."""

	def send(self, doc):
		with (
			patch("crm.api.itinerary.attach_pdf", return_value=frappe._dict({"file_url": "/files/x.pdf"})),
			patch("crm.api.whatsapp.create_whatsapp_message", return_value="MSG"),
		):
			return api.send_via_whatsapp(doc.name)

	def test_resaving_the_same_start_date_string_keeps_the_status(self):
		doc = self.new_itinerary()
		self.send(doc)
		self.assertEqual(frappe.db.get_value(ITINERARY_DOCTYPE, doc.name, "status"), "Sent")

		# Exactly what the browser sends back: a date as a JSON string, and
		# numbers that arrived as strings from number inputs.
		api.update_details(
			doc.name,
			{
				"start_date": "2026-11-02",
				"num_days": "5",
				"group_size": "4",
				"budget": "250000",
				"destination": "Maldives",
			},
		)

		row = frappe.db.get_value(ITINERARY_DOCTYPE, doc.name, ["status", "version"], as_dict=True)
		self.assertEqual(row.status, "Sent")
		self.assertEqual(row.version, 1)

	def test_a_real_change_still_marks_the_itinerary_revised(self):
		doc = self.new_itinerary()
		self.send(doc)

		api.update_details(doc.name, {"start_date": "2026-12-25"})
		self.assertEqual(frappe.db.get_value(ITINERARY_DOCTYPE, doc.name, "status"), "Revised")

	def test_coercion_matches_the_stored_types(self):
		self.assertEqual(api.coerce_field("start_date", "2026-11-02"), frappe.utils.getdate("2026-11-02"))
		self.assertEqual(api.coerce_field("num_days", "7"), 7)
		self.assertEqual(api.coerce_field("budget", "2500.5"), 2500.5)
		self.assertEqual(api.coerce_field("destination", 42), "42")
		self.assertIsNone(api.coerce_field("start_date", ""))


class TestSendVersioning(ItineraryTestCase):
	"""The PDF the customer holds must carry the version the record carries."""

	def send(self, doc, rendered_versions, fail=False):
		def fake_attach(document, is_private, token=""):
			# Record what the version field says at the moment of rendering.
			rendered_versions.append((cint_or_none(document.version), token))
			return frappe._dict(
				{"file_url": f"/files/x-{token}.pdf", "name": "FILE-1", "is_private": is_private}
			)

		sender = (
			patch("crm.api.whatsapp.create_whatsapp_message", side_effect=Exception("(#131047)"))
			if fail
			else patch("crm.api.whatsapp.create_whatsapp_message", return_value="MSG")
		)
		with (
			patch("crm.api.itinerary.attach_pdf", side_effect=fake_attach),
			sender,
		):
			return api.send_via_whatsapp(doc.name)

	def test_the_rendered_pdf_carries_the_bumped_version(self):
		doc = self.new_itinerary()
		rendered = []

		self.send(doc, rendered)
		api.update_days(doc.name, {"days": [make_day(1)]})
		self.assertEqual(frappe.db.get_value(ITINERARY_DOCTYPE, doc.name, "status"), "Revised")

		result = self.send(frappe.get_doc(ITINERARY_DOCTYPE, doc.name), rendered)

		self.assertEqual(result["version"], 2)
		# The second render saw version 2, not the stale 1.
		self.assertEqual(rendered[1][0], 2)

	def test_the_public_copy_gets_an_unguessable_name(self):
		doc = self.new_itinerary()
		rendered = []
		self.send(doc, rendered)

		token = rendered[0][1]
		self.assertTrue(token, "the public send copy has no random token")
		self.assertGreaterEqual(len(token), 16)

	def test_the_send_does_not_delete_the_public_copy_in_request(self):
		"""Meta fetches the media after the POST returns.

		An in-request delete races that fetch and the customer gets nothing, so
		the file must still be there when the endpoint returns. The hourly sweep
		is what removes it later.
		"""
		doc = self.new_itinerary()

		# `attach_pdf` runs for real so the File row and its name are real. Only
		# wkhtmltopdf is stubbed: the binary render is an environment test.
		with (
			patch("crm.api.itinerary.render_pdf", return_value=stub_pdf()),
			patch("crm.api.whatsapp.create_whatsapp_message", return_value="MSG"),
		):
			result = api.send_via_whatsapp(doc.name)

		self.assertTrue(result["success"])
		public = frappe.get_all(
			"File",
			filters={
				"attached_to_doctype": ITINERARY_DOCTYPE,
				"attached_to_name": doc.name,
				"is_private": 0,
			},
			pluck="file_name",
		)
		self.assertEqual(len(public), 1, "the send copy was removed inside the request")
		self.assertTrue(api.is_send_copy_name(public[0]))

	def test_a_failed_send_rolls_the_version_and_status_back(self):
		doc = self.new_itinerary()
		self.send(doc, [])
		api.update_days(doc.name, {"days": [make_day(1)]})

		result = self.send(frappe.get_doc(ITINERARY_DOCTYPE, doc.name), [], fail=True)
		self.assertFalse(result["success"])

		row = frappe.db.get_value(ITINERARY_DOCTYPE, doc.name, ["status", "version"], as_dict=True)
		# Nothing reached the customer, so nothing may claim a new version.
		self.assertEqual(row.status, "Revised")
		self.assertEqual(row.version, 1)

	def test_the_file_name_carries_the_version_and_the_token(self):
		doc = self.new_itinerary()
		doc.version = 3
		self.assertEqual(api.pdf_file_name(doc), "Maldives-Ravi-Kulkarni-v3.pdf")
		self.assertEqual(api.pdf_file_name(doc, "abc123"), "Maldives-Ravi-Kulkarni-v3-abc123.pdf")


class TestPublicPdfSweep(ItineraryTestCase):
	"""The hourly sweep owns the temporary public PDF's lifetime.

	The send cannot delete its own file without racing Meta's media fetch, so
	the file outlives the request and this job removes it afterwards. The job
	must be precise: a private attachment or a file somebody attached by hand
	has to survive it.
	"""

	def attach(self, doc, file_name, is_private=0, age_hours=0):
		"""Put one File on the itinerary, optionally backdated."""
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"attached_to_doctype": ITINERARY_DOCTYPE,
				"attached_to_name": doc.name,
				"is_private": is_private,
				"content": stub_pdf(),
			}
		).insert(ignore_permissions=True)

		if age_hours:
			old = frappe.utils.add_to_date(frappe.utils.now_datetime(), hours=-age_hours)
			frappe.db.set_value("File", file_doc.name, "creation", old, update_modified=False)

		# Frappe appends a content-hash suffix when the name is already taken, so
		# the caller has to assert on the name that was actually stored.
		return file_doc.file_name

	def names_on(self, doc):
		return set(
			frappe.get_all(
				"File",
				filters={"attached_to_doctype": ITINERARY_DOCTYPE, "attached_to_name": doc.name},
				pluck="file_name",
			)
		)

	def send_copy_name(self, version=1):
		return f"Trip-v{version}-{'a' * api.PUBLIC_PDF_TOKEN_LENGTH}.pdf"

	def test_removes_an_aged_public_send_copy(self):
		doc = self.new_itinerary()
		aged = self.attach(doc, self.send_copy_name(), is_private=0, age_hours=5)

		removed = api.cleanup_public_itinerary_pdfs()

		self.assertGreaterEqual(removed, 1)
		self.assertNotIn(aged, self.names_on(doc))

	def test_leaves_a_fresh_public_send_copy_alone(self):
		doc = self.new_itinerary()
		fresh = self.attach(doc, self.send_copy_name(2), is_private=0)

		api.cleanup_public_itinerary_pdfs()

		# Meta may not have fetched it yet. Deleting now breaks delivery.
		self.assertIn(fresh, self.names_on(doc))

	def test_never_touches_a_private_attachment(self):
		doc = self.new_itinerary()
		private = self.attach(doc, self.send_copy_name(1), is_private=1, age_hours=99)

		api.cleanup_public_itinerary_pdfs()

		self.assertIn(private, self.names_on(doc))

	def test_never_touches_a_public_file_without_the_send_token(self):
		doc = self.new_itinerary()
		# A brochure the agent attached by hand. Public, old, and not ours.
		manual = self.attach(doc, "hotel-brochure.pdf", is_private=0, age_hours=99)

		api.cleanup_public_itinerary_pdfs()

		self.assertIn(manual, self.names_on(doc))

	def test_sweeps_only_what_the_age_limit_allows(self):
		doc = self.new_itinerary()
		aged = self.attach(doc, self.send_copy_name(1), is_private=0, age_hours=5)
		fresh = self.attach(doc, self.send_copy_name(2), is_private=0)

		api.cleanup_public_itinerary_pdfs(older_than_hours=2)

		names = self.names_on(doc)
		self.assertNotIn(aged, names)
		self.assertIn(fresh, names)

	def test_the_name_pattern_matches_only_send_copies(self):
		token = "b" * api.PUBLIC_PDF_TOKEN_LENGTH
		self.assertTrue(api.is_send_copy_name(f"Bali-Trip-v3-{token}.pdf"))
		self.assertFalse(api.is_send_copy_name("Bali-Trip-v3.pdf"))
		self.assertFalse(api.is_send_copy_name("hotel-brochure.pdf"))
		self.assertFalse(api.is_send_copy_name(f"Bali-Trip-v3-{token}.pdf.exe"))
		# A short suffix is not the token this module writes.
		self.assertFalse(api.is_send_copy_name("Bali-Trip-v3-abc123.pdf"))
		self.assertFalse(api.is_send_copy_name(""))
		# Frappe appends six hex characters when the name is already taken. The
		# sweep still has to recognise the file it wrote.
		self.assertTrue(api.is_send_copy_name(f"Bali-Trip-v3-{token}d964ff.pdf"))

	def test_a_real_send_leaves_a_file_the_sweep_recognises(self):
		"""The producer and the consumer must agree on the name."""
		doc = self.new_itinerary()

		with (
			patch("crm.api.itinerary.render_pdf", return_value=stub_pdf()),
			patch("crm.api.whatsapp.create_whatsapp_message", return_value="MSG"),
		):
			api.send_via_whatsapp(doc.name)

		public = frappe.get_all(
			"File",
			filters={
				"attached_to_doctype": ITINERARY_DOCTYPE,
				"attached_to_name": doc.name,
				"is_private": 0,
			},
			pluck="file_name",
		)
		self.assertEqual(len(public), 1)
		self.assertTrue(
			api.is_send_copy_name(public[0]),
			f"the sweep would never remove {public[0]}",
		)

	def test_the_job_never_raises(self):
		# A scheduler job that throws takes the rest of its queue down. The
		# broken call here is the one the job's own error handler also uses, so
		# this proves the handler cannot re-raise either.
		with patch("crm.api.itinerary.frappe.get_all", side_effect=Exception("database gone")):
			self.assertEqual(api.cleanup_public_itinerary_pdfs(), 0)

	def test_logging_a_failure_cannot_itself_raise(self):
		with patch("crm.api.itinerary.frappe.log_error", side_effect=Exception("log table gone")):
			api.log_quietly("message", "title")

	def test_a_single_failed_delete_does_not_stop_the_sweep(self):
		doc = self.new_itinerary()
		self.attach(doc, self.send_copy_name(1), is_private=0, age_hours=5)
		self.attach(doc, self.send_copy_name(2), is_private=0, age_hours=5)

		real_delete = frappe.delete_doc
		calls = {"n": 0}

		def flaky(*args, **kwargs):
			calls["n"] += 1
			if calls["n"] == 1:
				raise Exception("locked")
			return real_delete(*args, **kwargs)

		with (
			patch("crm.api.itinerary.frappe.delete_doc", side_effect=flaky),
			patch("crm.api.itinerary.frappe.log_error") as mock_log,
		):
			removed = api.cleanup_public_itinerary_pdfs()

		self.assertEqual(removed, 1)
		mock_log.assert_called()

	def test_the_sweep_is_wired_into_the_hourly_scheduler(self):
		hourly = frappe.get_hooks("scheduler_events")["hourly"]
		self.assertIn("crm.api.itinerary.cleanup_public_itinerary_pdfs", hourly)
		# The follow-up worker's job must still be there beside it.
		self.assertIn("crm.api.followup_engine.process_followups", hourly)


class TestShrinkingTheTrip(ItineraryTestCase):
	"""Curated days must never disappear without the agent being told."""

	def test_a_shorter_trip_drops_the_days_past_the_new_end(self):
		doc = self.new_itinerary()
		api.update_days(doc.name, {"days": [make_day(n) for n in range(1, 6)]})

		payload = api.update_details(doc.name, {"num_days": 3})
		self.assertEqual([day["day_number"] for day in payload["days"]], [1, 2, 3])

	def test_a_longer_trip_keeps_every_day(self):
		doc = self.new_itinerary()
		api.update_days(doc.name, {"days": [make_day(n) for n in range(1, 4)]})

		payload = api.update_details(doc.name, {"num_days": 9})
		self.assertEqual(len(payload["days"]), 3)

	def test_the_skeleton_keeps_the_content_of_the_days_that_remain(self):
		doc = self.new_itinerary()
		api.update_days(doc.name, {"days": [make_day(n) for n in range(1, 6)]})
		api.update_details(doc.name, {"num_days": 3})

		answer = {"days": [{"day_number": n, "title": f"T{n}", "summary": ""} for n in (1, 2, 3)]}
		with patch("crm.api.itinerary.complete", return_value=answer):
			result = api.generate_skeleton(doc.name)

		self.assertEqual(len(result["days"]), 3)
		# The items the agent curated are still there under the new titles.
		self.assertEqual(result["days"][0]["title"], "T1")
		self.assertEqual(result["days"][0]["slots"][0]["items"][0]["title"], "Snorkelling at the reef")


class TestPrintFormatIsNotClobbered(ItineraryTestCase):
	def test_an_unchanged_format_is_not_rewritten(self):
		api.install_print_format()
		before = frappe.db.get_value("Print Format", api.PRINT_FORMAT, "modified")

		# The float margins used to compare unequal as strings ("12.0" != "12"),
		# so every migrate rewrote the format and discarded local changes.
		api.install_print_format()
		api.install_print_format()

		self.assertEqual(frappe.db.get_value("Print Format", api.PRINT_FORMAT, "modified"), before)

	def test_a_changed_template_is_written(self):
		api.install_print_format()
		frappe.db.set_value("Print Format", api.PRINT_FORMAT, "html", "<p>edited</p>")

		api.install_print_format()
		self.assertNotEqual(frappe.db.get_value("Print Format", api.PRINT_FORMAT, "html"), "<p>edited</p>")

	def test_same_value_compares_numbers_as_numbers(self):
		self.assertTrue(api.same_value(12.0, 12))
		self.assertTrue(api.same_value("12", 12))
		self.assertFalse(api.same_value(13.0, 12))
		self.assertTrue(api.same_value("Bottom Center", "Bottom Center"))


class TestDayNumberBound(ItineraryTestCase):
	def test_rejects_a_day_number_above_the_limit(self):
		doc = self.new_itinerary()
		with self.assertRaises(ItinerarySchemaError) as caught:
			api.update_days(doc.name, {"days": [make_day(5000)]})
		self.assertIn("5000", str(caught.exception))

	def test_accepts_the_highest_allowed_day_number(self):
		doc = self.new_itinerary()
		result = api.update_days(doc.name, {"days": [make_day(30)]})
		self.assertEqual(result["days"][0]["day_number"], 30)


class TestDraftDedup(ItineraryTestCase):
	def test_reports_an_existing_draft(self):
		doc = self.new_itinerary()
		found = api.get_draft_for_lead(self.lead.name)
		self.assertEqual(found["name"], doc.name)

	def test_reports_nothing_when_there_is_no_draft(self):
		self.assertIsNone(api.get_draft_for_lead(self.lead.name))

	def test_a_sent_itinerary_is_not_offered_as_a_draft(self):
		doc = self.new_itinerary()
		frappe.db.set_value(ITINERARY_DOCTYPE, doc.name, "status", "Sent")
		self.assertIsNone(api.get_draft_for_lead(self.lead.name))

	def test_a_stranger_cannot_probe_for_a_lead_s_drafts(self):
		self.new_itinerary()
		if not frappe.db.exists("User", OTHER_USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": OTHER_USER,
					"first_name": "Outsider",
					"send_welcome_email": 0,
					"roles": [{"role": "Sales User"}],
				}
			).insert(ignore_permissions=True)

		frappe.set_user(OTHER_USER)
		with self.assertRaises(frappe.PermissionError):
			api.get_draft_for_lead(self.lead.name)
		frappe.set_user("Administrator")


def cint_or_none(value):
	return frappe.utils.cint(value) if value is not None else None


class TestSendPermissionRollback(ItineraryTestCase):
	def test_a_whatsapp_permission_refusal_rolls_the_record_back(self):
		doc = self.new_itinerary()

		with (
			patch(
				"crm.api.itinerary.attach_pdf",
				return_value=frappe._dict({"file_url": "/files/x.pdf", "name": "F1", "is_private": 0}),
			),
			patch(
				"crm.api.whatsapp.create_whatsapp_message",
				side_effect=frappe.PermissionError("no whatsapp access"),
			),
		):
			with self.assertRaises(frappe.PermissionError):
				api.send_via_whatsapp(doc.name)

		# Nothing was sent, so the record must not say Sent.
		row = frappe.db.get_value(ITINERARY_DOCTYPE, doc.name, ["status", "version"], as_dict=True)
		self.assertEqual(row.status, "Draft")
		self.assertEqual(row.version, 1)
