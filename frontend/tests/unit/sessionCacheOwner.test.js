import { beforeEach, describe, expect, it, vi } from 'vitest'

// session.js drags in the router and the whole util barrel (which imports Vue
// components and virtual icon modules). Only the cache-owner guard is under
// test here, so both are stubbed out.
const clearCache = vi.fn(() => Promise.resolve())
vi.mock('@/utils', () => ({ clearCache }))
vi.mock('@/router', () => ({ default: { replace: vi.fn() } }))
// frappe-ui ships untranspiled TS with extensionless imports that vitest
// cannot resolve; the guard under test never touches a resource.
vi.mock('frappe-ui', () => ({
  createResource: (options) => ({ ...options, reset: () => {} }),
}))

// Neither happy-dom nor the Node built-in gives a complete Storage here, so the
// test owns one. It is installed before the import so the module under test
// closes over this object.
const store = new Map()
globalThis.localStorage = {
  getItem: (key) => (store.has(key) ? store.get(key) : null),
  setItem: (key, value) => store.set(key, String(value)),
  removeItem: (key) => store.delete(key),
  clear: () => store.clear(),
}

const { ensureCacheBelongsToSessionUser } = await import('@/stores/session')

const OWNER_KEY = 'crm_last_user'

function setSessionUser(user) {
  // happy-dom keeps document.cookie, so overwrite rather than append.
  document.cookie = `user_id=${user}; path=/`
}

describe('ensureCacheBelongsToSessionUser', () => {
  beforeEach(() => {
    clearCache.mockClear()
    localStorage.clear()
    setSessionUser('')
  })

  it('keeps the cache when the same user returns', async () => {
    localStorage.setItem(OWNER_KEY, 'ada@example.com')
    setSessionUser('ada@example.com')
    await ensureCacheBelongsToSessionUser()

    expect(clearCache).not.toHaveBeenCalled()
    expect(localStorage.getItem(OWNER_KEY)).toBe('ada@example.com')
  })

  it('clears the cache when another user takes over the browser', async () => {
    localStorage.setItem(OWNER_KEY, 'ada@example.com')
    setSessionUser('grace@example.com')
    await ensureCacheBelongsToSessionUser()

    expect(clearCache).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem(OWNER_KEY)).toBe('grace@example.com')
  })

  // The state of every browser that ran a build without this guard: it holds a
  // populated cache and no owner record. Treating that as a safe first run
  // would skip exactly the machines the guard exists for.
  it('treats a missing owner record as foreign and clears', async () => {
    setSessionUser('ada@example.com')
    await ensureCacheBelongsToSessionUser()

    expect(clearCache).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem(OWNER_KEY)).toBe('ada@example.com')
  })

  // Dropping the key without clearing would leave a populated cache that looks
  // like a first run to whoever logs in next, disarming the guard.
  it('clears and drops the key on a logged-out boot', async () => {
    localStorage.setItem(OWNER_KEY, 'ada@example.com')
    setSessionUser('')
    await ensureCacheBelongsToSessionUser()

    expect(clearCache).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem(OWNER_KEY)).toBeNull()
  })

  it('treats a Guest cookie as logged out', async () => {
    localStorage.setItem(OWNER_KEY, 'ada@example.com')
    setSessionUser('Guest')
    await ensureCacheBelongsToSessionUser()

    expect(clearCache).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem(OWNER_KEY)).toBeNull()
  })

  // The owner record must NOT be stamped on a clear that did not complete.
  // Stamping would mark the previous user's cache as owned by the new one, and
  // every later boot would then see a matching owner and skip the clear —
  // making the leak permanent instead of retried.
  it('does not hang, and does not claim ownership, when the clear never settles', async () => {
    vi.useFakeTimers()
    clearCache.mockImplementationOnce(() => new Promise(() => {}))
    localStorage.setItem(OWNER_KEY, 'ada@example.com')
    setSessionUser('grace@example.com')

    const pending = ensureCacheBelongsToSessionUser()
    await vi.advanceTimersByTimeAsync(3000)
    await pending

    expect(localStorage.getItem(OWNER_KEY)).toBe('ada@example.com')
    vi.useRealTimers()
  })

  it('does not claim ownership when the clear rejects', async () => {
    clearCache.mockImplementationOnce(() => Promise.reject(new Error('nope')))
    localStorage.setItem(OWNER_KEY, 'ada@example.com')
    setSessionUser('grace@example.com')

    await ensureCacheBelongsToSessionUser()

    expect(localStorage.getItem(OWNER_KEY)).toBe('ada@example.com')
  })

  it('retries the clear on the next boot after a failed one', async () => {
    clearCache.mockImplementationOnce(() => Promise.reject(new Error('nope')))
    localStorage.setItem(OWNER_KEY, 'ada@example.com')
    setSessionUser('grace@example.com')

    await ensureCacheBelongsToSessionUser()
    await ensureCacheBelongsToSessionUser()

    expect(clearCache).toHaveBeenCalledTimes(2)
    expect(localStorage.getItem(OWNER_KEY)).toBe('grace@example.com')
  })
})
