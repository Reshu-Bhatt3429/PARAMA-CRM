<template>
  <div ref="chartElement" class="h-full min-h-[300px] w-full px-4 py-2" />
</template>

<script setup>
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import {
  DatasetComponent,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components'
import { init, use } from 'echarts/core'
import { LabelLayout } from 'echarts/features'
import { CanvasRenderer } from 'echarts/renderers'
import { resolveChartOptions } from '@/utils/chartOptions'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

use([
  BarChart,
  LineChart,
  PieChart,
  DatasetComponent,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
  LabelLayout,
  CanvasRenderer,
])

const props = defineProps({
  options: { type: Object, required: true },
})

const chartElement = ref(null)
let chart
let resizeObserver
let themeObserver

function render() {
  if (!chart || !chartElement.value) return
  const styles = getComputedStyle(chartElement.value)
  const options = resolveChartOptions(props.options, (name) =>
    styles.getPropertyValue(name).trim(),
  )
  chart.setOption(options, { notMerge: true })
}

onMounted(async () => {
  await nextTick()
  if (!chartElement.value) return

  chart = init(chartElement.value, null, { renderer: 'canvas' })
  render()

  resizeObserver = new ResizeObserver(() => chart?.resize())
  resizeObserver.observe(chartElement.value)

  themeObserver = new MutationObserver(() => render())
  themeObserver.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ['class', 'data-theme'],
  })
})

watch(
  () => props.options,
  () => render(),
  { deep: true },
)

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  themeObserver?.disconnect()
  chart?.dispose()
  chart = null
})
</script>
