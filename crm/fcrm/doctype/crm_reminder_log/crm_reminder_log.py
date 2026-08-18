# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class CRMReminderLog(Document):
	"""Outbox record of one task reminder. The ledger only -- no feature reads it yet.

	The precedent this deliberately does NOT follow is the event-reminder path,
	which is registered on both the `all` and the `hourly` scheduler events and
	therefore fires the same reminder twice. A ledger row with a unique
	`dedup_key` makes the number of schedules it is called from irrelevant: the
	second call collides on the index instead of notifying the user again.

	`due_date` is part of the key on purpose. Moving a task's due date should
	produce a NEW reminder, and a key without the due date would treat it as a
	repeat of one already sent.
	"""

	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		channel: DF.Literal["Notification", "Email"]
		dedup_key: DF.Data
		due_date: DF.Datetime | None
		last_error: DF.SmallText | None
		offset_minutes: DF.Int
		recipient: DF.Link
		sent_at: DF.Datetime | None
		status: DF.Literal["Claimed", "Sent", "Failed", "Suppressed"]
		task: DF.Link
	# end: auto-generated types

	pass


def reminder_key(task: str, recipient: str, offset_minutes: int, due_date, channel: str) -> str:
	"""The unique key of one reminder. The only place its shape is defined.

	`due_date` is normalised to a string first: the same instant arrives as a
	`datetime` from a document and as a `str` from a query, and two spellings of
	one key would let the reminder fire twice.
	"""
	import frappe

	stamp = frappe.utils.get_datetime_str(due_date) if due_date else ""
	return f"{task}:{recipient}:{int(offset_minutes or 0)}:{stamp}:{channel}"
