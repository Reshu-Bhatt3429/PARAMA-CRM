const CSS_VARIABLE_RE = /var\((--[^,)]+)(?:,\s*([^)]+))?\)/g

/**
 * Clone ECharts options while resolving CSS custom properties for canvas.
 * Canvas APIs do not understand `var(--token)`, unlike SVG attributes.
 */
export function resolveChartOptions(value, getVariable) {
  if (Array.isArray(value)) {
    return value.map((item) => resolveChartOptions(item, getVariable))
  }

  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, item]) => item !== undefined)
        .map(([key, item]) => [key, resolveChartOptions(item, getVariable)]),
    )
  }

  if (typeof value !== 'string') return value

  return value.replace(CSS_VARIABLE_RE, (_match, name, fallback = '') => {
    return getVariable(name) || fallback.trim()
  })
}
