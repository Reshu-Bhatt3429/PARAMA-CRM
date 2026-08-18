/**
 * Tag chips — the parts that are pure functions (master spec §5, item 2).
 *
 * The component that renders chips owns the network and the DOM; everything
 * that decides WHAT to render lives here so it can be tested without mounting
 * anything.
 */

/**
 * Eight pastels, fixed and ordered. A chip's colour is derived from its name,
 * so the same tag is the same colour on every record and on every machine
 * without storing a colour anywhere.
 *
 * Written as whole class strings on purpose: Tailwind scans this file, and a
 * class assembled at runtime (`bg-${hue}-100`) is never generated.
 */
export const TAG_COLORS = [
  { bg: 'bg-rose-100', text: 'text-rose-800', ring: 'ring-rose-200' },
  { bg: 'bg-amber-100', text: 'text-amber-800', ring: 'ring-amber-200' },
  { bg: 'bg-lime-100', text: 'text-lime-800', ring: 'ring-lime-200' },
  { bg: 'bg-emerald-100', text: 'text-emerald-800', ring: 'ring-emerald-200' },
  { bg: 'bg-teal-100', text: 'text-teal-800', ring: 'ring-teal-200' },
  { bg: 'bg-sky-100', text: 'text-sky-800', ring: 'ring-sky-200' },
  { bg: 'bg-indigo-100', text: 'text-indigo-800', ring: 'ring-indigo-200' },
  { bg: 'bg-fuchsia-100', text: 'text-fuchsia-800', ring: 'ring-fuchsia-200' },
]

/**
 * A stable index into TAG_COLORS for a tag name.
 *
 * Case-folded first: "VIP" and "vip" are the same tag to the server's remover,
 * so they must not be two different colours here.
 */
export function tagColorIndex(name) {
  const text = String(name ?? '').toLowerCase()
  let hash = 0
  for (let i = 0; i < text.length; i++) {
    // djb2, kept in 32-bit range so the result is identical everywhere.
    hash = (hash * 33 + text.charCodeAt(i)) | 0
  }
  return Math.abs(hash) % TAG_COLORS.length
}

export function tagColor(name) {
  return TAG_COLORS[tagColorIndex(name)]
}

export function tagChipClass(name) {
  const color = tagColor(name)
  return `${color.bg} ${color.text} ${color.ring}`
}

/** `_user_tags` is stored as ",a,b". This is the only place that knows that. */
export function splitTags(value) {
  if (Array.isArray(value)) {
    return value.map((tag) => String(tag).trim()).filter(Boolean)
  }
  return String(value ?? '')
    .split(',')
    .map((tag) => tag.trim())
    .filter(Boolean)
}

/**
 * §2.13: a record header is not a badge shelf. At most `max` chips, and the
 * rest collapse into one "+N".
 */
export function visibleTags(tags, max = 2) {
  const all = splitTags(tags)
  if (all.length <= max) {
    return { shown: all, hidden: [], overflow: 0 }
  }
  return {
    shown: all.slice(0, max),
    hidden: all.slice(max),
    overflow: all.length - max,
  }
}

export function hasTag(tags, tag) {
  const wanted = String(tag ?? '')
    .trim()
    .toLowerCase()
  if (!wanted) return false
  return splitTags(tags).some((existing) => existing.toLowerCase() === wanted)
}

/**
 * Fuzzy score for one candidate, or `null` when it does not match at all.
 * Higher is better.
 *
 * Three tiers, because a picker that ranks "Bali" below "Brazil Adventure
 * Later" for the query "bal" is worse than no ranking:
 *   3000+ prefix match
 *   2000+ substring match
 *   1000+ subsequence match, penalised by how spread out the letters are
 * Shorter candidates win inside a tier, so the exact tag floats to the top.
 */
export function fuzzyScore(query, candidate) {
  const needle = String(query ?? '')
    .trim()
    .toLowerCase()
  const haystack = String(candidate ?? '').toLowerCase()

  if (!needle) return 1000 - haystack.length
  if (!haystack) return null

  if (haystack.startsWith(needle)) return 3000 - haystack.length
  const at = haystack.indexOf(needle)
  if (at >= 0) return 2000 - at - haystack.length

  let cursor = 0
  let spread = 0
  let previous = -1
  for (const character of needle) {
    const found = haystack.indexOf(character, cursor)
    if (found === -1) return null
    if (previous >= 0) spread += found - previous - 1
    previous = found
    cursor = found + 1
  }
  return 1000 - spread - haystack.length
}

/**
 * The picker's option list: every known tag that matches, best first, with the
 * ones already on the record removed.
 */
export function rankTags(tags, query, { exclude = [] } = {}) {
  const taken = new Set(splitTags(exclude).map((tag) => tag.toLowerCase()))
  return splitTags(tags)
    .filter((tag) => !taken.has(tag.toLowerCase()))
    .map((tag) => ({ tag, score: fuzzyScore(query, tag) }))
    .filter((row) => row.score !== null)
    .sort((a, b) => b.score - a.score || a.tag.localeCompare(b.tag))
    .map((row) => row.tag)
}

/**
 * Whether the picker should offer "Create '<query>'".
 *
 * Not offered for a name that already exists in any casing — the server treats
 * those as the same tag, so the row would be a no-op that looks like a change.
 */
export function canCreateTag(query, { known = [], onRecord = [] } = {}) {
  const wanted = String(query ?? '').trim()
  if (!wanted) return false
  if (wanted.includes(',')) return false
  return !hasTag(known, wanted) && !hasTag(onRecord, wanted)
}
