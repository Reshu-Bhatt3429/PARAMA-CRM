import {
  briefNoteTitle,
  briefToNoteHtml,
  cacheKey,
  clearBriefCache,
  dueDateFromHint,
  forgetBrief,
  normalizeTone,
  recallBrief,
  rememberBrief,
} from '@/utils/aiBrief'

// A fixed "now" so the suite does not change verdict with the wall clock —
// the Stage 1B lesson from `test_quiet_hours_defer_instead_of_cancel`.
const now = new Date(2026, 7, 19, 14, 0, 0) // 2026-08-19 14:00 local

function brief(overrides = {}) {
  return {
    bullets: ['Wants Maldives in November.', 'Budget unchanged.'],
    next_step: {
      description: 'Send the resort options.',
      due_hint: 'tomorrow',
    },
    tone: 'neutral',
    generated_at: '2026-08-19 14:00:00',
    ...overrides,
  }
}

describe('dueDateFromHint', () => {
  it('turns today into today', () => {
    expect(dueDateFromHint('today', now)).toBe('2026-08-19 09:00:00')
  })

  it('turns tomorrow into the next day', () => {
    expect(dueDateFromHint('tomorrow', now)).toBe('2026-08-20 09:00:00')
  })

  it('turns this_week and next_week into later days', () => {
    expect(dueDateFromHint('this_week', now)).toBe('2026-08-22 09:00:00')
    expect(dueDateFromHint('next_week', now)).toBe('2026-08-26 09:00:00')
  })

  it('crosses a month boundary correctly', () => {
    const endOfMonth = new Date(2026, 7, 30, 14, 0, 0)
    expect(dueDateFromHint('this_week', endOfMonth)).toBe('2026-09-02 09:00:00')
  })

  it('returns null for a hint it does not know', () => {
    expect(dueDateFromHint('eventually', now)).toBeNull()
    expect(dueDateFromHint(null, now)).toBeNull()
    expect(dueDateFromHint(undefined, now)).toBeNull()
  })
})

describe('normalizeTone', () => {
  it('keeps the four tones the card can render', () => {
    for (const tone of ['positive', 'neutral', 'negative', 'frustrated']) {
      expect(normalizeTone(tone)).toBe(tone)
    }
  })

  it('drops anything else', () => {
    expect(normalizeTone('ecstatic')).toBeNull()
    expect(normalizeTone(null)).toBeNull()
    expect(normalizeTone(undefined)).toBeNull()
  })
})

describe('briefToNoteHtml', () => {
  it('renders the bullets as a list', () => {
    const html = briefToNoteHtml(brief())
    expect(html).toContain('<li>Wants Maldives in November.</li>')
    expect(html).toContain('<li>Budget unchanged.</li>')
  })

  it('renders the next step and the tone', () => {
    const html = briefToNoteHtml(brief())
    expect(html).toContain('Send the resort options.')
    expect(html).toContain('neutral')
  })

  it('leaves out a next step that is not there', () => {
    const html = briefToNoteHtml(brief({ next_step: null }))
    expect(html).not.toContain('Suggested next step')
  })

  it('leaves out a tone that is not there', () => {
    expect(briefToNoteHtml(brief({ tone: null }))).not.toContain('Tone')
  })

  it('leaves out a tone the card would not render', () => {
    expect(briefToNoteHtml(brief({ tone: 'ecstatic' }))).not.toContain('Tone')
  })

  it('escapes markup a model wrote', () => {
    // A note is rendered as HTML in the timeline, and this text came from a
    // model by way of a customer's own words.
    const html = briefToNoteHtml(
      brief({ bullets: ['<img src=x onerror="alert(1)">'] }),
    )
    expect(html).not.toContain('<img')
    expect(html).toContain('&lt;img')
  })

  it('survives an empty brief without throwing', () => {
    expect(briefToNoteHtml({})).toBe('<ul></ul>')
    expect(briefToNoteHtml(null)).toBe('<ul></ul>')
  })
})

describe('briefNoteTitle', () => {
  it('dates the title so two saved briefs are tellable apart', () => {
    expect(briefNoteTitle('2026-08-19 14:00:00')).toBe('AI Brief — 2026-08-19')
  })

  it('falls back to the bare prefix on an unreadable timestamp', () => {
    expect(briefNoteTitle('not a date')).toBe('AI Brief')
  })
})

describe('the session-local brief cache', () => {
  beforeEach(() => clearBriefCache())

  it('keys on both the doctype and the name', () => {
    expect(cacheKey('CRM Lead', 'L-1')).toBe('CRM Lead:L-1')
    expect(cacheKey('CRM Deal', 'L-1')).not.toBe(cacheKey('CRM Lead', 'L-1'))
  })

  it('gives a remembered brief back', () => {
    rememberBrief('CRM Lead', 'L-1', brief())
    expect(recallBrief('CRM Lead', 'L-1').tone).toBe('neutral')
  })

  it('returns null for a record with no brief', () => {
    expect(recallBrief('CRM Lead', 'L-2')).toBeNull()
  })

  it('does not leak one record’s brief into another', () => {
    rememberBrief('CRM Lead', 'L-1', brief())
    expect(recallBrief('CRM Lead', 'L-3')).toBeNull()
    expect(recallBrief('CRM Deal', 'L-1')).toBeNull()
  })

  it('forgets a dismissed brief', () => {
    rememberBrief('CRM Lead', 'L-1', brief())
    forgetBrief('CRM Lead', 'L-1')
    expect(recallBrief('CRM Lead', 'L-1')).toBeNull()
  })
})
