<template>
  <LayoutHeader>
    <template #left-header>
      <Breadcrumbs :items="breadcrumbs" />
    </template>
    <template #right-header>
      <Badge v-if="doc" :theme="statusTheme" variant="subtle" :label="__(doc.status)" />
      <span v-if="doc" class="text-sm text-ink-gray-5">v{{ doc.version }}</span>

      <Tooltip :text="aiTooltip">
        <span>
          <Button
            :label="__('Generate skeleton')"
            :disabled="busy || !aiReady"
            :loading="working === 'skeleton'"
            @click="generateSkeleton"
          />
        </span>
      </Tooltip>

      <Tooltip :text="aiTooltip">
        <span>
          <Button
            :label="generateAllLabel"
            variant="solid"
            :disabled="busy || !aiReady"
            :loading="working === 'all-days'"
            @click="generateAllDays"
          />
        </span>
      </Tooltip>

      <Button
        :label="__('Download PDF')"
        :disabled="busy"
        :loading="working === 'pdf'"
        @click="downloadPdf"
      />
      <Button
        :label="__('Send on WhatsApp')"
        :disabled="busy"
        :loading="working === 'whatsapp'"
        @click="sendOnWhatsApp"
      />
    </template>
  </LayoutHeader>

  <div v-if="loading" class="flex h-64 items-center justify-center">
    <LoadingIndicator class="h-5 w-5 text-ink-gray-5" />
  </div>

  <ErrorMessage v-else-if="loadError" class="m-5" :message="loadError" />

  <div v-else-if="doc" class="flex-1 overflow-y-auto px-3 pb-16 sm:px-5">
    <!-- Progress is shown while "Generate all days" walks the itinerary, so the
         agent watches the work happen instead of a single long spinner. -->
    <div
      v-if="progress"
      class="mt-4 flex items-center gap-3 rounded-lg border border-outline-blue-1 bg-surface-blue-1 px-4 py-2.5"
    >
      <LoadingIndicator class="h-4 w-4 text-ink-blue-2" />
      <span class="text-base text-ink-blue-2">{{ progress }}</span>
    </div>

    <ErrorMessage v-if="actionError" class="mt-4" :message="actionError" />

    <div
      v-if="sendHint"
      class="mt-4 rounded-lg border border-outline-amber-1 bg-surface-amber-1 px-4 py-2.5 text-base text-ink-amber-3"
    >
      {{ sendHint }}
    </div>

    <!-- trip facts -->
    <section class="mt-5">
      <h2 class="text-lg font-semibold text-ink-gray-8">{{ __('Trip') }}</h2>
      <div class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <FormControl v-model="form.title" type="text" :label="__('Title')" @change="saveDetails" />
        <FormControl
          v-model="form.destination"
          type="text"
          :label="__('Destination')"
          @change="saveDetails"
        />
        <FormControl
          v-model="form.start_date"
          type="date"
          :label="__('Start date')"
          @change="saveDetails"
        />
        <FormControl
          v-model.number="form.num_days"
          type="number"
          min="1"
          max="30"
          :label="__('Days')"
          @change="saveDetails"
        />
        <FormControl
          v-model.number="form.group_size"
          type="number"
          min="0"
          :label="__('Group size')"
          @change="saveDetails"
        />
        <FormControl
          v-model.number="form.budget"
          type="number"
          min="0"
          :label="__('Budget')"
          @change="saveDetails"
        />
      </div>
    </section>

    <!-- days -->
    <section class="mt-8">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold text-ink-gray-8">
          {{ __('Day by day') }}
          <span class="ml-2 text-base font-normal text-ink-gray-5">
            {{ __('{0} items', [itemCount]) }}
            <template v-if="unverifiedCount">
              · {{ __('{0} places to confirm', [unverifiedCount]) }}
            </template>
          </span>
        </h2>
        <Button :label="__('Add day')" iconLeft="plus" :disabled="busy" @click="onAddDay" />
      </div>

      <div v-if="!days.length" class="mt-4 rounded-lg border border-dashed py-10 text-center">
        <p class="text-base text-ink-gray-5">{{ __('This itinerary has no days yet.') }}</p>
        <p class="mt-1 text-sm text-ink-gray-4">
          {{ __('Generate a skeleton, or add the days by hand.') }}
        </p>
      </div>

      <div
        v-for="day in days"
        :key="day.day_number"
        class="mt-4 rounded-lg border bg-surface-white"
      >
        <div class="flex items-start justify-between gap-3 border-b px-4 py-3">
          <div class="min-w-0 flex-1">
            <div class="text-xs uppercase tracking-wide text-ink-gray-4">
              {{ __('Day') }} {{ day.day_number }}
            </div>
            <TextInput
              v-model="day.title"
              class="mt-1 w-full font-medium"
              type="text"
              :placeholder="__('Day title')"
              @change="saveDays"
            />
            <TextInput
              v-model="day.summary"
              class="mt-1.5 w-full"
              type="text"
              :placeholder="__('One line about this day')"
              @change="saveDays"
            />
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <Tooltip :text="aiTooltip">
              <span>
                <Button
                  :label="__('Regenerate day')"
                  :disabled="busy || !aiReady"
                  :loading="working === `day-${day.day_number}`"
                  @click="regenerateDay(day.day_number)"
                />
              </span>
            </Tooltip>
            <Button
              icon="trash-2"
              :tooltip="__('Remove day')"
              :disabled="busy"
              @click="onRemoveDay(day.day_number)"
            />
          </div>
        </div>

        <div v-for="slot in day.slots" :key="slot.time_of_day" class="border-b px-4 py-3 last:border-b-0">
          <div class="flex items-center justify-between">
            <span class="text-xs font-medium uppercase tracking-wide text-ink-gray-5">
              {{ __(timeOfDayLabels[slot.time_of_day]) }}
            </span>
            <Button
              icon="plus"
              :tooltip="__('Add item')"
              :disabled="busy"
              @click="onAddItem(day.day_number, slot.time_of_day)"
            />
          </div>

          <p v-if="!slot.items.length" class="py-2 text-sm text-ink-gray-4">
            {{ __('Nothing planned.') }}
          </p>

          <div
            v-for="(item, index) in slot.items"
            :key="index"
            class="mt-2 rounded-md border border-outline-gray-1 p-3"
          >
            <div class="flex items-start gap-2">
              <TextInput
                v-model="item.title"
                class="w-full font-medium"
                type="text"
                :placeholder="__('What happens')"
                @change="saveDays"
              />
              <div class="flex shrink-0 items-center gap-1">
                <Button
                  icon="arrow-up"
                  :tooltip="__('Move up')"
                  :disabled="busy || index === 0"
                  @click="onMoveItem(day.day_number, slot.time_of_day, index, -1)"
                />
                <Button
                  icon="arrow-down"
                  :tooltip="__('Move down')"
                  :disabled="busy || index === slot.items.length - 1"
                  @click="onMoveItem(day.day_number, slot.time_of_day, index, 1)"
                />
                <Button
                  icon="trash-2"
                  :tooltip="__('Remove item')"
                  :disabled="busy"
                  @click="onRemoveItem(day.day_number, slot.time_of_day, index)"
                />
              </div>
            </div>

            <TextInput
              v-model="item.description"
              class="mt-2 w-full"
              type="text"
              :placeholder="__('What the customer should know')"
              @change="saveDays"
            />

            <div class="mt-2 flex flex-wrap items-center gap-2">
              <TextInput
                v-model="item.place_name"
                class="w-56"
                type="text"
                :placeholder="__('Place name')"
                @change="saveDays"
              />
              <!-- The AI names places it cannot check. This badge is the agent's
                   to-do list, and clicking it is them saying the place is real. -->
              <Tooltip
                :text="
                  item.verified
                    ? __('Confirmed by you. Click to mark unconfirmed.')
                    : __('The AI suggested this place. Click once you have checked it exists.')
                "
              >
                <button
                  v-if="item.place_name"
                  type="button"
                  class="rounded px-2 py-1 text-xs"
                  :class="
                    item.verified
                      ? 'bg-surface-green-2 text-ink-green-3'
                      : 'bg-surface-amber-2 text-ink-amber-3'
                  "
                  :disabled="busy"
                  @click="onToggleVerified(day.day_number, slot.time_of_day, index)"
                >
                  {{ item.verified ? __('confirmed') : __('unverified') }}
                </button>
              </Tooltip>
              <TextInput
                v-model="item.duration_hours"
                class="w-28"
                type="number"
                min="0"
                step="0.5"
                :placeholder="__('Hours')"
                @change="saveDays"
              />
              <TextInput
                v-model="item.est_cost"
                class="w-36"
                type="number"
                min="0"
                :placeholder="__('Cost pp')"
                @change="saveDays"
              />
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- pricing -->
    <section class="mt-8">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold text-ink-gray-8">{{ __('Price tiers') }}</h2>
        <Button :label="__('Add tier')" iconLeft="plus" :disabled="busy" @click="onAddTier" />
      </div>
      <div
        v-for="(tier, index) in form.price_tiers"
        :key="index"
        class="mt-2 flex items-center gap-2"
      >
        <TextInput
          v-model="tier.tier_label"
          class="w-64"
          type="text"
          :placeholder="__('Double Sharing')"
          @change="saveDetails"
        />
        <TextInput
          v-model="tier.price_per_person"
          class="w-40"
          type="number"
          min="0"
          :placeholder="__('Per person')"
          @change="saveDetails"
        />
        <Button icon="trash-2" :tooltip="__('Remove tier')" :disabled="busy" @click="onRemoveTier(index)" />
      </div>
      <p v-if="!form.price_tiers.length" class="mt-2 text-sm text-ink-gray-4">
        {{ __('No price tiers yet.') }}
      </p>
    </section>

    <!-- terms -->
    <section class="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2">
      <FormControl
        v-model="form.inclusions"
        type="textarea"
        :rows="4"
        :label="__('Inclusions')"
        :description="__('One per line.')"
        @change="saveDetails"
      />
      <FormControl
        v-model="form.exclusions"
        type="textarea"
        :rows="4"
        :label="__('Exclusions')"
        :description="__('One per line.')"
        @change="saveDetails"
      />
      <FormControl
        v-model="form.terms"
        type="textarea"
        :rows="4"
        :label="__('Terms and conditions')"
        :description="__('One per line.')"
        @change="saveDetails"
      />
      <FormControl
        v-model="form.internal_notes"
        type="textarea"
        :rows="4"
        :label="__('Internal notes')"
        :description="__('Never printed and never sent to the customer.')"
        @change="saveDetails"
      />
    </section>
  </div>
