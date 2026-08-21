<template>
  <div
    class="itinerary-preview"
    :data-theme="themeKey"
    :style="{ '--preview-font': previewFont }"
    :aria-label="__('Live itinerary preview')"
  >
    <article class="preview-page cover-page">
      <div
        v-if="form.cover_image"
        class="cover-image"
        :style="{ backgroundImage: `url(${form.cover_image})` }"
      />
      <div class="cover-wash" />

      <header class="cover-header">
        <div class="brand-lockup">
          <img
            v-if="form.brand_logo || agency.logo"
            :src="form.brand_logo || agency.logo"
            :alt="__('Brand logo')"
            class="brand-logo"
          />
          <div v-else class="brand-mark">{{ agencyInitials }}</div>
          <div>
            <div class="brand-name">{{ agency.name || __('Travel Desk') }}</div>
            <div class="brand-subtitle">{{ __('Travel itinerary') }}</div>
          </div>
        </div>
        <div class="document-badge">{{ __('Prepared itinerary') }}</div>
      </header>

      <div class="cover-copy">
        <p v-if="form.subtitle" class="cover-tagline" :style="taglineStyle">
          {{ form.subtitle }}
        </p>
        <h1 :style="titleStyle">{{ displayTitle }}</h1>
        <div v-if="form.customer_name" class="prepared-for">
          <span>{{ __('Prepared for') }}</span>
          <strong>{{ form.customer_name }}</strong>
        </div>
      </div>

      <div class="cover-facts">
        <div>
          <span>{{ __('Destination') }}</span>
          <strong>{{ form.destination || __('To be confirmed') }}</strong>
        </div>
        <div>
          <span>{{ __('Duration') }}</span>
          <strong>{{ durationText }}</strong>
        </div>
        <div>
          <span>{{ __('Group') }}</span>
          <strong>{{ groupText || __('To be confirmed') }}</strong>
        </div>
        <div>
          <span>{{ __('Price') }}</span>
          <strong>{{ priceSummary }}</strong>
        </div>
      </div>

      <footer class="page-footer cover-footer">
        <span>{{ contactLine }}</span>
        <span>{{ __('Version {0}', [version]) }}</span>
      </footer>
    </article>

    <article
      v-for="(pageDays, pageIndex) in dayPages"
      :key="`days-${pageIndex}`"
      class="preview-page content-page"
    >
      <header class="content-header">
        <div>
          <h2>{{ __('Detailed itinerary') }}</h2>
          <p>{{ form.destination || __('Your journey') }}</p>
        </div>
        <span>{{ durationText }}</span>
      </header>

      <div class="day-list">
        <section
          v-for="day in pageDays"
          :key="day.day_number"
          class="day-section"
        >
          <div class="day-heading">
            <div class="day-number">{{ day.day_number }}</div>
            <div>
              <p>{{ dayDate(day.day_number) }}</p>
              <h3>{{ day.title || __('Untitled day') }}</h3>
            </div>
          </div>

          <div class="day-body" :class="{ 'has-image': day.image }">
            <img
              v-if="day.image"
              :src="day.image"
              :alt="day.title"
              class="day-image"
            />
            <div class="day-copy">
              <div v-if="day.highlights.length" class="highlight-list">
                <span v-for="highlight in day.highlights" :key="highlight">{{
                  highlight
                }}</span>
              </div>
              <p v-if="day.description" class="day-description">
                {{ day.description }}
              </p>
              <p v-else-if="day.summary" class="day-description">
                {{ day.summary }}
              </p>

              <div
                v-if="day.accommodation || mealLabels(day).length"
                class="day-essentials"
              >
                <div v-if="day.accommodation">
                  <span>{{ __('Stay') }}</span>
                  <strong>{{ day.accommodation }}</strong>
                </div>
                <div v-if="mealLabels(day).length">
                  <span>{{ __('Meals') }}</span>
                  <strong>{{ mealLabels(day).join(' · ') }}</strong>
                </div>
              </div>

              <div v-if="scheduledItems(day).length" class="schedule-list">
                <div v-for="item in scheduledItems(day)" :key="item.key">
                  <span>{{ __(timeLabels[item.time]) }}</span>
                  <div>
                    <strong>{{ item.title }}</strong>
                    <p v-if="item.description">{{ item.description }}</p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>
      </div>

      <footer class="page-footer">
        <span>{{ agency.name || __('Travel Desk') }}</span>
        <span>{{ __('Page {0}', [pageIndex + 2]) }}</span>
      </footer>
    </article>

    <article class="preview-page content-page details-page">
      <header class="content-header">
        <div>
          <h2>{{ __('Inclusions and policies') }}</h2>
          <p>{{ form.title || __('Travel itinerary') }}</p>
        </div>
      </header>

      <div class="terms-columns">
        <section>
          <h3>{{ __('What is included') }}</h3>
          <ul v-if="inclusions.length">
            <li v-for="item in inclusions" :key="item">{{ item }}</li>
          </ul>
          <p v-else class="empty-copy">
            {{ __('No inclusions have been added.') }}
          </p>
        </section>
        <section>
          <h3>{{ __('What is excluded') }}</h3>
          <ul v-if="exclusions.length">
            <li v-for="item in exclusions" :key="item">{{ item }}</li>
          </ul>
          <p v-else class="empty-copy">
            {{ __('No exclusions have been added.') }}
          </p>
        </section>
      </div>

      <section
        v-if="form.price_tiers?.length || form.budget"
        class="pricing-block"
      >
        <div>
          <h3>{{ __('Package pricing') }}</h3>
          <p>{{ __('Rates are per person and subject to availability.') }}</p>
        </div>
        <div class="pricing-options">
          <div
            v-if="form.departure_type === 'Private Departure' && form.budget"
          >
            <span>{{ __('Private departure') }}</span>
            <strong>{{ money(form.budget) }}</strong>
          </div>
          <div v-for="tier in form.price_tiers" :key="tier.tier_label">
            <span>{{ tier.tier_label }}</span>
            <strong>{{ money(tier.price_per_person) }}</strong>
          </div>
        </div>
      </section>

      <section v-if="terms.length" class="policy-block">
        <h3>{{ __('Terms, conditions and safety') }}</h3>
        <ul>
          <li v-for="item in terms" :key="item">{{ item }}</li>
        </ul>
      </section>

      <section class="contact-block">
        <h3>{{ __('Plan with us') }}</h3>
        <p>{{ contactLine }}</p>
      </section>

      <footer class="page-footer">
        <span>{{ agency.name || __('Travel Desk') }}</span>
        <span>{{ __('Page {0}', [dayPages.length + 2]) }}</span>
      </footer>
    </article>
  </div>
