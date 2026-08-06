/**
 * Pure helpers for the WhatsApp team inbox (`@/pages/WhatsAppInbox.vue`).
 *
 * A conversation is one row from `crm.api.whatsapp.get_whatsapp_conversations`:
 * `{ reference_doctype, reference_name, display_name, phone, last_message,
 *    last_message_type, last_at, message_count, assigned_to, needs_reply,
 *    waiting_since, priority }`.
 */

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
