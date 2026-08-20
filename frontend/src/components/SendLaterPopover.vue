<template>
  <div class="w-64 rounded-lg bg-surface-modal p-1.5 shadow-2xl">
    <div class="px-2 pb-1 pt-1.5 text-xs text-ink-gray-5">
      {{ __('Send later') }}
    </div>

    <button
      v-for="preset in presets"
      :key="preset.key"
      class="flex w-full items-center justify-between gap-2 rounded px-2 py-1.5 text-base text-ink-gray-8 hover:bg-surface-gray-2"
      @click="emit('schedule', { preset: preset.key })"
    >
      <span>{{ preset.label }}</span>
      <span class="shrink-0 text-sm text-ink-gray-5">{{ preset.when }}</span>
    </button>

    <div class="my-1 border-t border-outline-gray-1" />

    <div class="px-2 pb-1 pt-0.5">
      <div class="mb-1 text-xs text-ink-gray-5">{{ __('Pick a time') }}</div>
      <input
        v-model="custom"
        type="datetime-local"
        :min="minimum"
        class="w-full rounded border border-outline-gray-2 bg-surface-base px-2 py-1 text-base text-ink-gray-8 focus:border-outline-gray-4 focus:outline-none focus:ring-0"
      />
      <Button
        class="mt-1.5 w-full"
        variant="subtle"
        :disabled="!customIsValid"
        :label="__('Schedule')"
        @click="emit('schedule', { send_at: toServerDatetime(custom) })"
      />
    </div>

    <p class="px-2 pb-1 pt-1.5 text-xs text-ink-gray-5">
      {{
        __(
          'Sent on the first hourly run after the time you choose, in your own timezone.',
        )
      }}
    </p>
  </div>
</template>

<script setup>
/**
 * The Send Later popover (master spec item 5).
 *
 * The times shown here are a PREVIEW. The authoritative time is computed on the
 * server, in the sender's own timezone, from the preset key this popover posts
 * -- so a laptop whose clock or timezone is wrong cannot send at the wrong hour.
 * Only the custom picker posts a time, and it posts a naive local wall clock
 * that the server reads in the sender's timezone.
 *
 * The footnote is not decoration. The sweep that delivers runs HOURLY, so
 * "18:00" means "the first sweep at or after 18:00", and promising the minute
 * would be a promise the engine does not make.
 */
import {
  PRESET_LATER_TODAY,
  PRESET_NEXT_MONDAY,
  PRESET_TOMORROW_MORNING,
  availablePresets,
  earliestPickable,
  isFutureDatetime,
  presetPreview,
  toServerDatetime,
} from '@/utils/sendLater'
import { formatDate } from '@/utils'
import { Button } from 'frappe-ui'
import { computed, ref } from 'vue'

const emit = defineEmits(['schedule'])

const now = new Date()
const custom = ref('')
const minimum = earliestPickable(now)

const LABELS = {
  [PRESET_LATER_TODAY]: () => __('Later today'),
  [PRESET_TOMORROW_MORNING]: () => __('Tomorrow morning'),
  [PRESET_NEXT_MONDAY]: () => __('Next Monday'),
}

const presets = computed(() =>
  availablePresets(now).map((key) => ({
    key,
    label: LABELS[key](),
    when: formatDate(presetPreview(key, now), 'ddd, h:mm a'),
  })),
)

const customIsValid = computed(() => isFutureDatetime(custom.value, new Date()))
</script>
