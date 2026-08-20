<template>
  <div
    class="flex h-full w-full flex-col justify-center gap-2 overflow-hidden rounded bg-surface-base p-4"
  >
    <div class="flex items-start justify-between gap-2">
      <div class="min-w-0">
        <Tooltip :text="__(config.tooltip || '')" placement="top">
          <div class="truncate text-p-sm text-ink-gray-6">
            {{ __(config.title || 'Monthly revenue target') }}
          </div>
        </Tooltip>
      </div>
      <!-- UX §2.13: one small badge, and only when there is something to say. -->
      <span
        v-if="view.isOver"
        class="shrink-0 rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7 ring-1 ring-inset ring-outline-gray-1"
      >
        {{ __('+{0}% over', [view.overPercent]) }}
      </span>
    </div>

    <div class="truncate text-2xl-semibold text-ink-gray-9">
      {{ money(view.achieved) }}
    </div>

    <!-- ONE thin bar, light track, accent fill (UX §2.8: bars, never gauges). -->
    <div
      class="h-1.5 w-full overflow-hidden rounded-full bg-surface-gray-2"
      role="progressbar"
      :aria-valuemin="0"
      :aria-valuemax="100"
      :aria-valuenow="Math.round(view.barPercent)"
      :aria-label="__('Progress toward the monthly revenue target')"
    >
      <!-- 500 ms ease-out, and nothing at all for a reader who asked the OS for
           reduced motion. -->
      <div
        class="h-full rounded-full bg-surface-gray-9 transition-[width] duration-500 ease-out motion-reduce:transition-none"
        :style="{ width: fillWidth }"
      />
    </div>

    <div class="truncate text-p-sm text-ink-gray-5">
      <template v-if="view.hasTarget">
        {{
          __('{0} / {1} target ({2}%)', [
            money(view.achieved),
            money(view.target),
            percentLabel,
          ])
        }}
      </template>
      <template v-else>
        {{ __('No monthly target set — add one in Settings.') }}
      </template>
    </div>

    <div v-if="config.subtitle" class="truncate text-xs text-ink-gray-4">
      {{ config.subtitle }}
    </div>
  </div>
</template>

<script setup>
/**
 * Target meter (master spec §5, item 7; UX §2.8).
 *
 * A thin bar and a number, not a gauge and not a circle. The period is the
 * CALENDAR MONTH and is deliberately not the dashboard's date filter, which is
 * why the subtitle the server sends is rendered rather than dropped: a reader
 * who has just picked "Last 90 Days" has to be told this one widget ignored it.
 *
 * The fill animates from 0 on the first render only. Re-animating on every
 * dashboard refresh would make a static number look like it keeps changing.
 */

import { meterView } from '@/utils/targetMeter'
import { formatNumber } from '@/utils/numberFormat'
import { Tooltip } from 'frappe-ui'
import { computed, onMounted, ref } from 'vue'

const props = defineProps({
  config: { type: Object, required: true },
})

const mounted = ref(false)
onMounted(() => {
  // One frame at zero width, so the transition has somewhere to travel from.
  requestAnimationFrame(() => {
    mounted.value = true
  })
})

const view = computed(() => meterView(props.config))

const fillWidth = computed(() =>
  mounted.value ? `${view.value.barPercent}%` : '0%',
)

const percentLabel = computed(() => {
  const percent = view.value.percent
  return Number.isInteger(percent) ? String(percent) : percent.toFixed(1)
})

function money(value) {
  const prefix = props.config?.prefix || ''
  const amount = formatNumber(value, '', 0)
  return prefix ? `${prefix} ${amount}` : amount
}
</script>
