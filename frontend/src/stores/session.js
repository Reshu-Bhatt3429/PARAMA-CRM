import { defineStore } from 'pinia'
import { createResource } from 'frappe-ui'
import router from '@/router'
import { ref, computed } from 'vue'
import { clearCache } from '@/utils'

// The browser keeps the frappe-ui resource cache (localStorage + IndexedDB)
// keyed by nothing but the resource name, so it is shared across users of the
// same browser. This key records who filled it.
const CACHE_OWNER_KEY = 'crm_last_user'

// A wedged IndexedDB — an open connection in another tab, a corrupt store —
// can leave a clear pending forever. Neither the app boot nor the logout
// redirect may hang on that.
const CACHE_CLEAR_TIMEOUT_MS = 3000

export function getSessionUser() {
  let cookies = new URLSearchParams(document.cookie.split('; ').join('&'))
  let _sessionUser = cookies.get('user_id')
  if (_sessionUser === 'Guest') {
    _sessionUser = null
  }
  return _sessionUser
}

/**
 * Clears the cache, and always settles within CACHE_CLEAR_TIMEOUT_MS.
 * A timeout is reported and then ignored: proceeding with a stale cache is bad,
 * but an app that never mounts is worse.
 *
 * @returns {Promise<boolean>} true only if the clear actually completed.
 *   A timeout or an error returns false, and the caller MUST NOT record the
 *   cache as owned by anyone — see adoptCacheForUser.
 */
async function clearCacheGuarded() {
  let timer
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => {
      console.warn(
        `[crm] Cache clear did not finish within ${CACHE_CLEAR_TIMEOUT_MS}ms; continuing.`,
      )
      resolve(false)
    }, CACHE_CLEAR_TIMEOUT_MS)
  })

  try {
    return await Promise.race([
      Promise.resolve(clearCache()).then(() => true),
      timeout,
    ])
  } catch (error) {
    console.warn('[crm] Cache clear failed; continuing.', error)
    return false
  } finally {
    clearTimeout(timer)
  }
}

/**
 * Makes the cache this user's own: clears it unless the stored owner record
 * already names them, then stamps the record.
 *
 * An ABSENT owner record counts as foreign, not as a first run. Every browser
 * that ran a build without this guard is in exactly that state and holds the
 * leaked cache, so treating "absent" as safe would skip the machines the guard
 * exists for. The price is one extra refetch, once, per existing browser.
 */
async function adoptCacheForUser(user) {
  let previous = null
  let storageReadable = true
  try {
    previous = localStorage.getItem(CACHE_OWNER_KEY)
  } catch {
    // Storage can be blocked (private mode, blocked cookies). Then no owner
    // record can be trusted, so the cache is treated as foreign.
    storageReadable = false
  }

  if (!storageReadable || previous !== user) {
    // Stamp ONLY on a completed clear. A timed-out clear can still land later
    // and abort, or not run at all; stamping regardless would mark the previous
    // user's cache as owned by this one, and every later boot would then see a
    // matching owner and skip the clear. Leaving the record alone costs one
    // retry on the next boot and keeps the leak recoverable.
    const cleared = await clearCacheGuarded()
    if (!cleared) {
      console.warn(
        '[crm] Cache ownership not recorded because the clear did not complete; it will be retried on the next load.',
      )
      return
    }
  }

  try {
    localStorage.setItem(CACHE_OWNER_KEY, user)
  } catch {
    // Non-fatal: the guard degrades to clearing on every boot.
  }
}

/**
 * Boot-time guard against serving one user the cache another user filled.
 * Logout clears the cache itself, but a session can also change without this
 * app running — another tab, an expired cookie, a direct /login visit. Call
 * this before the app mounts and await it: the clear must finish before any
 * resource reads its cached value.
 */
export async function ensureCacheBelongsToSessionUser() {
  const user = getSessionUser()

  if (!user) {
    // A logged-out boot. Clear the cache rather than only dropping the owner
    // key: a cache left in place with no owner record would look like a first
    // run to whoever logs in next, and the guard would wave it through.
    await clearCacheGuarded()
    try {
      localStorage.removeItem(CACHE_OWNER_KEY)
    } catch {
      // Non-fatal, see adoptCacheForUser.
    }
    return
  }

  await adoptCacheForUser(user)
}

export const sessionStore = defineStore('crm-session', () => {
  function sessionUser() {
    return getSessionUser()
  }

  let user = ref(sessionUser())
  const isLoggedIn = computed(() => !!user.value)

  const login = createResource({
    url: 'login',
    onError() {
      throw new Error(__('Invalid Email or Password'))
    },
    async onSuccess() {
      user.value = sessionUser()
      // Same rule as the boot guard, for a login that happens without a page
      // load. The boot guard normally cleared already; this closes the gap.
      if (user.value) {
        await adoptCacheForUser(user.value)
      }
      login.reset()
      router.replace({ path: '/' })
    },
  })

  const logout = createResource({
    url: 'logout',
    async onSuccess() {
      user.value = null
      // The cached resources hold the previous user's data — WhatsApp account,
      // templates, activity feeds. Clear them before the redirect, otherwise
      // the next login on this browser reads them straight back out.
      try {
        localStorage.removeItem(CACHE_OWNER_KEY)
      } catch {
        // Non-fatal, see adoptCacheForUser.
      }
      await clearCacheGuarded()
      window.location.href = '/login?redirect-to=/crm'
    },
  })

  return {
    user,
    isLoggedIn,
    login,
    logout,
  }
})
