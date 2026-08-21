const ALLOWED_HTTP_PROTOCOLS = new Set(['http:', 'https:'])

function currentOrigin() {
  return globalThis.location?.origin || 'http://localhost'
}

/**
 * Resolve a URL while rejecting executable schemes such as javascript: and
 * data:. Relative application/file URLs are allowed and resolved against the
 * current origin. Bare website hostnames can opt into an https:// prefix.
 */
export function getSafeHttpUrl(
  rawUrl,
  { baseUrl = currentOrigin(), assumeHttps = false } = {},
) {
  if (typeof rawUrl !== 'string') return null

  const value = rawUrl.trim()
  if (!value) return null

  const hasScheme = /^[a-zA-Z][a-zA-Z\d+\-.]*:/.test(value)
  const isRelative = /^(?:\/|\.\/|\.\.\/|#)/.test(value)
  const candidate =
    assumeHttps && !hasScheme && !isRelative && !value.startsWith('//')
      ? `https://${value}`
      : value

  try {
    const parsed = new URL(candidate, baseUrl)
    return ALLOWED_HTTP_PROTOCOLS.has(parsed.protocol) ? parsed.href : null
  } catch {
    return null
  }
}

/** Open only a validated HTTP(S) URL and sever the opener relationship. */
export function openSafeUrl(
  rawUrl,
  {
    target = '_blank',
    baseUrl,
    assumeHttps = false,
    openWindow = globalThis.window?.open?.bind(globalThis.window),
  } = {},
) {
  const safeUrl = getSafeHttpUrl(rawUrl, { baseUrl, assumeHttps })
  if (!safeUrl || typeof openWindow !== 'function') return false

  const openedWindow = openWindow(safeUrl, target, 'noopener,noreferrer')
  if (openedWindow) openedWindow.opener = null
  return true
}
