import {
  emailState,
  emailStateIcon,
  emailStateTimestamp,
  showEmailState,
} from '@/utils/emailStatus'

describe('emailState', () => {
  it('says nothing about a message with no state at all', () => {
    expect(emailState({})).toBe('none')
    expect(emailState(null)).toBe('none')
    expect(emailState(undefined)).toBe('none')
  })

  it('reports a queued message as pending', () => {
    expect(emailState({ delivery_status: 'Sending' })).toBe('pending')
    expect(emailState({ delivery_status: 'Scheduled' })).toBe('pending')
  })

  it('reports a delivered message as sent', () => {
    expect(emailState({ delivery_status: 'Sent' })).toBe('sent')
  })

  it('reports a failed message as failed', () => {
    expect(emailState({ delivery_status: 'Error' })).toBe('failed')
    expect(emailState({ delivery_status: 'Bounced' })).toBe('failed')
  })

  it('lets an open outrank every delivery state', () => {
    // A message somebody has read was delivered. Showing "queued" beside
    // "opened" would be the app arguing with itself.
    expect(
      emailState({ read_by_recipient: 1, delivery_status: 'Sending' }),
    ).toBe('opened')
    expect(emailState({ read_by_recipient: 1 })).toBe('opened')
  })

  it('treats an unknown status as pending, never as delivered', () => {
    expect(emailState({ delivery_status: 'Something New' })).toBe('pending')
  })
})

describe('emailStateIcon', () => {
  it('draws one gray tick for sent and two for opened', () => {
    expect(emailStateIcon('sent')).toBe('check')
    expect(emailStateIcon('opened')).toBe('check-check')
  })

  it('draws nothing at all for none', () => {
    expect(emailStateIcon('none')).toBeNull()
    expect(emailStateIcon(undefined)).toBeNull()
  })
})

describe('emailStateTimestamp', () => {
  it('names when an opened message was opened', () => {
    expect(
      emailStateTimestamp({
        read_by_recipient: 1,
        read_by_recipient_on: '2026-08-19 14:00:00',
      }),
    ).toBe('2026-08-19 14:00:00')
  })

  it('has nothing to add for a message that was merely sent', () => {
    expect(emailStateTimestamp({ delivery_status: 'Sent' })).toBeNull()
  })

  it('has nothing to add when the read receipt carries no time', () => {
    expect(emailStateTimestamp({ read_by_recipient: 1 })).toBeNull()
  })
})

describe('showEmailState', () => {
  it('marks a message this site sent', () => {
    expect(
      showEmailState({
        communication_type: 'Communication',
        data: { delivery_status: 'Sent' },
      }),
    ).toBe(true)
  })

  it('never marks an automated notification', () => {
    expect(
      showEmailState({
        communication_type: 'Automated Message',
        data: { delivery_status: 'Sent' },
      }),
    ).toBe(false)
  })

  it('never marks an incoming message', () => {
    // A received email has no delivery status of ours. A tick on somebody
    // else's message would be a lie about who sent it.
    expect(
      showEmailState({ communication_type: 'Communication', data: {} }),
    ).toBe(false)
  })
})
