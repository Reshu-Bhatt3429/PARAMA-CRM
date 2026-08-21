import { getSafeHttpUrl, openSafeUrl } from '@/utils/safeUrl'
import { describe, expect, it, vi } from 'vitest'

const BASE_URL = 'https://crm.example.com/crm'

describe('getSafeHttpUrl', () => {
  it('allows HTTPS and relative application URLs', () => {
    expect(getSafeHttpUrl('https://files.example.com/a.pdf')).toBe(
      'https://files.example.com/a.pdf',
    )
    expect(getSafeHttpUrl('/private/files/a.pdf', { baseUrl: BASE_URL })).toBe(
      'https://crm.example.com/private/files/a.pdf',
    )
  })

  it.each([
    'javascript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'file:///etc/passwd',
  ])('rejects the executable or local scheme %s', (url) =>
    expect(getSafeHttpUrl(url, { baseUrl: BASE_URL })).toBeNull(),
  )

  it('adds HTTPS only for a bare website hostname', () => {
    expect(
      getSafeHttpUrl('example.com/path', {
        baseUrl: BASE_URL,
        assumeHttps: true,
      }),
    ).toBe('https://example.com/path')
  })
})

describe('openSafeUrl', () => {
  it('opens a safe URL without an opener', () => {
    const opened = { opener: {} }
    const openWindow = vi.fn(() => opened)

    expect(
      openSafeUrl('/private/files/a.pdf', {
        baseUrl: BASE_URL,
        openWindow,
      }),
    ).toBe(true)
    expect(openWindow).toHaveBeenCalledWith(
      'https://crm.example.com/private/files/a.pdf',
      '_blank',
      'noopener,noreferrer',
    )
    expect(opened.opener).toBeNull()
  })

  it('does not open a rejected URL', () => {
    const openWindow = vi.fn()
    expect(openSafeUrl('javascript:alert(1)', { openWindow })).toBe(false)
    expect(openWindow).not.toHaveBeenCalled()
  })
})
