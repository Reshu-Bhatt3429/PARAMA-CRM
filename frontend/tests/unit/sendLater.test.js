import {
  PRESET_LATER_TODAY,
  PRESET_NEXT_MONDAY,
  PRESET_TOMORROW_MORNING,
  availablePresets,
  canCancelJob,
  earliestPickable,
  isFutureDatetime,
  parseLocalDatetime,
  presetIsAvailable,
  presetPreview,
  toServerDatetime,
} from '@/utils/sendLater'

// A fixed "now" so the suite does not change verdict with the wall clock.
// 2026-08-19 is a Wednesday.
const wednesdayMorning = new Date(2026, 7, 19, 10, 0, 0)
const wednesdayEvening = new Date(2026, 7, 19, 19, 30, 0)
const monday = new Date(2026, 7, 17, 8, 0, 0)

describe('presetPreview', () => {
  it('puts "later today" at six in the evening, today', () => {
    const at = presetPreview(PRESET_LATER_TODAY, wednesdayMorning)
    expect(at.getDate()).toBe(19)
    expect(at.getHours()).toBe(18)
    expect(at.getMinutes()).toBe(0)
  })

  it('puts "tomorrow morning" at nine, the next day', () => {
    const at = presetPreview(PRESET_TOMORROW_MORNING, wednesdayMorning)
    expect(at.getDate()).toBe(20)
    expect(at.getHours()).toBe(9)
  })

  it('puts "next monday" on the next Monday at nine', () => {
    const at = presetPreview(PRESET_NEXT_MONDAY, wednesdayMorning)
    expect(at.getDay()).toBe(1)
    expect(at.getDate()).toBe(24)
    expect(at.getHours()).toBe(9)
  })

  it('never reads "next monday" as today when today is a Monday', () => {
    const at = presetPreview(PRESET_NEXT_MONDAY, monday)
    expect(at.getDay()).toBe(1)
    expect(at.getDate()).toBe(24)
  })

  it('returns nothing for a preset it does not know', () => {
    expect(presetPreview('whenever', wednesdayMorning)).toBeNull()
  })
})

describe('presetIsAvailable', () => {
  it('offers "later today" in the morning', () => {
    expect(presetIsAvailable(PRESET_LATER_TODAY, wednesdayMorning)).toBe(true)
  })

  it('withdraws "later today" once six has passed', () => {
    // Offering an option so the server can refuse it is worse than not
    // offering it.
    expect(presetIsAvailable(PRESET_LATER_TODAY, wednesdayEvening)).toBe(false)
  })

  it('keeps tomorrow and next Monday available at any hour', () => {
    expect(presetIsAvailable(PRESET_TOMORROW_MORNING, wednesdayEvening)).toBe(
      true,
    )
    expect(presetIsAvailable(PRESET_NEXT_MONDAY, wednesdayEvening)).toBe(true)
  })
})

describe('availablePresets', () => {
  it('lists all three in the morning', () => {
    expect(availablePresets(wednesdayMorning)).toEqual([
      PRESET_LATER_TODAY,
      PRESET_TOMORROW_MORNING,
      PRESET_NEXT_MONDAY,
    ])
  })

  it('drops the one that has already passed', () => {
    expect(availablePresets(wednesdayEvening)).toEqual([
      PRESET_TOMORROW_MORNING,
      PRESET_NEXT_MONDAY,
    ])
  })
})

describe('toServerDatetime', () => {
  it('writes the local wall clock, never a UTC instant', () => {
    // An ISO string with a Z would be a different hour on the server.
    expect(toServerDatetime(new Date(2026, 7, 19, 18, 5, 0))).toBe(
      '2026-08-19 18:05:00',
    )
  })

  it('pads every field to two digits', () => {
    expect(toServerDatetime(new Date(2026, 0, 2, 3, 4, 5))).toBe(
      '2026-01-02 03:04:05',
    )
  })

  it('accepts what the picker produces', () => {
    expect(toServerDatetime('2026-08-19T18:05')).toBe('2026-08-19 18:05:00')
  })

  it('returns an empty string for nothing at all', () => {
    expect(toServerDatetime('')).toBe('')
    expect(toServerDatetime('not a date')).toBe('')
  })
})

describe('parseLocalDatetime', () => {
  it('reads a naive server string as local time', () => {
    const parsed = parseLocalDatetime('2026-08-19 18:00:00')
    expect(parsed.getHours()).toBe(18)
    expect(parsed.getDate()).toBe(19)
  })

  it('reads the picker form as local time too', () => {
    expect(parseLocalDatetime('2026-08-19T18:00').getHours()).toBe(18)
  })

  it('returns null for junk', () => {
    expect(parseLocalDatetime('tomorrowish')).toBeNull()
    expect(parseLocalDatetime('')).toBeNull()
  })
})

describe('earliestPickable', () => {
  it('is a minute from now, in the form the input wants', () => {
    expect(earliestPickable(new Date(2026, 7, 19, 10, 0, 0))).toBe(
      '2026-08-19T10:01',
    )
  })
})

describe('isFutureDatetime', () => {
  it('accepts a later time', () => {
    expect(isFutureDatetime('2026-08-19 18:00:00', wednesdayMorning)).toBe(true)
  })

  it('refuses a time that has passed', () => {
    expect(isFutureDatetime('2026-08-19 09:00:00', wednesdayMorning)).toBe(
      false,
    )
  })

  it('refuses junk', () => {
    expect(isFutureDatetime('whenever', wednesdayMorning)).toBe(false)
  })
})

describe('canCancelJob', () => {
  it('allows a cancel up to the claim', () => {
    expect(canCancelJob({ state: 'Scheduled' })).toBe(true)
    expect(canCancelJob({ state: 'Draft' })).toBe(true)
  })

  it('refuses once the job is claimed or past it', () => {
    // The recipients may already be with the provider. A button that said
    // "Cancel" would be lying about what it can do.
    for (const state of ['Claimed', 'Queued', 'Sent', 'Failed', 'Cancelled']) {
      expect(canCancelJob({ state })).toBe(false)
    }
  })

  it('refuses when there is no job', () => {
    expect(canCancelJob()).toBe(false)
    expect(canCancelJob({})).toBe(false)
  })
})
