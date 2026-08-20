"""The generic sequence engine: enrolment, stage transitions, at-most-once send.

What this module is for
-----------------------
A follow-up sequence is the same machine whatever it sends on. A conversation is
enrolled, it waits, a stage falls due, something goes out, the row advances, and
a customer reply stops the whole thing. Only the last part of each of those
sentences is channel-specific. This module owns the machine; a **channel
adapter** owns the channel.

That split is what lets the WhatsApp follow-up engine and a later email sequence
share one set of guarantees instead of two sets of bugs.

The ordering contract (the whole point)
---------------------------------------
`deliver` runs the same four steps for every channel, and the order is the
guarantee, not an implementation detail:

1. Claim the stage's unique key through the adapter.
2. **Commit.** The key is now spent whatever happens next.
3. Only then call the outside world.
4. Record the outcome and commit again.

A crash between 2 and 4 loses a message; it can never repeat one. That is the
correct trade for a paid, customer-visible message, and it is why the commit
seam is an adapter method rather than a bare `frappe.db.commit()` -- the tests
neutralise it and assert its ordering.

Writing an adapter
------------------
Subclass `ChannelAdapter` and implement every method that raises
`NotImplementedError`. The core never touches a doctype, a field name or a
provider; if an adapter method needs to look one up, that is the adapter's job.

An email adapter (spec item 21) implements the same interface over
`crm.outbound`: `claim` creates the `CRM Outbound Recipient` row whose unique
idempotency key is the claim, `send` hands the row to the outbound adapter and
returns the Email Queue row, and `record_success` stores that queue name so
`crm.outbound.refresh_delivery_states` can read the real delivery state back.
Nothing in this module changes for it.

Error contract: this module never swallows an exception it did not expect. The
sweep catches per-row failures so one row cannot cost the others their run, and
that is the only broad catch here.
"""

from datetime import datetime, timedelta

import frappe


class AlreadyClaimed(Exception):
	"""The stage's key is already spent. Raised by `ChannelAdapter.claim`.

	This is not an error condition. It is the outbox guard doing its job after a
	crashed or replayed run, and the core answers it by advancing the row rather
	than by retrying -- a retry would collide on the same key every run for ever.
	"""


# --- the adapter interface -------------------------------------------------


