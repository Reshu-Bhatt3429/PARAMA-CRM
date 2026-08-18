"""The incremental sweep pattern every nightly CRM job is required to use.

Three rules, and each of them is a real failure this replaces.

* **A watermark.** A sweep that re-reads the whole table every night gets slower
  every night and eventually stops finishing inside its window. The watermark is
  the cursor of the last row processed, so a run picks up where the previous one
  stopped -- and so a crashed run resumes instead of restarting.
* **Pagination.** Batches of a few hundred rows, committed one batch at a time.
  A single unbounded query holds one transaction across the whole table and
  turns any error into "nothing was done".
* **A per-job lock.** Two copies of the same sweep running together would each
  advance the same watermark and each skip the other's rows. The lock is
  non-blocking: the second copy exits, it does not queue up behind the first.

The cursor is `(watermark_field, name)`, not `watermark_field` alone. Timestamps
collide -- a bulk import gives thousands of rows the same `modified` -- and a
cursor that compared only the timestamp would either skip every row after the
first of a colliding group or loop on it for ever.

These sweeps are SYSTEM jobs. They read with the query builder and no permission
conditions, which is correct here and only here: no user's scope is involved,
nothing is returned to a caller, and no result reaches a request. A whitelisted
endpoint that wants this data must go through `frappe.get_list` or apply the
org-hierarchy conditions itself (master spec §3).
"""

from contextlib import contextmanager

import frappe

# Watermarks live in the framework's own key-value table rather than a new
# doctype: one string per job, no schema, and `bench migrate` never has to touch
# it. The prefix keeps them together and greppable.
WATERMARK_PREFIX = "crm_sweep_watermark:"

DEFAULT_BATCH_SIZE = 200

# A sweep that has not renewed its lock for this long is treated as dead. Long
# enough for a big batch, short enough that a crashed worker does not block
# tonight's run as well.
LOCK_TIMEOUT = 30 * 60

# The cursor halves are joined by a character that cannot occur in a datetime or
# in a document name.
CURSOR_SEPARATOR = "\x1f"


def watermark_dv_key(job_name: str) -> str:
	return f"{WATERMARK_PREFIX}{job_name}"


def get_watermark(job_name: str) -> tuple[str, str]:
	"""The cursor the last run stopped at, as (value, name). Empty when new."""
	raw = frappe.db.get_default(watermark_dv_key(job_name)) or ""
	if not raw:
		return "", ""

	value, _, name = raw.partition(CURSOR_SEPARATOR)
	return value, name


def set_watermark(job_name: str, value, name: str) -> None:
	frappe.db.set_default(
		watermark_dv_key(job_name),
		f"{frappe.utils.cstr(value)}{CURSOR_SEPARATOR}{frappe.utils.cstr(name)}",
	)


def reset_watermark(job_name: str) -> None:
	"""Forget the cursor so the next run starts from the beginning."""
	frappe.db.set_default(watermark_dv_key(job_name), "")


@contextmanager
def sweep_lock(job_name: str, timeout: int = LOCK_TIMEOUT):
	"""Hold the exclusive right to run one sweep. Yields True or False.

	Non-blocking on purpose. The caller of a nightly job that is already running
	should return immediately, not wait half an hour to do the work twice.
	"""
	lock = frappe.cache.lock(f"crm:sweep:{job_name}", timeout=timeout, blocking_timeout=0)
	acquired = False
	try:
		acquired = lock.acquire(blocking=False)
		yield acquired
	finally:
		if acquired:
			try:
				lock.release()
			except Exception:
				# The timeout expired and the lock is already gone. Nothing to
				# release, and nothing worth failing the sweep over.
				pass


def read_batch(
	doctype: str,
	fields: list[str],
	cursor: tuple[str, str],
	limit: int,
	watermark_field: str = "modified",
	extra_conditions=None,
) -> list[frappe._dict]:
	"""One page of rows strictly after `cursor`, ordered by the cursor.

	`(watermark_field, name) > (value, name)` is expressed as
	`f > v OR (f = v AND name > n)`, which is what makes the paging total: every
	row is visited exactly once even when a thousand of them share a timestamp.
	"""
	table = frappe.qb.DocType(doctype)
	selected = {"name", watermark_field, *fields}

	query = frappe.qb.from_(table).select(*[table[field] for field in sorted(selected)])

	value, last_name = cursor
	if value:
		query = query.where(
			(table[watermark_field] > value) | ((table[watermark_field] == value) & (table.name > last_name))
		)

	if extra_conditions is not None:
		query = query.where(extra_conditions(table))

	query = query.orderby(table[watermark_field]).orderby(table.name).limit(limit)
	return query.run(as_dict=True)


def run_sweep(
	job_name: str,
	doctype: str,
	handler,
	fields: list[str] | None = None,
	batch_size: int = DEFAULT_BATCH_SIZE,
	max_batches: int | None = None,
	watermark_field: str = "modified",
	extra_conditions=None,
	commit_between_batches: bool = True,
) -> dict:
	"""Walk a doctype from the stored cursor, handing `handler` one row at a time.

	`handler(row)` returns True when it changed something; the count of those is
	reported as `changed`. It must not commit: the sweep owns the commit points.

	Returns a dict with `read`, `changed`, `batches`, `locked` and `finished`.
	`finished` is False when `max_batches` cut the run short, which is the signal
	to the caller that there is more to do on the next tick.

	Running it twice produces the same result as running it once: the cursor only
	moves forward over rows already handled, and every handler this pattern is
	built for writes a value derived from the row rather than an increment.
	"""
	fields = list(fields or [])
	stats = {"read": 0, "changed": 0, "batches": 0, "locked": False, "finished": False}

	with sweep_lock(job_name) as acquired:
		if not acquired:
			# Another copy is mid-sweep. Both advancing one cursor would make each
			# of them skip the other's rows.
			return stats

		stats["locked"] = True
		cursor = get_watermark(job_name)

		while max_batches is None or stats["batches"] < max_batches:
			rows = read_batch(
				doctype,
				fields,
				cursor,
				batch_size,
				watermark_field=watermark_field,
				extra_conditions=extra_conditions,
			)
			if not rows:
				stats["finished"] = True
				break

			for row in rows:
				stats["read"] += 1
				if handler(row):
					stats["changed"] += 1

			last = rows[-1]
			cursor = (frappe.utils.cstr(last.get(watermark_field)), last.get("name"))
			set_watermark(job_name, cursor[0], cursor[1])
			stats["batches"] += 1

			if commit_between_batches:
				# The batch is durable before the next one starts, so a crash costs
				# one batch rather than the whole run.
				frappe.db.commit()

			if len(rows) < batch_size:
				stats["finished"] = True
				break

	return stats
