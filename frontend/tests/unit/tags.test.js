import {
  TAG_COLORS,
  canCreateTag,
  fuzzyScore,
  hasTag,
  rankTags,
  splitTags,
  tagChipClass,
  tagColor,
  tagColorIndex,
  visibleTags,
} from '@/utils/tags'

describe('splitTags', () => {
  it('drops the leading comma the column is stored with', () => {
    expect(splitTags(',VIP,Honeymoon')).toEqual(['VIP', 'Honeymoon'])
  })

  it('trims and drops empties', () => {
    expect(splitTags(', VIP , ,Bali ')).toEqual(['VIP', 'Bali'])
  })

  it('accepts an array unchanged', () => {
    expect(splitTags(['VIP', ' Bali '])).toEqual(['VIP', 'Bali'])
  })

  it('handles nothing at all', () => {
    expect(splitTags('')).toEqual([])
    expect(splitTags(null)).toEqual([])
    expect(splitTags(undefined)).toEqual([])
  })
})

describe('tagColorIndex', () => {
  it('is stable for the same name', () => {
    expect(tagColorIndex('Bali')).toBe(tagColorIndex('Bali'))
  })

  it('ignores case, because the server treats casings as one tag', () => {
    expect(tagColorIndex('VIP')).toBe(tagColorIndex('vip'))
  })

  it('always lands inside the palette', () => {
    for (const name of ['a', 'Bali', 'Honeymoon 2026', '', '—', 'ünïcode']) {
      const index = tagColorIndex(name)
      expect(index).toBeGreaterThanOrEqual(0)
      expect(index).toBeLessThan(TAG_COLORS.length)
    }
  })

  it('spreads a realistic tag set over more than one colour', () => {
    const names = ['Bali', 'Honeymoon', 'VIP', 'Repeat', 'Corporate', 'Cruise']
    expect(new Set(names.map(tagColorIndex)).size).toBeGreaterThan(1)
  })
})

describe('tagChipClass', () => {
  it('returns whole class names so Tailwind can find them', () => {
    const classes = tagChipClass('Bali')
    expect(classes).toContain(tagColor('Bali').bg)
    expect(classes).not.toContain('${')
  })
})

describe('visibleTags', () => {
  it('shows everything when it fits', () => {
    expect(visibleTags(['a', 'b'], 2)).toEqual({
      shown: ['a', 'b'],
      hidden: [],
      overflow: 0,
    })
  })

  it('collapses the rest into one +N (UX rule 2.13)', () => {
    expect(visibleTags(['a', 'b', 'c', 'd'], 2)).toEqual({
      shown: ['a', 'b'],
      hidden: ['c', 'd'],
      overflow: 2,
    })
  })

  it('reads the stored column form', () => {
    expect(visibleTags(',a,b,c', 2).overflow).toBe(1)
  })

  it('survives an empty record', () => {
    expect(visibleTags('', 2)).toEqual({ shown: [], hidden: [], overflow: 0 })
  })
})

describe('hasTag', () => {
  it('matches case-insensitively', () => {
    expect(hasTag(['VIP'], 'vip')).toBe(true)
    expect(hasTag(['VIP'], 'Bali')).toBe(false)
  })

  it('is false for an empty query', () => {
    expect(hasTag(['VIP'], '  ')).toBe(false)
  })
})

describe('fuzzyScore', () => {
  it('ranks a prefix above a substring above a subsequence', () => {
    const prefix = fuzzyScore('bal', 'Bali')
    const substring = fuzzyScore('bal', 'Sunny Bali')
    const subsequence = fuzzyScore('bal', 'Beach And Lagoon')
    expect(prefix).toBeGreaterThan(substring)
    expect(substring).toBeGreaterThan(subsequence)
  })

  it('returns null when the letters are not there in order', () => {
    expect(fuzzyScore('zzz', 'Bali')).toBeNull()
    expect(fuzzyScore('ilab', 'Bali')).toBeNull()
  })

  it('prefers the shorter candidate inside a tier', () => {
    expect(fuzzyScore('bal', 'Bali')).toBeGreaterThan(
      fuzzyScore('bal', 'Bali Deluxe'),
    )
  })

  it('scores everything when nothing is typed', () => {
    expect(fuzzyScore('', 'Bali')).not.toBeNull()
    expect(fuzzyScore('  ', 'Bali')).not.toBeNull()
  })

  it('is case-insensitive', () => {
    expect(fuzzyScore('BAL', 'bali')).toBe(fuzzyScore('bal', 'Bali'))
  })
})

describe('rankTags', () => {
  const known = ['Bali', 'Bali Deluxe', 'Honeymoon', 'Corporate']

  it('puts the best match first', () => {
    expect(rankTags(known, 'bal')[0]).toBe('Bali')
  })

  it('drops what is already on the record', () => {
    expect(rankTags(known, '', { exclude: ['Bali'] })).not.toContain('Bali')
  })

  it('excludes case-insensitively', () => {
    expect(rankTags(known, '', { exclude: ['bali'] })).not.toContain('Bali')
  })

  it('drops non-matches entirely', () => {
    expect(rankTags(known, 'zzzz')).toEqual([])
  })

  it('lists everything when nothing is typed', () => {
    expect(rankTags(known, '')).toHaveLength(known.length)
  })
})

describe('canCreateTag', () => {
  const known = ['Bali']

  it('offers a genuinely new name', () => {
    expect(canCreateTag('Maldives', { known })).toBe(true)
  })

  it('does not offer one that already exists in another casing', () => {
    expect(canCreateTag('bali', { known })).toBe(false)
  })

  it('does not offer one already on the record', () => {
    expect(canCreateTag('VIP', { known, onRecord: ['vip'] })).toBe(false)
  })

  it('does not offer an empty name', () => {
    expect(canCreateTag('   ', { known })).toBe(false)
  })

  it('does not offer a name with a comma, which the server refuses', () => {
    expect(canCreateTag('Bali, Honeymoon', { known })).toBe(false)
  })
})
