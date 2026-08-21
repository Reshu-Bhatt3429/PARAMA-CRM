<template>
  <div class="h-full w-full">
    <div
      v-if="item.type == 'number_chart'"
      class="flex h-full w-full rounded shadow overflow-hidden cursor-pointer"
    >
      <Tooltip :text="__(item.data.tooltip)">
        <NumberChart
          v-if="item.data"
          :key="index"
          class="!items-start"
          :config="item.data"
        />
      </Tooltip>
    </div>
    <div
      v-else-if="item.type == 'spacer'"
      class="rounded bg-surface-base h-full overflow-hidden text-ink-gray-5 flex items-center justify-center"
      :class="editing ? 'border border-dashed border-outline-gray-2' : ''"
    >
      {{ editing ? __('Spacer') : '' }}
    </div>
    <div
      v-else-if="item.type == 'axis_chart'"
      class="h-full w-full rounded-md bg-surface-base shadow"
    >
      <SafeAxisChart v-if="item.data" :config="item.data" />
    </div>
    <div
      v-else-if="item.type == 'donut_chart'"
      class="h-full w-full rounded-md bg-surface-base shadow overflow-hidden"
    >
      <SafeDonutChart v-if="item.data" :config="item.data" />
    </div>
    <!-- Target meter (master spec §5, item 7). Its own type because frappe-ui
         has no progress primitive, and UX §2.8 forbids the gauge that would
         otherwise be reached for. -->
    <div
      v-else-if="item.type == 'progress_chart'"
      class="h-full w-full rounded-md shadow overflow-hidden"
    >
      <TargetMeter v-if="item.data" :config="item.data" />
    </div>
  </div>
</template>
<script setup>
import TargetMeter from '@/components/Dashboard/TargetMeter.vue'
import { NumberChart, Tooltip } from 'frappe-ui'
import { defineAsyncComponent } from 'vue'

const SafeAxisChart = defineAsyncComponent(
  () => import('@/components/Dashboard/SafeAxisChart.vue'),
)
const SafeDonutChart = defineAsyncComponent(
  () => import('@/components/Dashboard/SafeDonutChart.vue'),
)

defineProps({
  index: { type: Number, required: true },
  item: { type: Object, required: true },
  editing: { type: Boolean, default: false },
})
</script>