</template>

<script setup>
import LayoutHeader from '@/components/LayoutHeader.vue'
import {
  TIME_OF_DAY_LABELS,
  addDay,
  addItem,
  countItems,
  countUnverifiedPlaces,
  emptyDayNumbers,
  isDayEmpty,
  moveItem,
  normalizeDays,
  removeDay,
  removeItem,
  serializeDays,
  toggleVerified,
} from '@/utils/itinerary'
import {
  Badge,
  Breadcrumbs,
  LoadingIndicator,
  Tooltip,
  call,
  toast,
} from 'frappe-ui'
import { computed, reactive, ref } from 'vue'

const props = defineProps({
  itineraryId: { type: String, required: true },
})

const timeOfDayLabels = TIME_OF_DAY_LABELS

const doc = ref(null)
const days = ref([])
const form = reactive({
  title: '',
  destination: '',
  start_date: '',
  num_days: 1,
  group_size: 0,
  budget: 0,
  inclusions: '',
  exclusions: '',
  terms: '',
  internal_notes: '',
  price_tiers: [],
})

const loading = ref(true)
const loadError = ref('')
const actionError = ref('')
const sendHint = ref('')
const working = ref('')
const progress = ref('')
const aiReady = ref(false)

const busy = computed(() => Boolean(working.value))
const itemCount = computed(() => countItems(days.value))
const unverifiedCount = computed(() => countUnverifiedPlaces(days.value))

