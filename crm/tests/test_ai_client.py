# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""Tests for the AI client's hardening (F6): budget, schema, request size.

The client's provider shapes, key handling and retry behaviour are covered in
`test_followup_engine.TestAIClient` and are unchanged. What is tested here is
what Stage 1B added, and every test in this file states the failure it stops.

No request leaves the process. `requests.post` is stubbed in every test that
reaches the network path, and the tests that touch `CRM AI Settings` write
inside the transaction the base class rolls back.
"""

import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.ai import client as ai_client
from crm.ai import schema as ai_schema


def http_response(status_code=200, payload=None, text=""):
	response = MagicMock()
	response.status_code = status_code
	response.text = text or json.dumps(payload or {})
	response.json.return_value = payload if payload is not None else {}
	return response


def anthropic_answer(text: str):
	return http_response(payload={"content": [{"type": "text", "text": text}]})


def ai_settings(limit=0, used=0, month=None):
	return frappe._dict(
		{
			"provider": "Anthropic",
			"model": "claude-sonnet-5",
			"api_key": "sk-secret-value",
			"max_monthly_requests": limit,
			"requests_this_month": used,
			"usage_month": month or ai_client.current_month(),
		}
	)


# --- the schema validator --------------------------------------------------


class TestSchemaValidator(FrappeTestCase):
	"""`crm.ai.schema`. "It is JSON" was never the question."""

	def refuses(self, instance, schema):
		self.assertRaises(ai_schema.SchemaViolation, ai_schema.validate, instance, schema)

	def test_types_are_checked(self):
		ai_schema.validate("text", {"type": "string"})
		ai_schema.validate(3, {"type": "integer"})
		ai_schema.validate(3.5, {"type": "number"})
		ai_schema.validate(True, {"type": "boolean"})
		ai_schema.validate(None, {"type": "null"})
		ai_schema.validate([], {"type": "array"})
		ai_schema.validate({}, {"type": "object"})

		self.refuses(3, {"type": "string"})
		self.refuses("3", {"type": "integer"})
		self.refuses([], {"type": "object"})

	def test_a_boolean_is_not_a_number(self):
		"""Python says `True == 1`. A template variable must not become "True"."""
		self.refuses(True, {"type": "integer"})
		self.refuses(True, {"type": "number"})

	def test_a_list_of_types_accepts_any_of_them(self):
		ai_schema.validate("text", {"type": ["string", "null"]})
		ai_schema.validate(None, {"type": ["string", "null"]})
		self.refuses(3, {"type": ["string", "null"]})

	def test_nullable_accepts_a_missing_value(self):
		ai_schema.validate(None, {"type": "string", "nullable": True})
		self.refuses(None, {"type": "string"})

	def test_required_members_must_be_present(self):
		"""The failure this catches: an empty object where three values were asked for."""
		schema = {"type": "object", "properties": {"1": {"type": "string"}}, "required": ["1"]}

		ai_schema.validate({"1": "Ann"}, schema)
		self.refuses({}, schema)
		self.refuses({"2": "Ann"}, schema)

	def test_a_required_member_must_also_match_its_own_schema(self):
		schema = {"type": "object", "properties": {"1": {"type": "string"}}, "required": ["1"]}
		self.refuses({"1": None}, schema)
		self.refuses({"1": ["Ann"]}, schema)

	def test_additional_properties_can_be_refused(self):
		schema = {"type": "object", "properties": {"1": {"type": "string"}}, "additionalProperties": False}

		ai_schema.validate({"1": "Ann"}, schema)
		self.refuses({"1": "Ann", "2": "Bali"}, schema)

	def test_additional_properties_can_be_a_schema(self):
		schema = {"type": "object", "additionalProperties": {"type": "string"}}

		ai_schema.validate({"anything": "Ann"}, schema)
		self.refuses({"anything": 3}, schema)

	def test_array_items_and_bounds(self):
		schema = {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2}

		ai_schema.validate(["a"], schema)
		ai_schema.validate(["a", "b"], schema)
		self.refuses([], schema)
		self.refuses(["a", "b", "c"], schema)
		self.refuses([1], schema)

	def test_string_and_number_bounds(self):
		self.refuses("", {"type": "string", "minLength": 1})
		self.refuses("x" * 130, {"type": "string", "maxLength": 120})
		self.refuses(-1, {"type": "number", "minimum": 0})
		self.refuses(11, {"type": "number", "maximum": 10})
		self.refuses("nope", {"type": "string", "pattern": r"^\d+$"})
		ai_schema.validate("2026", {"type": "string", "pattern": r"^\d+$"})

	def test_enum_and_const(self):
		self.refuses("night", {"enum": ["morning", "afternoon"]})
		ai_schema.validate("morning", {"enum": ["morning", "afternoon"]})
		self.refuses(False, {"const": True})

	def test_combinators(self):
		any_of = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
		ai_schema.validate("a", any_of)
		ai_schema.validate(1, any_of)
		self.refuses([], any_of)

		one_of = {"oneOf": [{"type": "string"}, {"type": "string", "maxLength": 2}]}
		# "ab" matches both, which is exactly what oneOf refuses.
		self.refuses("ab", one_of)

		all_of = {"allOf": [{"type": "string"}, {"maxLength": 2}]}
		ai_schema.validate("ab", all_of)
		self.refuses("abc", all_of)

	def test_the_message_names_the_member_that_is_wrong(self):
		"""A retry prompt that names the fault is the one a model can act on."""
		schema = {
			"type": "object",
			"properties": {"days": {"type": "array", "items": {"type": "object"}}},
		}

		with self.assertRaises(ai_schema.SchemaViolation) as caught:
			ai_schema.validate({"days": ["not an object"]}, schema)

		self.assertIn("days[0]", str(caught.exception))

	def test_a_schema_keyword_this_validator_does_not_implement_is_refused(self):
		"""An author who writes `$ref` finds out at once, not silently."""
		self.assertRaises(ai_schema.UnsupportedSchema, ai_schema.validate, {}, {"$ref": "#/x"})
		self.assertRaises(ai_schema.UnsupportedSchema, ai_schema.validate, {}, {"type": "tuple"})

	def test_the_schemas_this_app_actually_sends_are_supported(self):
		"""The follow-up engine's variable schema, exactly as it builds it."""
		names = ["first_name", "destination"]
		schema = {
			"type": "object",
			"properties": {str(index): {"type": "string"} for index in range(1, len(names) + 1)},
			"required": [str(index) for index in range(1, len(names) + 1)],
			"additionalProperties": False,
		}

		ai_schema.validate({"1": "Ann", "2": "Bali"}, schema)
		self.refuses({"1": "Ann"}, schema)
		self.refuses({"1": "Ann", "2": "Bali", "3": "extra"}, schema)

	def test_the_itinerary_schemas_are_enforced_not_skipped(self):
		"""The real schemas, from the module that sends them.

		A schema this validator cannot read is logged and the answer is let
		through, so "the itinerary is safe" would be a false claim if these used
		a keyword it does not implement. They are checked here against the actual
		constants, so a later edit to either schema fails this test rather than
		silently turning the check off.
		"""
		from crm.api.itinerary import DAY_SCHEMA, SKELETON_SCHEMA

		ai_schema.validate(
			{"days": [{"day_number": 1, "title": "Arrival", "summary": "Land and settle in"}]},
			SKELETON_SCHEMA,
		)
		# A day the model left half-written is refused, not written to the record.
		self.refuses({"days": [{"day_number": 1, "title": "Arrival"}]}, SKELETON_SCHEMA)
		self.refuses({"days": "one day"}, SKELETON_SCHEMA)

		ai_schema.validate(
			{
				"title": "Day 1",
				"summary": "Arrival",
				"highlights": ["Airport pickup"],
				"description": "Arrive and settle in.",
				"accommodation": "Hotel",
				"meals": {"breakfast": False, "lunch": True, "dinner": True},
				"slots": [
					{
						"time_of_day": "morning",
						"items": [
							{
								"title": "Airport pickup",
								"description": "A driver meets the group.",
								"place_name": None,
								"duration_hours": 1.5,
								"est_cost": None,
							}
						],
					}
				],
			},
			DAY_SCHEMA,
		)
		self.refuses({"title": "Day 1"}, DAY_SCHEMA)
		self.refuses(
			{"slots": [{"time_of_day": "midnight", "items": []}]},
			DAY_SCHEMA,
		)


