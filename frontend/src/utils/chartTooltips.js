function escapeTooltipText(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

function formatChartValue(value, precision = 0, shorten = false) {
  const number = Number(value)
  if (!Number.isFinite(number)) return escapeTooltipText(value)

  const decimalPlaces =
    precision || Math.min(String(number).split('.')[1]?.length || 0, 2)

  return new Intl.NumberFormat('en-US', {
    notation: shorten ? 'compact' : 'standard',
    minimumFractionDigits: shorten ? 0 : decimalPlaces,
    maximumFractionDigits: shorten ? precision : decimalPlaces,
  }).format(number)
}

function formatSeriesName(value) {
  return escapeTooltipText(value)
    .split('_')
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

export function createAxisTooltipFormatter(config) {
  return (input) => {
    const params = (Array.isArray(input) ? input : [input])
      .filter(Boolean)
      .filter((entry) => entry.value?.[1] !== 0)
      .sort((a, b) => Number(b.value?.[1]) - Number(a.value?.[1]))

    if (!params.length) return ''

    const xIndex = config.swapXY ? 1 : 0
    const yIndex = config.swapXY ? 0 : 1
    const lines = [
      escapeTooltipText(params[0].value?.[xIndex] ?? params[0].name),
    ]

    for (const entry of params) {
      lines.push(
        `${formatSeriesName(entry.seriesName)}: ${formatChartValue(entry.value?.[yIndex])}`,
      )
    }

    return lines.join('\n')
  }
}

export function createDonutTooltipFormatter(total) {
  return (params) => {
    const value = params?.value?.[1] ?? params?.value
    const percentage = total > 0 ? (Number(value) / total) * 100 : 0

    return [
      escapeTooltipText(params?.name),
      `${formatChartValue(value, 1, true)} (${percentage.toFixed(0)}%)`,
    ].join('\n')
  }
}
