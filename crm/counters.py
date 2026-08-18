"""Atomic counter reservations: the one way this app spends a capped budget.

Every cap in the CRM has the same failure. Two workers read "9 of 10 used", both
decide there is room, and both send the tenth message. Reading and then writing
cannot prevent that, however short the gap is, and the gap is never zero.

So a cap is spent with ONE statement:

    UPDATE ... SET count = count + 1 WHERE <there is still room>

The database decides. The winner sees one row changed, the loser sees none, and
the loser does not send. `rows_affected()` is how the caller reads that verdict.

Two callers today:

* `crm.ai.client` reserves one request from the monthly AI budget BEFORE the
  network call, so a crash mid-request over-counts by one rather than losing the
  charge -- the safe direction for a bill.
* `crm.automation_context.reserve_daily_slot` reserves one of a workflow rule's
  daily actions (Stage 5).

Nothing here commits. The reservation is part of the caller's transaction, so a
caller that rolls back releases the slot it took, and a caller that commits keeps
it. That is the correct coupling: the count means "we did the thing", and a
rolled-back thing was not done.
"""

import re

import frappe
from frappe import _

# Only a plain identifier may be interpolated into a statement. A field name
# reaches SQL as an identifier, where parameter binding cannot protect it.
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def rows_affected() -> int:
	"""How many rows the statement just executed changed.

	Named, because it is the value every reservation turns on. The driver's
	cursor is the authority; `ROW_COUNT()` is the fallback for a driver that does
	not expose one.
	"""
	cursor = getattr(frappe.db, "_cursor", None)
	count = getattr(cursor, "rowcount", None)
	if count is None or count < 0:
		count = frappe.db.sql("select row_count()")[0][0]
	return frappe.utils.cint(count)


def validated_field(doctype: str, fieldname: str) -> str:
	"""The field name, proven to exist on the doctype. Raises otherwise.

	Two checks, because one is not enough: the pattern refuses anything that is
	not a plain identifier, and the meta lookup refuses an identifier that is not
	a real field of this doctype.
	"""
	if not IDENTIFIER.match(fieldname or ""):
		frappe.throw(_("{0} is not a valid field name.").format(fieldname))

	if not frappe.get_meta(doctype).get_field(fieldname):
		frappe.throw(_("{0} has no field {1}.").format(doctype, fieldname))

	return fieldname


def reserve_daily_slot(
	doctype: str,
	name: str,
	cap: int,
	count_field: str,
	day_field: str,
	day=None,
) -> bool:
	"""Claim one of today's allowed actions on one row. True when claimed.

	The claim is one statement:

	    UPDATE ... SET count = (count + 1, or 1 on a new day), day = today
	    WHERE name = ... AND (no cap OR it is a new day OR count < cap)

	The day rollover is part of the same statement on purpose. A separate "reset
	the counter if the day changed" step is a window in which two workers each
	reset the counter to zero and each then spend a full day's cap.

	`cap <= 0` means unlimited, matching the follow-up engine's daily send cap.

	The counter and day columns belong to the caller's own doctype -- Stage 5's
	workflow rule supplies its own -- so both names are checked against that
	doctype before they reach the statement.
	"""
	count_field = validated_field(doctype, count_field)
	day_field = validated_field(doctype, day_field)
	day = frappe.utils.cstr(day or frappe.utils.nowdate())

	frappe.db.sql(
		f"""
		update `tab{doctype}`
		set `{count_field}` = case when `{day_field}` = %(day)s then `{count_field}` + 1 else 1 end,
			`{day_field}` = %(day)s
		where name = %(name)s
			and (
				%(cap)s <= 0
				or `{day_field}` is null
				or `{day_field}` != %(day)s
				or `{count_field}` < %(cap)s
			)
		""",
		{"day": day, "name": name, "cap": frappe.utils.cint(cap)},
	)

	return rows_affected() == 1
