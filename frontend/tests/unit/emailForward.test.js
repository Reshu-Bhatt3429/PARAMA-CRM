import {
  deletableAttachments,
  forwardQuote,
  forwardSubject,
  forwardedAttachments,
} from '@/utils/emailForward'

const email = (overrides = {}) => ({
  subject: 'Your itinerary',
  sender: 'ann@example.com',
  sender_full_name: 'Ann Lee',
  recipients: 'bob@example.com',
  content: '<p>Here it is</p>',
  communication_date: '2026-08-18 10:00:00',
  attachments: [],
  ...overrides,
})

describe('forwardSubject', () => {
  it('prefixes the subject', () => {
    expect(forwardSubject('Your itinerary')).toBe('Fwd: Your itinerary')
  })

  it('does not stack prefixes', () => {
    expect(forwardSubject('Fwd: Your itinerary')).toBe('Fwd: Your itinerary')
    expect(forwardSubject('fwd: Your itinerary')).toBe('fwd: Your itinerary')
  })

  it('still prefixes a reply', () => {
    expect(forwardSubject('Re: Your itinerary')).toBe('Fwd: Re: Your itinerary')
  })

  it('copes with no subject', () => {
    expect(forwardSubject('')).toBe('Fwd:')
    expect(forwardSubject(null)).toBe('Fwd:')
  })
})

describe('forwardQuote', () => {
  it('puts a divider before the original', () => {
    expect(forwardQuote(email())).toMatch(/^<hr>/)
  })

  it('quotes the original body verbatim', () => {
    expect(forwardQuote(email())).toContain(
      '<blockquote><p>Here it is</p></blockquote>',
    )
  })

  it('names the original sender, date, subject and recipients', () => {
    const quote = forwardQuote(email())
    expect(quote).toContain('Ann Lee')
    expect(quote).toContain('ann@example.com')
    expect(quote).toContain('2026-08-18 10:00:00')
    expect(quote).toContain('Your itinerary')
    expect(quote).toContain('bob@example.com')
  })

  it('escapes the header fields a stranger wrote', () => {
    const quote = forwardQuote(email({ subject: '<img onerror=x>' }))
    expect(quote).not.toContain('<img onerror=x>')
    expect(quote).toContain('&lt;img onerror=x&gt;')
  })

  it('uses the labels it is given', () => {
    const quote = forwardQuote(email(), { from: 'Von', subject: 'Betreff' })
    expect(quote).toContain('Von:')
    expect(quote).toContain('Betreff:')
  })

  it('omits the date and recipients when the message has none', () => {
    const quote = forwardQuote(
      email({ communication_date: null, recipients: null }),
    )
    expect(quote).not.toContain('Date:')
    expect(quote).not.toContain('To:')
  })

  it('survives a missing message', () => {
    expect(() => forwardQuote(undefined)).not.toThrow()
  })
})

describe('forwardedAttachments', () => {
  it('marks every carried file', () => {
    const carried = forwardedAttachments([
      { name: 'FILE-1', file_name: 'a.pdf', file_url: '/a.pdf' },
    ])
    expect(carried).toEqual([
      {
        name: 'FILE-1',
        file_name: 'a.pdf',
        file_url: '/a.pdf',
        forwarded: true,
      },
    ])
  })

  it('drops entries with no File name to re-link', () => {
    expect(forwardedAttachments([null, {}, { file_name: 'x' }])).toEqual([])
  })

  it('survives no attachments', () => {
    expect(forwardedAttachments(undefined)).toEqual([])
  })
})

describe('deletableAttachments', () => {
  it('keeps forwarded files out of the delete list', () => {
    const files = [{ name: 'FILE-1', forwarded: true }, { name: 'FILE-2' }]
    expect(deletableAttachments(files).map((f) => f.name)).toEqual(['FILE-2'])
  })

  it('deletes everything this composer uploaded', () => {
    const files = [{ name: 'FILE-1' }, { name: 'FILE-2' }]
    expect(deletableAttachments(files)).toHaveLength(2)
  })

  it('survives no attachments', () => {
    expect(deletableAttachments(null)).toEqual([])
  })
})
