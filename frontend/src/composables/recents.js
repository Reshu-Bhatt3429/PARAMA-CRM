/**
 * Recently viewed — the stateful half (master spec §5, item 11).
 *
 * `src/utils/recents.js` holds the rules; this file holds the one storage
 * handle the whole app shares. Two components reading the same `useStorage`
 * key would otherwise each keep their own copy and overwrite each other.
 */

import { sessionStore } from '@/stores/session'
import { recentsKey, mergeRecent, sanitizeRecents } from '@/utils/recents'
import { useStorage } from '@vueuse/core'
import { computed } from 'vue'

let handle = null

function storage() {
  if (!handle) {
    const { user } = sessionStore()
    const site = window.site_name || window.location.hostname
    handle = useStorage(recentsKey(site, user), [])
  }
  return handle
}

export function useRecents() {
  const recents = computed(() => sanitizeRecents(storage().value))

  /**
   * Record a visit. Called from the record pages, so it must never throw: a
   * full or disabled localStorage cannot be allowed to break a lead page.
   */
  function record(doctype, name) {
    try {
      storage().value = mergeRecent(storage().value, { doctype, name })
    } catch (error) {
      console.warn('[crm] Could not record a recently viewed record.', error)
    }
  }

  function clear() {
    try {
      storage().value = []
    } catch (error) {
      console.warn('[crm] Could not clear the recently viewed list.', error)
    }
  }

  return { recents, record, clear }
}

/** Test seam: forget the cached handle so a new session gets a new key. */
export function resetRecentsHandle() {
  handle = null
}
