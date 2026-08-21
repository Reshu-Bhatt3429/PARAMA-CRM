<template>
  <LayoutHeader>
    <template #left-header><Breadcrumbs :items="breadcrumbs" /></template>
    <template #right-header>
      <Badge
        v-if="doc"
        :theme="statusTheme"
        variant="subtle"
        :label="__(doc.status)"
      />
      <span v-if="doc" class="hidden text-sm text-ink-gray-5 sm:inline"
        >v{{ doc.version }}</span
      >
      <Button
        :label="__('AI assistant')"
        iconLeft="sparkles"
        :disabled="busy"
        @click="showAssistant = true"
      />
      <Button
        :label="__('PDF')"
        iconLeft="download"
        :loading="working === 'pdf'"
        :disabled="busy"
        @click="downloadPdf"
      />
      <Button
        :label="__('WhatsApp')"
        iconLeft="message-circle"
        variant="solid"
        :loading="working === 'whatsapp'"
        :disabled="busy"
        @click="sendOnWhatsApp"
      />
    </template>
  </LayoutHeader>

  <div v-if="loading" class="flex h-64 items-center justify-center">
    <LoadingIndicator class="h-5 w-5" />
  </div>
  <ErrorMessage v-else-if="loadError" class="m-5" :message="loadError" />

  <div v-else-if="doc" class="itinerary-shell">
    <div class="mobile-switcher">
      <button
        :class="{ active: mobileView === 'edit' }"
        @click="mobileView = 'edit'"
      >
        {{ __('Edit') }}
      </button>
      <button
        :class="{ active: mobileView === 'preview' }"
        @click="mobileView = 'preview'"
      >
        {{ __('Preview') }}
      </button>
    </div>
    <div v-if="progress" class="progress-banner">
      <LoadingIndicator class="h-4 w-4" />{{ progress }}
    </div>
    <ErrorMessage v-if="actionError" class="mx-5 mt-3" :message="actionError" />
    <div v-if="sendHint" class="hint-banner">{{ sendHint }}</div>

    <main class="builder-grid">
      <div
        class="editor-pane"
        :class="{ 'mobile-hidden': mobileView !== 'edit' }"
      >
        <div class="editor-intro">
          <div>
            <p class="eyebrow">{{ __('Itinerary builder') }}</p>
            <h1>{{ form.title || __('Untitled itinerary') }}</h1>
            <p>
              {{
                __(
                  'Build once, preview live, then export or send the same customer-ready plan.',
                )
              }}
            </p>
          </div>
          <div class="actions">
            <Button
              :label="__('Load sample')"
              iconLeft="wand-2"
              :disabled="busy"
              @click="loadDemo"
            /><Button
              :label="__('Clear')"
              :disabled="busy"
              @click="clearBuilder"
            />
          </div>
        </div>

        <details class="editor-card" open>
          <summary>
            <span class="number">01</span
            ><span
              ><strong>{{ __('Expedition details') }}</strong
              ><small>{{
                __('The facts shown on the cover page')
              }}</small></span
            >
          </summary>
          <div class="body form-grid">
            <FormControl
              v-model="form.title"
              type="text"
              :label="__('Title')"
              @change="saveDetails"
            />
            <FormControl
              v-model="form.subtitle"
              type="text"
              :label="__('Subtitle / tagline')"
              @change="saveDetails"
            />
            <FormControl
              v-model="form.destination"
              type="text"
              :label="__('Destination')"
              @change="saveDetails"
            />
            <FormControl
              v-model="form.customer_name"
              type="text"
              :label="__('Prepared for')"
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
              @change="onNumDaysChange"
            />
            <FormControl
              v-model="form.duration_label"
              type="text"
              :label="__('Duration label')"
              @change="saveDetails"
            />
            <FormControl
              v-model="form.departure_type"
              type="select"
              :label="__('Departure type')"
              :options="departureOptions"
              @change="saveDetails"
            />
            <FormControl
              v-model.number="form.group_size"
              type="number"
              min="0"
              :label="__('Group size')"
              @change="onGroupSizeChange"
            />
            <FormControl
              v-model="form.group_size_label"
              type="text"
              :label="__('Group label')"
              @change="saveDetails"
            />
            <FormControl
              v-model="form.currency"
              type="text"
              :label="__('Currency')"
              @change="saveDetails"
            />
            <FormControl
              v-if="form.departure_type === 'Private Departure'"
              v-model.number="form.budget"
              type="number"
              min="0"
              :label="__('Private package price')"
              @change="saveDetails"
            />

            <MediaField
              :label="__('Cover image')"
              :value="form.cover_image"
              :upload-args="uploadArgs"
              @upload="(url) => setMedia('cover_image', url)"
              @remove="setMedia('cover_image', '')"
            />
            <MediaField
              :label="__('Brand logo')"
              :value="form.brand_logo || agency.logo"
              :editable-value="form.brand_logo"
              logo
              :upload-args="uploadArgs"
              @upload="(url) => setMedia('brand_logo', url)"
              @remove="setMedia('brand_logo', '')"
            />
          </div>
        </details>

        <details class="editor-card" open>
          <summary>
            <span class="number">02</span
            ><span
              ><strong>{{ __('Day planner') }}</strong
              ><small>{{ plannerSummary }}</small></span
            >
          </summary>
          <div class="body">
            <div class="toolbar">
              <p>
                {{
                  __(
                    'Use proposal fields for the PDF and the schedule for operational detail.',
                  )
                }}
              </p>
              <Button
                :label="__('Add day')"
                iconLeft="plus"
                :disabled="busy || days.length >= 30"
                @click="onAddDay"
              />
            </div>
            <div v-if="!days.length" class="empty">
              <strong>{{ __('No days yet') }}</strong
              ><span>{{
                __('Add a day, import a proposal, or ask AI to draft the trip.')
              }}</span>
            </div>

            <article
              v-for="(day, dayIndex) in days"
              :key="day.day_number"
              class="day-card"
            >
              <header>
                <div class="day-number">
                  {{ String(day.day_number).padStart(2, '0') }}
                </div>
                <div class="day-name">
                  <span>{{ __('Day {0}', [day.day_number]) }}</span
                  ><strong>{{ day.title || __('Untitled day') }}</strong>
                </div>
                <div class="actions day-actions">
                  <Button
                    icon="arrow-up"
                    :tooltip="__('Move up')"
                    :disabled="busy || dayIndex === 0"
                    @click="onMoveDay(day.day_number, -1)"
                  />
                  <Button
                    icon="arrow-down"
                    :tooltip="__('Move down')"
                    :disabled="busy || dayIndex === days.length - 1"
                    @click="onMoveDay(day.day_number, 1)"
                  />
                  <Tooltip :text="aiTooltip"
                    ><span
                      ><Button
                        icon="sparkles"
                        :tooltip="__('Generate day')"
                        :loading="working === `day-${day.day_number}`"
                        :disabled="busy || !aiReady"
                        @click="regenerateDay(day.day_number)" /></span
                  ></Tooltip>
                  <Button
                    icon="trash-2"
                    :tooltip="__('Remove day')"
                    :disabled="busy"
                    @click="onRemoveDay(day.day_number)"
                  />
                </div>
              </header>
              <div class="day-fields">
                <FormControl
                  v-model="day.title"
                  type="text"
                  :label="__('Day title')"
                  @change="saveDays"
                />
                <FormControl
                  v-model="day.summary"
                  type="text"
                  :label="__('Short summary')"
                  @change="saveDays"
                />
                <FormControl
                  v-model="day.highlights_input"
                  type="text"
                  :label="__('Highlights')"
                  :description="__('Comma separated, up to eight')"
                  @change="saveDays"
                />
                <FormControl
                  v-model="day.accommodation"
                  type="text"
                  :label="__('Accommodation')"
                  @change="saveDays"
                />
                <FormControl
                  v-model="day.description"
                  class="wide"
                  type="textarea"
                  :rows="4"
                  :label="__('Detailed description')"
                  @change="saveDays"
                />
                <div>
                  <span class="field-label">{{ __('Meals') }}</span>
                  <div class="meals">
                    <label v-for="meal in meals" :key="meal.key"
                      ><input
                        v-model="day.meals[meal.key]"
                        type="checkbox"
                        @change="saveDays"
                      />{{ __(meal.label) }}</label
                    >
                  </div>
                </div>
                <div class="wide image-editor">
                  <span class="field-label">{{ __('Day image') }}</span>
                  <img v-if="day.image" :src="day.image" :alt="day.title" />
                  <div class="actions">
                    <FileUploader
                      file-types="image/*"
                      :upload-args="uploadArgs"
                      @success="(file) => setDayImage(day, file.file_url)"
                      ><template #default="{ openFileSelector, uploading }"
                        ><Button
                          :label="__('Upload')"
                          iconLeft="image-up"
                          :loading="uploading"
                          @click="openFileSelector" /></template
                    ></FileUploader>
                    <Button
                      :label="__('Generate artwork')"
                      iconLeft="wand-2"
                      @click="generateArtwork(day, dayIndex)"
                    />
                    <Button
                      v-if="day.image"
                      :label="__('Remove')"
                      @click="setDayImage(day, null)"
                    />
                  </div>
                </div>
              </div>

              <details class="schedule">
                <summary>
                  {{ __('Detailed schedule') }}
                  <span>{{ scheduledCount(day) }} {{ __('items') }}</span>
                </summary>
                <div
                  v-for="slot in day.slots"
                  :key="slot.time_of_day"
                  class="slot"
                >
                  <div class="slot-head">
                    <strong>{{ __(timeOfDayLabels[slot.time_of_day]) }}</strong
                    ><Button
                      icon="plus"
                      :tooltip="__('Add item')"
                      @click="onAddItem(day.day_number, slot.time_of_day)"
                    />
                  </div>
                  <p v-if="!slot.items.length" class="muted">
                    {{ __('Nothing planned.') }}
                  </p>
                  <div
                    v-for="(item, itemIndex) in slot.items"
                    :key="itemIndex"
                    class="schedule-item"
                  >
                    <div class="item-title">
                      <TextInput
                        v-model="item.title"
                        type="text"
                        :placeholder="__('What happens')"
                        @change="saveDays"
                      /><Button
                        icon="arrow-up"
                        :disabled="itemIndex === 0"
                        @click="
                          onMoveItem(
                            day.day_number,
                            slot.time_of_day,
                            itemIndex,
                            -1,
                          )
                        "
                      /><Button
                        icon="arrow-down"
                        :disabled="itemIndex === slot.items.length - 1"
                        @click="
                          onMoveItem(
                            day.day_number,
                            slot.time_of_day,
                            itemIndex,
                            1,
                          )
                        "
                      /><Button
                        icon="trash-2"
                        @click="
                          onRemoveItem(
                            day.day_number,
                            slot.time_of_day,
                            itemIndex,
                          )
                        "
                      />
                    </div>
                    <TextInput
                      v-model="item.description"
                      type="text"
                      :placeholder="__('Customer-facing detail')"
                      @change="saveDays"
                    />
                    <div class="item-meta">
                      <TextInput
                        v-model="item.place_name"
                        type="text"
                        :placeholder="__('Place')"
                        @change="saveDays"
                      />
                      <button
                        v-if="item.place_name"
                        class="verify"
                        :class="{ verified: item.verified }"
                        @click="
                          onToggleVerified(
                            day.day_number,
                            slot.time_of_day,
                            itemIndex,
                          )
                        "
                      >
                        {{
                          item.verified ? __('Confirmed') : __('Verify place')
                        }}
                      </button>
                      <TextInput
                        v-model="item.duration_hours"
                        type="number"
                        min="0"
                        step="0.5"
                        :placeholder="__('Hours')"
                        @change="saveDays"
                      />
                      <TextInput
                        v-model="item.est_cost"
                        type="number"
                        min="0"
                        :placeholder="__('Cost pp')"
                        @change="saveDays"
                      />
                    </div>
                  </div>
                </div>
              </details>
            </article>
          </div>
        </details>

        <details class="editor-card">
          <summary>
            <span class="number">03</span
            ><span
              ><strong>{{ __('Inclusions and exclusions') }}</strong
              ><small>{{ __('Build clear package lists') }}</small></span
            >
          </summary>
          <div class="body list-columns">
            <ListEditor
              v-model="lists.inclusions"
              :label="__('Inclusions')"
              :placeholder="__('e.g. Airport transfers')"
              @save="saveDetails"
            /><ListEditor
              v-model="lists.exclusions"
              :label="__('Exclusions')"
              :placeholder="__('e.g. International flights')"
              @save="saveDetails"
            />
          </div>
        </details>

        <details class="editor-card">
          <summary>
            <span class="number">04</span
            ><span
              ><strong>{{ __('Pricing, contact and policies') }}</strong
              ><small>{{
                __('Customer-facing commercial details')
              }}</small></span
            >
          </summary>
          <div class="body">
            <div class="toolbar">
              <strong>{{ __('Price tiers') }}</strong
              ><Button
                :label="__('Add tier')"
                iconLeft="plus"
                @click="onAddTier"
              />
            </div>
            <div
              v-for="(tier, index) in form.price_tiers"
              :key="index"
              class="tier"
            >
              <TextInput
                v-model="tier.tier_label"
                :placeholder="__('Double sharing')"
                @change="saveDetails"
              /><TextInput
                v-model="tier.price_per_person"
                type="number"
                min="0"
                :placeholder="__('Per person')"
                @change="saveDetails"
              /><Button icon="trash-2" @click="onRemoveTier(index)" />
            </div>
            <p v-if="!form.price_tiers.length" class="muted">
              {{ __('No price tiers yet.') }}
            </p>
            <div class="contact-grid">
              <FormControl
                v-model="form.contact_email"
                type="email"
                :label="__('Contact email')"
                @change="saveDetails"
              /><FormControl
                v-model="form.contact_phone"
                type="tel"
                :label="__('Contact phone')"
                @change="saveDetails"
              /><FormControl
                v-model="form.contact_website"
                type="url"
                :label="__('Website')"
                @change="saveDetails"
              />
            </div>
            <ListEditor
              v-model="lists.terms"
              class="terms"
              :label="__('Important terms and policies')"
              :placeholder="__('e.g. Routes are subject to weather')"
              @save="saveDetails"
            />
            <FormControl
              v-model="form.internal_notes"
              class="mt-4"
              type="textarea"
              :rows="3"
              :label="__('Internal notes')"
              :description="__('Never printed or sent to the customer.')"
              @change="saveDetails"
            />
          </div>
        </details>

        <details class="editor-card">
          <summary>
            <span class="number">05</span
            ><span
              ><strong>{{ __('Typography and theme') }}</strong
              ><small>{{
                __('Control the live preview and PDF styling')
              }}</small></span
            >
          </summary>
          <div class="body form-grid">
            <FormControl
              v-model="form.theme"
              type="select"
              :label="__('Theme')"
              :options="themeOptions"
              @change="saveDetails"
            /><FormControl
              v-model="form.font_preset"
              type="select"
              :label="__('Font combination')"
              :options="fontOptions"
              @change="saveDetails"
            /><FormControl
              v-model="form.title_weight"
              type="select"
              :label="__('Title weight')"
              :options="weightOptions"
              @change="saveDetails"
            /><FormControl
              v-model="form.title_style"
              type="select"
              :label="__('Title style')"
              :options="styleOptions"
              @change="saveDetails"
            /><FormControl
              v-model="form.tagline_style"
              type="select"
              :label="__('Tagline style')"
              :options="taglineOptions"
              @change="saveDetails"
            /><FormControl
              v-model="form.title_case"
              type="select"
              :label="__('Title case')"
              :options="caseOptions"
              @change="saveDetails"
            />
          </div>
        </details>
      </div>

      <aside
        class="preview-pane"
        :class="{ 'mobile-hidden': mobileView !== 'preview' }"
      >
        <div class="preview-head">
          <div>
            <strong>{{ __('Live PDF preview') }}</strong
            ><span>{{ __('A4 · {0} pages', [previewPages]) }}</span>
          </div>
          <Badge :label="__(form.theme || 'Sunrise')" variant="subtle" />
        </div>
        <ItineraryPreview
          :form="form"
          :days="days"
          :agency="agency"
          :version="doc.version"
        />
      </aside>
    </main>
  </div>

  <Dialog v-model="showAssistant" :options="{ size: 'xl' }">
    <template #body>
      <div class="assistant-modal">
        <header>
          <div>
            <p class="eyebrow">{{ __('AI assistant') }}</p>
            <h2>{{ __('Draft or import an itinerary') }}</h2>
          </div>
          <Button icon="x" variant="ghost" @click="showAssistant = false" />
        </header>
        <div class="assistant-tabs">
          <button
            :class="{ active: assistantTab === 'generate' }"
            @click="assistantTab = 'generate'"
          >
            {{ __('Generate with AI') }}</button
          ><button
            :class="{ active: assistantTab === 'paste' }"
            @click="assistantTab = 'paste'"
          >
            {{ __('Paste itinerary') }}
          </button>
        </div>
        <div v-if="assistantTab === 'generate'" class="assistant-content">
          <div v-if="!aiReady" class="notice">{{ aiTooltip }}</div>
          <div class="form-grid">
            <FormControl
              v-model="assistant.destination"
              :label="__('Destination')"
            /><FormControl
              v-model.number="assistant.days"
              type="number"
              min="1"
              max="15"
              :label="__('Duration in days')"
            /><FormControl
              v-model="assistant.vibe"
              type="select"
              :label="__('Trip style')"
              :options="vibeOptions"
            /><FormControl
              v-model="assistant.instructions"
              class="wide"
              type="textarea"
              :rows="4"
              :label="__('Custom instructions')"
              :placeholder="
                __(
                  'Pace, interests, hotel style, accessibility, or constraints',
                )
              "
            />
          </div>
          <div class="assistant-actions">
            <Button
              :label="__('Generate complete itinerary')"
              variant="solid"
              iconLeft="sparkles"
              :disabled="!aiReady || busy"
              :loading="working === 'assistant'"
              @click="generateFromAssistant"
            />
          </div>
        </div>
        <div v-else class="assistant-content">
          <p class="assistant-copy">
            {{
              __(
                'Paste a proposal, email, or rough day plan. Secure server AI is used when configured; a local parser works without it.',
              )
            }}
          </p>
          <FormControl
            v-model="pastedText"
            type="textarea"
            :rows="12"
            :placeholder="pastePlaceholder"
          />
          <div class="assistant-actions">
            <Button
              :label="__('Import itinerary')"
              variant="solid"
              iconLeft="clipboard-paste"
              :disabled="!pastedText.trim() || busy"
              :loading="working === 'import'"
              @click="importPasted"
            />
          </div>
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import ItineraryPreview from '@/components/Itinerary/ItineraryPreview.vue'
import ListEditor from '@/components/Itinerary/ListEditor.vue'
import MediaField from '@/components/Itinerary/MediaField.vue'
import LayoutHeader from '@/components/LayoutHeader.vue'
import {
  TIME_OF_DAY_LABELS,
  addDay,
  addItem,
  arrayToLines,
  buildDemoItinerary,
  countItems,
  countUnverifiedPlaces,
  emptyDay,
  generatedDayArtwork,
  isDayEmpty,
  linesToArray,
  moveDay,
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
  Button,
  Dialog,
  ErrorMessage,
  FileUploader,
  FormControl,
  LoadingIndicator,
  TextInput,
  Tooltip,
  call,
  toast,
} from 'frappe-ui'
import { computed, reactive, ref } from 'vue'
import { openSafeUrl } from '@/utils/safeUrl'

