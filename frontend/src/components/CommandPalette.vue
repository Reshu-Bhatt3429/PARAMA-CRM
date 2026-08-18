<template>
  <Dialog v-model:open="show" :size="'xl'" bare>
    <template #default>
      <div class="flex flex-col">
        <!-- Search row -->
        <div
          class="flex items-center gap-2 border-b border-outline-gray-1 px-4 py-3"
        >
          <span class="lucide-search size-4 shrink-0 text-ink-gray-5" />
          <input
            ref="inputRef"
            v-model="query"
            type="text"
            role="combobox"
            aria-controls="crm-palette-listbox"
            aria-expanded="true"
            aria-autocomplete="list"
            :aria-activedescendant="activeRow?.domId || undefined"
            :placeholder="__('Search leads, deals, contacts…')"
            class="w-full border-0 bg-transparent p-0 text-base text-ink-gray-9 placeholder-ink-gray-4 focus:outline-none focus:ring-0"
            autocomplete="off"
            spellcheck="false"
            @keydown="onKeydown"
          />
          <LoadingIndicator v-if="loading" class="size-4 text-ink-gray-5" />
          <span
            v-else
            class="hidden shrink-0 rounded border border-outline-gray-2 px-1.5 py-0.5 text-xs text-ink-gray-5 sm:inline"
          >
            esc
          </span>
        </div>

        <!-- Results -->
        <div
          id="crm-palette-listbox"
          ref="listRef"
          role="listbox"
          :aria-label="__('Search results')"
          class="max-h-[22rem] overflow-y-auto py-2"
        >
          <div
            v-if="!rows.length"
            class="px-4 py-8 text-center text-base text-ink-gray-5"
          >
            {{
              searching
                ? __('No matches for “{0}”.', [query.trim()])
                : __('Type to search.')
            }}
          </div>

          <div v-for="section in sections" :key="section.key" class="mb-1">
            <div
              class="px-4 pb-1 pt-2 text-xs font-medium uppercase tracking-wide text-ink-gray-4"
            >
              {{ __(section.label) }}
            </div>
            <div
              v-for="row in rowsOf(section.key)"
              :id="row.domId"
              :key="row.domId"
              role="option"
              :aria-selected="row.domId === activeRow?.domId"
              class="mx-2 flex cursor-pointer items-center gap-3 rounded px-2 py-1.5"
              :class="
                row.domId === activeRow?.domId
                  ? 'bg-surface-gray-3'
                  : 'hover:bg-surface-gray-2'
              "
              @click="activate(row)"
              @mousemove="setActive(row)"
            >
              <span
                :class="[iconOf(row), 'size-4 shrink-0 text-ink-gray-6']"
                aria-hidden="true"
              />
              <div class="min-w-0 flex-1">
                <div class="truncate text-base text-ink-gray-8">
                  {{ row.kind === 'action' ? __(row.label) : row.title }}
                </div>
                <div
                  v-if="rowSubtitle(row)"
                  class="truncate text-sm text-ink-gray-5"
                >
                  {{ rowSubtitle(row) }}
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Footer legend -->
        <div
          class="hidden items-center gap-4 border-t border-outline-gray-1 px-4 py-2 text-xs text-ink-gray-5 sm:flex"
        >
          <span class="flex items-center gap-1">
            <span class="lucide-arrow-up size-3" aria-hidden="true" />
            <span class="lucide-arrow-down size-3" aria-hidden="true" />
            {{ __('navigate') }}
          </span>
          <span class="flex items-center gap-1">
            <span class="lucide-corner-down-left size-3" aria-hidden="true" />
            {{ __('open') }}
          </span>
        </div>
      </div>
    </template>
  </Dialog>

  <LeadModal v-if="showLeadModal" v-model="showLeadModal" />
  <DealModal v-if="showDealModal" v-model="showDealModal" />
</template>

<script setup>
/**
 * The Cmd/Ctrl+K command palette (master spec §5, items 10 and 11).
 *
 * Mounted once, in `GlobalModals.vue`, so the same instance serves the desktop
 * sidebar trigger, the mobile top-bar trigger and the global shortcut.
 *
 * Two rules from §2 are load-bearing here:
 *  - the palette is NEVER blank. With no query it shows recently viewed records
 *    plus three quick actions.
 *  - every result is permission-checked SERVER-side. Recents are references in
 *    localStorage, and their titles come back from `resolve_records`, which
 *    drops anything the user may no longer read.
 */

