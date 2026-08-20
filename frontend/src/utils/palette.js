/**
 * Command palette — the parts that are pure functions (master spec §5, items
 * 10 and 11).
 *
 * The component owns the network, the focus and the keyboard listener.
 * Everything that decides what a row IS, where it goes and which one is
 * highlighted lives here, because that is the part with edge cases.
 */

/**
 * Where a search hit opens.
 *
 * CRM Task and FCRM Note have no record route in this app: both are edited in a
 * modal on top of their list, and a task raised from a lead is read on that
 * lead's page. So a task or note that names a parent opens the parent, and one
 * that does not opens its own list. That is a routing limit of the existing app,
 * not of the search endpoint, which returns every group the same way.
 */
export const RECORD_ROUTES = {
  'CRM Lead': { name: 'Lead', param: 'leadId' },
  'CRM Deal': { name: 'Deal', param: 'dealId' },
  Contact: { name: 'Contact', param: 'contactId' },
  'CRM Organization': { name: 'Organization', param: 'organizationId' },
}

export const FALLBACK_ROUTES = {
  'CRM Task': { name: 'Tasks' },
  'FCRM Note': { name: 'Notes' },
}

export function recordRoute(item) {
  if (!item?.doctype || !item?.name) return null

  const direct = RECORD_ROUTES[item.doctype]
  if (direct) {
    return { name: direct.name, params: { [direct.param]: item.name } }
  }

  const parent = RECORD_ROUTES[item.reference_doctype]
  if (parent && item.reference_docname) {
    return {
      name: parent.name,
      params: { [parent.param]: item.reference_docname },
    }
  }

  return FALLBACK_ROUTES[item.doctype] || null
}

/** Lucide icon name per group. Kept here so the row renderer stays dumb. */
export const DOCTYPE_ICONS = {
  'CRM Lead': 'lucide-user-round-search',
  'CRM Deal': 'lucide-handshake',
  Contact: 'lucide-contact-round',
  'CRM Organization': 'lucide-building-2',
  'CRM Task': 'lucide-square-check-big',
  'FCRM Note': 'lucide-notebook-pen',
}

export function doctypeIcon(doctype) {
  return DOCTYPE_ICONS[doctype] || 'lucide-file'
}

/**
 * The quick actions on the empty state. §2's "Cmd+K is never empty" is the
 * whole reason they exist: with no query and no history, the palette still has
 * three useful things to offer.
 */
export const QUICK_ACTIONS = [
  { id: 'create-lead', label: 'Create lead', icon: 'lucide-user-round-plus' },
  { id: 'create-deal', label: 'Create deal', icon: 'lucide-circle-plus' },
  {
    id: 'go-dashboard',
    label: 'Go to Dashboard',
    icon: 'lucide-layout-dashboard',
    route: { name: 'Dashboard' },
  },
]

/**
 * Flatten the rendered sections into the one list the arrow keys walk.
 *
 * Every row gets a DOM id, which is what `aria-activedescendant` points at: a
 * listbox announces the highlighted row by id, and a palette whose highlight is
 * only a background colour is unusable with a screen reader.
 */
export function flattenSections(sections) {
  const rows = []
  for (const section of sections || []) {
    for (const item of section.items || []) {
      rows.push({
        ...item,
        sectionKey: section.key,
        // `domId`, not `id`: a quick action already has an `id` of its own and
        // the activation branch reads it.
        domId: `crm-palette-row-${rows.length}`,
      })
    }
  }
  return rows
}

/** Arrow-key movement. Wraps at both ends, and survives an empty list. */
export function moveIndex(current, delta, length) {
  if (!length) return -1
  if (current < 0) return delta > 0 ? 0 : length - 1
  return (current + delta + length) % length
}

/**
 * The sections the palette renders for a given state.
 *
 * `results` is what `crm.api.search.palette_search` returned. `recents` is what
 * `crm.api.search.resolve_records` returned — already permission-filtered
 * server-side, so anything in it is safe to render.
 */
export function buildSections({ query, results, recents, actions } = {}) {
  const typed = String(query ?? '').trim()

  if (typed.length >= 2) {
    return (results || [])
      .filter((group) => group.items?.length)
      .map((group) => ({
        key: group.doctype,
        label: group.label,
        items: (group.items || []).map((item) => ({ ...item, kind: 'record' })),
      }))
  }

  const sections = []
  if (recents?.length) {
    sections.push({
      key: 'recents',
      label: 'Recently viewed',
      items: recents.map((item) => ({ ...item, kind: 'record' })),
    })
  }
  sections.push({
    key: 'actions',
    label: 'Quick actions',
    items: (actions || QUICK_ACTIONS).map((action) => ({
      ...action,
      kind: 'action',
    })),
  })
  return sections
}

/**
 * The line under a search row. A record shows what the server sent; an action
 * shows nothing, because a one-line action does not need explaining.
 */
export function rowSubtitle(row) {
  if (!row || row.kind === 'action') return ''
  return row.subtitle || ''
}
