/**
 * Pure view maths for the dashboard Target Meter (master spec §5, item 7).
 *
 * It lives here, and not in the component, for the reason every other Stage-2/3
 * helper does: there are no component tests in this repo, so the decisions worth
 * testing have to be reachable without mounting anything.
 *
 * Three decisions are encoded here and each one is a rule from the spec:
 *
 * * **No target set is not 0%.** A site that never entered a target must read
 *   "no target set", not "0% of 0". `hasTarget` is what the component branches
 *   on; `percent` stays 0 so nothing downstream divides by zero.
 * * **The bar caps at 100%.** UX §2.8 asks for one thin bar, and a bar that
 *   renders 240% either overflows its track or silently rescales the axis. The
 *   overshoot is reported as a separate "+N% over" badge instead.
 * * **The server's percentage wins when it sent one.** The backend already
 *   computed achieved/target in base currency; recomputing it here would give
 *   the reader two numbers that can disagree after a rounding change.
 */

function toNumber(value) {
  const number = typeof value === 'number' ? value : parseFloat(value)
  return Number.isFinite(number) ? number : 0
}

export function meterView(config = {}) {
  const achieved = toNumber(config?.value)
  const target = toNumber(config?.target)
  const hasTarget = target > 0

  const serverPercent =
    typeof config?.percent === 'number' && Number.isFinite(config.percent)
      ? config.percent
      : null

  let percent = 0
  if (hasTarget) {
    percent =
      serverPercent === null
        ? Math.round((achieved / target) * 1000) / 10
        : serverPercent
  }

  // A negative achieved total (an adjusted deal value) is a full track, not a
  // bar drawn backwards off the left edge.
  const barPercent = hasTarget ? Math.min(100, Math.max(0, percent)) : 0
  const isOver = hasTarget && percent > 100

  return {
    hasTarget,
    achieved,
    target,
    percent,
    barPercent,
    isOver,
    overPercent: isOver ? Math.round(percent - 100) : 0,
  }
}
