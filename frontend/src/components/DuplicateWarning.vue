<template>
  <div
    v-if="visible"
    role="status"
    class="mt-4 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-amber-900"
  >
    <span
      class="lucide-triangle-alert mt-0.5 size-4 shrink-0"
      aria-hidden="true"
    />
    <div class="min-w-0 flex-1 text-sm">
      <div v-for="match in matches" :key="match.doctype + match.name">
        {{ headline(match) }}
        <a
          v-if="hrefFor(match)"
          :href="hrefFor(match)"
          target="_blank"
          rel="noopener"
          class="font-medium underline underline-offset-2"
        >
          {{ match.title }}
        </a>
        <span v-else class="font-medium">{{ match.title }}</span>
      </div>
    </div>
    <button
      type="button"
      class="shrink-0 text-sm font-medium underline underline-offset-2"
      @click="dismiss"
    >
      {{ __('Continue anyway') }}
    </button>
  </div>
</template>

<script setup>
/**
 * Duplicate warning under a create form (master spec §5, item 3).
 *
 * NON-BLOCKING by design: it is a banner, never a dialog, and it never disables
 * the Create button. The agency's real duplicates come from the same customer
 * arriving twice, and a hard block on that would stop legitimate work — two
 * travellers do share a household phone number.
 *
 * The dismissal is keyed on WHAT matched. "Continue anyway" silences this
 * address; typing a different one arms the warning again, because that is a
 * different question.
 */

import { recordRoute } from '@/utils/palette'
import { call } from 'frappe-ui'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

const DEBOUNCE_MS = 400

const props = defineProps({
  doctype: { type: String, required: true },
  email: { type: String, default: '' },
  phone: { type: String, default: '' },
})

const router = useRouter()
const matches = ref([])
const dismissedKeys = ref(new Set())

let timer = null
let token = 0

const signature = computed(() =>
  [(props.email || '').trim().toLowerCase(), (props.phone || '').trim()].join(
    '|',
  ),
)

const visible = computed(
  () => matches.value.length > 0 && !dismissedKeys.value.has(signature.value),
)

const LABELS = {
  'CRM Lead': 'Matches an existing lead:',
  'CRM Deal': 'Matches an existing deal:',
  Contact: 'Matches an existing contact:',
}

function headline(match) {
  return __(LABELS[match.doctype] || 'Matches an existing record:')
}

function hrefFor(match) {
  const route = recordRoute(match)
  if (!route) return null
  try {
    return router.resolve(route).href
  } catch {
    // A route this build does not know is a plain-text name, not a crash.
    return null
  }
}

function dismiss() {
  dismissedKeys.value = new Set([...dismissedKeys.value, signature.value])
}

async function check() {
  const mine = ++token
  const email = (props.email || '').trim()
  const phone = (props.phone || '').trim()

  if (!email && !phone) {
    matches.value = []
    return
  }

  try {
    const found = await call('crm.api.duplicates.check_duplicates', {
      doctype: props.doctype,
      email,
      phone,
    })
    if (mine !== token) return
    matches.value = found || []
  } catch (error) {
    if (mine !== token) return
    // A warning that cannot be computed shows nothing. It must never be the
    // reason a lead cannot be created.
    matches.value = []
    console.warn('[crm] Duplicate check failed.', error)
  }
}

watch(signature, () => {
  clearTimeout(timer)
  timer = setTimeout(check, DEBOUNCE_MS)
})

onBeforeUnmount(() => clearTimeout(timer))
</script>
