/**
 * The Today payload, fetched once and shared (master spec §5, item 24).
 *
 * Three surfaces read it and it is ONE request: the Today page renders the
 * list, the sidebar renders the count badge, and the Deals list asks whether
 * deal-health chips may render at all.
 *
 * `dealHealthEnabled` starts false and stays false if the fetch fails. That is
 * deliberate: the acceptance criterion for item 22 is that a switched-off flag
 * leaves NO chip anywhere, and a client that guessed "probably on" while the
 * server was unreachable would break it.
 */

import { removeItem } from '@/utils/today'
import { createResource } from 'frappe-ui'
import { computed, ref } from 'vue'

const EMPTY = {
  items: [],
  counts: { all: 0, task: 0, reply: 0, deal: 0, approval: 0 },
  deal_health_enabled: false,
}

const payload = ref({ ...EMPTY })
const loaded = ref(false)

export const todayResource = createResource({
  url: 'crm.api.today.get_today',
  auto: true,
  onSuccess: (data) => {
    payload.value = { ...EMPTY, ...data }
    loaded.value = true
  },
  onError: () => {
    payload.value = { ...EMPTY }
    loaded.value = true
  },
})

export const todayPayload = computed(() => payload.value)
export const todayItems = computed(() => payload.value.items || [])
export const todayCounts = computed(() => payload.value.counts || EMPTY.counts)
export const todayCount = computed(() => Number(todayCounts.value.all) || 0)
export const todayLoaded = computed(() => loaded.value)
export const dealHealthEnabled = computed(() =>
  Boolean(payload.value.deal_health_enabled),
)

export function reloadToday() {
  return todayResource.reload()
}

/**
 * Drop one row the reader has just acted on, then refresh in the background.
 *
 * The local removal is what makes the row disappear without a reload (item 24's
 * acceptance criterion); the refresh is what keeps the counts honest when the
 * action also changed something else.
 */
export function dropTodayItem(key) {
  payload.value = removeItem(payload.value, key)
  todayResource.reload()
}
