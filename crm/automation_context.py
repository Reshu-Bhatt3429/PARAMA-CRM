"""The five guards every automation rule needs, and no rule engine yet.

Stage 5 adds workflow rules: "when a deal reaches Won, send this template and
create that task". Rules are the feature in this app most able to do damage
without anyone touching a button, and each of the five helpers below exists
because of one specific way a rule engine hurts a customer.

* **A depth ceiling.** A rule that writes a field can trigger a rule that writes
  the field back. Synchronous nesting is how one save becomes a hundred, and the
  ceiling is what stops it. `execution_depth` restores the previous depth in
  `finally`, so a rule that throws does not leave the counter raised and every
  later rule refused.
* **Real transition detection.** `doc.get_doc_before_save()` is the only honest
  answer to "did this field actually change?". A rule that fires on the current
  value alone fires again on every later save of the same document -- the
  customer gets the same "your deal is won" email once a day for a week.
* **A durable execution key.** Two workers, a retry, and a scheduler that runs
  the same rule twice all need one answer to "has this already happened?". The
  key is (rule, document, source version or event, action), and the doctype that
  stores it in Stage 5 carries the unique index that makes it a guard rather
  than a log.
* **Side effects after commit.** A job enqueued inside the transaction can be
  picked up before the row it describes is visible, or after the transaction
  rolls back -- and then it sends mail about a state that never existed.
* **An atomic daily cap.** Read-then-write lets two workers both read 9 of 10
  and both send. One `UPDATE ... WHERE` under the cap cannot.

Nothing here is wired to anything. There is no rule doctype and no consumer in
this stage; this is the library Stage 5 builds on, tested on its own.
"""

import hashlib
import inspect
from contextlib import contextmanager

import frappe
from frappe import _

from crm.counters import reserve_daily_slot

# Re-exported: the daily cap a workflow rule spends is the same atomic
# reservation the AI budget uses, so there is one implementation and one test of
# it. `crm.counters` explains why a read-then-write cannot do this job.
__all__ = [
	"DepthExceeded",
	"KwargCollision",
	"current_depth",
	"enqueue_parameters",
	"execution_depth",
	"execution_key",
	"has_changed",
	"is_transition",
	"previous_value",
	"queue_after_commit",
	"reserve_daily_slot",
]

# Where the synchronous nesting depth is kept. `frappe.local` is per request and
# per background job, so one worker's depth can never be read by another, and a
# finished request cannot leak its depth into the next one.
DEPTH_ATTRIBUTE = "crm_automation_depth"

# Three levels of rule-triggers-rule is already more than any travel agency
# workflow needs, and it is shallow enough that a runaway chain is stopped while
# it is still cheap.
DEFAULT_DEPTH_LIMIT = 3

# A unique index on a Data column is 140 characters in Frappe. A key longer than
# that is replaced by a digest of itself rather than truncated: truncation makes
# two different executions collide, which would silently drop the second one.
MAX_KEY_LENGTH = 140
KEY_SEPARATOR = "\x1f"


class DepthExceeded(frappe.ValidationError):
	"""The synchronous nesting ceiling was reached. The chain stops here."""


# --- depth ceiling ---------------------------------------------------------


def current_depth() -> int:
	"""How many automation actions are already running above this one."""
	return frappe.utils.cint(getattr(frappe.local, DEPTH_ATTRIBUTE, 0))


@contextmanager
def execution_depth(limit: int = DEFAULT_DEPTH_LIMIT):
	"""Run one automation action one level deeper. Raises at the ceiling.

	The previous depth is restored in `finally`, not decremented, so an action
	that raises cannot leave the counter above where it started. A decrement
	would drift on any path that skipped it, and a drifted counter refuses work
	that is perfectly safe.
	"""
	depth = current_depth()
	if depth >= limit:
		raise DepthExceeded(
			_("This automation is nested more than {0} levels deep and was stopped.").format(limit)
		)

	setattr(frappe.local, DEPTH_ATTRIBUTE, depth + 1)
	try:
		yield depth + 1
	finally:
		setattr(frappe.local, DEPTH_ATTRIBUTE, depth)


# --- real transition detection ---------------------------------------------


def previous_value(doc, fieldname: str):
	"""What the field held before this save, or None on an insert."""
	before = doc.get_doc_before_save()
	return before.get(fieldname) if before else None


def has_changed(doc, fieldname: str) -> bool:
	"""True when THIS save changed the field.

	An insert counts as a change: the field went from not existing to holding a
	value, and a rule that watches for "status becomes Won" must fire on a deal
	that is created already won.
	"""
	before = doc.get_doc_before_save()
	if before is None:
		return True
	return before.get(fieldname) != doc.get(fieldname)


