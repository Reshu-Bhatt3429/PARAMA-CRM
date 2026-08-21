<template>
  <EChart :options="options" />
</template>

<script setup>
import EChart from '@/components/Dashboard/EChart.vue'
import { createDonutTooltipFormatter } from '@/utils/chartTooltips'
import { computed } from 'vue'

const props = defineProps({
  config: { type: Object, required: true },
})

const options = computed(() => {
  const config = props.config
  const data = [...(config.data || [])].sort(
    (a, b) => Number(b[config.valueColumn]) - Number(a[config.valueColumn]),
  )
  const values = data.map((row) => Number(row[config.valueColumn]) || 0)
  const total = values.reduce((sum, value) => sum + value, 0)
  const showInlineLabels = Boolean(config.showInlineLabels)
  const custom = config.echartOptions || {}
  const center = ['50%', config.subtitle ? '50%' : '48%']

  const base = {
    animation: true,
    animationDuration: 700,
    color: config.colors,
    textStyle: { fontFamily: ['InterVar', 'sans-serif'] },
    title: {
      top: '4px',
      left: config.dir === 'rtl' ? 'right' : '0.8%',
      padding: config.dir === 'rtl' ? [0, 10, 0, 0] : 0,
      text: config.title,
      subtext: config.subtitle,
      itemGap: -3,
      textStyle: {
        fontSize: 14,
        fontWeight: 500,
        lineHeight: 24,
        color: 'var(--ink-gray-8)',
      },
      subtextStyle: {
        fontSize: 13,
        fontWeight: 400,
        lineHeight: 20,
        color: 'var(--ink-gray-6)',
      },
    },
    dataset: {
      source: [
        [config.categoryColumn, config.valueColumn],
        ...data.map((row) => [
          row[config.categoryColumn],
          row[config.valueColumn],
        ]),
      ],
    },
    series: [
      {
        type: 'pie',
        name: config.categoryColumn,
        center,
        radius: ['40%', '70%'],
        labelLine: {
          show: showInlineLabels,
          lineStyle: { width: 2 },
          length: 10,
          length2: 20,
          smooth: true,
        },
        label: {
          show: showInlineLabels,
          formatter: ({ value, name }) => {
            const percentage = total > 0 ? (Number(value[1]) / total) * 100 : 0
            return `${name} (${percentage.toFixed(0)}%)`
          },
        },
        emphasis: { scaleSize: 5 },
      },
    ],
    legend: showInlineLabels
      ? null
      : {
          left: 'center',
          bottom: 0,
          padding: [0, 10, 10, 10],
          orient: 'horizontal',
          show: true,
          type: 'scroll',
          itemGap: 12,
          formatter: (name) => {
            const index = data.findIndex(
              (row) => row[config.categoryColumn] === name,
            )
            const percentage = total > 0 ? (values[index] / total) * 100 : 0
            return `${name} (${percentage.toFixed(0)}%)`
          },
          textStyle: {
            padding: [0, 0, 0, -5],
            color: 'var(--ink-gray-8)',
          },
          icon: 'circle',
          pageIconColor: 'var(--ink-gray-6)',
          pageInactiveColor: 'var(--ink-gray-4)',
          pageIconSize: 10,
          pageTextStyle: { color: 'var(--ink-gray-6)' },
          animationDurationUpdate: 300,
        },
    tooltip: {
      trigger: 'item',
      confine: true,
      appendToBody: false,
      formatter: createDonutTooltipFormatter(total),
      renderMode: 'richText',
    },
  }

  return {
    ...base,
    ...custom,
    tooltip: {
      ...base.tooltip,
      ...(custom.tooltip || {}),
      formatter: base.tooltip.formatter,
      renderMode: 'richText',
    },
  }
})
</script>
