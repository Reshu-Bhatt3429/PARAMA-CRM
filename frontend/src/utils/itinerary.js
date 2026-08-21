/**
 * Pure helpers for the itinerary editor.
 *
 * The itinerary is data, not prose: the editor, the PDF and the WhatsApp
 * summary all render the same structure. This module owns every structural
 * change to that data -- add, remove, move, renumber, serialise -- so the Vue
 * page stays a thin layer of inputs and buttons over functions that can be
 * tested without mounting anything.
 *
 * Every function returns new arrays instead of mutating its input. That keeps
 * Vue's reactivity honest and makes each operation trivially reversible.
 *
 * The shape mirrors `crm/fcrm/doctype/crm_itinerary/crm_itinerary.py` exactly.
 * The backend rejects unknown keys, so `serializeDays` is the single place
 * allowed to decide what leaves the browser.
 */

export const TIMES_OF_DAY = ['morning', 'afternoon', 'evening']

export const TIME_OF_DAY_LABELS = {
  morning: 'Morning',
  afternoon: 'Afternoon',
  evening: 'Evening',
}

const ITEM_KEYS = [
  'title',
  'description',
  'place_name',
  'duration_hours',
  'est_cost',
  'verified',
]

export function emptyItem(title = '') {
  return {
    title,
    description: '',
    place_name: null,
    duration_hours: null,
    est_cost: null,
    // The agent decides what is real. A hand-added item is unverified until
    // they say otherwise, exactly like an AI-drafted one.
    verified: false,
  }
}

export function emptySlot(timeOfDay) {
  return { time_of_day: timeOfDay, items: [] }
}

export function emptyDay(dayNumber, title = '', summary = '') {
  return {
    day_number: dayNumber,
    title,
    summary,
    highlights: [],
    highlights_input: '',
    description: '',
    accommodation: '',
    meals: { breakfast: false, lunch: false, dinner: false },
    image: null,
    slots: TIMES_OF_DAY.map(emptySlot),
  }
}

/**
 * Fill in whatever a payload is missing so the editor can render it.
 *
 * A day from the server always has the three slots, but a half-written local
 * edit or an older document may not. Nothing here throws: the editor must be
 * able to open a broken itinerary and let the agent fix it by hand.
 */
export function normalizeDays(days) {
  if (!Array.isArray(days)) return []

  return days
    .filter((day) => day && typeof day === 'object')
    .map((day, index) => {
      const highlights = normalizeHighlights(
        Object.prototype.hasOwnProperty.call(day, 'highlights_input')
          ? day.highlights_input
          : day.highlights,
      )
      const bySlot = new Map(
        (Array.isArray(day.slots) ? day.slots : [])
          .filter((slot) => slot && TIMES_OF_DAY.includes(slot.time_of_day))
          .map((slot) => [
            slot.time_of_day,
            Array.isArray(slot.items) ? slot.items.filter(Boolean) : [],
          ]),
      )

      return {
        day_number: toInt(day.day_number, index + 1),
        title: day.title || '',
        summary: day.summary || '',
        highlights,
        highlights_input: highlights.join(', '),
        description: day.description || '',
        accommodation: day.accommodation || '',
        meals: normalizeMeals(day.meals),
        image: normalizeImage(day.image),
        slots: TIMES_OF_DAY.map((timeOfDay) => ({
          time_of_day: timeOfDay,
          items: (bySlot.get(timeOfDay) || []).map(normalizeItem),
        })),
      }
    })
    .sort((a, b) => a.day_number - b.day_number)
}

function normalizeItem(item) {
  return {
    title: item.title || '',
    description: item.description || '',
    place_name: item.place_name || null,
    duration_hours: toNumberOrNull(item.duration_hours),
    est_cost: toNumberOrNull(item.est_cost),
    verified: item.verified === true,
  }
}

/**
 * The exact payload `crm.api.itinerary.update_days` accepts.
 *
 * Number inputs hand back strings, so every numeric field is coerced here. An
 * empty string becomes null rather than 0: "no estimate" and "free" are
 * different claims to make to a customer.
 */
