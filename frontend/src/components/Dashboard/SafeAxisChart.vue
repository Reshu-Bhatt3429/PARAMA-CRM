<template>
  <EChart :options="options" />
</template>

<script setup>
import EChart from '@/components/Dashboard/EChart.vue'
import { createAxisTooltipFormatter } from '@/utils/chartTooltips'
import { computed } from 'vue'

const props = defineProps({
  config: { type: Object, required: true },
})

const options = computed(() => {
  const config = props.config
  const isRTL =
    config.dir === 'rtl' ||
    (!config.dir && document.documentElement.getAttribute('dir') === 'rtl')
  const swapXY = Boolean(config.swapXY)
  const custom = config.echartOptions || {}
  const hasSecondaryAxis = config.series.some((series) => series.axis === 'y2')
  const categoryAxis = {
    type: config.xAxis.type,
    inverse: isRTL,
    axisTick: { show: false },
    axisLabel: { hideOverlap: true },
    splitLine: { show: false },
    ...(config.xAxis.echartOptions || {}),
  }
  const valueAxis = {
    type: 'value',
    name: config.yAxis.title,
    nameLocation: 'middle',
    nameGap: swapXY ? 28 : 46,
    nameTextStyle: {
      color: 'var(--ink-gray-6)',
      fontSize: 12,
    },
    splitLine: { lineStyle: { color: 'var(--ink-gray-3)' } },
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { formatter: formatCompactValue },
    ...(config.yAxis.echartOptions || {}),
  }

  const base = {
    animation: true,
    animationDuration: 500,
    color: config.colors,
    textStyle: { fontFamily: ['InterVar', 'sans-serif'] },
    title: {
      top: 4,
      left: isRTL ? 'right' : 0,
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
    grid: {
      left: '1%',
      right: '1.5%',
      top: config.subtitle ? 62 : 48,
      bottom: config.series.length > 1 ? 42 : 12,
      containLabel: true,
    },
    xAxis: swapXY ? valueAxis : categoryAxis,
    yAxis: swapXY
      ? categoryAxis
      : [
          valueAxis,
          {
            ...valueAxis,
            show: hasSecondaryAxis,
            position: isRTL ? 'left' : 'right',
            name: config.y2Axis?.title,
            ...(config.y2Axis?.echartOptions || {}),
          },
        ],
    legend: {
      show: config.series.length > 1,
      type: 'scroll',
      bottom: 8,
      icon: 'circle',
      textStyle: { color: 'var(--ink-gray-8)' },
    },
    tooltip: {
      show: true,
      trigger: 'axis',
      confine: true,
      appendToBody: false,
      axisPointer: { type: 'shadow' },
      formatter: createAxisTooltipFormatter(config),
      renderMode: 'richText',
    },
    series: config.series.map((series) => {
      const isLine = series.type === 'line' || series.type === 'area'
      const values = (config.data || []).map((row) => {
        const category = row[config.xAxis.key]
        const value = row[series.name]
        return swapXY ? [value, category] : [category, value]
      })

      return {
        type: isLine ? 'line' : 'bar',
        name: series.name,
        data: values,
        yAxisIndex: !swapXY && series.axis === 'y2' ? 1 : 0,
        stack: config.stacked ? 'stack' : undefined,
        barMaxWidth: 60,
        connectNulls: true,
        showSymbol: Boolean(series.showDataPoints || series.showDataLabels),
        symbol: 'circle',
        symbolSize: 7,
        areaStyle:
          series.type === 'area'
            ? { color: series.color, opacity: series.fillOpacity || 0.5 }
            : undefined,
        lineStyle: isLine
          ? { width: series.lineWidth || 2, type: series.lineType }
          : undefined,
        label: {
          show: Boolean(series.showDataLabels),
          position: swapXY ? (isRTL ? 'left' : 'right') : 'top',
          formatter: (params) =>
            formatCompactValue(params.value?.[swapXY ? 0 : 1]),
        },
        itemStyle: { color: series.color },
        ...(series.echartOptions || {}),
      }
    }),
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
    series: base.series,
  }
})

function formatCompactValue(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return value
  return new Intl.NumberFormat('en-US', {
    notation: Math.abs(number) >= 1000 ? 'compact' : 'standard',
    maximumFractionDigits: 1,
  }).format(number)
}
</script>
