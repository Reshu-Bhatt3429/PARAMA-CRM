import {
  createAxisTooltipFormatter,
  createDonutTooltipFormatter,
} from '@/utils/chartTooltips'
import { describe, expect, it } from 'vitest'

describe('safe chart tooltips', () => {
  it('escapes database labels in axis tooltips', () => {
    const formatter = createAxisTooltipFormatter({ swapXY: false })
    const result = formatter([
      {
        value: ['<img src=x onerror=alert(1)>', 12],
        seriesName: '<script>alert(1)</script>',
      },
    ])

    expect(result).not.toContain('<img')
    expect(result).not.toContain('<script>')
    expect(result).toContain('&lt;img')
    expect(result).toContain('&lt;script&gt;')
  })

  it('escapes database labels in donut tooltips', () => {
    const formatter = createDonutTooltipFormatter(20)
    const result = formatter({
      name: '<svg onload=alert(1)>',
      value: ['source', 5],
    })

    expect(result).toBe('&lt;svg onload=alert(1)&gt;\n5 (25%)')
  })
})