export function serializeDays(days) {
  return {
    days: normalizeDays(days).map((day) => ({
      day_number: day.day_number,
      title: trim(day.title),
      summary: trim(day.summary),
      highlights: normalizeHighlights(
        Object.prototype.hasOwnProperty.call(day, 'highlights_input')
          ? day.highlights_input
          : day.highlights,
      ),
      description: trim(day.description),
      accommodation: trim(day.accommodation),
      meals: normalizeMeals(day.meals),
      image: normalizeImage(day.image),
      slots: day.slots.map((slot) => ({
        time_of_day: slot.time_of_day,
        items: slot.items
          .filter((item) => trim(item.title))
          .map((item) => pickItem(item)),
      })),
    })),
  }
}

function pickItem(item) {
  const clean = {}
  for (const key of ITEM_KEYS) clean[key] = item[key]

  clean.title = trim(clean.title)
  clean.description = trim(clean.description)
  clean.place_name = trim(clean.place_name) || null
  clean.duration_hours = toNumberOrNull(clean.duration_hours)
  clean.est_cost = toNumberOrNull(clean.est_cost)
  clean.verified = clean.verified === true
  return clean
}

// --- structural edits ------------------------------------------------------

export function addItem(days, dayNumber, timeOfDay, title = '') {
  return mapSlot(days, dayNumber, timeOfDay, (items) => [
    ...items,
    emptyItem(title),
  ])
}

export function removeItem(days, dayNumber, timeOfDay, index) {
  return mapSlot(days, dayNumber, timeOfDay, (items) =>
    items.filter((_, position) => position !== index),
  )
}

/**
 * Move one item within its slot. `offset` is -1 for up and 1 for down.
 * A move off either end is a no-op, so the buttons never need disabling logic
 * of their own.
 */
export function moveItem(days, dayNumber, timeOfDay, index, offset) {
  return mapSlot(days, dayNumber, timeOfDay, (items) => {
    const target = index + offset
    if (index < 0 || index >= items.length) return items
    if (target < 0 || target >= items.length) return items

    const moved = [...items]
    ;[moved[index], moved[target]] = [moved[target], moved[index]]
    return moved
  })
}

/**
 * Flip an item's verified flag. This is the agent confirming a place the AI
 * invented is a real place -- the one claim no model is allowed to make.
 */
export function toggleVerified(days, dayNumber, timeOfDay, index) {
  return mapSlot(days, dayNumber, timeOfDay, (items) =>
    items.map((item, position) =>
      position === index ? { ...item, verified: !item.verified } : item,
    ),
  )
}

export function addDay(days) {
  const normalized = normalizeDays(days)
  return renumberDays([...normalized, emptyDay(normalized.length + 1)])
}

export function removeDay(days, dayNumber) {
  return renumberDays(
    normalizeDays(days).filter((day) => day.day_number !== dayNumber),
  )
}

export function moveDay(days, dayNumber, offset) {
  const normalized = normalizeDays(days)
  const index = normalized.findIndex((day) => day.day_number === dayNumber)
  const target = index + offset
  if (index < 0 || target < 0 || target >= normalized.length) return normalized

  const moved = [...normalized]
  ;[moved[index], moved[target]] = [moved[target], moved[index]]
  return renumberDays(moved)
}

/**
 * Make the day numbers 1..n again after an insert or a delete.
 * A gap in the numbering is not invalid, but it reads as a mistake in the PDF.
 */
export function renumberDays(days) {
  return (Array.isArray(days) ? days : [])
    .map((day) => normalizeDays([day])[0])
    .filter(Boolean)
    .map((day, index) => ({ ...day, day_number: index + 1 }))
}

function mapSlot(days, dayNumber, timeOfDay, transform) {
  return normalizeDays(days).map((day) =>
    day.day_number !== dayNumber
      ? day
      : {
          ...day,
          slots: day.slots.map((slot) =>
            slot.time_of_day !== timeOfDay
              ? slot
              : { ...slot, items: transform(slot.items) },
          ),
        },
  )
}

