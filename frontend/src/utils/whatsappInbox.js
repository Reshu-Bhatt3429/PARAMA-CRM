/**
 * Pure helpers for the WhatsApp team inbox (`@/pages/WhatsAppInbox.vue`).
 *
 * A conversation is one row from `crm.api.whatsapp.get_whatsapp_conversations`:
 * `{ reference_doctype, reference_name, display_name, phone, status,
 *    last_message, last_message_type, last_at, message_count, assigned_to,
 *    needs_reply, waiting_since, priority }`.
 */

/**
 * Pastel pill palettes. `surface-*-2` backgrounds with the matching dark ink
 * step: every pair clears WCAG AA in both themes (amber 6.5/8.3, green
 * 7.2/9.3, red 5.7/4.8, purple 6.6/7.1, indigo 9.3/6.6, gray 7.4/10.4).
 */
const PILL_TINTS = {
  red: 'bg-surface-red-2 text-ink-red-8',
  amber: 'bg-surface-amber-2 text-ink-amber-9',
  green: 'bg-surface-green-2 text-ink-green-9',
  purple: 'bg-surface-purple-2 text-ink-purple-8',
  indigo: 'bg-surface-blue-2 text-ink-blue-8',
  gray: 'bg-surface-gray-2 text-ink-gray-6',
}

/**
 * Stable identity of a conversation, used as a list key and to match the
 * `whatsapp_message` realtime payload against the open thread.
 */
export function conversationKey(conversation) {
  if (!conversation) return ''
  const { reference_doctype: doctype, reference_name: name } = conversation
  if (!doctype || !name) return ''
  return `${doctype}:${name}`
}

/**
 * Whether a realtime `whatsapp_message` payload belongs to a conversation.
 */
export function isSameConversation(conversation, payload) {
  const key = conversationKey(conversation)
  return Boolean(key) && key === conversationKey(payload)
}

/**
 * Up to two uppercase initials for the conversation avatar. Falls back to the
 * last two digits of the phone number, then to a neutral placeholder.
 */
export function conversationInitials(conversation) {
  const name = (conversation?.display_name || '').trim()
  const words = name.split(/\s+/).filter(Boolean)

  if (words.length >= 2) {
    return (words[0][0] + words[words.length - 1][0]).toUpperCase()
  }
  if (words.length === 1) {
    return words[0].slice(0, 2).toUpperCase()
  }

  const digits = (conversation?.phone || '').replace(/\D/g, '')
  if (digits) return digits.slice(-2)

  return '#'
}

/**
 * One-line preview for the conversation list. The backend already renders a
 * type-aware body (`📷 Image`, `📄 Document`, …); this only marks who spoke
 * last and supplies an empty-thread fallback.
 */
export function conversationPreview(conversation) {
  const message = (conversation?.last_message || '').trim()
  if (!message) return __('No messages yet')
  if (conversation?.last_message_type === 'Outgoing') {
    return __('You: {0}', [message])
  }
  return message
}

/**
 * Dot colour and tooltip for a conversation's priority. Anything the backend
 * has not taught us about reads as `cold`, so a new bucket can never render an
 * unstyled dot.
 */
export function priorityMeta(priority) {
  if (priority === 'hot') {
    return {
      priority: 'hot',
      dotClass: 'bg-red-500',
      label: __('Hot — unanswered today, or a busy week'),
    }
  }
  if (priority === 'warm') {
    return {
      priority: 'warm',
      dotClass: 'bg-amber-500',
      label: __('Warm — active in the last 3 days'),
    }
  }
  return {
    priority: 'cold',
    dotClass: 'bg-surface-gray-4',
    label: __('Cold — no recent activity'),
  }
}

/**
 * The priority pill shown on a conversation card: short label plus the tint
 * classes. The long explanation stays in `priorityMeta` and is used as the
 * pill's tooltip, so the two never disagree about what a bucket means.
 */
export function priorityPill(priority) {
  const meta = priorityMeta(priority)

  if (meta.priority === 'hot') {
    return {
      label: __('High intent'),
      class: PILL_TINTS.red,
      title: meta.label,
    }
  }
  if (meta.priority === 'warm') {
    return { label: __('Warm'), class: PILL_TINTS.amber, title: meta.label }
  }
  return { label: __('Cold'), class: PILL_TINTS.gray, title: meta.label }
}

/**
 * Tint family of every seeded CRM Lead / CRM Deal status, keyed lowercase.
 * Statuses a site added itself fall through to the neutral gray pill rather
 * than rendering untinted.
 */
const STATUS_TINTS = {
  new: 'indigo',
  open: 'indigo',
  contacted: 'amber',
  nurture: 'amber',
  negotiation: 'amber',
  demo: 'amber',
  'demo/making': 'amber',
  qualified: 'purple',
  qualification: 'indigo',
  'proposal sent': 'purple',
  'proposal/quotation': 'purple',
  ready: 'purple',
  'ready to close': 'purple',
  converted: 'green',
  booked: 'green',
  won: 'green',
  unqualified: 'red',
  junk: 'red',
  lost: 'red',
}