const props = defineProps({ itineraryId: { type: String, required: true } })
const timeOfDayLabels = TIME_OF_DAY_LABELS
const meals = [
  { key: 'breakfast', label: 'Breakfast' },
  { key: 'lunch', label: 'Lunch' },
  { key: 'dinner', label: 'Dinner' },
]
const departureOptions = ['Group Departure', 'Private Departure']
const themeOptions = ['Sunrise', 'Midnight', 'Meadow']
const fontOptions = [
  'Modern Alpine',
  'Classic Bold',
  'Elegant Serif',
  'Clean Geometric',
]
const weightOptions = ['900', '700', '500', '400']
const styleOptions = ['Normal', 'Italic']
const taglineOptions = [
  'Bold Normal',
  'Bold Italic',
  'Medium Normal',
  'Medium Italic',
]
const caseOptions = ['Uppercase', 'Capitalize', 'Normal']
const vibeOptions = ['Adventure', 'Cultural', 'Leisure', 'Budget']
const doc = ref(null)
const days = ref([])
const agency = reactive({
  name: '',
  phone: '',
  email: '',
  website: '',
  logo: '',
})
const form = reactive(defaultForm())
const lists = reactive({ inclusions: [], exclusions: [], terms: [] })
const loading = ref(true),
  loadError = ref(''),
  actionError = ref(''),
  sendHint = ref('')
