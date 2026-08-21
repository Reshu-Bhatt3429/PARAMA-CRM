import {
  TIMES_OF_DAY,
  addDay,
  addItem,
  countItems,
  countUnverifiedPlaces,
  buildDemoItinerary,
  emptyDay,
  emptyDayNumbers,
  emptyItem,
  isDayEmpty,
  linesToArray,
  moveDay,
  moveItem,
  normalizeHighlights,
  normalizeDays,
  removeDay,
  removeItem,
  renumberDays,
  serializeDays,
  toggleVerified,
} from '@/utils/itinerary'

function dayWithItems(dayNumber, titles) {
  const day = emptyDay(dayNumber, `Day ${dayNumber}`, 'A summary.')
  day.slots[0].items = titles.map((title) => emptyItem(title))
  return day
}

describe('emptyDay', () => {
  it('always has the three slots in order', () => {
    const day = emptyDay(3, 'Arrival', 'Land and settle in.')
    expect(day.day_number).toBe(3)
    expect(day.title).toBe('Arrival')
    expect(day.summary).toBe('Land and settle in.')
    expect(day.slots.map((slot) => slot.time_of_day)).toEqual(TIMES_OF_DAY)
    expect(day.slots.every((slot) => slot.items.length === 0)).toBe(true)
  })
})

describe('emptyItem', () => {
  it('is never verified', () => {
    expect(emptyItem('Beach walk')).toEqual({
      title: 'Beach walk',
      description: '',
      place_name: null,
      duration_hours: null,
      est_cost: null,
      verified: false,
    })
  })
})

describe('normalizeDays', () => {
  it('returns an empty list for anything that is not an array', () => {
    expect(normalizeDays(null)).toEqual([])
    expect(normalizeDays(undefined)).toEqual([])
    expect(normalizeDays('days')).toEqual([])
  })

  it('fills in missing slots', () => {
    const [day] = normalizeDays([{ day_number: 1, title: 'Arrival' }])
    expect(day.slots.map((slot) => slot.time_of_day)).toEqual(TIMES_OF_DAY)
    expect(day.summary).toBe('')
  })

  it('drops a slot with an unknown time of day', () => {
    const [day] = normalizeDays([
      {
        day_number: 1,
        slots: [
          { time_of_day: 'midnight', items: [emptyItem('Ghost tour')] },
          { time_of_day: 'morning', items: [emptyItem('Breakfast')] },
        ],
      },
    ])
    expect(countItems([day])).toBe(1)
    expect(day.slots[0].items[0].title).toBe('Breakfast')
  })

  it('puts the slots back in morning-afternoon-evening order', () => {
    const [day] = normalizeDays([
      {
        day_number: 1,
        slots: [
          { time_of_day: 'evening', items: [] },
          { time_of_day: 'morning', items: [] },
        ],
      },
    ])
    expect(day.slots.map((slot) => slot.time_of_day)).toEqual(TIMES_OF_DAY)
  })

  it('sorts the days by number', () => {
    const days = normalizeDays([emptyDay(3), emptyDay(1), emptyDay(2)])
    expect(days.map((day) => day.day_number)).toEqual([1, 2, 3])
  })

  it('numbers a day that has no number', () => {
    const days = normalizeDays([{ title: 'One' }, { title: 'Two' }])
    expect(days.map((day) => day.day_number)).toEqual([1, 2])
  })

  it('treats anything but true as unverified', () => {
    const [day] = normalizeDays([
      {
        day_number: 1,
        slots: [
          {
            time_of_day: 'morning',
            items: [
              { title: 'A', verified: 'true' },
              { title: 'B', verified: 1 },
              { title: 'C', verified: true },
            ],
          },
        ],
      },
    ])
    expect(day.slots[0].items.map((item) => item.verified)).toEqual([
      false,
      false,
      true,
    ])
  })
})