# --- the client's use of it ------------------------------------------------


class TestSchemaEnforcement(FrappeTestCase):
	SCHEMA = {  # noqa: RUF012
		"type": "object",
		"properties": {"1": {"type": "string"}},
		"required": ["1"],
		"additionalProperties": False,
	}

	def tearDown(self):
		frappe.db.rollback()

	def test_an_answer_that_misses_a_required_key_is_retried_once_then_refused(self):
		"""Before this, `{}` was a valid answer and the caller wrote it to a customer."""
		empty = anthropic_answer("{}")

		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings()),
			patch.object(ai_client, "record_usage"),
			patch.object(ai_client.requests, "post", side_effect=[empty, empty]) as post,
		):
			self.assertRaises(ai_client.AIResponseError, ai_client.complete, "fill", json_schema=self.SCHEMA)

		self.assertEqual(post.call_count, 2)

	def test_the_retry_tells_the_model_the_schema_was_the_problem(self):
		"""'Not valid JSON' to a model that returned valid JSON is unactionable."""
		wrong = anthropic_answer('{"2": "Bali"}')
		right = anthropic_answer('{"1": "Ann"}')

		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings()),
			patch.object(ai_client, "record_usage"),
			patch.object(ai_client.requests, "post", side_effect=[wrong, right]) as post,
		):
			answer = ai_client.complete("fill", json_schema=self.SCHEMA)

		self.assertEqual(answer, {"1": "Ann"})
		retry_prompt = post.call_args[1]["json"]["messages"][0]["content"]
		self.assertIn("did not match the schema", retry_prompt)
		self.assertIn("missing '1'", retry_prompt)

	def test_a_valid_answer_passes_straight_through(self):
		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings()),
			patch.object(ai_client, "record_usage"),
			patch.object(ai_client.requests, "post", return_value=anthropic_answer('{"1": "Ann"}')) as post,
		):
			self.assertEqual(ai_client.complete("fill", json_schema=self.SCHEMA), {"1": "Ann"})

		self.assertEqual(post.call_count, 1)

	def test_a_schema_this_app_wrote_badly_is_logged_not_charged_to_the_model(self):
		"""The schema is ours. Refusing the answer would hide our own bug."""
		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings()),
			patch.object(ai_client, "record_usage"),
			patch.object(ai_client.requests, "post", return_value=anthropic_answer('{"1": "Ann"}')) as post,
			patch.object(frappe, "log_error") as log_mock,
		):
			answer = ai_client.complete("fill", json_schema={"$ref": "#/definitions/thing"})

		self.assertEqual(answer, {"1": "Ann"})
		self.assertEqual(post.call_count, 1)
		log_mock.assert_called_once()


