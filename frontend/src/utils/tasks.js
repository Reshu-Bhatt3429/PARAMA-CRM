/**
 * Task due-date presentation (master spec item 1).
 *
 * The chip IS the reminder. A task row shows its due date in one of three
 * tones -- gray for later, amber for today, red for past -- and that is the
 * whole passive half of the reminder feature. No extra icon, no badge shelf
 * (spec §2.13).
 *
 * This module stays free of frappe-ui imports on purpose: it is a pure helper
 * with a unit suite (`tests/unit/taskDueDate.test.js`) that cannot load the
 * frappe-ui bundle. The datetime parsing mirrors
 * `src/utils/whatsappInbox.js::parseServerDatetime` for the same reason.
 */

/** Statuses that end a task. A finished task's due date is history, not a due date. */
export const CLOSED_TASK_STATUSES = ['Done', 'Canceled', 'Cancelled']

/**
 * Frappe sends naive `YYYY-MM-DD HH:MM:SS` strings in the site's timezone. The
 * `T` makes the string parse the same way in every engine, still as local time.
 */
function parseServerDatetime(value) {
  if (!value) return null

  const parsed =
    value instanceof Date ? value : new Date(String(value).replace(' ', 'T'))
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

function sameCalendarDay(a, b) {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  )
}

/**
 * One of `'none' | 'future' | 'today' | 'overdue'`.
 *
 * `'today'` beats `'overdue'` for a time earlier today on purpose: a task due
 * at 09:00 that you are looking at over lunch is today's work, and painting it
 * red alongside last month's genuinely missed work would make red mean nothing.
 * Overdue starts at midnight.
 */
export function taskDueState(dueDate, status, now = new Date()) {
  if (!dueDate) return 'none'
  if (CLOSED_TASK_STATUSES.includes(status)) return 'none'

  const due = parseServerDatetime(dueDate)
  if (!due) return 'none'

  if (sameCalendarDay(due, now)) return 'today'
  return due.getTime() < now.getTime() ? 'overdue' : 'future'
}

/** Tailwind text colour for a due state, in the app's ink scale. */
export function taskDueClass(state) {
  if (state === 'overdue') return 'text-ink-red-3'
  if (state === 'today') return 'text-ink-amber-3'
  return 'text-ink-gray-7'
}

/**
 * The tooltip suffix that says WHY the chip is coloured. Returned untranslated;
 * the caller wraps it in `__()` so the strings are extracted from the component.
 */
export function taskDueLabel(state) {
  if (state === 'overdue') return 'Overdue'
  if (state === 'today') return 'Due today'
  return ''
}