const statusTheme = computed(() => {
  if (doc.value?.status === 'Sent') return 'green'
  if (doc.value?.status === 'Revised') return 'orange'
  return 'gray'
})

const aiTooltip = computed(() =>
  aiReady.value
    ? ''
    : __('AI is not configured. Open Settings → AI & Follow-ups to add a provider.'),
)

const generateAllLabel = computed(() => {
  const pending = emptyDayNumbers(days.value).length
  return pending && pending !== days.value.length
    ? __('Generate {0} empty days', [pending])
    : __('Generate all days')
})

const breadcrumbs = computed(() => [
  { label: __('Itineraries'), route: { name: 'Itineraries' } },
  {
    label: doc.value?.title || props.itineraryId,
    route: { name: 'Itinerary', params: { itineraryId: props.itineraryId } },
  },
])

function apply(payload) {
  doc.value = payload
  days.value = normalizeDays(payload.days)
  Object.assign(form, {
    title: payload.title || '',
    destination: payload.destination || '',
    start_date: payload.start_date || '',
    num_days: payload.num_days || 1,
    group_size: payload.group_size || 0,
    budget: payload.budget || 0,
    inclusions: payload.inclusions || '',
    exclusions: payload.exclusions || '',
    terms: payload.terms || '',
    internal_notes: payload.internal_notes || '',
    price_tiers: (payload.price_tiers || []).map((tier) => ({ ...tier })),
  })
}