</template>

<script setup>
import {
  TIME_OF_DAY_LABELS,
  linesToArray,
  normalizeDays,
} from '@/utils/itinerary'
import { computed } from 'vue'

const props = defineProps({
  form: { type: Object, required: true },
  days: { type: Array, default: () => [] },
  agency: { type: Object, default: () => ({}) },
  version: { type: Number, default: 1 },
})

const timeLabels = TIME_OF_DAY_LABELS
const normalizedDays = computed(() => normalizeDays(props.days))
const dayPages = computed(() => {
  const pages = []
  for (let index = 0; index < normalizedDays.value.length; index += 2) {
    pages.push(normalizedDays.value.slice(index, index + 2))
  }
  return pages
})

const themeKey = computed(() =>
  String(props.form.theme || 'Sunrise').toLowerCase(),
)
const displayTitle = computed(() => {
  const title = props.form.title || __('Untitled itinerary')
  if (props.form.title_case === 'Uppercase') return title.toUpperCase()
  if (props.form.title_case === 'Capitalize') {
    return title.replace(/\b\w/g, (letter) => letter.toUpperCase())
  }
  return title
})
const previewFont = computed(
  () =>
    ({
      'Modern Alpine': 'ui-sans-serif, system-ui, sans-serif',
      'Classic Bold': 'Arial, Helvetica, sans-serif',
      'Elegant Serif': 'Georgia, Cambria, serif',
      'Clean Geometric': 'Verdana, ui-sans-serif, sans-serif',
    })[props.form.font_preset] || 'ui-sans-serif, system-ui, sans-serif',
)
const titleStyle = computed(() => ({
  fontWeight: props.form.title_weight || '900',
  fontStyle: props.form.title_style === 'Italic' ? 'italic' : 'normal',
}))
const taglineStyle = computed(() => {
  const style = props.form.tagline_style || 'Bold Normal'
  return {
    fontWeight: style.startsWith('Bold') ? '700' : '500',
    fontStyle: style.endsWith('Italic') ? 'italic' : 'normal',
  }
})
const durationText = computed(
  () => props.form.duration_label || durationLabel(props.form.num_days || 1),
)
const groupText = computed(
  () =>
    props.form.group_size_label ||
    (props.form.group_size
      ? __('{0} travellers', [props.form.group_size])
      : ''),
)
const inclusions = computed(() => linesToArray(props.form.inclusions))
const exclusions = computed(() => linesToArray(props.form.exclusions))
const terms = computed(() => linesToArray(props.form.terms))
const agencyInitials = computed(() =>
  String(props.agency.name || 'Travel Desk')
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0])
    .join('')
    .toUpperCase(),
)
const contactLine = computed(() =>
  [
    props.form.contact_email || props.agency.email,
    props.form.contact_phone || props.agency.phone,
    props.form.contact_website || props.agency.website,
  ]
    .filter(Boolean)
    .join(' · '),
)
const priceSummary = computed(() => {
  if (
    props.form.departure_type === 'Private Departure' &&
    Number(props.form.budget)
  ) {
    return money(props.form.budget)
  }
  const prices = (props.form.price_tiers || [])
    .map((tier) => Number(tier.price_per_person))
    .filter((value) => Number.isFinite(value) && value > 0)
  return prices.length
    ? __('From {0}', [money(Math.min(...prices))])
    : __('On request')
})