class ChannelAdapter:
	"""Everything the sequence core is not allowed to know.

	Grouped by the phase that calls it. Every method that raises
	`NotImplementedError` is required; the three attributes below are only used
	to label log entries.
	"""

	#: Prefixes every log title, e.g. "CRM follow-up engine".
	log_prefix = "CRM sequence"

	#: What one enrolled row is called in a log line, e.g. "Follow-up".
	row_label = "Sequence row"

	#: Who the send goes to, in a log line, e.g. "Meta".
	provider_label = "the provider"

	# --- transaction seam ---

	def commit(self) -> None:
		"""End the current transaction. See the module docstring."""
		raise NotImplementedError

	def rollback(self) -> None:
		"""Discard the current transaction."""
		raise NotImplementedError

	def lock(self, name: str):
		"""Re-read one row with `SELECT ... FOR UPDATE` and return it."""
		raise NotImplementedError

	def row_name(self, row) -> str:
		raise NotImplementedError

	# --- enrolment ---

	def known_keys(self) -> set:
		"""Every conversation key that already has a row. Mutated by the core."""
		raise NotImplementedError

	def list_conversations(self):
		"""The conversations the channel knows about, newest state included."""
		raise NotImplementedError

	def conversation_key(self, conversation) -> str | None:
		"""The enrolment key for one conversation, or None to skip it."""
		raise NotImplementedError

	def enrolment_cutoff(self, settings):
		"""The oldest last-activity an enrolment may have, or None for no bound."""
		raise NotImplementedError

	def stop_keywords(self, settings) -> list[str]:
		raise NotImplementedError

	def enroll_one(self, key: str, conversation, stages, cutoff, keywords) -> bool:
		"""Create one row. True when a row was written."""
		raise NotImplementedError

	# --- sweep ---

	def due_rows(self) -> list[str]:
		"""Names of the rows whose next stage is due, in the order to serve them."""
		raise NotImplementedError

	def budget_left(self, settings) -> bool:
		"""False when the channel's send cap for this period is spent."""
		raise NotImplementedError

	# --- one row's decision ---

	def is_active(self, row) -> bool:
		"""True when the row is waiting to send. Anything else stops the sweep."""
		raise NotImplementedError

	def is_terminal(self, row) -> bool:
		"""True for a state no send may ever escape, e.g. an opt-out."""
		raise NotImplementedError

	def due_at(self, row):
		raise NotImplementedError

	def current_stage(self, row) -> int:
		raise NotImplementedError

	def latest_inbound_at(self, row):
		"""When the customer last wrote, or None."""
		raise NotImplementedError

	def outbound_is_newer(self, row, latest_inbound) -> bool:
		"""True when OUR last message is newer than the customer's."""
		raise NotImplementedError

	def mark_replied(self, row, latest_inbound) -> None:
		raise NotImplementedError

	def defer(self, row, until) -> None:
		"""Push the row's next attempt to `until`. Never cancels it."""
		raise NotImplementedError

	def mark_exhausted(self, row) -> None:
		raise NotImplementedError

	def park(self, row, reason: str) -> None:
		"""Stop sweeping a row the engine cannot serve, and record why."""
		raise NotImplementedError

	# --- building one send ---

	def resolve_content(self, stage):
		"""`(content, None)` when the stage can be sent, else `(None, reason)`."""
		raise NotImplementedError

	def resolve_destination(self, row) -> str:
		"""The address to send to, re-derived at send time. "" when there is none."""
		raise NotImplementedError

	def no_destination_reason(self) -> str:
		raise NotImplementedError

	def build_payload(self, row, stage, content) -> dict:
		"""The variable values, body or context this stage sends."""
		raise NotImplementedError

	def is_draft_mode(self, settings) -> bool:
		raise NotImplementedError

	def hold_for_approval(self, row, stage_number: int, payload: dict) -> None:
		raise NotImplementedError

	# --- the at-most-once send ---

	def claim_key(self, row, stage_number: int) -> str:
		"""One key per row, per cycle, per stage. Must be unique in the outbox."""
		raise NotImplementedError

	def claim(self, row, stage_number: int, key: str, now):
		"""Write the outbox row that spends `key`. Raises `AlreadyClaimed`.

		Returns whatever `record_success` and `record_failure` need to find the
		row again. It is NOT committed here -- the core owns the commit.
		"""
		raise NotImplementedError

	def send(self, row, content, payload: dict, destination: str):
		"""Hand the message to the outside world. Raising means it failed."""
		raise NotImplementedError

	def record_success(self, claim, reference) -> None:
		raise NotImplementedError

	def record_failure(self, claim) -> None:
		raise NotImplementedError

	def apply_advance(self, row, stage_number: int, exhausted: bool, next_due, sent_at) -> None:
		"""Persist the transition the core decided. `next_due` is None when exhausted."""
		raise NotImplementedError


# --- enrolment -------------------------------------------------------------


def enroll(adapter: ChannelAdapter, settings, stages) -> int:
	"""Create a row for every conversation the adapter knows and has none for.

	Each enrolment runs inside its own savepoint: one conversation that cannot be
	enrolled -- a deleted lead, an unparseable address -- must not cost the whole
	run its work. That is a real failure this replaces, not a hypothetical one.
	"""
	created = 0
	known = adapter.known_keys()
	cutoff = adapter.enrolment_cutoff(settings)
	keywords = adapter.stop_keywords(settings)

	for conversation in adapter.list_conversations():
		key = adapter.conversation_key(conversation)
		if not key or key in known:
			continue

		savepoint = f"enroll_{frappe.generate_hash(length=8)}"
		frappe.db.savepoint(savepoint)
		try:
			if adapter.enroll_one(key, conversation, stages, cutoff, keywords):
				created += 1
			known.add(key)
		except Exception:
			frappe.db.rollback(save_point=savepoint)
			frappe.log_error(frappe.get_traceback(), f"{adapter.log_prefix}: enrolment failed for {key}")

	adapter.commit()
	return created


