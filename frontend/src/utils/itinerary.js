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

/**
 * Make the day numbers 1..n again after an insert or a delete.
 * A gap in the numbering is not invalid, but it reads as a mistake in the PDF.
 */
export function renumberDays(days) {
  return normalizeDays(days).map((day, index) => ({
    ...day,
    day_number: index + 1,
  }))
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
  return day.slots.every((slot) => !slot.items || slot.items.length === 0)
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