function money(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return ''
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: props.form.currency || 'INR',
      maximumFractionDigits: 0,
    }).format(number)
  } catch {
    return `${props.form.currency || 'INR'} ${number.toLocaleString()}`
  }
}

function durationLabel(days) {
  const nights = Math.max(0, days - 1)
  return `${days} ${days === 1 ? __('Day') : __('Days')} / ${nights} ${nights === 1 ? __('Night') : __('Nights')}`
}

function dayDate(dayNumber) {
  if (!props.form.start_date) return __('Day {0}', [dayNumber])
  const date = new Date(`${props.form.start_date}T00:00:00`)
  date.setDate(date.getDate() + dayNumber - 1)
  return date.toLocaleDateString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  })
}

function mealLabels(day) {
  return [
    day.meals.breakfast && __('Breakfast'),
    day.meals.lunch && __('Lunch'),
    day.meals.dinner && __('Dinner'),
  ].filter(Boolean)
}

function scheduledItems(day) {
  return day.slots.flatMap((slot) =>
    slot.items.map((item, index) => ({
      ...item,
      time: slot.time_of_day,
      key: `${slot.time_of_day}-${index}-${item.title}`,
    })),
  )
}
</script>

<style scoped>
.itinerary-preview {
  --accent: #ea580c;
  --accent-soft: #ffedd5;
  --paper: #fffdf9;
  --ink: #172033;
  --muted: #596579;
  --line: #e7dfd4;
  display: grid;
  gap: 18px;
  color: var(--ink);
  font-family: var(--preview-font);
}

.itinerary-preview[data-theme='midnight'] {
  --accent: #2563eb;
  --accent-soft: #dbeafe;
  --paper: #f8fbff;
  --ink: #07111f;
  --muted: #50647f;
  --line: #d8e3f2;
}

