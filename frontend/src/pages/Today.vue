<template>
  <LayoutHeader>
    <template #left-header>
      <div class="flex items-center gap-2 text-lg font-medium text-ink-gray-9">
        <span class="lucide-sun size-4 text-ink-gray-7" aria-hidden="true" />
        {{ __('Today') }}
      </div>
    </template>
    <template #right-header>
      <Button
        :label="__('Refresh')"
        :loading="todayResource.loading"
        @click="reloadToday"
      >
        <template #prefix>
          <span class="lucide-refresh-ccw size-4" aria-hidden="true" />
        </template>
      </Button>
    </template>
  </LayoutHeader>

  <div class="flex flex-1 flex-col overflow-hidden">
    <!-- Filter chips. UX §2.17: chips narrow ONE list; they are not tabs onto
         four separate panels. -->
    <FadedScrollableDiv
      class="flex shrink-0 items-center gap-2 overflow-x-auto px-4 py-3 sm:px-5"
      orientation="horizontal"
    >
      <button
        v-for="chip in chips"
        :key="chip.key"
        type="button"
        class="inline-flex shrink-0 items-center gap-1.5 rounded-full px-3 py-1 text-sm ring-1 ring-inset transition-colors"
        :class="
          filter === chip.key
            ? 'bg-surface-gray-9 text-ink-base ring-transparent'
            : 'bg-surface-white text-ink-gray-7 ring-outline-gray-2 hover:bg-surface-gray-2'
        "
        :aria-pressed="filter === chip.key"
        @click="setFilter(chip.key)"
      >
        {{ __(chip.label) }}
        <span
          class="rounded-full px-1.5 text-xs"
          :class="
            filter === chip.key ? 'bg-surface-gray-8' : 'bg-surface-gray-2'
          "
        >
          {{ chip.count }}
        </span>
      </button>
    </FadedScrollableDiv>

    <div class="flex-1 overflow-y-auto px-4 pb-8 sm:px-5">
      <div
        v-if="todayResource.loading && !todayLoaded"
        class="flex flex-col gap-2 pt-2"
      >
        <div
          v-for="n in 5"
          :key="n"
          class="h-14 animate-pulse rounded-lg bg-surface-gray-2"
        />
      </div>

      <div
        v-else-if="!visible.length"
        class="flex flex-col items-center justify-center gap-2 py-20 text-center"
      >
        <span
          class="lucide-circle-check-big size-8 text-ink-gray-4"
          aria-hidden="true"
        />
        <div class="text-base text-ink-gray-7">
          {{ emptyMessage }}
        </div>
      </div>

      <ul
        v-else
        ref="listEl"
        class="flex flex-col divide-y divide-outline-gray-1"
        role="listbox"
        tabindex="0"
        :aria-activedescendant="activeId"
        :aria-label="__('Today')"
        @keydown="onKeydown"
      >
        <li
          v-for="(item, index) in visible"
          :id="rowId(index)"
          :key="item.key"
          role="option"
          :aria-selected="index === cursor"
          class="flex items-center gap-3 px-1 py-3"
          :class="index === cursor ? 'bg-surface-gray-2' : ''"
          @mouseenter="cursor = index"
        >
          <span
            class="size-4 shrink-0 text-ink-gray-6"
            :class="typeIcon(item.type)"
            aria-hidden="true"
          />

          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2">
              <span class="truncate text-base text-ink-gray-9">
                {{ item.title }}
              </span>
              <span
                v-if="item.overdue"
                class="shrink-0 rounded-full bg-surface-red-1 px-2 py-0.5 text-xs font-medium text-ink-red-4"
              >
                {{ __('Overdue') }}
              </span>
            </div>
            <div class="truncate text-sm text-ink-gray-5">
              {{ item.context }}
            </div>
          </div>

          <!-- ONE inline action per row (UX §2.17). -->
          <Button
            class="shrink-0"
            :label="__(actionLabel(item))"
            :loading="busyKey === item.key"
            @click="act(item)"
          />
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup>
/**
 * Today (master spec §5, item 24; UX §2.17).
 *
 * ONE prioritised list, four sources, filter chips — deliberately not four
 * stacked panels. The server merges and sorts, so this component only filters,
 * moves a cursor and fires one action per row.
 *
 * Acting removes the row locally and reloads in the background, so the list
 * settles without the page blinking. Approve is the ONLY write, and it is the
 * existing `crm.api.followup_engine.approve_pending` with its own manager check
 * and row lock; Open and Reply just navigate.
 *
 * Keyboard: j / k / arrows move, Enter fires the row's action. The handler is on
 * the list rather than on `window`, so it cannot swallow a keystroke meant for
 * the Cmd+K palette or a modal on top of this page.
 */

import LayoutHeader from '@/components/LayoutHeader.vue'
import FadedScrollableDiv from '@/components/FadedScrollableDiv.vue'
import {
  dropTodayItem,
  reloadToday,
  todayCounts,
  todayItems,
  todayLoaded,
  todayResource,
} from '@/composables/today'
import {
  TODAY_FILTERS,
  actionLabel,
  chipCounts,
  filterItems,
  itemRoute,
  moveCursor,
  replyRoute,
  typeIcon,
} from '@/utils/today'
import { call, toast, usePageMeta } from 'frappe-ui'
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

const filter = ref('all')
const cursor = ref(-1)
const busyKey = ref('')
const listEl = ref(null)

const chips = computed(() => chipCounts(todayCounts.value))
const visible = computed(() => filterItems(todayItems.value, filter.value))

const activeId = computed(() =>
  cursor.value >= 0 ? rowId(cursor.value) : undefined,
)

const emptyMessage = computed(() => {
  if (filter.value !== 'all') {
    const chip = TODAY_FILTERS.find((row) => row.key === filter.value)
    return __('Nothing under {0} right now.', [__(chip?.label || '')])
  }
  return __('Nothing needs you right now.')
})

function rowId(index) {
  return `crm-today-row-${index}`
}

function setFilter(key) {
  filter.value = key
  cursor.value = -1
}

function onKeydown(event) {
  const keys = {
    j: 1,
    ArrowDown: 1,
    k: -1,
    ArrowUp: -1,
  }

  if (event.key in keys) {
    event.preventDefault()
    cursor.value = moveCursor(
      cursor.value,
      keys[event.key],
      visible.value.length,
    )
    document.getElementById(rowId(cursor.value))?.scrollIntoView({
      block: 'nearest',
    })
    return
  }

  if (event.key === 'Enter' && cursor.value >= 0) {
    event.preventDefault()
    act(visible.value[cursor.value])
  }
}

async function act(item) {
  if (!item || busyKey.value) return

  if (item.action === 'approve') {
    await approve(item)
    return
  }

  const target = item.action === 'reply' ? replyRoute(item) : itemRoute(item)
  if (!target) {
    toast.error(__('This item has nowhere to open.'))
    return
  }
  router.push(target)
}

async function approve(item) {
  busyKey.value = item.key
  try {
    await call('crm.api.followup_engine.approve_pending', {
      followup: item.name,
    })
    // The row goes now; the reload behind it keeps the counts honest.
    dropTodayItem(item.key)
    toast.success(__('Follow-up approved.'))
  } catch (error) {
    toast.error(error.messages?.[0] || __('Could not approve the follow-up.'))
  } finally {
    busyKey.value = ''
  }
}

// A filter change can shorten the list under the cursor.
watch(visible, (rows) => {
  if (cursor.value >= rows.length) cursor.value = rows.length - 1
})

usePageMeta(() => ({ title: __('Today') }))
</script>
