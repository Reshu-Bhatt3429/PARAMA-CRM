import { meterView } from '@/utils/targetMeter'

describe('meterView', () => {
  it('reports the plain case', () => {
    const view = meterView({ value: 250000, target: 1000000, percent: 25 })
    expect(view.hasTarget).toBe(true)
    expect(view.percent).toBe(25)
    expect(view.barPercent).toBe(25)
    expect(view.isOver).toBe(false)
    expect(view.overPercent).toBe(0)
  })

  it('derives the percentage when the server did not send one', () => {
    const view = meterView({ value: 300000, target: 1200000 })
    expect(view.percent).toBe(25)
    expect(view.barPercent).toBe(25)
  })

  it('prefers the percentage the server computed', () => {
    // The server rounds to one decimal; recomputing here would show 33.3 next
    // to a stored 33.4 and the two numbers would argue in public.
    const view = meterView({ value: 1, target: 3, percent: 33.4 })
    expect(view.percent).toBe(33.4)
  })

  it('caps the bar at 100% and reports the overshoot separately (§2.8)', () => {
    const view = meterView({ value: 2400000, target: 1000000, percent: 240 })
    expect(view.percent).toBe(240)
    expect(view.barPercent).toBe(100)
    expect(view.isOver).toBe(true)
    expect(view.overPercent).toBe(140)
  })

  it('treats exactly on target as not over', () => {
    const view = meterView({ value: 1000, target: 1000, percent: 100 })
    expect(view.isOver).toBe(false)
    expect(view.barPercent).toBe(100)
  })

  it('says there is no target rather than reporting 0%', () => {
    const view = meterView({ value: 500000, target: 0 })
    expect(view.hasTarget).toBe(false)
    expect(view.percent).toBe(0)
    expect(view.barPercent).toBe(0)
    expect(view.isOver).toBe(false)
  })

  it('does not divide by a missing or negative target', () => {
    expect(meterView({ value: 10 }).hasTarget).toBe(false)
    expect(meterView({ value: 10, target: -5 }).hasTarget).toBe(false)
    expect(meterView().percent).toBe(0)
  })

  it('draws a negative total as an empty track, not backwards', () => {
    const view = meterView({ value: -100, target: 1000, percent: -10 })
    expect(view.barPercent).toBe(0)
    expect(view.isOver).toBe(false)
  })

  it('accepts the numeric strings a JSON payload can carry', () => {
    const view = meterView({ value: '500', target: '1000' })
    expect(view.achieved).toBe(500)
    expect(view.percent).toBe(50)
  })

  it('ignores an unusable percentage from the server', () => {
    const view = meterView({ value: 500, target: 1000, percent: 'lots' })
    expect(view.percent).toBe(50)
  })
})
