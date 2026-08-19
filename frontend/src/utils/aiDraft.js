/**
 * The email composer's AI draft (master spec item 14), pure parts only.
 *
 * The server returns PLAIN TEXT, deliberately: the editor is a rich-text editor
 * and handing it model-written HTML would mean trusting a model's markup inside
 * a message an agent is about to send. This module turns that text into the
 * paragraphs the editor gets, with every character escaped on the way.
 */

/** The three chips in the popover. Each one is a starting instruction, still editable. */
export const DRAFT_PRESETS = [
  {
    key: 'follow_up',
    label: 'Follow up',
    instruction: 'Write a short, friendly follow-up.',
  },
  {
    key: 'answer',
    label: 'Answer their question',
    instruction: 'Answer the question in their last message.',
  },
  {
    key: 'pricing',
    label: 'Send pricing info',
    instruction: 'Send the pricing we have discussed, with no new numbers.',
  },
]

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/**
 * Plain-text body -> the HTML fragment inserted at the caret.
 *
 * A blank line starts a paragraph; a single newline inside one is a `<br>`.
 * That is the only structure plain text has, and reproducing it is the
 * difference between a drafted email and one long run-on paragraph.
 */
export function bodyToHtml(body) {
  const text = String(body ?? '')
    .replace(/\r\n/g, '\n')
    .trim()
  if (!text) return ''

  return text
    .split(/\n{2,}/)
    .map((paragraph) =>
      paragraph
        .split('\n')
        .map((line) => escapeHtml(line.trim()))
        .filter(Boolean)
        .join('<br>'),
    )
    .filter(Boolean)
    .map((paragraph) => `<p>${paragraph}</p>`)
    .join('')
}

/**
 * The disclosure line under the prompt input.
 *
 * It names the fields, because "some lead data" is not a disclosure. The labels
 * come from the server (`crm.api.ai_draft.sent_fields`), which reads the same
 * whitelist the prompt builder reads, so this line cannot drift away from what
 * is actually sent.
 */
export function disclosureLine(fields, labels = {}) {
  const l = {
    prefix: 'Sends to the AI provider',
    andMessages: 'and the last 10 emails on this record',
    nothing: 'Sends the last 10 emails on this record to the AI provider',
    ...labels,
  }

  const named = (fields || []).filter(Boolean)
  if (!named.length) return `${l.nothing}.`

  return `${l.prefix}: ${named.join(', ')}, ${l.andMessages}.`
}