// --- read-only questions the page asks -------------------------------------

export function countItems(days) {
  return normalizeDays(days).reduce(
    (total, day) =>
      total + day.slots.reduce((sum, slot) => sum + slot.items.length, 0),
    0,
  )
}

export function isDayEmpty(day) {
  if (!day || !Array.isArray(day.slots)) return true
  const hasScheduledItems = day.slots.some((slot) => slot.items?.length)
  const meals = normalizeMeals(day.meals)
  const hasProposalContent = Boolean(
    trim(day.description) ||
    trim(day.accommodation) ||
    normalizeHighlights(day.highlights).length ||
    normalizeImage(day.image) ||
    Object.values(meals).some(Boolean),
  )
  return !hasScheduledItems && !hasProposalContent
}

/** Day numbers with no items yet, so "Generate all days" can skip the rest. */
export function emptyDayNumbers(days) {
  return normalizeDays(days)
    .filter(isDayEmpty)
    .map((day) => day.day_number)
}

/** How many AI-named places still need a human to confirm them. */
export function countUnverifiedPlaces(days) {
  return normalizeDays(days).reduce(
    (total, day) =>
      total +
      day.slots.reduce(
        (sum, slot) =>
          sum +
          slot.items.filter((item) => item.place_name && !item.verified).length,
        0,
      ),
    0,
  )
}

// --- coercion --------------------------------------------------------------

function trim(value) {
  return typeof value === 'string' ? value.trim() : value ? String(value) : ''
}

export function normalizeHighlights(value) {
  const values = Array.isArray(value)
    ? value
    : typeof value === 'string'
      ? value.split(',')
      : []
  return values.map(trim).filter(Boolean).slice(0, 8)
}

export function normalizeMeals(value) {
  const meals = value && typeof value === 'object' ? value : {}
  return {
    breakfast: meals.breakfast === true,
    lunch: meals.lunch === true,
    dinner: meals.dinner === true,
  }
}

function normalizeImage(value) {
  const image = trim(value)
  return image || null
}

export function linesToArray(value) {
  return String(value || '')
    .split(/\r?\n/)
    .map(trim)
    .filter(Boolean)
}

export function arrayToLines(value) {
  return (Array.isArray(value) ? value : [])
    .map(trim)
    .filter(Boolean)
    .join('\n')
}

