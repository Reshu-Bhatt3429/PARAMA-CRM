import { resolveChartOptions } from '@/utils/chartOptions'
import { describe, expect, it } from 'vitest'

describe('chart option resolution', () => {
  it('resolves nested CSS variables and removes undefined overrides', () => {
    const options = resolveChartOptions(
      {
        color: undefined,
        title: { color: 'var(--ink-gray-8)' },
        series: [{ lineStyle: { color: 'var(--missing, #123456)' } }],
      },
      (name) => (name === '--ink-gray-8' ? '#16162a' : ''),
    )

    expect(options).toEqual({
      title: { color: '#16162a' },
      series: [{ lineStyle: { color: '#123456' } }],
    })
  })
})
