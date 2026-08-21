<template>
  <LayoutHeader>
    <template #left-header>
      <Breadcrumbs
        :items="[{ label: __('Itineraries'), route: { name: 'Itineraries' } }]"
      />
    </template>
    <template #right-header>
      <div class="flex items-center gap-2">
        <TextInput
          v-model="search"
          class="w-36 sm:w-48"
          type="text"
          :placeholder="__('Search itineraries')"
          @input="reload"
        >
          <template #prefix>
            <FeatherIcon name="search" class="h-4 w-4 text-ink-gray-5" />
          </template>
        </TextInput>
        <Button
          :label="__('New itinerary')"
          icon-left="plus"
          variant="solid"
          @click="startCreate"
        />
      </div>
    </template>
  </LayoutHeader>

  <div class="flex-1 overflow-y-auto px-3 sm:px-5">
    <div
      v-if="itineraries.loading && !itineraries.data"
      class="flex h-64 items-center justify-center"
    >
      <LoadingIndicator class="h-5 w-5 text-ink-gray-5" />
    </div>

    <ErrorMessage
      v-else-if="itineraries.error"
      class="mt-6"
      :message="errorText"
    />

    <table v-else-if="itineraries.data?.length" class="w-full text-base">
      <thead class="sticky top-0 bg-surface-white">
        <tr class="border-b text-ink-gray-5">
          <th class="py-2 pr-3 text-left font-normal">{{ __('Title') }}</th>
          <th class="py-2 pr-3 text-left font-normal">
            {{ __('Destination') }}
          </th>
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
            <Badge
              :theme="statusTheme(itinerary.status)"
              variant="subtle"
              :label="__(itinerary.status)"
            />
          </td>
          <td class="py-2.5 pr-3 text-ink-gray-7">v{{ itinerary.version }}</td>
          <td class="whitespace-nowrap py-2.5 text-ink-gray-5">
            {{ timeAgo(itinerary.modified) }}
          </td>
        </tr>
      </tbody>
    </table>

    <div
      v-else
      class="flex h-72 flex-col items-center justify-center px-6 text-center text-ink-gray-5"
    >
      <LucideMap class="h-7 w-7" />
      <p class="mt-3 text-base font-medium text-ink-gray-8">
        {{ hasSearch ? __('No itineraries found') : __('No itineraries yet') }}
      </p>
      <p class="mt-1 max-w-md text-sm">
        {{
          hasSearch
            ? __('Try another title or clear the search.')
            : __(
                'Start with a lead and we will prefill the customer, trip dates, travellers, and budget.',
              )
        }}
      </p>
      <Button
        class="mt-4"
        :label="hasSearch ? __('Clear search') : __('Create first itinerary')"
        :icon-left="hasSearch ? 'x' : 'plus'"
        :variant="hasSearch ? 'subtle' : 'solid'"
        @click="hasSearch ? clearSearch() : startCreate()"
      />
    </div>

    <div v-if="itineraries.hasNextPage" class="flex justify-center py-4">
      <Button :label="__('Load more')" @click="itineraries.next()" />
    </div>
  </div>

  <Dialog v-model="showCreate" :options="{ size: 'md' }">
    <template #body>
      <form @submit.prevent="createItinerary(false)">
        <div class="bg-surface-modal px-4 pb-6 pt-5 sm:px-6">
          <h3 class="text-2xl font-semibold text-ink-gray-9">
            {{ __('Create an itinerary') }}
          </h3>
          <p class="mt-1 max-w-lg text-p-sm text-ink-gray-5">
            {{
              __(
                'Choose the lead this proposal belongs to. Their customer and trip details will be copied into an editable draft.',
              )
            }}
          </p>

          <Link
            v-model="selectedLead"
            class="mt-5"
            doctype="CRM Lead"
            :label="__('Lead')"
            :placeholder="__('Search by customer or lead name')"
          />

          <div
            v-if="existingDraft"
            class="mt-4 rounded-md border border-outline-gray-2 bg-surface-gray-1 p-3"
          >
            <p class="text-base font-medium text-ink-gray-8">
              {{ __('A draft already exists for this lead') }}
            </p>
            <p class="mt-1 text-sm text-ink-gray-6">
              {{ existingDraft.title || existingDraft.name }}
            </p>
            <p class="mt-1 text-sm text-ink-gray-5">
              {{
                __(
                  'Open it to continue working, or create another draft for a different trip.',
                )
              }}
            </p>
          </div>

          <ErrorMessage
            v-if="createError"
            class="mt-3"
            :message="createError"
          />

          <div class="mt-4 flex items-center justify-between gap-3">
            <p class="text-sm text-ink-gray-5">
              {{ __('Cannot find the customer?') }}
            </p>
            <Button
              type="button"
              variant="ghost"
              :label="__('Go to leads')"
              icon-right="arrow-up-right"
              @click="goToLeads"
            />
          </div>
        </div>

        <div
          class="flex flex-col-reverse gap-2 px-4 pb-6 sm:flex-row sm:justify-end sm:px-6"
        >
          <Button
            type="button"
            :label="__('Cancel')"
            :disabled="creating"
            @click="showCreate = false"
          />
          <template v-if="existingDraft">
            <Button
              type="button"
              :label="__('Create another')"
              :loading="creating"
              :disabled="creating"
              @click="createItinerary(true)"
            />
            <Button
              type="button"
              variant="solid"
              :label="__('Open existing draft')"
              :disabled="creating"
              @click="openExistingDraft"
            />
          </template>
          <Button
            v-else
            type="submit"
            variant="solid"
            :label="__('Create itinerary')"
            :loading="creating"
            :disabled="creating || !selectedLead"
          />
        </div>
      </form>
    </template>
  </Dialog>
