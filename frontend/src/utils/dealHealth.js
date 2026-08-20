/**
 * Deal-health flags — the pure half (master spec §5, item 22; UX §2.13).
 *
 * §2.13 is why `chipLabel` exists and why nothing here returns a list of
 * badges: a record row is not a badge shelf, so three problems collapse into
 * ONE "Needs attention" chip and the detail only appears when the reader asks
 * for it.
 *
 * The server writes the column as `{"flags": ["stalled", ...]}`, or leaves it
 * empty when nothing is wrong. `parseHealthFlags` accepts every shape that can
 * arrive — the JSON string a list query returns, the object a loaded document
 * returns, an already-split array, null — because a chip that throws would take
 * the whole list row with it.
 */

/** The namespaced column the sweep writes. Must match `crm/deal_health.py`. */
export const HEALTH_FIELD = 'custom_parama_health_flags'

/** Display order, and the only names that are allowed through. */
export const HEALTH_FLAGS = ['close_date_passed', 'stalled', 'awaiting_reply']

const LABELS = {
  close_date_passed: 'Expected close date has passed',
  stalled: 'No stage change for a while',
  awaiting_reply: 'Customer is waiting for a reply',
}

export function parseHealthFlags(value) {
  if (!value) return []

  let payload = value
  if (typeof value === 'string') {
    try {
      payload = JSON.parse(value)
    } catch {
      return []
    }
  }

  const flags = Array.isArray(payload) ? payload : payload?.flags
  if (!Array.isArray(flags)) return []

  return HEALTH_FLAGS.filter((flag) => flags.includes(flag))
}

export function hasHealthFlags(value) {
  return parseHealthFlags(value).length > 0
}

export function healthFlagLabel(flag) {
  return LABELS[flag] || flag
}

/**
 * What the collapsed chip says.
 *
 * One problem names itself, because "Needs attention" where "Stalled" would do
 * costs the reader a click for nothing. Two or more collapse into the count.
 */
export function chipLabel(value) {
  const flags = parseHealthFlags(value)
  if (!flags.length) return ''
  if (flags.length === 1) return healthFlagLabel(flags[0])
  return `Needs attention (${flags.length})`
}
