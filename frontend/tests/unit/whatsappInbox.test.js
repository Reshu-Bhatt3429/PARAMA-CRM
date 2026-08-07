import {
  conversationInitials,
  conversationKey,
  conversationPreview,
  filterConversations,
  groupSizeLabel,
  humanizeAge,
  isSameConversation,
  priorityMeta,
  priorityPill,
  statusPill,
  travelWindowLabel,
  waitingLabel,
  waitingPill,
  groupMessagesByDay,
  dayLabel,
} from '@/utils/whatsappInbox'

const conversation = (overrides = {}) => ({
  reference_doctype: 'CRM Lead',
  reference_name: 'LEAD-0001',
  display_name: 'Ada Lovelace',
  phone: '+1 555-0100',
  last_message: 'Sounds good',
  last_message_type: 'Incoming',
  last_at: '2026-08-01 10:00:00',
  message_count: 4,
  ...overrides,
})

describe('conversationKey', () => {
  it('joins doctype and name', () => {
    expect(conversationKey(conversation())).toBe('CRM Lead:LEAD-0001')
  })

  it('returns empty string for incomplete input', () => {
    expect(conversationKey(null)).toBe('')
    expect(conversationKey({})).toBe('')
    expect(conversationKey({ reference_doctype: 'CRM Lead' })).toBe('')
  })
})

describe('isSameConversation', () => {
  it('matches a realtime payload for the same reference', () => {
    const payload = {
      reference_doctype: 'CRM Lead',
      reference_name: 'LEAD-0001',
    }
    expect(isSameConversation(conversation(), payload)).toBe(true)
  })

  it('rejects a payload for another reference', () => {
    const payload = {
      reference_doctype: 'CRM Deal',
      reference_name: 'LEAD-0001',
    }
    expect(isSameConversation(conversation(), payload)).toBe(false)
  })

  it('never matches when either side is incomplete', () => {
    expect(isSameConversation(null, { reference_doctype: 'CRM Lead' })).toBe(
      false,
    )
    expect(isSameConversation(conversation(), {})).toBe(false)
  })
})

describe('conversationInitials', () => {
  it('uses first and last word of the name', () => {
    expect(conversationInitials(conversation())).toBe('AL')
    expect(
      conversationInitials(conversation({ display_name: 'Ada B Lovelace' })),
    ).toBe('AL')
  })

  it('uses the first two letters of a single word', () => {
    expect(conversationInitials(conversation({ display_name: 'Acme' }))).toBe(
      'AC',
    )
  })

  it('falls back to the last two phone digits', () => {
    expect(
      conversationInitials(
        conversation({ display_name: '', phone: '+1 555-0142' }),
      ),
    ).toBe('42')
  })

  it('falls back to a placeholder without name or phone', () => {
    expect(
      conversationInitials(conversation({ display_name: '  ', phone: '' })),
    ).toBe('#')
  })
})

describe('conversationPreview', () => {
  it('returns an incoming message unchanged', () => {
    expect(conversationPreview(conversation())).toBe('Sounds good')
  })

  it('prefixes outgoing messages', () => {
    expect(
      conversationPreview(conversation({ last_message_type: 'Outgoing' })),
    ).toBe('You: Sounds good')
  })

  it('keeps the backend media label', () => {
    expect(
      conversationPreview(conversation({ last_message: '📷 Image' })),
    ).toBe('📷 Image')
  })

  it('falls back when the thread has no readable message', () => {
    expect(conversationPreview(conversation({ last_message: '   ' }))).toBe(
      'No messages yet',
    )
    expect(conversationPreview(undefined)).toBe('No messages yet')
  })
})

describe('filterConversations', () => {
  const list = [
    conversation(),
    conversation({
      reference_name: 'LEAD-0002',
      display_name: 'Grace Hopper',
      phone: '+1 555-0199',
      last_message: '📄 Document',
    }),
  ]

  it('returns a copy when the query is empty', () => {
    const result = filterConversations(list, '   ')
    expect(result).toEqual(list)
    expect(result).not.toBe(list)
  })

  it('matches the display name case-insensitively', () => {
    expect(filterConversations(list, 'grace')).toHaveLength(1)
    expect(filterConversations(list, 'grace')[0].reference_name).toBe(
      'LEAD-0002',
    )
  })

  it('matches the last message', () => {
    expect(filterConversations(list, 'document')).toHaveLength(1)
  })

  it('matches phone numbers ignoring formatting', () => {
    expect(filterConversations(list, '555 0199')).toHaveLength(1)
    expect(filterConversations(list, '5550199')[0].reference_name).toBe(
      'LEAD-0002',
    )
  })

  it('returns an empty list for no match', () => {
    expect(filterConversations(list, 'nobody')).toEqual([])
  })

  it('handles a missing list', () => {
    expect(filterConversations(undefined, 'grace')).toEqual([])
  })
})