function messageOf(error, fallback) {
  return error?.messages?.[0] || error?.message || fallback
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    apply(
      await call('crm.api.itinerary.get_itinerary_for_editor', {
        itinerary: props.itineraryId,
      }),
    )
  } catch (error) {
    loadError.value = messageOf(error, __('Could not open this itinerary.'))
  } finally {
    loading.value = false
  }

  // A failure here only decides whether the generate buttons are live. Manual
  // editing must keep working with no AI provider at all.
  try {
    aiReady.value = Boolean(await call('crm.api.itinerary.is_ai_configured'))
  } catch {
    aiReady.value = false
  }
}

// --- saving ---------------------------------------------------------------

async function saveDays() {
  actionError.value = ''
  try {
    const result = await call('crm.api.itinerary.update_days', {
      itinerary: props.itineraryId,
      days_json: serializeDays(days.value),
    })
    days.value = normalizeDays(result.days)
    doc.value = { ...doc.value, status: result.status, version: result.version }
  } catch (error) {
    actionError.value = messageOf(error, __('Could not save the itinerary.'))
  }
}

/**
 * Ask before a smaller day count throws curated days away.
 *
 * The backend truncates `days_json` the moment `num_days` shrinks, which is
 * what stops a later skeleton run from discarding those days with no warning.
 * The agent has to be the one who decides, so the confirmation lives here and
 * names how much work is at stake.
 */
function confirmShrink() {
  const dropped = days.value.filter(
    (day) => day.day_number > form.num_days && !isDayEmpty(day),
  )
  if (!dropped.length) return true

  return window.confirm(
    __('{0} planned days sit past day {1} and will be removed. Continue?', [
      dropped.length,
      form.num_days,
    ]),
  )
}

async function saveDetails() {
  actionError.value = ''
  if (!confirmShrink()) {
    // Put the field back to what the document still holds.
    form.num_days = doc.value?.num_days || form.num_days
    return
  }
  try {
    apply(
      await call('crm.api.itinerary.update_details', {
        itinerary: props.itineraryId,
        values: {
          title: form.title,
          destination: form.destination,
          start_date: form.start_date || null,
          num_days: form.num_days,
          group_size: form.group_size,
          budget: form.budget,
          inclusions: form.inclusions,
          exclusions: form.exclusions,
          terms: form.terms,
          internal_notes: form.internal_notes,
          price_tiers: form.price_tiers,
        },
      }),
    )
  } catch (error) {
    actionError.value = messageOf(error, __('Could not save the trip details.'))
  }
}

// --- structural edits -----------------------------------------------------

async function mutate(next) {
  days.value = next
  await saveDays()
}