</template>

<script setup>
import LucideMap from '~icons/lucide/map'
import LayoutHeader from '@/components/LayoutHeader.vue'
import Link from '@/components/Controls/Link.vue'
import { formatDate, timeAgo } from '@/utils'
import {
  Breadcrumbs,
  Dialog,
  ErrorMessage,
  LoadingIndicator,
  call,
  createListResource,
  debounce,
} from 'frappe-ui'
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const search = ref('')
const showCreate = ref(false)
const selectedLead = ref('')
const existingDraft = ref(null)
const createError = ref('')
const creating = ref(false)

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
  () =>
    itineraries.error?.messages?.[0] || __('Could not load the itineraries.'),
)
const hasSearch = computed(() => Boolean(search.value.trim()))

// Debounced so a fast typist does not fire a query per keystroke.
const reload = debounce(() => {
  const term = search.value.trim()
  itineraries.update({
    filters: term ? { title: ['like', `%${term}%`] } : {},
  })
  itineraries.reload()
}, 300)

watch(selectedLead, () => {
  existingDraft.value = null
  createError.value = ''
})

function clearSearch() {
  search.value = ''
  itineraries.update({ filters: {} })
  itineraries.reload()
}

function startCreate() {
  selectedLead.value = ''
  existingDraft.value = null
  createError.value = ''
  showCreate.value = true
}

async function createItinerary(allowDuplicate = false) {
  if (!selectedLead.value || creating.value) return

  creating.value = true
  createError.value = ''
  try {
    if (!allowDuplicate) {
      const draft = await call('crm.api.itinerary.get_draft_for_lead', {
        lead: selectedLead.value,
      })
      if (draft) {
        existingDraft.value = draft
        return
      }
    }

    const itinerary = await call('crm.api.itinerary.create_from_lead', {
      lead: selectedLead.value,
    })
    showCreate.value = false
    open(itinerary.name)
  } catch (error) {
    createError.value =
      error.messages?.[0] ||
      error.message ||
      __(
        'Could not create the itinerary. Check your lead access and try again.',
      )
  } finally {
    creating.value = false
  }
}

function openExistingDraft() {
  if (!existingDraft.value) return
  showCreate.value = false
  open(existingDraft.value.name)
}

function goToLeads() {
  showCreate.value = false
  router.push({ name: 'Leads' })
}

function statusTheme(status) {
  if (status === 'Sent') return 'green'
  if (status === 'Revised') return 'orange'
  return 'gray'
}

function open(name) {
  router.push({ name: 'Itinerary', params: { itineraryId: name } })
}
</script>