def enrolment_cutoff(settings, default_days: int):
	"""The oldest last-activity an enrolment is allowed to have.

	`None` for the stored value means the field was never saved -- a Single only
	applies its field defaults while nothing at all has been saved, so a field
	added after the first save reads back as `None`. Treating that as 0 would
	silently turn the backlog guard off, so `default_days` is used instead. An
	explicit 0 is still honoured and means "no bound".
	"""
	stored = settings.get("ignore_older_than_days")
	days = default_days if stored is None else frappe.utils.cint(stored)
	if days <= 0:
		return None
	return frappe.utils.add_to_date(frappe.utils.now_datetime(), days=-days)


# --- the sweep -------------------------------------------------------------


def sweep(adapter: ChannelAdapter, settings, stages) -> int:
	"""Serve every due row. Returns the number of messages that went out.

	Each row is processed in its own transaction, so one row's failure can never
	roll back another row's committed -- and already paid for -- send. The budget
	is re-checked per row rather than once per sweep, because a sweep that takes
	minutes must not spend a cap that ran out while it was running.
	"""
	due = adapter.due_rows()
	if not due:
		return 0

	sent = 0
	for name in due:
		if not adapter.budget_left(settings):
			break

		try:
			if process_row(adapter, name, settings, stages):
				sent += 1
		except Exception:
			adapter.rollback()
			frappe.log_error(frappe.get_traceback(), f"{adapter.log_prefix}: send failed for {name}")

	return sent


def process_row(adapter: ChannelAdapter, name: str, settings, stages) -> bool:
	"""Decide what happens to one due row. True when a message went out.

	Everything below runs in a transaction that began after the previous row
	committed, so the snapshot is current. That matters: MariaDB's REPEATABLE
	READ would otherwise hide a reply another worker committed while this job was
	running, and we would message a customer who had already answered.
	"""
	# Recomputed per row, not once per sweep: a long sweep must not send past the
	# start of quiet hours.
	now = frappe.utils.now_datetime()

	row = adapter.lock(name)
	if not adapter.is_active(row):
		adapter.commit()
		return False

	due_at = adapter.due_at(row)
	if not due_at or frappe.utils.get_datetime(due_at) > now:
		adapter.commit()
		return False

	latest_inbound = adapter.latest_inbound_at(row)
	if latest_inbound and not adapter.outbound_is_newer(row, latest_inbound):
		adapter.mark_replied(row, latest_inbound)
		adapter.commit()
		return False

	if in_quiet_hours(now, settings):
		adapter.defer(row, quiet_hours_end_after(now, settings))
		adapter.commit()
		return False

	return send_stage(adapter, row, settings, stages, adapter.current_stage(row) + 1, now)


# --- quiet hours -----------------------------------------------------------


def in_quiet_hours(now, settings) -> bool:
	"""True when `now` falls inside the configured quiet window."""
	start = to_time(settings.quiet_hours_start)
	end = to_time(settings.quiet_hours_end)
	if not start or not end or start == end:
		return False

	moment = frappe.utils.get_datetime(now).time()
	if start < end:
		return start <= moment < end
	# The window wraps midnight, which is the normal case (21:00 -> 09:00).
	return moment >= start or moment < end


def quiet_hours_end_after(now, settings):
	"""The next moment the quiet window closes, at or after `now`."""
	end = to_time(settings.quiet_hours_end)
	now = frappe.utils.get_datetime(now)
	if not end:
		return now

	candidate = datetime.combine(now.date(), end)
	if candidate <= now:
		candidate += timedelta(days=1)
	return candidate


