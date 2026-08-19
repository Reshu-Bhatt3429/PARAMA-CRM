/**
 * Is the AI provider usable? One answer, shared by every AI surface.
 *
 * Every sparkle in the app has to know before it is clicked, because UX §2.2
 * says a disabled feature explains where to switch it on instead of throwing an
 * error toast at whoever pressed it. Asking once and sharing the answer keeps
 * one round trip in the page rather than one per button.
 *
 * The value starts `null`, meaning "not asked yet". A button that renders on
 * `null` as if AI were off would flash the wrong state on every page load, so
 * callers wait for a boolean.
 */
import { call } from 'frappe-ui'
import { ref } from 'vue'

export const aiReady = ref(null)

let pending = null

/** Ask the server once. Later callers get the same promise, then the cached answer. */
export function loadAiReady() {
  if (aiReady.value !== null) return Promise.resolve(aiReady.value)
  if (pending) return pending

  pending = call('crm.ai.api.is_available')
    .then((answer) => {
      aiReady.value = Boolean(answer)
      return aiReady.value
    })
    .catch(() => {
      // An unreachable endpoint is not a configured provider. Treating it as
      // "on" would put the error toast back that this composable exists to
      // avoid.
      aiReady.value = false
      return false
    })
    .finally(() => {
      pending = null
    })

  return pending
}

/** Test seam, and what Settings calls after the key changes. */
export function resetAiReady() {
  aiReady.value = null
  pending = null
}
