/**
 * The Today page — the parts that are pure functions (master spec §5, item 24).
 *
 * UX §2.17: ONE prioritised list with filter chips, not four stacked panels.
 * The server already merged and sorted the list, so what is left here is the
 * chip filter, the keyboard cursor, and turning a row into a route and a verb.
 *
 * The component owns the network, the focus and the key listener. Everything
 * with an edge case lives here, because there are no component tests in this
 * repo and a decision that is not a function is a decision that is not tested.
 */

import { RECORD_ROUTES } from '@/utils/palette'

/** Chip order. `all` first, then the four sources in priority order. */
export const TODAY_FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'task', label: 'Tasks' },
  { key: 'reply', label: 'Replies' },
  { key: 'deal', label: 'Deals' },
  { key: 'approval', label: 'Approvals' },
]

const TYPE_ICONS = {
  task: 'lucide-square-check-big',
  reply: 'lucide-message-circle',
  deal: 'lucide-handshake',
  approval: 'lucide-badge-check',
}

const ACTION_LABELS = {
  open: 'Open',
  approve: 'Approve',
  reply: 'Reply',
}

export function typeIcon(type) {
  return TYPE_ICONS[type] || 'lucide-circle'
}

export function actionLabel(item) {
  return ACTION_LABELS[item?.action] || ACTION_LABELS.open
}

export function filterItems(items, filter) {
  const rows = Array.isArray(items) ? items : []
  if (!filter || filter === 'all') return rows
  return rows.filter((item) => item.type === filter)
}

/** Chip counts, straight from the server's `counts` with a zero fallback. */
export function chipCounts(counts) {
  const source = counts || {}
  return TODAY_FILTERS.map((chip) => ({
    ...chip,
    count: Number(source[chip.key]) || 0,
  }))
}

/**
 * Where a row goes.
 *
 * A reply and a flagged deal open their own record. A task and an approval have
 * no record page in this app, so they open the parent they belong to — the same
 * limitation `@/utils/palette` documents, resolved the same way. `reference_*`
 * is the parent; `doctype`/`name` is the row itself.
 */
export function itemRoute(item) {
  if (!item) return null

  const direct = RECORD_ROUTES[item.doctype]
  if (direct && item.name) {
    return { name: direct.name, params: { [direct.param]: item.name } }
  }

  const parent = RECORD_ROUTES[item.reference_doctype]
  if (parent && item.reference_name) {
    return {
      name: parent.name,
      params: { [parent.param]: item.reference_name },
    }
  }

  if (item.type === 'task') return { name: 'Tasks' }
  return null
}

/**
 * A reply always opens the WhatsApp inbox rather than the record, because that
 * is where the composer is. The record page has no reply box for a thread.
 */
export function replyRoute(item) {
  if (!item) return null
  return {
    name: 'WhatsApp',
    query: { doctype: item.reference_doctype, name: item.reference_name },
  }
}

/** Arrow / j / k movement. Wraps at both ends, and survives an empty list. */
export function moveCursor(current, delta, length) {
  if (!length) return -1
  if (current < 0) return delta > 0 ? 0 : length - 1
  return (current + delta + length) % length
}

/** Drop one acted-on row without a round trip, and fix the counts to match. */
export function removeItem(payload, key) {
  const items = (payload?.items || []).filter((item) => item.key !== key)
  const counts = { all: items.length }
  for (const chip of TODAY_FILTERS) {
    if (chip.key === 'all') continue
    counts[chip.key] = items.filter((item) => item.type === chip.key).length
  }
  return { ...payload, items, counts }
}
