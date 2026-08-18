"""The WhatsApp channel adapter: Meta's semantics, as the sequence core sees them.

This class holds no logic of its own. Every method is a one-line dispatch to the
follow-up engine module, which is where the WhatsApp rules already lived and
where they stay: approved-template-only sends, the anti-open-relay recipient
check, the sanitiser that keeps an invented link out of a template variable, the
Send Log outbox whose unique `dedup_key` is the claim, and draft-for-approval
mode.

Why the engine module is passed in rather than imported
-------------------------------------------------------
Two reasons, and both are load-bearing.

1. **No import cycle.** The follow-up engine drives the core, and the core drives
   this adapter back into the engine. Taking the module as a constructor
   argument means this file imports nothing from `crm.api`, so the cycle never
   exists.
2. **The seams stay patchable.** Every call below is an attribute lookup on the
   module object, made at call time. `patch.object(engine, "create_template_message")`
   therefore still intercepts the send, exactly as it did before the extraction,
   and the suite still cannot reach Meta.

An email adapter (spec item 21) does NOT subclass this one. It implements
`ChannelAdapter` over `crm.outbound` -- the Email Queue's own delivery states,
an unsubscribe link, and a `CRM Outbound Recipient` row as the claim.
"""

import frappe
from frappe import _

from crm.sequences import core


class WhatsAppFollowupAdapter(core.ChannelAdapter):
	"""Drives `CRM WhatsApp Followup` rows through the generic sequence core."""

	log_prefix = "CRM follow-up engine"
	row_label = "Follow-up"
	provider_label = "Meta"

	def __init__(self, engine):
		#: The `crm.api.followup_engine` module. Read at call time, never cached
		#: attribute by attribute -- see the module docstring.
		self.engine = engine

	@property
	def channel(self) -> str:
		"""The suppression-ledger channel this adapter writes and reads."""
		from crm.suppression import CHANNEL_WHATSAPP

		return CHANNEL_WHATSAPP

	# --- transaction seam ---

	def commit(self) -> None:
		self.engine.commit()

	def rollback(self) -> None:
		self.engine.rollback()

	def lock(self, name: str):
		return self.engine.lock_followup(name)

	def row_name(self, row) -> str:
		return row.name

	# --- enrolment ---

	def known_keys(self) -> set:
		return set(frappe.get_all(self.engine.FOLLOWUP_DOCTYPE, pluck="lead"))

	def list_conversations(self):
		return self.engine.get_conversation_aggregates()

	def conversation_key(self, conversation) -> str | None:
		if conversation.get("reference_doctype") != self.engine.LEAD_DOCTYPE:
			return None
		return conversation.get("reference_name")

	def enrolment_cutoff(self, settings):
		return self.engine.enrolment_cutoff(settings)

	def stop_keywords(self, settings) -> list[str]:
		return self.engine.get_stop_keywords(settings)

	def enroll_one(self, key: str, conversation, stages, cutoff, keywords) -> bool:
		return self.engine.enroll_one(key, conversation, stages, cutoff, keywords)

	# --- sweep ---

	def due_rows(self) -> list[str]:
		return frappe.get_all(
			self.engine.FOLLOWUP_DOCTYPE,
			filters={
				"state": self.engine.STATE_ACTIVE,
				"next_due": ["<=", frappe.utils.get_datetime_str(frappe.utils.now_datetime())],
			},
			pluck="name",
			order_by="next_due asc",
		)

	def budget_left(self, settings) -> bool:
		return self.engine.daily_budget_left(settings)

	# --- one row's decision ---

	def is_active(self, row) -> bool:
		return row.state == self.engine.STATE_ACTIVE

	def is_terminal(self, row) -> bool:
		return row.state in self.engine.TERMINAL_STATES

	def due_at(self, row):
		return row.next_due

	def current_stage(self, row) -> int:
		return row.current_stage

	def latest_inbound_at(self, row):
		return self.engine.get_latest_incoming(row.lead)

	def outbound_is_newer(self, row, latest_inbound) -> bool:
		return self.engine.agency_spoke_last(row.last_agency_message, latest_inbound)

	def mark_replied(self, row, latest_inbound) -> None:
		row.db_set(
			{
				"state": self.engine.STATE_REPLIED,
				"last_customer_message": latest_inbound,
				"next_due": None,
			},
			update_modified=False,
		)

	def defer(self, row, until) -> None:
		row.db_set("next_due", until, update_modified=False)

	def mark_exhausted(self, row) -> None:
		row.db_set(
			{"state": self.engine.STATE_EXHAUSTED, "next_due": None},
			update_modified=False,
		)

	def park(self, row, reason: str) -> None:
		self.engine.park_followup(row, reason)

	# --- building one send ---

	def resolve_content(self, stage):
		return self.engine.resolve_template(stage)

	def resolve_destination(self, row) -> str:
		return self.engine.resolve_recipient(row)

	def no_destination_reason(self) -> str:
		return _("No valid WhatsApp number on the lead.")

	def build_payload(self, row, stage, content) -> dict:
		names = self.engine.template_variable_names(content)
		return self.engine.build_params(row, stage, content, names)

	def is_draft_mode(self, settings) -> bool:
		return settings.send_mode == self.engine.SEND_MODE_DRAFT

	def hold_for_approval(self, row, stage_number: int, payload: dict) -> None:
		self.engine.hold_for_approval(row, stage_number, payload)

	# --- the at-most-once send ---

	def claim_key(self, row, stage_number: int) -> str:
		return self.engine.dedup_key(row.lead, row.cycle, stage_number)

	def claim(self, row, stage_number: int, key: str, now):
		"""Insert the Send Log row that spends `key`.

		The unique index on `dedup_key` is the outbox guard: a second attempt at
		the same stage of the same cycle collides here instead of paying Meta
		twice.
		"""
		log = frappe.new_doc(self.engine.SEND_LOG_DOCTYPE)
		log.update(
			{
				"followup": row.name,
				"lead": row.lead,
				"stage": stage_number,
				"dedup_key": key,
				"status": self.engine.SEND_CLAIMED,
				"sent_at": now,
			}
		)

		try:
			log.insert(ignore_permissions=True)
		except (frappe.UniqueValidationError, frappe.DuplicateEntryError) as error:
			raise core.AlreadyClaimed(key) from error

		return log

	def send(self, row, content, payload: dict, destination: str):
		return self.engine.create_template_message(row, content, payload, destination)

	def record_success(self, claim, reference) -> None:
		frappe.db.set_value(
			self.engine.SEND_LOG_DOCTYPE,
			claim.name,
			{"whatsapp_message": reference.name, "status": self.engine.SEND_SENT},
			update_modified=False,
		)

	def record_failure(self, claim) -> None:
		frappe.db.set_value(
			self.engine.SEND_LOG_DOCTYPE,
			claim.name,
			"status",
			self.engine.SEND_FAILED,
			update_modified=False,
		)

	def apply_advance(self, row, stage_number: int, exhausted: bool, next_due, sent_at) -> None:
		update = {
			"current_stage": stage_number,
			"pending_stage": 0,
			"pending_params": None,
			"blocked_reason": None,
		}
		if sent_at:
			update["last_agency_message"] = sent_at

		if exhausted:
			update["state"] = self.engine.STATE_EXHAUSTED
			update["next_due"] = None
		else:
			update["state"] = self.engine.STATE_ACTIVE
			update["next_due"] = next_due

		row.db_set(update, update_modified=False)