class TestRequestSizeCap(FrappeTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_an_oversized_prompt_never_reaches_the_provider(self):
		"""The budget counts REQUESTS. Without this, one request is a whole month."""
		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings()),
			patch.object(ai_client, "claim_request") as claim_mock,
			patch.object(ai_client.requests, "post") as post,
		):
			self.assertRaises(
				ai_client.AIRequestError,
				ai_client.complete,
				"x" * (ai_client.MAX_REQUEST_BYTES + 1),
			)

		post.assert_not_called()
		claim_mock.assert_not_called()

	def test_the_system_prompt_counts_towards_the_ceiling(self):
		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings()),
			patch.object(ai_client.requests, "post") as post,
		):
			self.assertRaises(
				ai_client.AIRequestError,
				ai_client.complete,
				"hi",
				system="x" * (ai_client.MAX_REQUEST_BYTES + 1),
			)

		post.assert_not_called()

	def test_a_prompt_at_the_ceiling_is_allowed(self):
		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings()),
			patch.object(ai_client, "claim_request", return_value=True),
			patch.object(ai_client, "record_usage"),
			patch.object(ai_client.requests, "post", return_value=anthropic_answer("ok")),
		):
			self.assertEqual(ai_client.complete("x" * ai_client.MAX_REQUEST_BYTES), "ok")

	def test_the_size_is_measured_in_bytes_not_characters(self):
		"""A prompt of accented or Devanagari text is bigger than it looks."""
		wide = "ह" * ai_client.MAX_REQUEST_BYTES  # three bytes each

		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings()),
			patch.object(ai_client.requests, "post") as post,
		):
			self.assertRaises(ai_client.AIRequestError, ai_client.complete, wide)

		post.assert_not_called()