describe('serializeDays', () => {
  it('emits only the keys the backend accepts', () => {
    const day = dayWithItems(1, ['Sunrise hike'])
    day.slots[0].items[0].expanded = true
    day.collapsed = false

    const payload = serializeDays([day])
    expect(Object.keys(payload)).toEqual(['days'])
    expect(Object.keys(payload.days[0]).sort()).toEqual([
      'accommodation',
      'day_number',
      'description',
      'highlights',
      'image',
      'meals',
      'slots',
      'summary',
      'title',
    ])
    expect(Object.keys(payload.days[0].slots[0].items[0]).sort()).toEqual([
      'description',
      'duration_hours',
      'est_cost',
      'place_name',
      'title',
      'verified',
    ])
  })

  it('turns the strings a number input produces into numbers', () => {
    const day = dayWithItems(1, ['Diving'])
    day.slots[0].items[0].est_cost = '2500'
    day.slots[0].items[0].duration_hours = '3.5'

    const item = serializeDays([day]).days[0].slots[0].items[0]
    expect(item.est_cost).toBe(2500)
    expect(item.duration_hours).toBe(3.5)
  })

  it('turns a blank cost into null, not zero', () => {
    const day = dayWithItems(1, ['Free walking tour'])
    day.slots[0].items[0].est_cost = ''

    expect(serializeDays([day]).days[0].slots[0].items[0].est_cost).toBeNull()
  })

  it('rejects a negative or unreadable number', () => {
    const day = dayWithItems(1, ['Diving'])
    day.slots[0].items[0].est_cost = '-40'
    day.slots[0].items[0].duration_hours = 'about two'

    const item = serializeDays([day]).days[0].slots[0].items[0]
    expect(item.est_cost).toBeNull()
    expect(item.duration_hours).toBeNull()
  })

  it('turns a blank place name into null', () => {
    const day = dayWithItems(1, ['Rest'])
    day.slots[0].items[0].place_name = '   '
    expect(serializeDays([day]).days[0].slots[0].items[0].place_name).toBeNull()
  })

  it('drops an item the agent left without a title', () => {
    const day = dayWithItems(1, ['Real item', '   '])
    const items = serializeDays([day]).days[0].slots[0].items
    expect(items).toHaveLength(1)
    expect(items[0].title).toBe('Real item')
  })

  it('trims the text the agent typed', () => {
    const day = dayWithItems(1, ['  Museum visit  '])
    day.title = '  Day one  '
    expect(serializeDays([day]).days[0].title).toBe('Day one')
    expect(serializeDays([day]).days[0].slots[0].items[0].title).toBe(
      'Museum visit',
    )
  })

  it('serializes highlights, meals, accommodation, narrative, and image', () => {
    const day = emptyDay(1)
    day.highlights_input = '  Monastery, Valley, Monastery '
    day.description = '  A measured day in the valley. '
    day.accommodation = ' Leh hotel '
    day.meals = { breakfast: true, lunch: false, dinner: true }
    day.image = 'https://example.com/leh.jpg'

    const saved = serializeDays([day]).days[0]
    expect(saved.highlights).toEqual(['Monastery', 'Valley', 'Monastery'])
    expect(saved.description).toBe('A measured day in the valley.')
    expect(saved.accommodation).toBe('Leh hotel')
    expect(saved.meals).toEqual({ breakfast: true, lunch: false, dinner: true })
    expect(saved.image).toBe('https://example.com/leh.jpg')
  })
})

describe('proposal helpers', () => {
  it('normalizes comma-separated highlights and caps them at eight', () => {
    expect(normalizeHighlights('A, B, , C')).toEqual(['A', 'B', 'C'])
    expect(normalizeHighlights('1,2,3,4,5,6,7,8,9')).toHaveLength(8)
  })

  it('splits customer lists on lines', () => {
    expect(linesToArray('Transfer\n\n Breakfast ')).toEqual([
      'Transfer',
      'Breakfast',
    ])
  })

  it('moves and renumbers complete days', () => {
    const days = [emptyDay(1, 'A'), emptyDay(2, 'B'), emptyDay(3, 'C')]
    expect(
      moveDay(days, 2, -1).map((day) => [day.day_number, day.title]),
    ).toEqual([
      [1, 'B'],
      [2, 'A'],
      [3, 'C'],
    ])
  })

  it('builds a complete sample without putting a data URL in an Attach Image field', () => {
    const demo = buildDemoItinerary({ name: 'Travel Desk' })
    expect(demo.details.cover_image).toBe('')
    expect(demo.days).toHaveLength(3)
    expect(demo.days[0].image).toMatch(/^data:image\/svg\+xml;base64,/)
    expect(demo.details.price_tiers.length).toBeGreaterThan(0)
  })
})

