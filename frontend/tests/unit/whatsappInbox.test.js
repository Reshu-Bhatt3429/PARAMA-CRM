import {
  conversationInitials,
  conversationKey,
  conversationPreview,
  filterConversations,
  humanizeAge,
  isSameConversation,
  priorityMeta,
  waitingLabel,
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