.itinerary-preview[data-theme='meadow'] {
  --accent: #15803d;
  --accent-soft: #dcfce7;
  --paper: #fbfdf8;
  --ink: #173228;
  --muted: #587064;
  --line: #dce8dc;
}

.preview-page {
  position: relative;
  width: 100%;
  min-height: min(1120px, calc((100vw - 390px) * 1.414));
  aspect-ratio: 210 / 297;
  overflow: hidden;
  border-radius: 14px;
  background: var(--paper);
  box-shadow: 0 16px 38px rgba(20, 28, 45, 0.14);
}

.cover-page {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: clamp(26px, 5vw, 58px);
  color: white;
  background: #101827;
}

.cover-image,
.cover-wash {
  position: absolute;
  inset: 0;
}

.cover-image {
  background-position: center;
  background-size: cover;
}

.cover-wash {
  background: linear-gradient(
    180deg,
    rgba(10, 16, 28, 0.42),
    rgba(10, 16, 28, 0.96) 82%
  );
}

.itinerary-preview[data-theme='midnight'] .cover-wash {
  background: linear-gradient(
    180deg,
    rgba(3, 10, 22, 0.35),
    rgba(3, 10, 22, 0.98) 80%
  );
}

.itinerary-preview[data-theme='meadow'] .cover-wash {
  background: linear-gradient(
    180deg,
    rgba(10, 35, 27, 0.28),
    rgba(10, 35, 27, 0.94) 82%
  );
}

.cover-header,
.cover-copy,
.cover-facts,
.page-footer {
  position: relative;
  z-index: 1;
}

.cover-header,
.content-header,
.page-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
}

.brand-lockup {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-logo,
.brand-mark {
  width: 46px;
  height: 46px;
  border-radius: 12px;
}

.brand-logo {
  object-fit: contain;
  background: white;
  padding: 5px;
}

.brand-mark {
  display: grid;
  place-items: center;
  background: var(--accent);
  color: white;
  font-weight: 800;
}

.brand-name {
  font-size: 14px;
  font-weight: 700;
}

.brand-subtitle,
.document-badge {
  color: rgba(255, 255, 255, 0.72);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
}

.document-badge {
  padding: 7px 10px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.12);
}

.cover-copy {
  max-width: 78%;
  margin-top: auto;
  padding-top: 42%;
}

.cover-tagline {
  max-width: 48ch;
  margin-bottom: 12px;
  color: color-mix(in srgb, var(--accent) 60%, white);
  font-size: clamp(13px, 2vw, 20px);
  line-height: 1.35;
}

.cover-copy h1 {
  max-width: 12ch;
  font-size: clamp(34px, 7vw, 76px);
  line-height: 0.98;
  letter-spacing: -0.03em;
  text-wrap: balance;
}

.prepared-for {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-top: 24px;
  color: rgba(255, 255, 255, 0.72);
  font-size: 12px;
}

.prepared-for strong {
  color: white;
  font-size: 16px;
}

.cover-facts {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 18px;
  margin-top: 44px;
  padding: 18px 0;
  border-top: 1px solid rgba(255, 255, 255, 0.22);
  border-bottom: 1px solid rgba(255, 255, 255, 0.22);
}