describe('priorityMeta', () => {
  it('maps the three buckets to their dot colour', () => {
    expect(priorityMeta('hot').dotClass).toBe('bg-red-500')
    expect(priorityMeta('warm').dotClass).toBe('bg-amber-500')
    expect(priorityMeta('cold').dotClass).toBe('bg-surface-gray-4')
  })

  it('always carries a tooltip label', () => {
    expect(priorityMeta('hot').label).toContain('Hot')
    expect(priorityMeta('warm').label).toContain('Warm')
    expect(priorityMeta('cold').label).toContain('Cold')
  })

  it('falls back to cold for anything unknown', () => {
    expect(priorityMeta(undefined)).toEqual(priorityMeta('cold'))
    expect(priorityMeta('lukewarm').priority).toBe('cold')
  })
})

describe('humanizeAge', () => {
  const now = new Date('2026-08-01T12:00:00')

  it('reports minutes under an hour', () => {
    expect(humanizeAge('2026-08-01 11:48:00', now)).toBe('12m')
  })

  it('rounds anything younger than a minute up to 1m', () => {
    expect(humanizeAge('2026-08-01 11:59:30', now)).toBe('1m')
  })

  it('reports hours under a day', () => {
    expect(humanizeAge('2026-08-01 09:00:00', now)).toBe('3h')
  })

  it('reports days beyond that', () => {
    expect(humanizeAge('2026-07-27 12:00:00', now)).toBe('5d')
  })

  it('accepts a Date and rejects junk', () => {
    expect(humanizeAge(new Date('2026-08-01T09:00:00'), now)).toBe('3h')
    expect(humanizeAge('not a date', now)).toBe('')
    expect(humanizeAge(null, now)).toBe('')
  })
})

describe('waitingLabel', () => {
  const now = new Date('2026-08-01T12:00:00')

  it('is empty when no reply is owed', () => {
    expect(waitingLabel(conversation({ needs_reply: false }), now)).toBe('')
    expect(waitingLabel(null, now)).toBe('')
  })

  it('humanizes the wait since the oldest unanswered message', () => {
    const waiting = conversation({
      needs_reply: true,
      waiting_since: '2026-08-01 09:00:00',
    })
    expect(waitingLabel(waiting, now)).toBe('waiting 3h')
  })

  it('falls back to the last message when the start is unknown', () => {
    const waiting = conversation({
      needs_reply: true,
      waiting_since: null,
      last_at: '2026-08-01 11:00:00',
    })
    expect(waitingLabel(waiting, now)).toBe('waiting 1h')
  })

  it('still marks the conversation when no timestamp parses', () => {
    const waiting = conversation({
      needs_reply: true,
      waiting_since: null,
      last_at: null,
    })
    expect(waitingLabel(waiting, now)).toBe('needs reply')
  })
})

describe('priorityPill', () => {
  it('labels the hot bucket as high intent and tints it red', () => {
    const pill = priorityPill('hot')
    expect(pill.label).toBe('High intent')
    expect(pill.class).toContain('bg-surface-red-2')
  })

  it('tints warm amber and cold gray', () => {
    expect(priorityPill('warm').class).toContain('bg-surface-amber-2')
    expect(priorityPill('cold').class).toContain('bg-surface-gray-2')
  })

  it('reuses the priorityMeta explanation as the tooltip', () => {
    expect(priorityPill('hot').title).toBe(priorityMeta('hot').label)
  })

  it('falls back to cold for anything unknown', () => {
    expect(priorityPill('lukewarm')).toEqual(priorityPill('cold'))
    expect(priorityPill(undefined)).toEqual(priorityPill('cold'))
  })
})

