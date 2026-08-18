import {
  QUICK_ACTIONS,
  buildSections,
  doctypeIcon,
  flattenSections,
  moveIndex,
  recordRoute,
  rowSubtitle,
} from '@/utils/palette'

const lead = { doctype: 'CRM Lead', name: 'CRM-LEAD-1', title: 'Ada' }

describe('recordRoute', () => {
  it('routes each doctype that has a record page', () => {
    expect(recordRoute(lead)).toEqual({
      name: 'Lead',
      params: { leadId: 'CRM-LEAD-1' },
    })
    expect(recordRoute({ doctype: 'CRM Deal', name: 'D-1' })).toEqual({
      name: 'Deal',
      params: { dealId: 'D-1' },
    })
    expect(recordRoute({ doctype: 'Contact', name: 'Ada-1' })).toEqual({
      name: 'Contact',
      params: { contactId: 'Ada-1' },
    })
    expect(recordRoute({ doctype: 'CRM Organization', name: 'Acme' })).toEqual({
      name: 'Organization',
      params: { organizationId: 'Acme' },
    })
  })

  it('opens a task on the record it belongs to', () => {
    expect(
      recordRoute({
        doctype: 'CRM Task',
        name: 'T-1',
        reference_doctype: 'CRM Lead',
        reference_docname: 'CRM-LEAD-1',
      }),
    ).toEqual({ name: 'Lead', params: { leadId: 'CRM-LEAD-1' } })
  })

  it('falls back to the list when a task belongs to nothing', () => {
    expect(recordRoute({ doctype: 'CRM Task', name: 'T-1' })).toEqual({
      name: 'Tasks',
    })
    expect(recordRoute({ doctype: 'FCRM Note', name: 'N-1' })).toEqual({
      name: 'Notes',
    })
  })

  it('refuses to guess', () => {
    expect(recordRoute(null)).toBeNull()
    expect(recordRoute({ doctype: 'CRM Lead' })).toBeNull()
    expect(recordRoute({ doctype: 'User', name: 'a@b.com' })).toBeNull()
  })
})

describe('doctypeIcon', () => {
  it('has one per group', () => {
    for (const doctype of [
      'CRM Lead',
      'CRM Deal',
      'Contact',
      'CRM Organization',
      'CRM Task',
      'FCRM Note',
    ]) {
      expect(doctypeIcon(doctype)).toMatch(/^lucide-/)
    }
  })

  it('falls back rather than rendering nothing', () => {
    expect(doctypeIcon('Anything Else')).toBe('lucide-file')
  })
})

describe('moveIndex', () => {
  it('walks forward and back', () => {
    expect(moveIndex(0, 1, 3)).toBe(1)
    expect(moveIndex(1, -1, 3)).toBe(0)
  })

  it('wraps at both ends', () => {
    expect(moveIndex(2, 1, 3)).toBe(0)
    expect(moveIndex(0, -1, 3)).toBe(2)
  })

  it('returns -1 for an empty list rather than NaN', () => {
    expect(moveIndex(0, 1, 0)).toBe(-1)
  })

  it('enters the list from either end when nothing is highlighted', () => {
    expect(moveIndex(-1, 1, 3)).toBe(0)
    expect(moveIndex(-1, -1, 3)).toBe(2)
  })
})

describe('buildSections', () => {
  const results = [
    { doctype: 'CRM Lead', label: 'Leads', items: [lead] },
    { doctype: 'CRM Deal', label: 'Deals', items: [] },
  ]

  it('is NEVER empty with no query (UX rule: Cmd+K is never blank)', () => {
    const sections = buildSections({ query: '', results: [], recents: [] })
    expect(sections).toHaveLength(1)
    expect(sections[0].key).toBe('actions')
    expect(sections[0].items).toHaveLength(QUICK_ACTIONS.length)
  })

  it('shows recents above the quick actions', () => {
    const sections = buildSections({ query: '', results: [], recents: [lead] })
    expect(sections.map((section) => section.key)).toEqual([
      'recents',
      'actions',
    ])
  })

  it('still shows recents while the query is too short to search', () => {
    const sections = buildSections({ query: 'a', results, recents: [lead] })
    expect(sections.map((section) => section.key)).toEqual([
      'recents',
      'actions',
    ])
  })

  it('switches to results once the query is long enough', () => {
    const sections = buildSections({ query: 'ad', results, recents: [lead] })
    expect(sections.map((section) => section.key)).toEqual(['CRM Lead'])
  })

  it('drops a group the server returned empty', () => {
    const sections = buildSections({ query: 'ad', results })
    expect(sections.every((section) => section.items.length)).toBe(true)
  })

  it('tags every row with what it is', () => {
    expect(buildSections({ query: 'ad', results })[0].items[0].kind).toBe(
      'record',
    )
    expect(buildSections({ query: '', results: [] })[0].items[0].kind).toBe(
      'action',
    )
  })

  it('survives being handed nothing', () => {
    expect(buildSections()).toHaveLength(1)
    expect(buildSections({})).toHaveLength(1)
  })
})

describe('flattenSections', () => {
  it('gives every row a unique DOM id for aria-activedescendant', () => {
    const rows = flattenSections(
      buildSections({ query: '', results: [], recents: [lead] }),
    )
    const ids = rows.map((row) => row.domId)
    expect(new Set(ids).size).toBe(ids.length)
    expect(ids.every((id) => id.startsWith('crm-palette-row-'))).toBe(true)
  })

  it('keeps the quick action id intact so activation can read it', () => {
    const rows = flattenSections(buildSections({ query: '', results: [] }))
    expect(rows.map((row) => row.id)).toEqual(
      QUICK_ACTIONS.map((action) => action.id),
    )
  })

  it('records which section each row came from', () => {
    const rows = flattenSections(
      buildSections({ query: '', results: [], recents: [lead] }),
    )
    expect(rows[0].sectionKey).toBe('recents')
    expect(rows[1].sectionKey).toBe('actions')
  })

  it('flattens nothing into nothing', () => {
    expect(flattenSections([])).toEqual([])
    expect(flattenSections(null)).toEqual([])
    expect(flattenSections([{ key: 'a' }])).toEqual([])
  })
})

describe('rowSubtitle', () => {
  it('shows the server line on a record', () => {
    expect(rowSubtitle({ kind: 'record', subtitle: 'Acme · New' })).toBe(
      'Acme · New',
    )
  })

  it('shows nothing on an action', () => {
    expect(rowSubtitle({ kind: 'action', subtitle: 'x' })).toBe('')
  })

  it('shows nothing for nothing', () => {
    expect(rowSubtitle(null)).toBe('')
    expect(rowSubtitle({ kind: 'record' })).toBe('')
  })
})
