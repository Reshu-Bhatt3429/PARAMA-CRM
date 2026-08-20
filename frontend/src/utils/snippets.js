/**
 * Snippet composer helpers (master spec item 23).
 *
 * The popover trigger, the search and the HTML-to-text conversion live here as
 * pure functions so they can be unit-tested; the components hold only the
 * wiring. Same reason as `src/utils/whatsappInbox.js`: the vitest suite in this
 * repo cannot mount components (no `@vue/test-utils`), so anything worth
 * testing has to be a function over plain values.
 *
 * The merge itself is NOT here. Tokens are resolved on the server, by
 * `crm.api.snippets.render`, against a record the caller has been
 * permission-checked against. A client-side merge would have to be handed the
 * record's fields, and the composer only ever holds the fields it happens to
 * have loaded.
 */

/**
 * Rank and filter snippets for the popover.
 *
 * A shortcut prefix match sorts above a title match, because the shortcut is
 * what the user is typing. Everything is case-insensitive.
 */
export function filterSnippets(snippets, query) {
  const list = snippets || []
  const term = (query || '').trim().toLowerCase()
  if (!term) return [...list]

  const scored = []
  for (const snippet of list) {
    const shortcut = (snippet.shortcut || '').toLowerCase()
    const title = (snippet.title || '').toLowerCase()

    let rank = -1
    if (shortcut.startsWith(term)) rank = 0
    else if (shortcut.includes(term)) rank = 1
    else if (title.includes(term)) rank = 2

    if (rank >= 0) scored.push({ snippet, rank })
  }

  return scored
    .map((entry, index) => ({ ...entry, index }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((entry) => entry.snippet)
}

/**
 * Is the caret inside a `/shortcut` the user is typing at the start of a line?
 *
 * Returns `{ active, query, from, to }`. `from`..`to` is the slice the chosen
 * snippet replaces -- the slash included, so nothing is left behind.
 *
 * Line start, not anywhere: a URL or a date typed mid-sentence contains
 * slashes, and a popover that opened on "24/7" would be an obstacle rather
 * than a feature.
 */
export function slashTrigger(text, caret) {
  const value = text || ''
  const position = Math.max(0, Math.min(caret ?? value.length, value.length))

  const lineStart = value.lastIndexOf('\n', position - 1) + 1
  if (value[lineStart] !== '/')
    return { active: false, query: '', from: 0, to: 0 }
  if (position <= lineStart) return { active: false, query: '', from: 0, to: 0 }

  const query = value.slice(lineStart + 1, position)
  // A space ends the attempt. The user is writing a sentence, not a shortcut.
  if (/\s/.test(query)) return { active: false, query: '', from: 0, to: 0 }

  return { active: true, query, from: lineStart, to: position }
}

/**
 * Replace the trigger slice with the snippet text.
 * Returns `{ text, caret }` -- the caret lands after what was inserted.
 */
export function applySnippet(text, trigger, body) {
  const value = text || ''
  const insert = body || ''
  const from = trigger?.from ?? value.length
  const to = trigger?.to ?? value.length

  return {
    text: value.slice(0, from) + insert + value.slice(to),
    caret: from + insert.length,
  }
}

const ENTITIES = {
  '&nbsp;': ' ',
  '&amp;': '&',
  '&lt;': '<',
  '&gt;': '>',
  '&quot;': '"',
  '&#39;': "'",
}

/**
 * A snippet body is HTML (it is authored in a rich-text field). A WhatsApp
 * message is plain text. Convert without a DOM so the helper stays testable
 * and so nothing in a snippet body can execute on its way through.
 */
export function htmlToText(html) {
  if (!html) return ''

  let text = String(html)
    .replace(/<\s*br\s*\/?\s*>/gi, '\n')
    .replace(/<\s*\/\s*(p|div|li|h[1-6])\s*>/gi, '\n')
    .replace(/<\s*li[^>]*>/gi, '- ')
    .replace(/<[^>]*>/g, '')

  for (const [entity, character] of Object.entries(ENTITIES)) {
    text = text.split(entity).join(character)
  }

  return text.replace(/\n{3,}/g, '\n\n').trim()
}
