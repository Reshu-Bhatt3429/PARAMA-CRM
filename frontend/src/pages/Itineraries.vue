<template>
  <LayoutHeader>
    <template #left-header>
      <Breadcrumbs :items="[{ label: __('Itineraries'), route: { name: 'Itineraries' } }]" />
    </template>
    <template #right-header>
      <TextInput
        v-model="search"
        class="w-48"
        type="text"
        :placeholder="__('Search itineraries')"
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
      v-if="itineraries.loading && !itineraries.data"
      class="flex h-64 items-center justify-center"
    >
      <LoadingIndicator class="h-5 w-5 text-ink-gray-5" />
    </div>

    <ErrorMessage v-else-if="itineraries.error" class="mt-6" :message="errorText" />

    <table v-else-if="itineraries.data?.length" class="w-full text-base">
      <thead class="sticky top-0 bg-surface-white">
        <tr class="border-b text-ink-gray-5">
          <th class="py-2 pr-3 text-left font-normal">{{ __('Title') }}</th>
          <th class="py-2 pr-3 text-left font-normal">{{ __('Destination') }}</th>
          <th class="py-2 pr-3 text-left font-normal">{{ __('Starts') }}</th>
          <th class="py-2 pr-3 text-left font-normal">{{ __('Days') }}</th>
          <th class="py-2 pr-3 text-left font-normal">{{ __('Status') }}</th>
          <th class="py-2 pr-3 text-left font-normal">{{ __('Version') }}</th>
          <th class="py-2 text-left font-normal">{{ __('Last updated') }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="itinerary in itineraries.data"
          :key="itinerary.name"
          class="cursor-pointer border-b hover:bg-surface-gray-1"
          @click="open(itinerary.name)"
        >
          <td class="max-w-xs truncate py-2.5 pr-3 font-medium text-ink-gray-8">
            {{ itinerary.title || itinerary.name }}
          </td>
          <td class="max-w-xs truncate py-2.5 pr-3 text-ink-gray-7">
            {{ itinerary.destination || '—' }}
          </td>
          <td class="whitespace-nowrap py-2.5 pr-3 text-ink-gray-7">
            {{ itinerary.start_date ? formatDate(itinerary.start_date) : '—' }}
          </td>
          <td class="py-2.5 pr-3 text-ink-gray-7">{{ itinerary.num_days }}</td>
          <td class="py-2.5 pr-3">
            <Badge :theme="statusTheme(itinerary.status)" variant="subtle" :label="__(itinerary.status)" />
          </td>
          <td class="py-2.5 pr-3 text-ink-gray-7">v{{ itinerary.version }}</td>
          <td class="whitespace-nowrap py-2.5 text-ink-gray-5">
            {{ timeAgo(itinerary.modified) }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-else class="flex h-64 flex-col items-center justify-center gap-2 text-ink-gray-5">
      <LucideMap class="h-7 w-7" />
      <p class="text-base">{{ __('No itineraries yet.') }}</p>
      <p class="text-sm">
        {{ __('Open a lead and press Itinerary to draft one.') }}
      </p>
    </div>

    <div v-if="itineraries.hasNextPage" class="flex justify-center py-4">
      <Button :label="__('Load more')" @click="itineraries.next()" />
    </div>
  </div>
</template>

<script setup>
import LucideMap from '~icons/lucide/map'
import LayoutHeader from '@/components/LayoutHeader.vue'
import { formatDate, timeAgo } from '@/utils'
import { Breadcrumbs, LoadingIndicator, createListResource, debounce } from 'frappe-ui'
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const search = ref('')

const itineraries = createListResource({
  doctype: 'CRM Itinerary',
  fields: [
    'name',
    'title',
    'lead',
    'destination',
    'start_date',
    'num_days',
    'status',
    'version',
    'modified',
  ],
  orderBy: 'modified desc',
  pageLength: 30,
  auto: true,
})

const errorText = computed(
  () => itineraries.error?.messages?.[0] || __('Could not load the itineraries.'),
)

// Debounced so a fast typist does not fire a query per keystroke.
const reload = debounce(() => {
  const term = search.value.trim()
  itineraries.update({
    filters: term ? { title: ['like', `%${term}%`] } : {},
  })
  itineraries.reload()
}, 300)

function statusTheme(status) {
  if (status === 'Sent') return 'green'
  if (status === 'Revised') return 'orange'
  return 'gray'
}

function open(name) {
  router.push({ name: 'Itinerary', params: { itineraryId: name } })
}
</script>
