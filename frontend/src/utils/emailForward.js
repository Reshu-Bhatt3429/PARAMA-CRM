/**
 * Forward an email (master spec item 6).
 *
 * Pure string work, kept out of the components so the suite can test it: the
 * forward's subject, the quoted original, and the marking of attachments that
 * were carried over rather than uploaded.
 */

const FORWARD_PREFIX = 'Fwd:'

/** `Fwd: <subject>`, and never `Fwd: Fwd: <subject>`. */
export function forwardSubject(subject) {
  const value = (subject || '').trim()
  if (!value) return FORWARD_PREFIX
  if (/^fwd:/i.test(value)) return value
  return `${FORWARD_PREFIX} ${value}`
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/**
 * The original message, below a divider, as one collapsed blockquote.
 *
 * The header lines are escaped because they are addresses and subjects a
 * stranger wrote. The body is not: it is the HTML we already render in the
 * timeline, and re-escaping it would forward markup instead of a message.
 */
export function forwardQuote(email, labels = {}) {
  const l = {
    forwarded: 'Forwarded message',
    from: 'From',
    date: 'Date',
    subject: 'Subject',
    to: 'To',
    ...labels,
  }

  const lines = [
    `<b>${escapeHtml(l.forwarded)}</b>`,
    `${escapeHtml(l.from)}: ${escapeHtml(email?.sender_full_name || '')} &lt;${escapeHtml(email?.sender || '')}&gt;`,
  ]
  if (email?.communication_date) {
    lines.push(`${escapeHtml(l.date)}: ${escapeHtml(email.communication_date)}`)
  }
  lines.push(`${escapeHtml(l.subject)}: ${escapeHtml(email?.subject || '')}`)
  if (email?.recipients) {
    lines.push(`${escapeHtml(l.to)}: ${escapeHtml(email.recipients)}`)
  }

  return (
    '<hr>' +
    `<p>${lines.join('<br>')}</p>` +
    `<blockquote>${email?.content || ''}</blockquote>`
  )
}

/**
 * Attachments carried over from the forwarded message.
 *
 * `forwarded: true` is load-bearing, not decoration. The composer's Discard
 * button hard-deletes the File rows it is holding, and these rows belong to the
 * ORIGINAL Communication -- deleting them would strip the attachments off an
 * email that was already sent. `keepOnDiscard` reads this flag.
 */
export function forwardedAttachments(attachments) {
  return (attachments || [])
    .filter((file) => file && file.name)
    .map((file) => ({
      name: file.name,
      file_name: file.file_name,
      file_url: file.file_url,
      forwarded: true,
    }))
}

/** The files Discard may delete: the ones this composer uploaded itself. */
export function deletableAttachments(attachments) {
  return (attachments || []).filter((file) => file && !file.forwarded)
}