import DealModal from '@/components/Modals/DealModal.vue'
import LeadModal from '@/components/Modals/LeadModal.vue'
import { useKeyboardShortcuts } from '@/composables/useKeyboardShortcuts'
import { useRecents } from '@/composables/recents'
import { showCommandPalette } from '@/composables/commandPalette'
import {
  buildSections,
  doctypeIcon,
  flattenSections,
  moveIndex,
  recordRoute,
  rowSubtitle,
} from '@/utils/palette'
import { call, Dialog, LoadingIndicator } from 'frappe-ui'
import { computed, nextTick, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

const SEARCH_DEBOUNCE_MS = 150
const MIN_QUERY_LENGTH = 2

const router = useRouter()
const { recents } = useRecents()

const show = showCommandPalette
const query = ref('')
const results = ref([])
const resolvedRecents = ref([])
const loading = ref(false)
const activeIndex = ref(0)
const inputRef = ref(null)
const listRef = ref(null)
const showLeadModal = ref(false)
const showDealModal = ref(false)

// Every in-flight search carries the token it was started with. A slow request
// for "ba" must never overwrite the answer for "bali" that came back first.
let searchToken = 0
let debounceTimer = null

const searching = computed(() => query.value.trim().length >= MIN_QUERY_LENGTH)

const sections = computed(() =>
  buildSections({
    query: query.value,
    results: results.value,
    recents: resolvedRecents.value,
  }),
)

const rows = computed(() => flattenSections(sections.value))
const activeRow = computed(() => rows.value[activeIndex.value] || null)

function rowsOf(sectionKey) {
  return rows.value.filter((row) => row.sectionKey === sectionKey)
}

function iconOf(row) {
  return row.kind === 'action' ? row.icon : doctypeIcon(row.doctype)
}

function setActive(row) {
  const index = rows.value.findIndex(
    (candidate) => candidate.domId === row.domId,
  )
  if (index >= 0) activeIndex.value = index
}

function scrollActiveIntoView() {
  const id = activeRow.value?.domId
  if (!id || !listRef.value) return
  listRef.value.querySelector(`#${CSS.escape(id)}`)?.scrollIntoView({
    block: 'nearest',
  })
}

function onKeydown(event) {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    activeIndex.value = moveIndex(activeIndex.value, 1, rows.value.length)
    nextTick(scrollActiveIntoView)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    activeIndex.value = moveIndex(activeIndex.value, -1, rows.value.length)
    nextTick(scrollActiveIntoView)
  } else if (event.key === 'Enter') {
    event.preventDefault()
    if (activeRow.value) activate(activeRow.value)
  }
  // Escape is handled by the Dialog itself.
}

function activate(row) {
  if (!row) return

  if (row.kind === 'action') {
    show.value = false
    if (row.route) {
      router.push(row.route)
    } else {
      runAction(row)
    }
    return
  }

  const route = recordRoute(row)
  if (!route) return
  show.value = false
  router.push(route)
}

function runAction(row) {
  if (row.id === 'create-lead') {
    showLeadModal.value = true
  } else if (row.id === 'create-deal') {
    showDealModal.value = true
  }
}

async function runSearch(typed) {
  const token = ++searchToken
  if (typed.trim().length < MIN_QUERY_LENGTH) {
    results.value = []
    loading.value = false
    return
  }

  loading.value = true
  try {
    const payload = await call('crm.api.search.palette_search', {
      query: typed,
    })
    if (token !== searchToken) return
    results.value = payload?.groups || []
  } catch (error) {
    if (token !== searchToken) return
    results.value = []
    console.warn('[crm] Palette search failed.', error)
  } finally {
    if (token === searchToken) loading.value = false
  }
}

watch(query, (typed) => {
  activeIndex.value = 0
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => runSearch(typed), SEARCH_DEBOUNCE_MS)
})

// A shorter list must never leave the highlight past its end.
watch(rows, (list) => {
  if (activeIndex.value >= list.length) activeIndex.value = 0
})

async function loadRecents() {
  const stored = recents.value
  if (!stored.length) {
    resolvedRecents.value = []
    return
  }
  try {
    // The server decides what of this the user may still see.
    resolvedRecents.value =
      (await call('crm.api.search.resolve_records', { records: stored })) || []
  } catch (error) {
    resolvedRecents.value = []
    console.warn('[crm] Could not resolve the recently viewed list.', error)
  }
}

watch(show, (open) => {
  if (!open) {
    clearTimeout(debounceTimer)
    searchToken++
    loading.value = false
    return
  }
  query.value = ''
  results.value = []
  activeIndex.value = 0
  loadRecents()
  nextTick(() => inputRef.value?.focus())
})

useKeyboardShortcuts({
  // `ignoreTyping: false` on purpose: Cmd+K has to work while the cursor sits
  // in a filter box, which is exactly when somebody reaches for it.
  ignoreTyping: false,
  shortcuts: [
    {
      match: (event) =>
        (event.metaKey || event.ctrlKey) && event.key?.toLowerCase() === 'k',
      action: () => (show.value = !show.value),
    },
  ],
})
</script>
