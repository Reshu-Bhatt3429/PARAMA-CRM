"""One follow-up row, stages on two channels: who answers which call.

Why a router and not a channel column
--------------------------------------
Master spec F3 is explicit that a channel is an ADAPTER, not a column on the
stage table. The stages of one sequence can still mix -- "nudge on WhatsApp,
then a proper email two days later" is the shape a travel agency actually wants
-- so something has to decide, per call, which adapter owns it. That is this
class, and it is deliberately dumb: it holds no rules of its own, it only
dispatches.

The split is not arbitrary. Two questions separate the halves:

* **Does this call touch the ROW?** Locking it, reading its due date, parking it,
  advancing it. The row is one `CRM WhatsApp Followup` whatever the stage sends
  on, so the WhatsApp adapter -- which is where those rules already live -- owns
  every one of them, for both channels.
* **Does this call touch the CHANNEL?** Building the message, finding the
  address, claiming the key, handing it over. Those go to the adapter that owns
  the stage being sent.

Which stage is "being sent" is never guessed: the core advances `current_stage`
only after a stage completes, so the pending stage is `current_stage + 1` from
the moment a row falls due until the moment it advances. Calls that carry a
stage or a stage number use that instead, and are exact.

The daily cap is shared
-----------------------
One cap covers both channels, counted as WhatsApp claims plus email claims. That
is stricter than a cap per channel, never looser, and it is what an agency means
by "no more than 50 follow-ups a day". `crm.sequences.core.sweep` asks for the
budget once per row and before it knows the row's channel, so a per-channel cap
would need the core to change shape for no gain in safety.

This class exists ONLY when a stage is configured on the email channel. A site
with a pure WhatsApp sequence gets the WhatsApp adapter directly and runs exactly
the code it ran before Stage 5.1 -- see
`crm.api.followup_engine.get_channel_adapter`.
"""

import frappe

from crm.sequences import core
from crm.sequences.email import CHANNEL_WHATSAPP, EmailSequenceAdapter, email_stage


