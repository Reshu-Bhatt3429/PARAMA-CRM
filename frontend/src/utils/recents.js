/**
 * Recently viewed — the pure half (master spec §5, item 11).
 *
 * The list lives in localStorage. One browser holds sessions for several sites
 * and several users, so the key carries both; the same rule the grouped
 * sidebar's collapse state already follows.
 *
 * What is stored is a REFERENCE, never a title: `{doctype, name}` and nothing
 * else. Titles are fetched fresh from `crm.api.search.resolve_records` every
 * time the palette opens, which is also what drops a record the user has since
 * lost access to. A cached title would render that record's name to somebody
 * who may no longer read it.
 */

export const RECENTS_CAP = 8

export function recentsKey(site, user) {
  return `crm:recents:${site || 'unknown'}:${user || 'guest'}`
}

/** Keep only well-formed `{doctype, name}` pairs, deduplicated, capped. */
export function sanitizeRecents(list, cap = RECENTS_CAP) {
  if (!Array.isArray(list)) return []
  const seen = new Set()
  const clean = []
  for (const entry of list) {
    if (!entry || typeof entry !== 'object') continue
    const doctype = String(entry.doctype ?? '').trim()
    const name = String(entry.name ?? '').trim()
    if (!doctype || !name) continue
    const key = `${doctype}:${name}`
    if (seen.has(key)) continue
    seen.add(key)
    clean.push({ doctype, name })
    if (clean.length >= cap) break
  }
  return clean
}

/** Most recent first, one entry per record, capped. */
export function mergeRecent(list, item, cap = RECENTS_CAP) {
  const entry = sanitizeRecents([item], 1)[0]
  if (!entry) return sanitizeRecents(list, cap)
  return sanitizeRecents([entry, ...(Array.isArray(list) ? list : [])], cap)
}

export function dropRecent(list, item, cap = RECENTS_CAP) {
  const entry = sanitizeRecents([item], 1)[0]
  const clean = sanitizeRecents(list, cap)
  if (!entry) return clean
  return clean.filter(
    (row) => !(row.doctype === entry.doctype && row.name === entry.name),
  )
}