const working = ref(''),
  progress = ref(''),
  aiReady = ref(false),
  mobileView = ref('edit')
const showAssistant = ref(false),
  assistantTab = ref('generate'),
  pastedText = ref('')
const assistant = reactive({
  destination: '',
  days: 5,
  vibe: 'Adventure',
  instructions: '',
})

const busy = computed(() => Boolean(working.value))
const plannerSummary = computed(() =>
  countUnverifiedPlaces(days.value)
    ? __('{0} days · {1} items · {2} places to verify', [
        days.value.length,
        countItems(days.value),
        countUnverifiedPlaces(days.value),
      ])
    : __('{0} days · {1} schedule items', [
        days.value.length,
        countItems(days.value),
      ]),
)
const previewPages = computed(() => Math.ceil(days.value.length / 2) + 2)
const statusTheme = computed(() =>
  doc.value?.status === 'Sent'
    ? 'green'
    : doc.value?.status === 'Revised'
      ? 'orange'
      : 'gray',
)
const aiTooltip = computed(() =>
  aiReady.value
    ? ''
    : __(
        'AI is not configured. Open Settings → AI & Follow-ups to add a provider.',
      ),
)
const breadcrumbs = computed(() => [
  { label: __('Itineraries'), route: { name: 'Itineraries' } },
  {
    label: doc.value?.title || props.itineraryId,
    route: { name: 'Itinerary', params: { itineraryId: props.itineraryId } },
  },
])
const uploadArgs = computed(() => ({
  doctype: 'CRM Itinerary',
  docname: props.itineraryId,
  private: false,
}))
const pastePlaceholder = `Ladakh Escape\nDestination: Ladakh\nDuration: 3 Days / 2 Nights\n\nDay 1: Arrival in Leh\nHighlights: Airport welcome, Old Leh walk\nAccommodation: Hotel in Leh\nMeals: Lunch, Dinner\nRest and acclimatise before an easy evening walk.`

