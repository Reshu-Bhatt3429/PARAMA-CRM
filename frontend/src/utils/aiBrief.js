/**
 * The Brief card's pure logic (master spec items 13 + 28 + 15, merged).
 *
 * Everything here is string and date work with no Vue and no network, so the
 * suite can test the parts that are easy to get quietly wrong: the due-date a
 * hint turns into, the Note a brief is saved as, and the escaping of text a
 * model wrote.
 */

const TONES = ['positive', 'neutral', 'negative', 'frustrated']

/**
 * How many days ahead each hint means.
 *
 * The model answers with a hint, not a date, on purpose: it does not know
 * today's date, the agency's calendar or the agent's workload, and a date it
 * invents reads as a commitment. These offsets turn the hint into a starting
 * point the agent then edits in the task modal, which is where the task is
 * actually created (C6 -- nothing is created without that click).
 */
const DUE_OFFSET_DAYS = {
  today: 0,
  tomorrow: 1,
  this_week: 3,
  next_week: 7,
}

/** `YYYY-MM-DD` for the local day `offset` days after `from`. */
function isoDay(from, offset) {
  const date = new Date(from.getFullYear(), from.getMonth(), from.getDate())
  date.setDate(date.getDate() + offset)
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
}

/**
 * The due date a next-step hint suggests, or null when there is no usable hint.
 *
 * Local dates throughout: a task due "tomorrow" means the agent's tomorrow, and
 * going through UTC would move it a day for anyone east of London.
 */
export function dueDateFromHint(hint, now = new Date()) {
  const offset = DUE_OFFSET_DAYS[hint]
  if (offset === undefined) return null
  return `${isoDay(now, offset)} 09:00:00`
}

/** A tone the card is willing to show, or null. Anything else is dropped. */
export function normalizeTone(tone) {
  return TONES.includes(tone) ? tone : null
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

/**
 * The brief as the HTML body of an FCRM Note.
 *
 * Every fragment is escaped. The text came from a model, by way of a customer's
 * own words, and a note is rendered as HTML in the timeline -- this is the one
 * place the two facts meet.
 */
export function briefToNoteHtml(brief, labels = {}) {
  const l = {
    nextStep: 'Suggested next step',
    tone: 'Tone',
    ...labels,
  }

  const bullets = (brief?.bullets || [])
    .map((bullet) => `<li>${escapeHtml(bullet)}</li>`)
    .join('')

  const parts = [`<ul>${bullets}</ul>`]

  if (brief?.next_step?.description) {
    parts.push(
      `<p><b>${escapeHtml(l.nextStep)}:</b> ${escapeHtml(brief.next_step.description)}</p>`,
    )
  }

  const tone = normalizeTone(brief?.tone)
  if (tone) {
    parts.push(`<p><b>${escapeHtml(l.tone)}:</b> ${escapeHtml(tone)}</p>`)
  }

  return parts.join('')
}

/** The Note title a saved brief gets. Dated, so two saved briefs are tellable apart. */
export function briefNoteTitle(generatedAt, prefix = 'AI Brief') {
  const date = generatedAt
    ? new Date(generatedAt.replace(' ', 'T'))
    : new Date()
  if (Number.isNaN(date.getTime())) return prefix
  return `${prefix} — ${isoDay(date, 0)}`
}

/**
 * Briefs held for this browser session only, keyed by record.
 *
 * Session-local is the default the spec asks for: a brief is a reading aid, not
 * a record. It survives a tab switch inside the record and dies with the page,
 * and an agent who wants it kept presses "Save as note".
 */
const cache = new Map()

export function cacheKey(doctype, name) {
  return `${doctype}:${name}`
}

export function rememberBrief(doctype, name, brief) {
  cache.set(cacheKey(doctype, name), brief)
}

export function recallBrief(doctype, name) {
  return cache.get(cacheKey(doctype, name)) || null
}

export function forgetBrief(doctype, name) {
  cache.delete(cacheKey(doctype, name))
}

/** Test seam. Nothing in the app calls this. */
export function clearBriefCache() {
  cache.clear()
}
