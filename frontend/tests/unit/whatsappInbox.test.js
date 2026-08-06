import {
  conversationInitials,
  conversationKey,
  conversationPreview,
  filterConversations,
  isSameConversation,
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