function defaultForm() {
  return {
    title: '',
    subtitle: '',
    customer_name: '',
    destination: '',
    start_date: '',
    num_days: 1,
    duration_label: '1 Day / 0 Nights',
    departure_type: 'Group Departure',
    group_size: 0,
    group_size_label: '',
    budget: 0,
    currency: 'INR',
    cover_image: '',
    brand_logo: '',
    theme: 'Sunrise',
    font_preset: 'Modern Alpine',
    title_weight: '900',
    title_style: 'Normal',
    tagline_style: 'Bold Normal',
    title_case: 'Uppercase',
    contact_email: '',
    contact_phone: '',
    contact_website: '',
    trip_vibe: 'Adventure',
    ai_instructions: '',
    internal_notes: '',
    price_tiers: [],
  }
}
function apply(payload) {
  doc.value = payload
  days.value = normalizeDays(payload.days)
  Object.assign(form, defaultForm(), payload, {
    price_tiers: (payload.price_tiers || []).map((tier) => ({ ...tier })),
  })
  Object.assign(agency, payload.agency || {})
  lists.inclusions = linesToArray(payload.inclusions)
  lists.exclusions = linesToArray(payload.exclusions)
  lists.terms = linesToArray(payload.terms)
  Object.assign(assistant, {
    destination: form.destination,
    days: form.num_days,
    vibe: form.trip_vibe,
    instructions: form.ai_instructions,
  })
}
const messageOf = (error, fallback) =>
  error?.messages?.[0] || error?.message || fallback