.cover-facts div,
.day-essentials div {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.cover-facts span,
.day-essentials span,
.pricing-options span {
  color: rgba(255, 255, 255, 0.62);
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.cover-facts strong {
  font-size: 12px;
  line-height: 1.35;
}

.page-footer {
  margin-top: 24px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
  color: var(--muted);
  font-size: 9px;
}

.cover-footer {
  border-color: rgba(255, 255, 255, 0.2);
  color: rgba(255, 255, 255, 0.68);
}

.content-page {
  display: flex;
  flex-direction: column;
  padding: clamp(24px, 4vw, 48px);
}

.content-header {
  padding-bottom: 18px;
  border-bottom: 1px solid var(--line);
}

.content-header h2 {
  font-size: clamp(22px, 4vw, 38px);
  line-height: 1.05;
  letter-spacing: -0.025em;
}

.content-header p,
.content-header > span {
  margin-top: 4px;
  color: var(--muted);
  font-size: 11px;
}

.day-list {
  flex: 1;
}

.day-section {
  padding: clamp(20px, 3vw, 34px) 0;
  border-bottom: 1px solid var(--line);
}

.day-section:last-child {
  border-bottom: 0;
}

.day-heading {
  display: flex;
  align-items: center;
  gap: 14px;
}

.day-number {
  display: grid;
  width: 38px;
  height: 38px;
  flex: none;
  place-items: center;
  border-radius: 50%;
  background: var(--accent);
  color: white;
  font-size: 17px;
  font-weight: 800;
}

.day-heading p {
  color: var(--muted);
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}

.day-heading h3,
.terms-columns h3,
.pricing-block h3,
.policy-block h3,
.contact-block h3 {
  margin-top: 3px;
  font-size: clamp(15px, 2vw, 21px);
  line-height: 1.2;
}

.day-body {
  display: grid;
  gap: 18px;
  margin-top: 16px;
}

.day-body.has-image {
  grid-template-columns: minmax(150px, 32%) 1fr;
}

.day-image {
  width: 100%;
  height: 100%;
  min-height: 160px;
  border-radius: 12px;
  object-fit: cover;
}

.highlight-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.highlight-list span {
  padding: 5px 8px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--ink);
  font-size: 9px;
  font-weight: 600;
}

.day-description {
  max-width: 70ch;
  margin-top: 12px;
  color: var(--muted);
  font-size: clamp(10px, 1.3vw, 13px);
  line-height: 1.55;
}

.day-essentials {
  display: flex;
  gap: 28px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}

.day-essentials span,
.pricing-options span {
  color: var(--muted);
}

.day-essentials strong {
  font-size: 10px;
}

.schedule-list {
  margin-top: 14px;
}

.schedule-list > div {
  display: grid;
  grid-template-columns: 74px 1fr;
  gap: 10px;
  padding: 7px 0;
  border-top: 1px solid var(--line);
}

.schedule-list span {
  color: var(--accent);
  font-size: 9px;
  font-weight: 700;
  text-transform: uppercase;
}

.schedule-list strong,
.schedule-list p {
  font-size: 10px;
}

.schedule-list p {
  margin-top: 2px;
  color: var(--muted);
  line-height: 1.4;
}

.terms-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 30px;
  margin-top: 30px;
}

.terms-columns section {
  min-width: 0;
}

.terms-columns ul,
.policy-block ul {
  margin: 14px 0 0;
  padding-left: 18px;
  color: var(--muted);
  font-size: 11px;
  line-height: 1.55;
}

.terms-columns li,
.policy-block li {
  margin-bottom: 7px;
}

.empty-copy {
  margin-top: 12px;
  color: var(--muted);
  font-size: 11px;
}

.pricing-block {
  display: grid;
  grid-template-columns: minmax(150px, 0.8fr) 1.4fr;
  gap: 24px;
  margin-top: 32px;
  padding: 22px;
  border-radius: 14px;
  background: var(--ink);
  color: white;
}

.pricing-block p {
  margin-top: 6px;
  color: rgba(255, 255, 255, 0.62);
  font-size: 10px;
}

.pricing-options {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(100px, 1fr));
  gap: 12px;
}

.pricing-options div {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.pricing-options strong {
  font-size: 14px;
}

.policy-block,
.contact-block {
  margin-top: 30px;
}

.contact-block {
  margin-top: auto;
  padding: 22px 0;
}

.contact-block p {
  margin-top: 8px;
  color: var(--muted);
  font-size: 11px;
}

@media (max-width: 760px) {
  .preview-page {
    min-height: 0;
  }

  .cover-facts {
    grid-template-columns: 1fr 1fr;
  }

  .day-body.has-image,
  .terms-columns,
  .pricing-block {
    grid-template-columns: 1fr;
  }

  .cover-copy {
    max-width: 100%;
  }
}
</style>
