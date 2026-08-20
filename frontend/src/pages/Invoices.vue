<template>
  <LayoutHeader>
    <template #left-header>
      <Breadcrumbs
        :items="[{ label: __('Invoices'), route: { name: 'Invoices' } }]"
      />
    </template>
    <template #right-header>
      <FormControl
        v-model="status"
        type="select"
        class="w-40"
        :options="statusOptions"
        @update:modelValue="reload"
      />
      <TextInput
        v-model="search"
        class="w-48"
        type="text"
        :placeholder="__('Search by number')"
        @input="reload"
      >
        <template #prefix>
          <FeatherIcon name="search" class="h-4 w-4 text-ink-gray-5" />
        </template>
      </TextInput>
    </template>
  </LayoutHeader>

  <div class="flex-1 overflow-y-auto px-3 sm:px-5">
    <div
      v-if="invoices.loading && !invoices.data"
      class="flex h-64 items-center justify-center"
    >
      <LoadingIndicator class="h-5 w-5 text-ink-gray-5" />
    </div>

    <ErrorMessage
      v-else-if="invoices.error"
      class="mt-6"
      :message="errorText"
    />

    <table v-else-if="rows.length" class="w-full text-base">
      <thead class="sticky top-0 bg-surface-white">
        <tr class="border-b text-ink-gray-5">
          <th class="py-2 pr-3 text-left font-normal">{{ __('Number') }}</th>
          <th class="py-2 pr-3 text-left font-normal">{{ __('Customer') }}</th>
          <th class="py-2 pr-3 text-left font-normal">{{ __('Deal') }}</th>
          <th class="py-2 pr-3 text-left font-normal">{{ __('Date') }}</th>
          <th class="py-2 pr-3 text-left font-normal">{{ __('Due') }}</th>
          <th class="py-2 pr-3 text-left font-normal">{{ __('Status') }}</th>
          <th class="py-2 pr-3 text-right font-normal">{{ __('Total') }}</th>
          <th class="py-2 text-right font-normal">{{ __('Remaining') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="row.name"
          class="cursor-pointer border-b hover:bg-surface-gray-1"
          @click="open(row.name)"
        >
          <td
            class="max-w-xs truncate py-2.5 pr-3 font-medium"
            :class="row.hasNumber ? 'text-ink-gray-8' : 'text-ink-gray-5'"
          >
            {{ row.number }}
          </td>
          <td class="max-w-xs truncate py-2.5 pr-3 text-ink-gray-7">
            {{ row.customer }}
          </td>
          <td class="max-w-[10rem] truncate py-2.5 pr-3 text-ink-gray-5">
            {{ dealLabel(row.deal) }}
          </td>
          <td class="whitespace-nowrap py-2.5 pr-3 text-ink-gray-7">
            {{
              row.invoiceDate ? formatDate(row.invoiceDate, DATE_FORMAT) : '—'
            }}
          </td>
          <td class="whitespace-nowrap py-2.5 pr-3 text-ink-gray-7">
            {{ row.dueDate ? formatDate(row.dueDate, DATE_FORMAT) : '—' }}
          </td>
          <td class="py-2.5 pr-3">
            <!-- Overdue is COMPUTED here for display only (design note §Data
                 model). The status column never stores it. -->
            <Tooltip :text="row.pill.tooltip" :disabled="!row.pill.tooltip">
              <Badge
                :theme="row.pill.theme"
                :variant="row.pill.variant"
                :label="row.pill.label"
              />
            </Tooltip>
          </td>
          <td class="whitespace-nowrap py-2.5 pr-3 text-right text-ink-gray-7">
            {{ formatMoney(row.grandTotal, row.currency) }}
          </td>
          <td
            class="whitespace-nowrap py-2.5 text-right"
            :class="
              row.pill.isOverdue
                ? 'font-medium text-ink-red-3'
                : 'text-ink-gray-8'
            "
          >
            {{ formatMoney(row.remaining, row.currency) }}
          </td>
        </tr>
      </tbody>
    </table>

    <div
      v-else
      class="flex h-64 flex-col items-center justify-center gap-2 text-ink-gray-5"
    >
      <LucideReceipt class="h-7 w-7" />
      <p class="text-base">{{ __('No invoices yet.') }}</p>
      <p class="text-sm">
        {{ __('Open a deal and press Create invoice to raise one.') }}
      </p>
    </div>

    <div v-if="invoices.hasNextPage" class="flex justify-center py-4">
      <Button :label="__('Load more')" @click="invoices.next()" />
    </div>
  </div>
</template>

<script setup>
/**
 * The invoice list (master spec §5, item 29).
 *
 * The rows come from the ordinary list resource on `CRM Invoice`, which is what
 * applies `get_invoice_permission_query_conditions` — a Sales User sees exactly
 * the invoices of the deals they can already see. The page itself is behind the
 * `invoices_enabled` flag in the sidebar and in the route guard.
 *
 * Overdue is worked out in `@/utils/invoices` from the status and the due date.
 * It is never a stored status, so it can never be a day out of date and it can
 * never be filtered on as if it were one.
 */
import LucideReceipt from '~icons/lucide/receipt'
import LayoutHeader from '@/components/LayoutHeader.vue'
import { formatDate } from '@/utils'
import {
  DATE_FORMAT,
  formatMoney,
  listRow,
  todayString,
} from '@/utils/invoices'
import {
  Badge,
  Breadcrumbs,
  FormControl,
  LoadingIndicator,
  Tooltip,
  createListResource,
  debounce,
} from 'frappe-ui'
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const search = ref('')
const status = ref('')

const statusOptions = [
  { label: __('All statuses'), value: '' },
  { label: __('Draft'), value: 'Draft' },
  { label: __('Sent'), value: 'Sent' },
  { label: __('Partially Paid'), value: 'Partially Paid' },
  { label: __('Paid'), value: 'Paid' },
  { label: __('Void'), value: 'Void' },
]

const invoices = createListResource({
  doctype: 'CRM Invoice',
  fields: [
    'name',
    'invoice_number',
    'customer_name',
    'deal',
    'status',
    'invoice_date',
    'due_date',
    'currency',
    'grand_total',
    'outstanding_amount',
  ],
  orderBy: 'creation desc',
  pageLength: 30,
  auto: true,
})

// One reference date for the whole render, so two rows on the page cannot
// disagree about what "today" is while the tab sits open over midnight.
const today = computed(() => todayString())

const rows = computed(() =>
  (invoices.data || []).map((row) => listRow(row, today.value)),
)

const errorText = computed(
  () => invoices.error?.messages?.[0] || __('Could not load the invoices.'),
)

// Debounced so a fast typist does not fire a query per keystroke.
const reload = debounce(() => {
  const term = search.value.trim()
  const filters = {}
  if (term) filters.invoice_number = ['like', `%${term}%`]
  if (status.value) filters.status = status.value
  invoices.update({ filters })
  invoices.reload()
}, 300)

// A deal name is a hash. Shortening it keeps the column narrow and the row
// readable; the invoice page carries the link that actually navigates.
function dealLabel(deal) {
  return deal ? deal.slice(0, 8) : '—'
}

function open(name) {
  router.push({ name: 'Invoice', params: { invoiceId: name } })
}
</script>