async function load() {
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
  try {
    aiReady.value = Boolean(await call('crm.api.itinerary.is_ai_configured'))
  } catch {
    aiReady.value = false
  }
}
function detailPayload() {
  const fields = [
    'title',
    'subtitle',
    'customer_name',
    'destination',
    'start_date',
    'num_days',
    'duration_label',
    'departure_type',
    'group_size',
    'group_size_label',
    'budget',
    'currency',
    'cover_image',
    'brand_logo',
    'theme',
    'font_preset',
    'title_weight',
    'title_style',
    'tagline_style',
    'title_case',
    'contact_email',
    'contact_phone',
    'contact_website',
    'trip_vibe',
    'ai_instructions',
    'internal_notes',
    'price_tiers',
  ]
  const values = Object.fromEntries(fields.map((field) => [field, form[field]]))
  values.start_date = values.start_date || null
  Object.assign(values, {
    inclusions: arrayToLines(lists.inclusions),
    exclusions: arrayToLines(lists.exclusions),
    terms: arrayToLines(lists.terms),
  })
  return values
}
function confirmShrink() {
  const dropped = days.value.filter(
    (day) => day.day_number > form.num_days && !isDayEmpty(day),
  )
  return (
    !dropped.length ||
    window.confirm(
      __('{0} planned days sit past day {1} and will be removed. Continue?', [
        dropped.length,
        form.num_days,
      ]),
    )
  )
}
async function saveDetails() {
  actionError.value = ''
  if (!confirmShrink()) {
    form.num_days = doc.value?.num_days || form.num_days
    return false
  }
  try {
    apply(
      await call('crm.api.itinerary.update_details', {
        itinerary: props.itineraryId,
        values: detailPayload(),
      }),
    )
    return true
  } catch (error) {
    actionError.value = messageOf(error, __('Could not save the trip details.'))
    return false
  }
}
async function saveDays() {
  actionError.value = ''
  try {
    const result = await call('crm.api.itinerary.update_days', {
      itinerary: props.itineraryId,
      days_json: serializeDays(days.value),
    })
    days.value = normalizeDays(result.days)
    doc.value = { ...doc.value, status: result.status, version: result.version }
    return true
  } catch (error) {
    actionError.value = messageOf(error, __('Could not save the itinerary.'))
    return false
  }
}
function durationLabel(count) {
  const nights = Math.max(0, count - 1)
  return `${count} ${count === 1 ? __('Day') : __('Days')} / ${nights} ${nights === 1 ? __('Night') : __('Nights')}`
}
async function onNumDaysChange() {
  const count = Math.max(1, Math.min(30, Number(form.num_days) || 1))
  form.num_days = count
  form.duration_label = durationLabel(count)
  await saveDetails()
}
async function onGroupSizeChange() {
  if (!form.group_size_label || /^\d+ travellers$/.test(form.group_size_label))
    form.group_size_label = form.group_size
      ? __('{0} travellers', [form.group_size])
      : ''
  await saveDetails()
}
async function mutate(next) {
  days.value = next
  await saveDays()
}
const onAddDay = () => mutate(addDay(days.value))
const onRemoveDay = (number) => {
  if (window.confirm(__('Remove day {0}?', [number])))
    mutate(removeDay(days.value, number))
}
const onMoveDay = (number, offset) =>
  mutate(moveDay(days.value, number, offset))