describe('moveItem', () => {
  const days = [dayWithItems(1, ['A', 'B', 'C'])]

  function titles(result) {
    return result[0].slots[0].items.map((item) => item.title)
  }

  it('moves an item up', () => {
    expect(titles(moveItem(days, 1, 'morning', 1, -1))).toEqual(['B', 'A', 'C'])
  })

  it('moves an item down', () => {
    expect(titles(moveItem(days, 1, 'morning', 1, 1))).toEqual(['A', 'C', 'B'])
  })

  it('does nothing at the top', () => {
    expect(titles(moveItem(days, 1, 'morning', 0, -1))).toEqual(['A', 'B', 'C'])
  })

  it('does nothing at the bottom', () => {
    expect(titles(moveItem(days, 1, 'morning', 2, 1))).toEqual(['A', 'B', 'C'])
  })

  it('ignores an index that does not exist', () => {
    expect(titles(moveItem(days, 1, 'morning', 9, -1))).toEqual(['A', 'B', 'C'])
  })

  it('leaves the original list untouched', () => {
    moveItem(days, 1, 'morning', 0, 1)
    expect(titles(days)).toEqual(['A', 'B', 'C'])
  })

  it('touches only the named day and slot', () => {
    const twoDays = [dayWithItems(1, ['A', 'B']), dayWithItems(2, ['X', 'Y'])]
    const result = moveItem(twoDays, 2, 'morning', 0, 1)
    expect(result[0].slots[0].items.map((item) => item.title)).toEqual([
      'A',
      'B',
    ])
    expect(result[1].slots[0].items.map((item) => item.title)).toEqual([
      'Y',
      'X',
    ])
  })
})

describe('addItem and removeItem', () => {
  it('appends an empty unverified item', () => {
    const result = addItem([emptyDay(1)], 1, 'afternoon', 'Spa')
    expect(result[0].slots[1].items).toHaveLength(1)
    expect(result[0].slots[1].items[0].title).toBe('Spa')
    expect(result[0].slots[1].items[0].verified).toBe(false)
  })

  it('removes the item at the given index', () => {
    const result = removeItem(
      [dayWithItems(1, ['A', 'B', 'C'])],
      1,
      'morning',
      1,
    )
    expect(result[0].slots[0].items.map((item) => item.title)).toEqual([
      'A',
      'C',
    ])
  })
})

describe('addDay and removeDay', () => {
  it('appends a numbered empty day', () => {
    const result = addDay([emptyDay(1), emptyDay(2)])
    expect(result.map((day) => day.day_number)).toEqual([1, 2, 3])
    expect(isDayEmpty(result[2])).toBe(true)
  })

  it('renumbers the days that are left after a delete', () => {
    const days = [
      dayWithItems(1, ['A']),
      dayWithItems(2, ['B']),
      dayWithItems(3, ['C']),
    ]
    const result = removeDay(days, 2)

    expect(result.map((day) => day.day_number)).toEqual([1, 2])
    // Day 3's content moved up into slot 2; nothing was lost.
    expect(result[1].slots[0].items[0].title).toBe('C')
  })

  it('does nothing when the day is not there', () => {
    expect(removeDay([emptyDay(1)], 7).map((day) => day.day_number)).toEqual([
      1,
    ])
  })
})

describe('renumberDays', () => {
  it('closes a gap in the numbering', () => {
    const result = renumberDays([emptyDay(2), emptyDay(5), emptyDay(9)])
    expect(result.map((day) => day.day_number)).toEqual([1, 2, 3])
  })
})

describe('toggleVerified', () => {
  it('flips one item and leaves the rest alone', () => {
    const days = [dayWithItems(1, ['A', 'B'])]
    const result = toggleVerified(days, 1, 'morning', 0)

    expect(result[0].slots[0].items[0].verified).toBe(true)
    expect(result[0].slots[0].items[1].verified).toBe(false)
  })

  it('flips back', () => {
    let days = [dayWithItems(1, ['A'])]
    days = toggleVerified(days, 1, 'morning', 0)
    days = toggleVerified(days, 1, 'morning', 0)
    expect(days[0].slots[0].items[0].verified).toBe(false)
  })
})

describe('counting helpers', () => {
  const days = [
    dayWithItems(1, ['A', 'B']),
    emptyDay(2),
    dayWithItems(3, ['C']),
  ]

  it('counts every item across every slot', () => {
    expect(countItems(days)).toBe(3)
    expect(countItems([])).toBe(0)
  })

  it('reports which days still need generating', () => {
    expect(emptyDayNumbers(days)).toEqual([2])
  })

  it('says an empty day is empty', () => {
    expect(isDayEmpty(emptyDay(1))).toBe(true)
    expect(isDayEmpty(dayWithItems(1, ['A']))).toBe(false)
    expect(isDayEmpty(null)).toBe(true)
  })

  it('counts the places a human still has to confirm', () => {
    const day = dayWithItems(1, ['A', 'B', 'C'])
    day.slots[0].items[0].place_name = 'Real Beach'
    day.slots[0].items[1].place_name = 'Checked Beach'
    day.slots[0].items[1].verified = true
    // No place name at all: nothing to confirm.
    expect(countUnverifiedPlaces([day])).toBe(1)
  })
})