describe('statusPill', () => {
  it('is null when the reference carries no status', () => {
    expect(statusPill('')).toBeNull()
    expect(statusPill(null)).toBeNull()
    expect(statusPill('   ')).toBeNull()
  })

  it('tints each stage family', () => {
    expect(statusPill('New').class).toContain('bg-surface-blue-2')
    expect(statusPill('Contacted').class).toContain('bg-surface-amber-2')
    expect(statusPill('Qualified').class).toContain('bg-surface-purple-2')
    expect(statusPill('Booked').class).toContain('bg-surface-green-2')
    expect(statusPill('Lost').class).toContain('bg-surface-red-2')
  })

  it('matches case-insensitively and keeps the original label', () => {
    const pill = statusPill('proposal sent')
    expect(pill.class).toContain('bg-surface-purple-2')
    expect(pill.label).toBe('proposal sent')
    expect(statusPill('BOOKED').class).toContain('bg-surface-green-2')
  })

  it('gives a site-defined status the neutral tint rather than none', () => {
    expect(statusPill('Awaiting Visa').class).toContain('bg-surface-gray-2')
  })
})

describe('waitingPill', () => {
  const now = new Date('2026-08-01T12:00:00')

  it('is null when no reply is owed', () => {
    expect(waitingPill(conversation({ needs_reply: false }), now)).toBeNull()
  })

  it('carries the humanized wait and an amber tint', () => {
    const pill = waitingPill(
      conversation({ needs_reply: true, waiting_since: '2026-08-01 09:00:00' }),
      now,
    )
    expect(pill.label).toBe('waiting 3h')
    expect(pill.class).toContain('bg-surface-amber-2')
  })
})

describe('travelWindowLabel', () => {
  it('joins both ends of the window', () => {
    expect(travelWindowLabel('12-09-2026', '20-09-2026')).toBe(
      '12-09-2026 → 20-09-2026',
    )
  })

  it('collapses a single-day trip to one date', () => {
    expect(travelWindowLabel('12-09-2026', '12-09-2026')).toBe('12-09-2026')
  })

  it('reads a half-open range as from / until', () => {
    expect(travelWindowLabel('12-09-2026', '')).toBe('from 12-09-2026')
    expect(travelWindowLabel('', '20-09-2026')).toBe('until 20-09-2026')
  })

  it('is empty when neither date is known', () => {
    expect(travelWindowLabel('', '')).toBe('')
    expect(travelWindowLabel(null, undefined)).toBe('')
  })
})

describe('groupSizeLabel', () => {
  it('pluralizes the traveller count', () => {
    expect(groupSizeLabel(1)).toBe('1 traveller')
    expect(groupSizeLabel(4)).toBe('4 travellers')
    expect(groupSizeLabel('12')).toBe('12 travellers')
  })

  it('treats zero, null and junk as absent', () => {
    expect(groupSizeLabel(0)).toBe('')
    expect(groupSizeLabel(null)).toBe('')
    expect(groupSizeLabel(undefined)).toBe('')
    expect(groupSizeLabel('many')).toBe('')
  })
})

describe('groupMessagesByDay', () => {
  const NOW = new Date(2026, 7, 6, 12, 0, 0)

  it('groups consecutive messages of the same day', () => {
    const groups = groupMessagesByDay(
      [
        { name: 'a', creation: '2026-08-05 09:00:00' },
        { name: 'b', creation: '2026-08-05 10:00:00' },
        { name: 'c', creation: '2026-08-06 08:00:00' },
      ],
      NOW,
    )
    expect(groups.map((g) => g.messages.length)).toEqual([2, 1])
    expect(groups[0].label).toBe('Yesterday')
    expect(groups[1].label).toBe('Today')
  })

  it('returns an empty list for no messages', () => {
    expect(groupMessagesByDay([], NOW)).toEqual([])
    expect(groupMessagesByDay(null, NOW)).toEqual([])
  })
})

describe('dayLabel', () => {
  const NOW = new Date(2026, 7, 6, 12, 0, 0)

  it('labels today and yesterday with words', () => {
    expect(dayLabel(new Date(2026, 7, 6, 1, 0), NOW)).toBe('Today')
    expect(dayLabel(new Date(2026, 7, 5, 23, 59), NOW)).toBe('Yesterday')
  })

  it('labels older dates with a readable date', () => {
    const label = dayLabel(new Date(2026, 6, 30), NOW)
    expect(label).toMatch(/30/)
    expect(label).toMatch(/Jul/)
  })

  it('includes the year for other years', () => {
    expect(dayLabel(new Date(2025, 11, 31), NOW)).toMatch(/2025/)
  })
})
