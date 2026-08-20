import {
  RECENTS_CAP,
  dropRecent,
  mergeRecent,
  recentsKey,
  sanitizeRecents,
} from '@/utils/recents'

const lead = (n) => ({ doctype: 'CRM Lead', name: `CRM-LEAD-${n}` })

describe('recentsKey', () => {
  it('is scoped to the site AND the user (UX rule 2.18)', () => {
    expect(recentsKey('crm.localhost', 'ann@example.com')).toBe(
      'crm:recents:crm.localhost:ann@example.com',
    )
  })

  it('separates two users of the same browser', () => {
    expect(recentsKey('site', 'a@x.com')).not.toBe(
      recentsKey('site', 'b@x.com'),
    )
  })

  it('separates two sites for the same user', () => {
    expect(recentsKey('one', 'a@x.com')).not.toBe(recentsKey('two', 'a@x.com'))
  })

  it('never produces a key that collides on missing values', () => {
    expect(recentsKey(null, null)).toBe('crm:recents:unknown:guest')
  })
})

describe('sanitizeRecents', () => {
  it('keeps only doctype and name', () => {
    expect(
      sanitizeRecents([{ doctype: 'CRM Lead', name: 'L-1', title: 'Ada' }]),
    ).toEqual([{ doctype: 'CRM Lead', name: 'L-1' }])
  })

  it('drops junk rather than throwing', () => {
    expect(
      sanitizeRecents([null, 7, 'x', {}, { doctype: 'CRM Lead' }]),
    ).toEqual([])
    expect(sanitizeRecents('not a list')).toEqual([])
    expect(sanitizeRecents(undefined)).toEqual([])
  })

  it('deduplicates by record, keeping the first', () => {
    expect(sanitizeRecents([lead(1), lead(1), lead(2)])).toEqual([
      lead(1),
      lead(2),
    ])
  })

  it('caps the list', () => {
    const many = Array.from({ length: 30 }, (_, i) => lead(i))
    expect(sanitizeRecents(many)).toHaveLength(RECENTS_CAP)
  })
})

describe('mergeRecent', () => {
  it('puts the newest first', () => {
    expect(mergeRecent([lead(1)], lead(2))).toEqual([lead(2), lead(1)])
  })

  it('moves a revisited record back to the top instead of duplicating it', () => {
    expect(mergeRecent([lead(1), lead(2)], lead(2))).toEqual([lead(2), lead(1)])
  })

  it('never grows past the cap', () => {
    let list = []
    for (let i = 0; i < 30; i++) list = mergeRecent(list, lead(i))
    expect(list).toHaveLength(RECENTS_CAP)
    expect(list[0]).toEqual(lead(29))
  })

  it('ignores a malformed visit rather than corrupting the list', () => {
    expect(mergeRecent([lead(1)], { doctype: 'CRM Lead' })).toEqual([lead(1)])
    expect(mergeRecent([lead(1)], null)).toEqual([lead(1)])
  })

  it('repairs a corrupted stored list on the way through', () => {
    expect(mergeRecent(['junk', null, lead(1)], lead(2))).toEqual([
      lead(2),
      lead(1),
    ])
  })
})

describe('dropRecent', () => {
  it('removes one record', () => {
    expect(dropRecent([lead(1), lead(2)], lead(1))).toEqual([lead(2)])
  })

  it('leaves the list alone when the record is not in it', () => {
    expect(dropRecent([lead(1)], lead(9))).toEqual([lead(1)])
    expect(dropRecent([lead(1)], null)).toEqual([lead(1)])
  })
})
