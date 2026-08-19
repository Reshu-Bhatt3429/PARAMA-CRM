<template>
  <div
    v-if="invoicesEnabled && tiles.data"
    class="grid grid-cols-1 gap-4 sm:grid-cols-3"
  >
    <router-link
      v-for="tile in views"
      :key="tile.key"
      class="flex flex-col justify-center gap-1 overflow-hidden rounded bg-surface-base p-4 shadow"
      :to="{ name: 'Invoices' }"
    >
      <div class="truncate text-p-sm text-ink-gray-6">{{ tile.label }}</div>
      <div
        class="truncate text-2xl-semibold"
        :class="tile.alert ? 'text-ink-red-3' : 'text-ink-gray-9'"
      >
        {{ tile.value }}
      </div>
      <div class="truncate text-xs text-ink-gray-4">{{ tile.caption }}</div>
    </router-link>
  </div>
</template>

<script setup>
/**
 * Outstanding, Overdue and Collected this month (design note §Flow).
 *
 * Three figures, one request. `crm.api.invoices.get_tiles` aggregates over
 * `frappe.get_list`, so each figure is already scoped to the invoices the reader
 * may see, and Void invoices are excluded from all three — that is what
 * "terminal negative state" means.
 *
 * Nothing is added up here. A tile that recomputed the total would eventually
 * disagree with the list it links to.
 */
import { invoicesEnabled } from '@/composables/invoices'
import { formatMoney } from '@/utils/invoices'
import { createResource } from 'frappe-ui'
import { computed, watch } from 'vue'

const tiles = createResource({
  url: 'crm.api.invoices.get_tiles',
  // Never auto: the endpoint refuses while the module is off, and an error toast
  // on every dashboard load would be the flag shouting rather than hiding.
  auto: false,
})

watch(
  invoicesEnabled,
  (enabled) => {
    if (enabled && !tiles.data) tiles.reload()
  },
  { immediate: true },
)

const views = computed(() => {
  const data = tiles.data
  if (!data) return []
  const currency = data.currency || 'INR'
  return [
    {
      key: 'outstanding',
      label: __('Outstanding'),
      value: formatMoney(data.outstanding.value, currency),
      caption: __('{0} open invoices', [data.outstanding.count]),
      alert: false,
    },
    {
      key: 'overdue',
      label: __('Overdue'),
      value: formatMoney(data.overdue.value, currency),
      caption: __('{0} past their due date', [data.overdue.count]),
      // Only red when there is something to be red about.
      alert: Number(data.overdue.count) > 0,
    },
    {
      key: 'collected',
      label: __('Collected this month'),
      value: formatMoney(data.collected.value, currency),
      caption: data.collected.period || '',
      alert: false,
    },
  ]
})
</script>
