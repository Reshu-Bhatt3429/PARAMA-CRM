import {
  HEALTH_FIELD,
  HEALTH_FLAGS,
  chipLabel,
  hasHealthFlags,
  healthFlagLabel,
  parseHealthFlags,
} from '@/utils/dealHealth'

describe('parseHealthFlags', () => {
  it('reads the JSON string a list query returns', () => {
    expect(parseHealthFlags('{"flags":["stalled"]}')).toEqual(['stalled'])
  })

  it('reads the object a loaded document returns', () => {
    expect(parseHealthFlags({ flags: ['awaiting_reply'] })).toEqual([
      'awaiting_reply',
    ])
  })

  it('reads a bare array', () => {
    expect(parseHealthFlags(['stalled'])).toEqual(['stalled'])
  })

  it('returns display order, not the order it was given', () => {
    expect(parseHealthFlags({ flags: ['awaiting_reply', 'stalled'] })).toEqual([
      'stalled',
      'awaiting_reply',
    ])
  })

  it('drops a name it does not know', () => {
    expect(parseHealthFlags({ flags: ['on_fire'] })).toEqual([])
  })

  it('never throws on the empty column or on junk', () => {
    for (const value of ['', null, undefined, 'not json', '[]', '{}', 7]) {
      expect(parseHealthFlags(value)).toEqual([])
    }
  })

  it('names the column the server writes', () => {
    expect(HEALTH_FIELD).toBe('custom_parama_health_flags')
  })
})

describe('hasHealthFlags', () => {
  it('is false for a healthy deal', () => {
    expect(hasHealthFlags('')).toBe(false)
    expect(hasHealthFlags(null)).toBe(false)
  })

  it('is true once there is one flag', () => {
    expect(hasHealthFlags('{"flags":["stalled"]}')).toBe(true)
  })
})

describe('chipLabel', () => {
  it('names the single problem instead of making the reader click (§2.13)', () => {
    expect(chipLabel('{"flags":["stalled"]}')).toBe(
      'No stage change for a while',
    )
  })

  it('collapses two or more into ONE chip with a count, never two badges', () => {
    expect(chipLabel('{"flags":["stalled","close_date_passed"]}')).toBe(
      'Needs attention (2)',
    )
  })

  it('says nothing at all for a healthy deal', () => {
    expect(chipLabel('')).toBe('')
  })
})

describe('healthFlagLabel', () => {
  it('has a sentence for every flag the server can send', () => {
    for (const flag of HEALTH_FLAGS) {
      expect(healthFlagLabel(flag)).not.toBe(flag)
      expect(healthFlagLabel(flag).length).toBeGreaterThan(0)
    }
  })

  it('falls back to the raw name rather than rendering undefined', () => {
    expect(healthFlagLabel('mystery')).toBe('mystery')
  })
})