/**
 * Pill for a conversation's pipeline stage. Returns null when the linked
 * Lead/Deal has no status, so the caller can `v-if` on it.
 */
export function statusPill(status) {
  const label = (status || '').trim()
  if (!label) return null

  const tint = STATUS_TINTS[label.toLowerCase()] || 'gray'
  return { label: __(label), class: PILL_TINTS[tint] }
}

/**
 * Amber pill for a conversation still owed a reply, or null when nothing is
 * owed. Wraps `waitingLabel` so the card only has to ask once.
 */
export function waitingPill(conversation, now = new Date()) {
  const label = waitingLabel(conversation, now)
  if (!label) return null

  return { label, class: PILL_TINTS.amber }
}

/**
 * Join two already-formatted dates into one travel window. Formatting stays
 * with the caller (`formatDate`, which honours the site's date format); this
 * only decides how a half-open range reads.
 */
export function travelWindowLabel(startLabel, endLabel) {
  const start = (startLabel || '').trim()
  const end = (endLabel || '').trim()

  if (start && end) return start === end ? start : `${start} → ${end}`
  if (start) return __('from {0}', [start])
  if (end) return __('until {0}', [end])
  return ''
}

/**
 * `4 travellers` / `1 traveller`, or '' when the lead carries no group size.
 * Zero and unparseable values count as absent, since the field is optional.
 */
export function groupSizeLabel(size) {
  const count = Number.parseInt(size, 10)
  if (!Number.isFinite(count) || count <= 0) return ''

  return count === 1 ? __('1 traveller') : __('{0} travellers', [count])
}

/**
 * Badge text for a conversation still waiting on a reply, e.g. `waiting 3h`.
 * Empty when nothing is owed, so the caller can `v-if` on it.
 */
export function waitingLabel(conversation, now = new Date()) {
  if (!conversation?.needs_reply) return ''

  const age = humanizeAge(
    conversation.waiting_since || conversation.last_at,
    now,
  )
  return age ? __('waiting {0}', [age]) : __('needs reply')
}

/**
 * Compact age of a server timestamp: `12m`, `3h`, `5d`. Anything younger than a
 * minute reads as `1m` rather than `0m`, and an unparseable value gives ''.
 */
export function humanizeAge(timestamp, now = new Date()) {
  const started = parseServerDatetime(timestamp)
  if (!started) return ''

  const minutes = Math.max(
    1,
    Math.floor((now.getTime() - started.getTime()) / 60000),
  )
  if (minutes < 60) return `${minutes}m`

  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h`

  return `${Math.floor(hours / 24)}d`
}

/**
 * Frappe sends naive `YYYY-MM-DD HH:MM:SS` strings in the site's timezone. The
 * `T` makes the string parse the same way in every engine, still as local time.
 *
 * This mirrors `dayjsLocal` (used by `formatDate` / `prettyDate`) as long as no
 * `systemTimezone` is configured, which is the case in this app: `setConfig` is
 * only called for `resourceFetcher` in `src/main.js`. This module stays free of
 * frappe-ui imports on purpose — it is a pure helper module with a unit suite
 * (`tests/unit/whatsappInbox.test.js`) that cannot load the frappe-ui bundle.
 */
function parseServerDatetime(value) {
  if (!value) return null

  const parsed =
    value instanceof Date ? value : new Date(String(value).replace(' ', 'T'))
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

/**
 * Case-insensitive search across name, phone and last message. Phone numbers
 * are compared digit-only so `555 0100` matches `+1 555-0100`.
 */
export function filterConversations(conversations, query) {
  const list = conversations || []
  const term = (query || '').trim().toLowerCase()
  if (!term) return [...list]

  const digits = term.replace(/\D/g, '')

  return list.filter((conversation) => {
    const haystack = [conversation.display_name, conversation.last_message]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()

    if (haystack.includes(term)) return true

    if (!digits) return false
    const phoneDigits = (conversation.phone || '').replace(/\D/g, '')
    return Boolean(phoneDigits) && phoneDigits.includes(digits)
  })
}

/**
 * Split a chronologically sorted message list into day groups for the
 * WhatsApp-style date dividers: [{ label, messages }]. Today and yesterday
 * get words, everything else a readable date.
 */
export function groupMessagesByDay(messages, now = new Date()) {
  const groups = []

  for (const message of messages || []) {
    const stamp = parseServerDatetime(message.creation)
    const label = stamp ? dayLabel(stamp, now) : ''
    const current = groups[groups.length - 1]

    if (current && current.label === label) {
      current.messages.push(message)
    } else {
      groups.push({ label, messages: [message] })
    }
  }

  return groups
}

export function dayLabel(date, now = new Date()) {
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const diffDays = Math.round((today - day) / 86400000)

  if (diffDays === 0) return 'Today'
  if (diffDays === 1) return 'Yesterday'

  // A future timestamp (diffDays < 0) is not "Today": fall through to the date.
  const options = { day: 'numeric', month: 'short' }
  if (day.getFullYear() !== today.getFullYear()) options.year = 'numeric'
  return day.toLocaleDateString(undefined, options)
}