def to_time(value):
	if value in (None, ""):
		return None
	try:
		return frappe.utils.get_time(value)
	except Exception:
		return None


# --- sending ---------------------------------------------------------------


def send_stage(adapter: ChannelAdapter, row, settings, stages, stage_number: int, now) -> bool:
	"""Send (or draft) one stage. True when a message was handed to the channel."""
	if adapter.is_terminal(row):
		adapter.commit()
		return False

	if stage_number > len(stages):
		adapter.mark_exhausted(row)
		adapter.commit()
		return False

	stage = stages[stage_number - 1]
	content, problem = adapter.resolve_content(stage)
	if problem:
		adapter.park(row, problem)
		adapter.commit()
		return False

	destination = adapter.resolve_destination(row)
	if not destination:
		adapter.park(row, adapter.no_destination_reason())
		adapter.commit()
		return False

	payload = adapter.build_payload(row, stage, content)

	if adapter.is_draft_mode(settings):
		adapter.hold_for_approval(row, stage_number, payload)
		adapter.commit()
		return False

	return deliver(adapter, row, stage_number, content, payload, stages, destination, now)


def deliver(
	adapter: ChannelAdapter, row, stage_number: int, content, payload: dict, stages, destination: str, now
) -> bool:
	"""Claim the key, commit, then call the outside world. See the module docstring.

	Returns True only when the channel accepted the handover, which for a channel
	that sends inside the insert is the point at which the message has already
	left.
	"""
	name = adapter.row_name(row)
	key = adapter.claim_key(row, stage_number)

	try:
		claim = adapter.claim(row, stage_number, key, now)
	except AlreadyClaimed:
		# The key is already spent. Advance instead of retrying, or the row would
		# collide on this key every run for ever.
		adapter.rollback()
		advance(adapter, adapter.lock(name), stage_number, stages, now)
		adapter.commit()
		frappe.log_error(
			f"{adapter.row_label} {name} stage {stage_number} was already claimed as {key}; stage skipped.",
			f"{adapter.log_prefix}: duplicate stage claim",
		)
		return False

	# The commit boundary. After this the key is spent whatever happens next, so a
	# crash can lose a send but can never repeat one.
	adapter.commit()

	try:
		reference = adapter.send(row, content, payload, destination)
	except Exception:
		# Undo the half-written message, then record the failure durably. The
		# stage is still consumed: we cannot know whether the provider got it.
		adapter.rollback()
		adapter.record_failure(claim)
		advance(adapter, adapter.lock(name), stage_number, stages, now)
		adapter.commit()
		frappe.log_error(
			frappe.get_traceback(),
			f"{adapter.log_prefix}: {adapter.provider_label} send failed for {name}",
		)
		return False

	adapter.record_success(claim, reference)
	advance(adapter, adapter.lock(name), stage_number, stages, now, sent_at=now)
	adapter.commit()
	return True


def advance(adapter: ChannelAdapter, row, stage_number: int, stages, now, sent_at=None) -> None:
	"""Move the state machine past a stage, sent or not.

	The core decides WHAT the transition is; the adapter writes it. The last stage
	exhausts the sequence rather than looping, which is what stops a customer who
	never answers from being messaged for ever.
	"""
	if stage_number >= len(stages):
		adapter.apply_advance(row, stage_number, exhausted=True, next_due=None, sent_at=sent_at)
		return

	next_due = frappe.utils.add_to_date(now, days=stages[stage_number].silence_days)
	adapter.apply_advance(row, stage_number, exhausted=False, next_due=next_due, sent_at=sent_at)


# --- keys ------------------------------------------------------------------


def sequence_key(key: str, cycle, stage_number: int) -> str:
	"""One outbox key per row, per run of the sequence, per stage.

	The cycle is what lets a re-armed sequence send stage 1 again. Without it the
	second run would collide with the first run's key on the unique index.
	"""
	return f"{key}-cycle-{frappe.utils.cint(cycle) or 1}-stage-{stage_number}"
