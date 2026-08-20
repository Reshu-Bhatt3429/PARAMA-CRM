<template>
  <div v-if="flags.length" class="flex flex-wrap items-center gap-1">
    <!-- UX §2.13: ONE chip. Several loud badges on a row is the thing this
         component exists to prevent. `.stop.prevent` because in a list the whole
         row is a link to the record, and a chip click means "tell me more". -->
    <button
      type="button"
      class="inline-flex max-w-full items-center gap-1 rounded-full bg-surface-amber-1 px-2 py-0.5 text-xs font-medium text-ink-amber-3 ring-1 ring-inset ring-outline-amber-1"
      :aria-expanded="expanded"
      :title="collapsedLabel"
      @click.stop.prevent="expanded = !expanded"
    >
      <span class="lucide-triangle-alert size-3 shrink-0" aria-hidden="true" />
      <span class="truncate">{{ __(collapsedLabel) }}</span>
      <span
        v-if="flags.length > 1"
        class="lucide-chevron-down size-3 shrink-0 transition-transform"
        :class="expanded ? 'rotate-180' : ''"
        aria-hidden="true"
      />
    </button>

    <template v-if="expanded && flags.length > 1">
      <span
        v-for="flag in flags"
        :key="flag"
        class="inline-flex max-w-full items-center rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs text-ink-gray-7 ring-1 ring-inset ring-outline-gray-1"
      >
        <span class="truncate">{{ __(healthFlagLabel(flag)) }}</span>
      </span>
    </template>
  </div>
</template>

<script setup>
/**
 * The "Needs attention" chip (master spec §5, item 22; UX §2.13).
 *
 * It renders nothing at all when the feature flag is off, even if the column
 * still holds values a previous sweep wrote. `dealHealthEnabled` comes from the
 * same Today payload the sidebar badge uses, so this costs no extra request,
 * and it starts false — a chip that appeared because the flag state had not
 * arrived yet would violate the acceptance criterion "flag OFF = no chips".
 */

import { dealHealthEnabled } from '@/composables/today'
import {
  chipLabel,
  healthFlagLabel,
  parseHealthFlags,
} from '@/utils/dealHealth'
import { computed, ref } from 'vue'

const props = defineProps({
  /** The raw `custom_parama_health_flags` column value. */
  value: { type: [String, Object, Array], default: null },
})

const expanded = ref(false)

const flags = computed(() =>
  dealHealthEnabled.value ? parseHealthFlags(props.value) : [],
)

const collapsedLabel = computed(() => chipLabel(props.value))
</script>