const onAddItem = (number, time) =>
  mutate(addItem(days.value, number, time, __('New item')))
const onRemoveItem = (number, time, index) =>
  mutate(removeItem(days.value, number, time, index))
const onMoveItem = (number, time, index, offset) =>
  mutate(moveItem(days.value, number, time, index, offset))
const onToggleVerified = (number, time, index) =>
  mutate(toggleVerified(days.value, number, time, index))
const scheduledCount = (day) =>
  day.slots.reduce((sum, slot) => sum + slot.items.length, 0)
function onAddTier() {
  form.price_tiers.push({ tier_label: '', price_per_person: 0 })
}
function onRemoveTier(index) {
  form.price_tiers.splice(index, 1)
  saveDetails()
}
async function setMedia(field, url) {
  form[field] = url || ''
  await saveDetails()
}
async function setDayImage(day, value) {
  day.image = value
  await saveDays()
}
async function generateArtwork(day, index) {
  day.image = generatedDayArtwork(
    day.title,
    form.destination,
    index,
    form.theme,
  )
  await saveDays()
}
async function runDay(number) {
  const result = await call('crm.api.itinerary.generate_day', {
    itinerary: props.itineraryId,
    day_number: number,
  })
  days.value = normalizeDays(
    days.value.map((day) => (day.day_number === number ? result.day : day)),
  )
}
async function regenerateDay(number) {
  actionError.value = ''
  working.value = `day-${number}`
  try {
    await runDay(number)
    toast.success(__('Day {0} drafted.', [number]))
  } catch (error) {
    actionError.value = messageOf(error, __('Could not draft this day.'))
  } finally {
    working.value = ''
  }
}
async function generateAllDays(targets) {
  for (const [index, number] of targets.entries()) {
    progress.value = __('Writing day {0} of {1}…', [index + 1, targets.length])
    await runDay(number)
  }
}
async function generateFromAssistant() {
  actionError.value = ''
  working.value = 'assistant'
  Object.assign(form, {
    destination: assistant.destination,
    num_days: assistant.days,
    duration_label: durationLabel(assistant.days),
    trip_vibe: assistant.vibe,
    ai_instructions: assistant.instructions,
  })
  try {
    if (!(await saveDetails())) return
    working.value = 'assistant'
    const result = await call('crm.api.itinerary.generate_skeleton', {
      itinerary: props.itineraryId,
    })
    days.value = normalizeDays(result.days)
    await generateAllDays(days.value.map((day) => day.day_number))
    showAssistant.value = false
    toast.success(__('The complete itinerary is ready to review.'))
  } catch (error) {
    actionError.value = messageOf(
      error,
      __('Generation stopped. Completed days are already saved.'),
    )
  } finally {
    progress.value = ''
    working.value = ''
  }
}
async function importPasted() {
  actionError.value = ''
  if (
    days.value.some((day) => !isDayEmpty(day)) &&
    !window.confirm(
      __('Importing will replace the current day plan. Continue?'),
    )
  )
    return
  working.value = 'import'
  try {
    const payload = await call('crm.api.itinerary.import_pasted_itinerary', {
      itinerary: props.itineraryId,
      text: pastedText.value,
      prefer_ai: aiReady.value ? 1 : 0,
    })
    apply(payload)
    showAssistant.value = false
    toast.success(
      payload.import_method === 'ai'
        ? __('Itinerary imported with AI.')
        : __('Itinerary imported with the local parser.'),
    )
  } catch (error) {
    actionError.value = messageOf(error, __('Could not import this itinerary.'))
  } finally {
    working.value = ''
  }
}
async function loadDemo() {
  if (
    days.value.some((day) => !isDayEmpty(day)) &&
    !window.confirm(__('Replace current content with the sample itinerary?'))
  )
    return
  const demo = buildDemoItinerary(agency)
  const demoDays = demo.days
  Object.assign(form, demo.details)
  lists.inclusions = linesToArray(demo.details.inclusions)
  lists.exclusions = linesToArray(demo.details.exclusions)
  lists.terms = linesToArray(demo.details.terms)
  working.value = 'sample'
  try {
    await saveDetails()
    days.value = demoDays
    await saveDays()
    toast.success(__('Sample itinerary loaded.'))
  } finally {
    working.value = ''
  }
}
async function clearBuilder() {
  if (!window.confirm(__('Clear the itinerary content and start again?')))
    return
  const clearedDays = [emptyDay(1)]
  Object.assign(form, defaultForm(), {
    title: __('Untitled itinerary'),
    contact_email: agency.email,
    contact_phone: agency.phone,
    contact_website: agency.website,
  })
  lists.inclusions = []
  lists.exclusions = []
  lists.terms = []
  working.value = 'clear'
  try {
    await saveDetails()
    days.value = clearedDays
    await saveDays()
    toast.success(__('Itinerary cleared.'))
  } finally {
    working.value = ''
  }
}
async function downloadPdf() {
  actionError.value = ''
  working.value = 'pdf'
  try {
    const result = await call('crm.api.itinerary.get_pdf', {
      itinerary: props.itineraryId,
    })
    if (!openSafeUrl(result.file_url)) {
      throw new Error(__('The server returned an invalid PDF URL.'))
    }
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
      doc.value = {
        ...doc.value,
        status: result.status,
        version: result.version,
      }
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

<style scoped>
.itinerary-shell {
  min-height: 0;
  flex: 1;
  overflow: hidden;
  background: #f7f7f5;
}
.builder-grid {
  display: grid;
  height: 100%;
  grid-template-columns: minmax(560px, 0.92fr) minmax(470px, 1.08fr);
  overflow: hidden;
}
.editor-pane,
.preview-pane {
  min-width: 0;
  overflow-y: auto;
  padding: 24px;
}
.editor-pane {
  border-right: 1px solid var(--outline-gray-2);
}
.preview-pane {
  background: #eef0ed;
}
.editor-intro,
.toolbar,
.preview-head,
.day-card > header,
.slot-head,
.actions {
  display: flex;
  align-items: center;
}
.editor-intro,
.toolbar,
.preview-head,
.slot-head {
  justify-content: space-between;
}
.editor-intro {
  align-items: flex-start;
  gap: 20px;
  margin-bottom: 20px;
}
.editor-intro h1 {
  margin-top: 3px;
  color: var(--ink-gray-9);
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.02em;
}
.editor-intro p:last-child,
.toolbar p,
.muted {
  color: var(--ink-gray-5);
  font-size: 12px;
}
.eyebrow {
  color: #9a4d13;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}
.actions {
  flex-wrap: wrap;
  gap: 6px;
}
.editor-card {
  margin-bottom: 14px;
  overflow: hidden;
  border: 1px solid var(--outline-gray-2);
  border-radius: 12px;
  background: white;
}
.editor-card > summary {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 18px;
  cursor: pointer;
  list-style: none;
}
.editor-card > summary::-webkit-details-marker {
  display: none;
}
.editor-card > summary:after {
  margin-left: auto;
  color: var(--ink-gray-4);
  content: '+';
  font-size: 20px;
}
.editor-card[open] > summary:after {
  content: '−';
}
.editor-card > summary span:nth-child(2) {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.editor-card > summary strong {
  color: var(--ink-gray-8);
  font-size: 15px;
}
.editor-card > summary small {
  color: var(--ink-gray-5);
  font-size: 11px;
}
.number {
  display: grid;
  width: 30px;
  height: 30px;
  place-items: center;
  border-radius: 8px;
  background: #fff3e8;
  color: #b45309;
  font-size: 10px;
  font-weight: 800;
}
.body {
  padding: 4px 18px 20px;
  border-top: 1px solid var(--outline-gray-1);
}
.form-grid,
.day-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  padding-top: 18px;
}
.toolbar {
  gap: 12px;
  padding: 16px 0 10px;
}
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  margin-top: 8px;
  padding: 38px;
  border: 1px dashed var(--outline-gray-3);
  border-radius: 10px;
  color: var(--ink-gray-5);
}
.day-card {
  margin-top: 12px;
  overflow: hidden;
  border: 1px solid var(--outline-gray-2);
  border-radius: 10px;
  background: #fcfcfb;
}
.day-card > header {
  gap: 10px;
  padding: 12px;
  border-bottom: 1px solid var(--outline-gray-1);
}
.day-number {
  display: grid;
  width: 36px;
  height: 36px;
  flex: none;
  place-items: center;
  border-radius: 50%;
  background: #172033;
  color: white;
  font-size: 12px;
  font-weight: 800;
}
.day-name {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
}
.day-name span {
  color: var(--ink-gray-4);
  font-size: 10px;
  text-transform: uppercase;
}
.day-name strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.day-fields {
  padding: 14px;
}
.wide {
  grid-column: 1/-1;
}
.field-label {
  display: block;
  margin-bottom: 6px;
  color: var(--ink-gray-6);
  font-size: 12px;
  font-weight: 500;
}
.meals {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.meals label {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 9px;
  border: 1px solid var(--outline-gray-2);
  border-radius: 7px;
  background: white;
  font-size: 12px;
}
.image-editor {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.image-editor img {
  width: 100%;
  height: 180px;
  border-radius: 9px;
  object-fit: cover;
}
.schedule {
  margin: 0 14px 14px;
  border-top: 1px solid var(--outline-gray-2);
}
.schedule > summary {
  padding: 12px 0;
  cursor: pointer;
  color: var(--ink-gray-7);
  font-size: 12px;
  font-weight: 600;
}
.schedule > summary span {
  color: var(--ink-gray-4);
  font-weight: 400;
}
.slot {
  padding: 10px 0;
  border-top: 1px solid var(--outline-gray-1);
}
.slot-head {
  font-size: 11px;
  text-transform: uppercase;
}
.schedule-item {
  display: grid;
  gap: 8px;
  margin-top: 8px;
  padding: 10px;
  border: 1px solid var(--outline-gray-1);
  border-radius: 8px;
  background: white;
}
.item-title {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto auto;
  gap: 5px;
}
.item-meta {
  display: grid;
  grid-template-columns: minmax(120px, 1fr) auto 90px 110px;
  gap: 7px;
  align-items: center;
}
.verify {
  padding: 7px 9px;
  border-radius: 6px;
  background: #fff4d6;
  color: #92400e;
  font-size: 11px;
  font-weight: 600;
}
.verify.verified {
  background: #dcfce7;
  color: #166534;
}
.list-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
  padding-top: 18px;
}
.tier {
  display: grid;
  grid-template-columns: 1fr 160px auto;
  gap: 8px;
  margin-bottom: 8px;
}
.contact-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-top: 18px;
  padding-top: 18px;
  border-top: 1px solid var(--outline-gray-1);
}
.terms {
  margin-top: 18px;
}
.preview-head {
  max-width: 760px;
  margin: 0 auto 14px;
}
.preview-head div {
  display: flex;
  flex-direction: column;
}
.preview-head strong {
  font-size: 13px;
}
.preview-head span {
  color: var(--ink-gray-5);
  font-size: 11px;
}
.preview-pane :deep(.itinerary-preview) {
  max-width: 760px;
  margin: 0 auto;
}
.mobile-switcher {
  display: none;
}
.progress-banner,
.hint-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 12px 20px 0;
  padding: 10px 13px;
  border: 1px solid #bfdbfe;
  border-radius: 8px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 13px;
}
.hint-banner {
  border-color: #fde68a;
  background: #fffbeb;
  color: #92400e;
}
.assistant-modal {
  padding: 22px;
}
.assistant-modal > header {
  display: flex;
  justify-content: space-between;
}
.assistant-modal h2 {
  margin-top: 3px;
  font-size: 22px;
  font-weight: 700;
}
.assistant-tabs {
  display: flex;
  gap: 4px;
  margin-top: 18px;
  padding: 4px;
  border-radius: 8px;
  background: var(--surface-gray-2);
}
.assistant-tabs button {
  flex: 1;
  padding: 8px;
  border-radius: 6px;
  color: var(--ink-gray-5);
  font-size: 12px;
  font-weight: 600;
}
.assistant-tabs button.active {
  background: white;
  color: var(--ink-gray-8);
  box-shadow: 0 1px 3px #00000014;
}
.assistant-content {
  padding-top: 18px;
}
.notice {
  margin-bottom: 14px;
  padding: 10px;
  border-radius: 7px;
  background: #fff7ed;
  color: #9a3412;
  font-size: 12px;
}
.assistant-copy {
  margin-bottom: 12px;
  color: var(--ink-gray-5);
  font-size: 13px;
}
.assistant-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 18px;
}
@media (max-width: 1100px) {
  .builder-grid {
    grid-template-columns: 1fr;
    overflow: visible;
  }
  .itinerary-shell {
    overflow-y: auto;
  }
  .editor-pane,
  .preview-pane {
    overflow: visible;
  }
  .mobile-switcher {
    position: sticky;
    top: 0;
    z-index: 5;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 4px;
    margin: 10px 12px 0;
    padding: 4px;
    border: 1px solid var(--outline-gray-2);
    border-radius: 9px;
    background: white;
  }
  .mobile-switcher button {
    padding: 8px;
    border-radius: 6px;
    color: var(--ink-gray-5);
    font-size: 12px;
    font-weight: 600;
  }
  .mobile-switcher button.active {
    background: #172033;
    color: white;
  }
  .mobile-hidden {
    display: none;
  }
}
@media (max-width: 640px) {
  .editor-pane,
  .preview-pane {
    padding: 14px 12px 28px;
  }
  .editor-intro {
    flex-direction: column;
  }
  .form-grid,
  .day-fields,
  .list-columns,
  .contact-grid {
    grid-template-columns: 1fr;
  }
  .wide {
    grid-column: auto;
  }
  .day-card > header {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .day-actions {
    width: 100%;
    padding-left: 46px;
  }
  .item-meta {
    grid-template-columns: 1fr 1fr;
  }
  .tier {
    grid-template-columns: 1fr 110px auto;
  }
}
</style>