class TestBudgetReservation(FrappeTestCase):
	"""Two workers, one last request of the month. Only one may have it."""

	def setUp(self):
		self.month = ai_client.current_month()

	def tearDown(self):
		frappe.db.rollback()
		frappe.clear_document_cache(ai_client.SETTINGS_DOCTYPE, ai_client.SETTINGS_DOCTYPE)

	def set_counter(self, used, month=None):
		frappe.db.set_single_value(
			ai_client.SETTINGS_DOCTYPE,
			{"usage_month": month or self.month, "requests_this_month": used},
		)

	def counted(self):
		return frappe.utils.cint(
			frappe.db.get_single_value(ai_client.SETTINGS_DOCTYPE, "requests_this_month", cache=False)
		)

	def test_the_claim_is_refused_at_the_cap_and_the_counter_does_not_move(self):
		self.set_counter(5)

		self.assertFalse(ai_client.claim_request(self.month, 5))
		self.assertEqual(self.counted(), 5)

	def test_each_claim_moves_the_counter_by_exactly_one(self):
		self.set_counter(0)

		self.assertTrue(ai_client.claim_request(self.month, 3))
		self.assertEqual(self.counted(), 1)
		self.assertTrue(ai_client.claim_request(self.month, 3))
		self.assertTrue(ai_client.claim_request(self.month, 3))
		self.assertEqual(self.counted(), 3)

		self.assertFalse(ai_client.claim_request(self.month, 3))
		self.assertEqual(self.counted(), 3)

	def test_a_cap_of_zero_means_unlimited(self):
		self.set_counter(9_999)

		self.assertTrue(ai_client.claim_request(self.month, 0))
		self.assertEqual(self.counted(), 10_000)

	def test_a_new_month_starts_from_zero(self):
		self.set_counter(500, month="1999-01")

		self.assertTrue(ai_client.claim_request(self.month, 5))
		self.assertEqual(self.counted(), 1)
		self.assertEqual(
			frappe.db.get_single_value(ai_client.SETTINGS_DOCTYPE, "usage_month", cache=False), self.month
		)

	def test_an_unexpected_database_failure_fails_open_and_is_logged(self):
		"""A dead counter must not take the AI features down with it."""
		with (
			patch.object(frappe.db, "get_single_value", side_effect=RuntimeError("db down")),
			patch.object(frappe, "log_error") as log_mock,
		):
			self.assertTrue(ai_client.claim_request(self.month, 5))

		log_mock.assert_called_once()

	def test_the_reservation_refuses_the_call_when_the_claim_is_refused(self):
		with patch.object(ai_client, "claim_request", return_value=False):
			self.assertRaises(
				ai_client.AIConfigurationError,
				ai_client.reserve_request,
				ai_settings(limit=5, used=5),
				self.month,
				5,
			)

	def test_the_budget_is_spent_before_the_network_call(self):
		"""A crash mid-request must over-count by one, never lose the charge."""
		order = []

		def claim(month, limit):
			order.append("claim")
			return True

		def post(*args, **kwargs):
			order.append("post")
			return anthropic_answer("ok")

		with (
			patch.object(ai_client, "load_settings", return_value=ai_settings()),
			patch.object(ai_client, "claim_request", side_effect=claim),
			patch.object(ai_client, "record_usage"),
			patch.object(ai_client.requests, "post", side_effect=post),
		):
			ai_client.complete("hi")

		self.assertEqual(order, ["claim", "post"])

	def test_the_claim_uses_the_limit_the_caller_loaded(self):
		"""A manager who raises the cap must not change a job that is in flight."""
		with (
			patch.object(ai_client, "claim_request", return_value=True) as claim_mock,
			patch.object(ai_client, "record_usage"),
		):
			ai_client.reserve_request(ai_settings(limit=42, used=1), self.month, 1)

		claim_mock.assert_called_once_with(self.month, 42)

	def test_the_display_counter_is_never_lowered(self):
		"""Two workers must not write each other's stale count back."""
		self.set_counter(7)

		ai_client.record_usage(self.month, 2)

		self.assertEqual(self.counted(), 7)

	def test_the_display_counter_catches_up_when_it_reads_low(self):
		self.set_counter(1)

		ai_client.record_usage(self.month, 5)

		self.assertEqual(self.counted(), 6)

	def test_a_counter_write_never_sinks_the_callers_work(self):
		with (
			patch.object(frappe.db, "set_single_value", side_effect=RuntimeError("db down")),
			patch.object(frappe, "log_error") as log_mock,
		):
			ai_client.record_usage(self.month, 1)

		log_mock.assert_called_once()
