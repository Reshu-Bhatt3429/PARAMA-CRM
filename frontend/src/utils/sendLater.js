/**
 * Send Later, the browser half (master spec item 5).
 *
 * The authoritative time is computed on the SERVER, in the sender's own
 * timezone (`crm.api.email.preset_datetime`). Everything here is preview: the
 * popover has to say "Tomorrow, 9:00 AM" before the user commits, and it cannot
 * ask the server for a label on every keystroke.
 *
 * That split is why `presetPreview` exists and why nothing here is ever sent to
 * the server as a time. The composer posts a preset KEY; only the custom picker
 * posts a datetime, and that one is a local wall-clock string the server reads
 * in the sender's timezone.
 *
 * This module stays free of frappe-ui imports on purpose: it is a pure helper
 * with a unit suite (`tests/unit/sendLater.test.js`) that cannot load the
 * frappe-ui bundle.
 */

export const PRESET_LATER_TODAY = 'later_today'
export const PRESET_TOMORROW_MORNING = 'tomorrow_morning'
export const PRESET_NEXT_MONDAY = 'next_monday'

/** Hours and minutes per preset. Mirrors `crm.api.email.PRESETS`. */
const PRESET_TIMES = {
  [PRESET_LATER_TODAY]: [18, 0],
  [PRESET_TOMORROW_MORNING]: [9, 0],
  [PRESET_NEXT_MONDAY]: [9, 0],
}

/**
 * The local Date one preset names, computed the same way the server does.
 *
 * "Next Monday" is strictly the NEXT one: on a Monday morning it means the day
 * in seven, not two hours ago.
 */
export function presetPreview(preset, now = new Date()) {
  const times = PRESET_TIMES[preset]
  if (!times) return null

  const [hour, minute] = times
  const at = new Date(now.getTime())
  at.setHours(hour, minute, 0, 0)

  if (preset === PRESET_TOMORROW_MORNING) {
    at.setDate(at.getDate() + 1)
  } else if (preset === PRESET_NEXT_MONDAY) {
    const weekday = now.getDay() // 0 = Sunday
    const monday = 1
    const ahead = (monday - weekday + 7) % 7 || 7
    at.setDate(at.getDate() + ahead)
  }

  return at
}

/**
 * True when a preset still has a future time today.
 *
 * "Later today" at 19:00 is not an option, and offering it so the server can
 * refuse it is a worse experience than not offering it.
 */
export function presetIsAvailable(preset, now = new Date()) {
  const at = presetPreview(preset, now)
  return Boolean(at) && at.getTime() > now.getTime()
}

/** The presets a popover should show right now, in order. */
export function availablePresets(now = new Date()) {
  return [
    PRESET_LATER_TODAY,
    PRESET_TOMORROW_MORNING,
    PRESET_NEXT_MONDAY,
  ].filter((preset) => presetIsAvailable(preset, now))
}

function pad(value) {
  return String(value).padStart(2, '0')
}

/**
 * A Date as the naive `YYYY-MM-DD HH:MM:SS` the server reads in the sender's
 * timezone. Never an ISO string with a `Z`: that would be an instant in UTC,
 * and the server would read the wall clock the user chose as a different hour.
 */
export function toServerDatetime(value) {
  const date = value instanceof Date ? value : parseLocalDatetime(value)
  if (!date) return ''
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    ` ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  )
}

/**
 * Parse what an `<input type="datetime-local">` produces, as LOCAL time.
 *
 * `new Date('2026-08-19T18:00')` is local in every engine, but
 * `new Date('2026-08-19 18:00')` is not, so the `T` is put back before parsing.
 */
export function parseLocalDatetime(value) {
  if (!value) return null
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value
  }

  const parsed = new Date(String(value).replace(' ', 'T'))
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

/**
 * The earliest value the custom picker should allow: one minute from now.
 * Returned in the `YYYY-MM-DDTHH:MM` form the input's `min` attribute takes.
 */
export function earliestPickable(now = new Date()) {
  const at = new Date(now.getTime() + 60 * 1000)
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}T${pad(
    at.getHours(),
  )}:${pad(at.getMinutes())}`
}

/** True when a chosen time is still worth sending. Mirrors the server's refusal. */
export function isFutureDatetime(value, now = new Date()) {
  const date = parseLocalDatetime(value)
  return Boolean(date) && date.getTime() > now.getTime()
}

/**
 * Whether a scheduled job may still be cancelled.
 *
 * Past `Claimed` the recipients may already be with the provider, and a button
 * that said "Cancel" would be lying about what it can do.
 */
export function canCancelJob(job = {}) {
  return ['Draft', 'Scheduled'].includes(job?.state)
}