class ChannelRouter(core.ChannelAdapter):
	"""Dispatches each call to the adapter that owns it. Holds no rules."""

	log_prefix = "CRM sequence router"
	row_label = "Follow-up"
	provider_label = "the channel"

	def __init__(self, engine, stages, whatsapp, email=None):
		self.engine = engine
		self.stages = stages or []
		self.whatsapp = whatsapp
		self.email = email or EmailSequenceAdapter(engine)
		#: The adapter that last answered `resolve_destination`. The core asks for
		#: the failure reason in a separate, argument-less call, so the two have to
		#: agree on which channel was being asked about.
		self.last_destination = whatsapp

	# --- dispatch ---

	def for_stage(self, stage):
		"""The adapter that owns one configured stage."""
		return self.email if stage is not None and email_stage(stage) else self.whatsapp

	def stage_at(self, stage_number: int):
		"""The configured stage with this 1-based number, or None past the end."""
		if 1 <= stage_number <= len(self.stages):
			return self.stages[stage_number - 1]
		return None

	def for_row(self, row):
		"""The adapter that owns the stage this row is about to send."""
		return self.for_stage(self.stage_at(frappe.utils.cint(row.current_stage) + 1))

	def for_claim(self, claim):
		"""The adapter that produced one claim, read off the claim's own doctype."""
		doctype = claim.get("doctype") if hasattr(claim, "get") else None
		return self.email if doctype == "CRM Outbound Job" else self.whatsapp

	# --- the row: always the row's own doctype ---

	def commit(self) -> None:
		self.whatsapp.commit()

	def rollback(self) -> None:
		self.whatsapp.rollback()

	def lock(self, name: str):
		return self.whatsapp.lock(name)

	def row_name(self, row) -> str:
		return self.whatsapp.row_name(row)

	def known_keys(self) -> set:
		return self.whatsapp.known_keys()

	def list_conversations(self):
		return self.whatsapp.list_conversations()

	def conversation_key(self, conversation):
		return self.whatsapp.conversation_key(conversation)

	def enrolment_cutoff(self, settings):
		return self.whatsapp.enrolment_cutoff(settings)

	def stop_keywords(self, settings) -> list[str]:
		return self.whatsapp.stop_keywords(settings)

	def enroll_one(self, key: str, conversation, stages, cutoff, keywords) -> bool:
		return self.whatsapp.enroll_one(key, conversation, stages, cutoff, keywords)

	def due_rows(self) -> list[str]:
		return self.whatsapp.due_rows()

	def is_active(self, row) -> bool:
		return self.whatsapp.is_active(row)

	def is_terminal(self, row) -> bool:
		return self.whatsapp.is_terminal(row)

	def due_at(self, row):
		return self.whatsapp.due_at(row)

	def current_stage(self, row) -> int:
		return self.whatsapp.current_stage(row)

	def mark_replied(self, row, latest_inbound) -> None:
		self.whatsapp.mark_replied(row, latest_inbound)

	def defer(self, row, until) -> None:
		self.whatsapp.defer(row, until)

	def mark_exhausted(self, row) -> None:
		self.whatsapp.mark_exhausted(row)

	def park(self, row, reason: str) -> None:
		self.whatsapp.park(row, reason)

	def apply_advance(self, row, stage_number: int, exhausted: bool, next_due, sent_at) -> None:
		self.whatsapp.apply_advance(row, stage_number, exhausted, next_due, sent_at)

	# --- the reply check: both channels count ---

	def latest_inbound_at(self, row):
		"""The newest customer message on EITHER channel.

		A row with an email stage can be answered by email, and the sweep must see
		that answer even if the Communication hook did not (the flag was off when
		the reply arrived, the hook failed, the reply was imported later). Taking
		the newest of the two is strictly safer than taking one: an extra stop
		costs a message, a missed stop costs the customer's patience.
		"""
		stamps = [
			self.whatsapp.latest_inbound_at(row),
			latest_inbound_email(row.lead),
		]
		stamps = [frappe.utils.get_datetime(stamp) for stamp in stamps if stamp]
		return max(stamps) if stamps else None

	def outbound_is_newer(self, row, latest_inbound) -> bool:
		return self.whatsapp.outbound_is_newer(row, latest_inbound)

	# --- the budget: one cap, both channels ---

	def budget_left(self, settings) -> bool:
		cap = frappe.utils.cint(settings.daily_send_cap)
		if cap <= 0:
			return True

		return (self.engine.count_sent_today() + self.email.count_sent_today()) < cap

	# --- the channel: whichever stage is being sent ---

	def resolve_content(self, stage):
		return self.for_stage(stage).resolve_content(stage)

	def resolve_destination(self, row) -> str:
		self.last_destination = self.for_row(row)
		return self.last_destination.resolve_destination(row)

	def no_destination_reason(self) -> str:
		return self.last_destination.no_destination_reason()

	def build_payload(self, row, stage, content) -> dict:
		return self.for_stage(stage).build_payload(row, stage, content)

	def is_draft_mode(self, settings) -> bool:
		return self.whatsapp.is_draft_mode(settings)

	def hold_for_approval(self, row, stage_number: int, payload: dict) -> None:
		self.for_stage(self.stage_at(stage_number)).hold_for_approval(row, stage_number, payload)

	def claim_key(self, row, stage_number: int) -> str:
		return self.for_stage(self.stage_at(stage_number)).claim_key(row, stage_number)

	def claim(self, row, stage_number: int, key: str, now):
		return self.for_stage(self.stage_at(stage_number)).claim(row, stage_number, key, now)

	def send(self, row, content, payload: dict, destination: str):
		return self.for_row(row).send(row, content, payload, destination)

	def record_success(self, claim, reference) -> None:
		self.for_claim(claim).record_success(claim, reference)

	def record_failure(self, claim) -> None:
		self.for_claim(claim).record_failure(claim)


def latest_inbound_email(lead: str):
	"""When this lead last emailed us, or None."""
	if not lead:
		return None

	rows = frappe.get_all(
		"Communication",
		filters={
			"communication_type": "Communication",
			"sent_or_received": "Received",
			"reference_doctype": "CRM Lead",
			"reference_name": lead,
		},
		fields=["communication_date", "creation"],
		order_by="creation desc",
		limit_page_length=1,
	)
	if not rows:
		return None
	return rows[0].communication_date or rows[0].creation


def uses_email(stages) -> bool:
	"""True when any configured stage sends on the email channel."""
	return any(email_stage(stage) for stage in stages or [])


__all__ = ["CHANNEL_WHATSAPP", "ChannelRouter", "latest_inbound_email", "uses_email"]
