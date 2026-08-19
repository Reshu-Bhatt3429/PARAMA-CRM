import {
  TODAY_FILTERS,
  actionLabel,
  chipCounts,
  filterItems,
  itemRoute,
  moveCursor,
  removeItem,
  replyRoute,
  typeIcon,
} from '@/utils/today'

const ITEMS = [
  { key: 'task:1', type: 'task', title: 'Call back', action: 'open' },
  { key: 'reply:a', type: 'reply', title: 'Ada', action: 'reply' },
  { key: 'deal:D1', type: 'deal', title: 'Acme', action: 'open' },
  { key: 'approval:F1', type: 'approval', title: 'Bo', action: 'approve' },
]

describe('TODAY_FILTERS', () => {
  it('is one list with chips, not four panels (§2.17)', () => {
    expect(TODAY_FILTERS.map((chip) => chip.key)).toEqual([
      'all',
      'task',
      'reply',
      'deal',
      'approval',
    ])
  })
})

describe('filterItems', () => {
  it('all means all', () => {
    expect(filterItems(ITEMS, 'all')).toHaveLength(4)
    expect(filterItems(ITEMS, null)).toHaveLength(4)
  })

  it('narrows to one type', () => {
    expect(filterItems(ITEMS, 'reply').map((row) => row.key)).toEqual([
      'reply:a',
    ])
  })

  it('survives a missing list', () => {
    expect(filterItems(undefined, 'task')).toEqual([])
  })
})

describe('chipCounts', () => {
  it('takes the counts the server sent', () => {
    const chips = chipCounts({
      all: 4,
      task: 1,
      reply: 1,
      deal: 1,
      approval: 1,
    })
    expect(chips.find((chip) => chip.key === 'all').count).toBe(4)
    expect(chips.find((chip) => chip.key === 'deal').count).toBe(1)
  })

  it('shows zero rather than undefined when a count is missing', () => {
    for (const chip of chipCounts(undefined)) {
      expect(chip.count).toBe(0)
    }
  })
})

describe('itemRoute', () => {
  it('opens a deal on its own record page', () => {
    expect(
      itemRoute({ type: 'deal', doctype: 'CRM Deal', name: 'D1' }),
    ).toEqual({ name: 'Deal', params: { dealId: 'D1' } })
  })

  it('opens a task on the record it belongs to, because a task has no page', () => {
    const route = itemRoute({
      type: 'task',
      doctype: 'CRM Task',
      name: '7',
      reference_doctype: 'CRM Lead',
      reference_name: 'L1',
    })
    expect(route).toEqual({ name: 'Lead', params: { leadId: 'L1' } })
  })

  it('falls back to the Tasks list for a task attached to nothing', () => {
    expect(itemRoute({ type: 'task', doctype: 'CRM Task', name: '7' })).toEqual(
      {
        name: 'Tasks',
      },
    )
  })

  it('returns nothing rather than a broken route', () => {
    expect(itemRoute(null)).toBe(null)
    expect(
      itemRoute({ type: 'approval', doctype: 'CRM WhatsApp Followup' }),
    ).toBe(null)
  })
})

describe('replyRoute', () => {
  it('goes to the inbox, which is the only place with a composer', () => {
    expect(
      replyRoute({ reference_doctype: 'CRM Lead', reference_name: 'L1' }),
    ).toEqual({
      name: 'WhatsApp',
      query: { doctype: 'CRM Lead', name: 'L1' },
    })
  })
})

describe('actionLabel', () => {
  it('gives each row exactly one verb', () => {
    expect(actionLabel({ action: 'open' })).toBe('Open')
    expect(actionLabel({ action: 'approve' })).toBe('Approve')
    expect(actionLabel({ action: 'reply' })).toBe('Reply')
  })

  it('defaults to Open rather than rendering an empty button', () => {
    expect(actionLabel({})).toBe('Open')
    expect(actionLabel(null)).toBe('Open')
  })
})

describe('typeIcon', () => {
  it('has an icon per type and a fallback', () => {
    for (const chip of TODAY_FILTERS) {
      if (chip.key === 'all') continue
      expect(typeIcon(chip.key)).toMatch(/^lucide-/)
    }
    expect(typeIcon('nonsense')).toBe('lucide-circle')
  })
})

describe('moveCursor', () => {
  it('starts at the top going down and the bottom going up', () => {
    expect(moveCursor(-1, 1, 3)).toBe(0)
    expect(moveCursor(-1, -1, 3)).toBe(2)
  })

  it('wraps at both ends', () => {
    expect(moveCursor(2, 1, 3)).toBe(0)
    expect(moveCursor(0, -1, 3)).toBe(2)
  })

  it('survives an empty list', () => {
    expect(moveCursor(0, 1, 0)).toBe(-1)
  })
})

describe('removeItem', () => {
  const payload = {
    items: ITEMS,
    counts: { all: 4, task: 1, reply: 1, deal: 1, approval: 1 },
    deal_health_enabled: true,
  }

  it('drops the row without a reload', () => {
    const next = removeItem(payload, 'approval:F1')
    expect(next.items.map((row) => row.key)).not.toContain('approval:F1')
    expect(next.items).toHaveLength(3)
  })

  it('fixes the counts so the chips and the list agree', () => {
    const next = removeItem(payload, 'approval:F1')
    expect(next.counts.all).toBe(3)
    expect(next.counts.approval).toBe(0)
    expect(next.counts.task).toBe(1)
  })

  it('keeps the rest of the payload', () => {
    expect(removeItem(payload, 'task:1').deal_health_enabled).toBe(true)
  })

  it('is a no-op for a key that is not there', () => {
    expect(removeItem(payload, 'task:999').items).toHaveLength(4)
  })

  it('survives an empty payload', () => {
    expect(removeItem(undefined, 'task:1').items).toEqual([])
  })
})
