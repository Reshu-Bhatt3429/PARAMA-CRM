# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

"""The AI budget claim: at-most-once, and how long it holds the row lock.

Stage 1B made the claim atomic and put it BEFORE the network call, which is
right, and left one flag open: the claim's row lock is held for the length of
the provider call, because it lives in the caller's transaction. That was
invisible while every AI call came from the scheduler. Stage 4 makes AI
interactive -- a person clicks and waits -- so two agents pressing the same
button now queue behind each other.

This module holds both halves:

* the unit tests, which assert that the interactive path releases the lock and
  the scheduler path still does not; and
* `hold_claim` / `timed_claim`, two diagnostic entry points used to MEASURE the
  contention on a real site with two concurrent connections. A unit test cannot
  measure a row lock: it would need two database connections, and a
  `FrappeTestCase` has one. They are run with `bench execute` and their output
  is recorded in `demo-package/specs/stage4-notes.md`.
"""

import time
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from crm.ai import client

SETTINGS_DOCTYPE = "CRM AI Settings"


def settings_stub(limit: int = 0):
	return frappe._dict(
		{
			"provider": "Anthropic",
			"model": "test-model",
			"api_key": "test",
			"max_monthly_requests": limit,
			"requests_this_month": 0,
			"usage_month": client.current_month(),
		}
	)


class BudgetTestCase(FrappeTestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		frappe.db.rollback()


# --- where the claim commits -----------------------------------------------


class TestClaimIsolation(BudgetTestCase):
	def test_the_scheduler_path_leaves_the_claim_in_the_callers_transaction(self):
		# The follow-up engine's claim and its send bookkeeping belong together.
		# A commit here would break that, so the default must not commit.
		with patch("crm.ai.client.commit") as commit:
			client.reserve_request(settings_stub(), client.current_month(), 0)

		commit.assert_not_called()

	def test_the_interactive_path_commits_the_claim_at_once(self):
		with patch("crm.ai.client.commit") as commit:
			client.reserve_request(settings_stub(), client.current_month(), 0, isolate=True)

		self.assertEqual(commit.call_count, 1)

	def test_a_commit_that_fails_does_not_cost_the_caller_their_answer(self):
		# The slot is claimed either way; only the lock hold time is at stake.
		with patch("crm.ai.client.commit", side_effect=Exception("no connection")):
			client.reserve_request(settings_stub(), client.current_month(), 0, isolate=True)

	def test_the_flag_travels_from_complete_to_the_reservation(self):
		with (
			patch("crm.ai.client.load_settings", return_value=settings_stub()),
			patch("crm.ai.client.dispatch", return_value="hello"),
			patch("crm.ai.client.reserve_request") as reserve,
		):
			client.complete("hello", isolate_budget_claim=True)

		self.assertTrue(reserve.call_args.kwargs["isolate"])

	def test_the_default_is_the_old_behaviour(self):
		with (
			patch("crm.ai.client.load_settings", return_value=settings_stub()),
			patch("crm.ai.client.dispatch", return_value="hello"),
			patch("crm.ai.client.reserve_request") as reserve,
		):
			client.complete("hello")

		self.assertFalse(reserve.call_args.kwargs["isolate"])


# --- the cap still holds ---------------------------------------------------


class TestAtMostOnce(BudgetTestCase):
	def test_a_claim_at_the_limit_is_refused(self):
		frappe.db.set_single_value(
			SETTINGS_DOCTYPE, {"usage_month": client.current_month(), "requests_this_month": 5}
		)

		self.assertFalse(client.claim_request(client.current_month(), 5))

	def test_a_claim_under_the_limit_is_granted_and_counted(self):
		month = client.current_month()
		frappe.db.set_single_value(SETTINGS_DOCTYPE, {"usage_month": month, "requests_this_month": 2})

		self.assertTrue(client.claim_request(month, 5))
		self.assertEqual(
			frappe.utils.cint(
				frappe.db.get_single_value(SETTINGS_DOCTYPE, "requests_this_month", cache=False)
			),
			3,
		)

	def test_zero_means_unlimited(self):
		month = client.current_month()
		frappe.db.set_single_value(SETTINGS_DOCTYPE, {"usage_month": month, "requests_this_month": 9999})

		self.assertTrue(client.claim_request(month, 0))

	def test_isolating_the_claim_does_not_relax_the_cap(self):
		month = client.current_month()
		frappe.db.set_single_value(SETTINGS_DOCTYPE, {"usage_month": month, "requests_this_month": 5})

		with patch("crm.ai.client.commit"):
			with self.assertRaises(client.AIConfigurationError):
				client.reserve_request(settings_stub(limit=5), month, 5, isolate=True)


# --- diagnostics, not tests ------------------------------------------------
#
# Two connections are needed to observe a row lock, so these are run as separate
# `bench execute` processes rather than as test methods.


def hold_claim(seconds: float = 4.0, isolate: int = 0) -> dict:
	"""Claim one AI request, hold the transaction, then roll back.

	Process A of the measurement. With `isolate=0` the claim's row lock is held
	for the whole sleep, which is what an in-transaction claim does during a
	provider call. With `isolate=1` the claim is committed first and the lock is
	released at once.

	Rolls back at the end, so an un-isolated run spends nothing. An isolated run
	has already committed its claim by then -- that is the point of it -- so the
	caller restores the counter afterwards.
	"""
	started = time.monotonic()
	claimed = client.claim_request(client.current_month(), 0)
	claim_took = time.monotonic() - started

	if frappe.utils.cint(isolate):
		frappe.db.commit()

	time.sleep(float(seconds))
	frappe.db.rollback()

	answer = {"role": "holder", "claimed": claimed, "claim_seconds": round(claim_took, 3)}
	print(frappe.as_json(answer))
	return answer


def timed_claim(isolate: int = 0) -> dict:
	"""Claim one AI request and report how long the claim itself took.

	Process B of the measurement. If the claim blocks behind another connection's
	uncommitted claim, that wait shows up here as seconds.
	"""
	started = time.monotonic()
	claimed = client.claim_request(client.current_month(), 0)
	claim_took = time.monotonic() - started

	if frappe.utils.cint(isolate):
		frappe.db.commit()
	else:
		frappe.db.rollback()

	answer = {"role": "waiter", "claimed": claimed, "claim_seconds": round(claim_took, 3)}
	print(frappe.as_json(answer))
	return answer


def budget_counter() -> dict:
	"""The month marker and the request count, uncached. For before/after lines."""
	answer = {
		"usage_month": frappe.db.get_single_value(SETTINGS_DOCTYPE, "usage_month", cache=False),
		"requests_this_month": frappe.utils.cint(
			frappe.db.get_single_value(SETTINGS_DOCTYPE, "requests_this_month", cache=False)
		),
	}
	print(frappe.as_json(answer))
	return answer


def set_budget_counter(requests_this_month: int) -> dict:
	"""Put the counter back after a measurement. Never called by the app."""
	frappe.db.set_single_value(
		SETTINGS_DOCTYPE,
		{
			"usage_month": client.current_month(),
			"requests_this_month": frappe.utils.cint(requests_this_month),
		},
	)
	frappe.db.commit()
	return budget_counter()