export function generatedDayArtwork(
  title,
  destination,
  index = 0,
  theme = 'Sunrise',
) {
  const palettes = {
    Sunrise: ['#0f172a', '#f97316', '#fbbf24', '#fff7ed'],
    Midnight: ['#07111f', '#2563eb', '#38bdf8', '#eff6ff'],
    Meadow: ['#16352b', '#15803d', '#84cc16', '#f7fee7'],
  }
  const [background, ridge, sun, ink] = palettes[theme] || palettes.Sunrise
  const safeTitle = escapeXml((trim(title) || `Day ${index + 1}`).slice(0, 42))
  const safeDestination = escapeXml(trim(destination) || 'Journey')
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 720">
  <rect width="1200" height="720" fill="${background}"/>
  <circle cx="910" cy="168" r="92" fill="${sun}" opacity=".92"/>
  <path d="M0 620 258 280 468 520 650 212 930 580 1080 388 1200 570V720H0Z" fill="${ridge}" opacity=".78"/>
  <path d="m554 338 96-126 105 217-106-97-58 66Z" fill="${ink}" opacity=".88"/>
  <rect x="72" y="72" width="1056" height="576" rx="28" fill="none" stroke="${ink}" stroke-opacity=".24" stroke-width="2"/>
  <text x="84" y="530" fill="${ink}" font-family="Arial, sans-serif" font-size="40" font-weight="700">${safeTitle}</text>
  <text x="87" y="584" fill="${ink}" opacity=".78" font-family="Arial, sans-serif" font-size="24" letter-spacing="4">${safeDestination.toUpperCase()}</text>
</svg>`
  return `data:image/svg+xml;base64,${encodeBase64(svg)}`
}

export function buildDemoItinerary(agency = {}) {
  const destination = 'Ladakh, India'
  const theme = 'Sunrise'
  const rawDays = [
    {
      title: 'Arrival in Leh and gentle acclimatisation',
      summary: 'Land, settle in, and keep the first day intentionally light.',
      highlights: ['Airport welcome', 'Old Leh walk', 'Sunset viewpoint'],
      description:
        'Meet your driver at Leh airport and transfer to the hotel. After a long rest, take an easy orientation walk through the old town and finish with a quiet sunset view.',
      accommodation: 'Boutique hotel in Leh',
      meals: { breakfast: false, lunch: true, dinner: true },
    },
    {
      title: 'Monasteries and the Indus Valley',
      summary:
        'A culture-rich loop through the valley without rushing the altitude.',
      highlights: ['Thiksey Monastery', 'Shey Palace', 'Indus Valley'],
      description:
        'Explore Thiksey at morning prayer time, continue to Shey Palace, and pause for a relaxed lunch beside the Indus before returning to Leh.',
      accommodation: 'Boutique hotel in Leh',
      meals: { breakfast: true, lunch: true, dinner: true },
    },
    {
      title: 'Khardung La and Nubra Valley',
      summary:
        'Cross the high pass and descend into Nubra’s broad desert valley.',
      highlights: ['Khardung La', 'Diskit Monastery', 'Hunder dunes'],
      description:
        'Drive over Khardung La with weather and road checks built into the schedule. Visit Diskit Monastery and reach Hunder in time for the soft evening light.',
      accommodation: 'Garden camp in Hunder',
      meals: { breakfast: true, lunch: true, dinner: true },
    },
  ]

  const days = rawDays.map((value, index) => ({
    ...emptyDay(index + 1, value.title, value.summary),
    ...value,
    highlights_input: value.highlights.join(', '),
    image: generatedDayArtwork(value.title, destination, index, theme),
  }))

  return {
    details: {
      title: 'Ladakh Alpine Circuit',
      subtitle: 'High passes, living monasteries, and room to breathe',
      customer_name: 'Sample Traveller',
      destination,
      start_date: '',
      num_days: days.length,
      duration_label: '3 Days / 2 Nights',
      departure_type: 'Group Departure',
      group_size: 8,
      group_size_label: 'Min 4 - Max 8 travellers',
      budget: 0,
      currency: 'INR',
      // Cover/logo fields are Frappe Attach Image values (short file URLs),
      // while day images live inside JSON and can safely hold generated SVG.
      cover_image: '',
      brand_logo: agency.logo || '',
      theme,
      font_preset: 'Modern Alpine',
      title_weight: '900',
      title_style: 'Normal',
      tagline_style: 'Bold Normal',
      title_case: 'Uppercase',
      contact_email: agency.email || '',
      contact_phone: agency.phone || '',
      contact_website: agency.website || '',
      trip_vibe: 'Adventure',
      ai_instructions: '',
      inclusions:
        'Private airport transfers\nAccommodation and listed meals\nLocal guide and permits',
      exclusions:
        'Flights to and from Leh\nPersonal expenses\nTravel insurance',
      terms:
        'Route timings remain subject to weather and local road conditions.\nA confirmed booking requires the agency’s stated advance payment.',
      internal_notes: '',
      price_tiers: [
        { tier_label: 'Double sharing', price_per_person: 28500 },
        { tier_label: 'Single room', price_per_person: 34900 },
      ],
    },
    days,
  }
}

function encodeBase64(value) {
  const bytes = new TextEncoder().encode(value)
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return globalThis.btoa(binary)
}

function escapeXml(value) {
  return value.replace(
    /[&<>"']/g,
    (character) =>
      ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&apos;',
      })[character],
  )
}

function toInt(value, fallback) {
  const number = Number.parseInt(value, 10)
  return Number.isFinite(number) && number > 0 ? number : fallback
}

function toNumberOrNull(value) {
  if (value === null || value === undefined || value === '') return null
  const number = Number(value)
  if (!Number.isFinite(number) || number < 0) return null
  return number
}