def is_transition(doc, fieldname: str, to_value, from_value=None) -> bool:
	"""True only when this save moved the field INTO `to_value`.

	This is the check that separates "the deal is won" from "the deal became
	won". Without it a rule fires again on every later save of a won deal, which
	is how a customer receives the same congratulation email every day.

	`from_value` narrows it further, for a rule that only applies to one
	particular move.
	"""
	if doc.get(fieldname) != to_value:
		return False

	if not has_changed(doc, fieldname):
		return False

	if from_value is not None and previous_value(doc, fieldname) != from_value:
		return False

	return True


# --- durable execution key -------------------------------------------------


def execution_key(rule: str, doctype: str, name: str, source: str, action: str) -> str:
	"""The identity of one rule action on one document version.

	`source` is what makes a repeat different from a retry: the document's
	`modified` timestamp, its version name, or the event that triggered the rule.
	Two workers that pick up the same event produce the same key and the second
	one loses on the unique index; the same rule on a LATER version of the same
	document produces a different key and is allowed to run.

	The result is stable across processes and Python versions -- it is built from
	the values, never from `hash()`.
	"""
	parts = [frappe.utils.cstr(part) for part in (rule, doctype, name, source, action)]
	readable = KEY_SEPARATOR.join(parts)

	if len(readable) <= MAX_KEY_LENGTH:
		return readable

	# Too long for the unique index. A digest of the SAME string keeps the
	# one-key-per-execution property; cutting the string short would not.
	return "sha256:" + hashlib.sha256(readable.encode("utf-8")).hexdigest()


# --- side effects after commit ---------------------------------------------


class KwargCollision(frappe.ValidationError):
	"""A job argument whose name `frappe.enqueue` would eat. Raised, never sent."""


def enqueue_parameters() -> frozenset[str]:
	"""Every parameter name `frappe.enqueue` consumes for itself.

	Read from the live signature rather than hard-coded, so an upgrade that adds
	a parameter to `frappe.enqueue` widens the guard automatically instead of
	silently re-opening the hole. `**kwargs` is excluded: that is the pass-through
	bucket, and it is the only name a job argument is allowed to land in.

	NOT read off `frappe.enqueue` itself: on this framework build that attribute
	is a lazy re-export whose signature is `(*args, **kwargs)`, which would make
	this guard an empty set that refuses nothing. The implementation lives in
	`frappe.utils.background_jobs.enqueue`, and `frappe.enqueue` hands every call
	to it, so its parameter names are the ones that eat job arguments. `unwrap`
	then sees through any decorator either function may gain later.
	"""
	from frappe.utils.background_jobs import enqueue as real_enqueue

	return frozenset(inspect.signature(inspect.unwrap(real_enqueue)).parameters) - {"kwargs"}


def queue_after_commit(method: str, queue: str = "short", /, **kwargs) -> None:
	"""Enqueue an automation side effect for AFTER this transaction commits.

	`enqueue_after_commit` is the whole point. A job enqueued inside the
	transaction races the transaction: a free worker can start it before the rows
	it reads are visible, and a transaction that later rolls back leaves a worker
	acting on a state that never existed. Both failures send a real message to a
	real customer.

	Why `method` and `queue` are positional-ONLY
	--------------------------------------------
	Everything after them is a JOB argument, forwarded to the worker. While those
	two were ordinary keyword parameters this helper had a trap of its own: a
	caller who meant `queue="Sales"` as a job argument silently set the RQ queue
	instead, and the worker was then called without the argument it required.
	Positional-only removes that shadowing entirely -- this function contributes
	no reserved names of its own, and a caller who writes `queue=` means the job
	argument and is told so by the check below.

	Why a collision RAISES
	----------------------
	`frappe.enqueue` consumes `event`, `timeout`, `job_name`, `now`, `at_front`
	and the rest before `**kwargs` sees them. A job argument named after one of
	them is swallowed at the enqueue and the worker then raises `TypeError:
	missing 1 required positional argument` into a log nobody reads -- an
	automation that quietly never fires. That happened once on a live site with
	`event` (see `crm.workflows.JOB_KWARGS` and
	`test_no_job_argument_collides_with_enqueue`), and the fix there was local to
	one caller. This is the same check, once, for every caller: a name that would
	be eaten fails LOUDLY at the enqueue instead of vanishing.

	The raise happens before `frappe.enqueue` is reached, so a refused call
	enqueues nothing at all.
	"""
	collisions = sorted(enqueue_parameters() & set(kwargs))
	if collisions:
		raise KwargCollision(
			_(
				"These job arguments would be consumed by the enqueue and never reach "
				"the worker: {0}. Rename them."
			).format(", ".join(collisions))
		)

	frappe.enqueue(method, queue=queue, enqueue_after_commit=True, **kwargs)


# --- atomic daily cap ------------------------------------------------------
# `reserve_daily_slot` is imported from `crm.counters` at the top of this module
# and re-exported here, because a rule's daily cap and the AI client's monthly
# budget are the same problem: two workers, one last slot, and no read-then-write
# that can tell them apart.