const onAddDay = () => mutate(addDay(days.value))
const onRemoveDay = (dayNumber) => mutate(removeDay(days.value, dayNumber))
const onAddItem = (dayNumber, timeOfDay) =>
  mutate(addItem(days.value, dayNumber, timeOfDay, __('New item')))
const onRemoveItem = (dayNumber, timeOfDay, index) =>
  mutate(removeItem(days.value, dayNumber, timeOfDay, index))
const onMoveItem = (dayNumber, timeOfDay, index, offset) =>
  mutate(moveItem(days.value, dayNumber, timeOfDay, index, offset))
const onToggleVerified = (dayNumber, timeOfDay, index) =>
  mutate(toggleVerified(days.value, dayNumber, timeOfDay, index))

function onAddTier() {
  form.price_tiers.push({ tier_label: '', price_per_person: 0 })
}

function onRemoveTier(index) {
  form.price_tiers.splice(index, 1)
  saveDetails()
}

// --- generation -----------------------------------------------------------

async function generateSkeleton() {
  actionError.value = ''
  working.value = 'skeleton'
  try {
    const result = await call('crm.api.itinerary.generate_skeleton', {
      itinerary: props.itineraryId,
    })
    days.value = normalizeDays(result.days)
    toast.success(__('Skeleton drafted. Now fill in the days.'))
  } catch (error) {
    actionError.value = messageOf(error, __('Could not draft the skeleton.'))
  } finally {
    working.value = ''
  }
}

async function runDay(dayNumber) {
  const result = await call('crm.api.itinerary.generate_day', {
    itinerary: props.itineraryId,
    day_number: dayNumber,
  })
  days.value = normalizeDays(
    days.value.map((day) => (day.day_number === dayNumber ? result.day : day)),
  )
}

async function regenerateDay(dayNumber) {
  actionError.value = ''
  working.value = `day-${dayNumber}`
  try {
    await runDay(dayNumber)
  } catch (error) {
    actionError.value = messageOf(error, __('Could not draft this day.'))
  } finally {
    working.value = ''
  }
}

/**
 * Walk the days one call at a time.
 *
 * Sequential on purpose: each day is prompted with the neighbouring days'
 * items so the itinerary does not repeat itself, which only works if the
 * earlier days are already written. It also lets the agent watch progress and
 * keeps one failed day from losing the whole itinerary.
 */
async function generateAllDays() {
  actionError.value = ''
  working.value = 'all-days'

  const pending = emptyDayNumbers(days.value)
  const targets = pending.length
    ? pending
    : days.value.map((day) => day.day_number)

  try {
    for (const [index, dayNumber] of targets.entries()) {
      progress.value = __('Writing day {0} of {1}…', [index + 1, targets.length])
      await runDay(dayNumber)
    }
    toast.success(__('The itinerary is drafted. Check the unverified places.'))
  } catch (error) {
    actionError.value = messageOf(
      error,
      __('The itinerary stopped part way. The finished days are saved.'),
    )
  } finally {
    progress.value = ''
    working.value = ''
  }
}

// --- delivery -------------------------------------------------------------

async function downloadPdf() {
  actionError.value = ''
  working.value = 'pdf'
  try {
    const result = await call('crm.api.itinerary.get_pdf', {
      itinerary: props.itineraryId,
    })
    window.open(result.file_url, '_blank')
  } catch (error) {
    actionError.value = messageOf(error, __('Could not build the PDF.'))
  } finally {
    working.value = ''
  }
}

async function sendOnWhatsApp() {
  actionError.value = ''
  sendHint.value = ''
  working.value = 'whatsapp'
  try {
    const result = await call('crm.api.itinerary.send_via_whatsapp', {
      itinerary: props.itineraryId,
    })
    if (result.success) {
      doc.value = { ...doc.value, status: result.status, version: result.version }
      toast.success(__('Sent to {0}', [result.to]))
    } else {
      actionError.value = result.error
      sendHint.value = result.hint || ''
    }
  } catch (error) {
    actionError.value = messageOf(error, __('Could not send the itinerary.'))
  } finally {
    working.value = ''
  }
}

load()
</script>
