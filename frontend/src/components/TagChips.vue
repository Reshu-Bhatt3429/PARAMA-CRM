<template>
  <div class="flex flex-wrap items-center gap-1" :aria-label="__('Tags')">
    <span
      v-for="tag in chips"
      :key="tag"
      class="group inline-flex max-w-[10rem] items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset transition-opacity duration-200"
      :class="[tagChipClass(tag), removing.has(tag) ? 'opacity-30' : '']"
      :title="tag"
    >
      <!-- `.stop.prevent`: in a list row the whole row is a link to the
           record, and a chip click means "filter", not "open". -->
      <button
        v-if="clickable"
        type="button"
        class="max-w-full truncate"
        @click.stop.prevent="emit('filter', tag)"
      >
        {{ tag }}
      </button>
      <span v-else class="max-w-full truncate">{{ tag }}</span>
      <button
        v-if="!readonly"
        type="button"
        class="lucide-x size-3 shrink-0 opacity-50 hover:opacity-100"
        :aria-label="__('Remove tag {0}', [tag])"
        @click.stop.prevent="remove(tag)"
      />
    </span>

    <button
      v-if="overflow"
      type="button"
      class="inline-flex items-center rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs font-medium text-ink-gray-7 ring-1 ring-inset ring-outline-gray-1 hover:bg-surface-gray-3"
      :title="hidden.join(', ')"
      :aria-expanded="expanded"
      :aria-label="
        expanded ? __('Show fewer tags') : __('Show {0} more tags', [overflow])
      "
      @click.stop.prevent="expanded = !expanded"
    >
      <span
        v-if="expanded"
        class="lucide-chevron-left size-3"
        aria-hidden="true"
      />
      <template v-else>+{{ overflow }}</template>
    </button>

    <Combobox
      v-if="!readonly"
      v-model="picked"
      v-model:query="query"
      :options="options"
      :filterable="false"
      trigger="button"
      :placeholder="__('Add a tag')"
      :empty-text="__('No tags yet')"
      @update:open="onOpenChange"
    >
      <template #trigger>
        <button
          type="button"
          class="inline-flex items-center gap-0.5 rounded-full border border-dashed border-outline-gray-2 px-2 py-0.5 text-xs text-ink-gray-6 hover:border-outline-gray-3 hover:text-ink-gray-8"
          :aria-label="__('Add a tag')"
        >
          <span class="lucide-plus size-3" aria-hidden="true" />
          <span v-if="!tags.length">{{ __('Tag') }}</span>
        </button>
      </template>
    </Combobox>
  </div>
</template>

<script setup>
/**
 * Tag chips for a record (master spec §5, item 2; UX §2.13).
 *
 * §2.13 is the reason this is a component and not a `v-for`: the record header
 * is not a badge shelf, so at most `max` chips render and the rest collapse
 * into one "+N" that expands on click.
 *
 * Removal is optimistic — the chip fades out the moment it is clicked and the
 * list is put back with a toast if the server refuses. Adding is not: a chip
 * that appears and then vanishes reads as a bug, and the add round trip is one
 * request on a record the user is already looking at.
 *
 * Every write goes through `crm.api.tags`, which checks WRITE on this record
 * before it touches the column (master spec §3).
 */

import {
  canCreateTag,
  rankTags,
  splitTags,
  tagChipClass,
  visibleTags,
} from '@/utils/tags'
import { call, Combobox, toast } from 'frappe-ui'
import { computed, ref, watch } from 'vue'

const props = defineProps({
  doctype: { type: String, required: true },
  name: { type: String, required: true },
  max: { type: Number, default: 2 },
  readonly: { type: Boolean, default: false },
  /** Renders each chip as a button that emits `filter` — used in list rows. */
  clickable: { type: Boolean, default: false },
  /** Skip the initial fetch when the caller already has `_user_tags`. */
  initial: { type: [String, Array], default: null },
})

const emit = defineEmits(['filter', 'update'])

const tags = ref(splitTags(props.initial))
const known = ref([])
const query = ref('')
const picked = ref(null)
const expanded = ref(false)
const removing = ref(new Set())

const view = computed(() => visibleTags(tags.value, props.max))
const chips = computed(() =>
  expanded.value ? splitTags(tags.value) : view.value.shown,
)
const hidden = computed(() => view.value.hidden)
const overflow = computed(() => view.value.overflow)

const options = computed(() => {
  const rows = rankTags(known.value, query.value, { exclude: tags.value }).map(
    (tag) => ({
      label: tag,
      value: tag,
    }),
  )

  rows.push({
    type: 'custom',
    key: 'create-tag',
    label: __("Create '{0}'", [query.value.trim()]),
    icon: 'lucide-plus',
    condition: ({ query: typed }) =>
      canCreateTag(typed, { known: known.value, onRecord: tags.value }),
    onClick: ({ query: typed }) => add(typed),
  })

  return rows
})

function setTags(next) {
  tags.value = splitTags(next)
  emit('update', tags.value)
}

async function load() {
  if (props.initial !== null) return
  try {
    setTags(
      await call('crm.api.tags.get_tags', {
        doctype: props.doctype,
        name: props.name,
      }),
    )
  } catch (error) {
    // A record whose tags cannot be read renders no chips rather than an error
    // banner: the tag row is decoration on a page that has already loaded.
    console.warn('[crm] Could not read tags.', error)
  }
}

async function loadVocabulary() {
  try {
    known.value = await call('crm.api.tags.search_tags', {
      doctype: props.doctype,
    })
  } catch (error) {
    known.value = []
    console.warn('[crm] Could not read the tag list.', error)
  }
}

async function add(tag) {
  const wanted = String(tag ?? '').trim()
  if (!wanted) return
  query.value = ''
  picked.value = null
  try {
    setTags(
      await call('crm.api.tags.add_tag', {
        doctype: props.doctype,
        name: props.name,
        tag: wanted,
      }),
    )
    if (
      !known.value.some(
        (existing) => existing.toLowerCase() === wanted.toLowerCase(),
      )
    ) {
      known.value = [...known.value, wanted]
    }
  } catch (error) {
    toast.error(error.messages?.[0] || __('Could not add the tag.'))
  }
}

async function remove(tag) {
  if (removing.value.has(tag)) return

  // Optimistic: fade the chip now, put it back only if the server refuses.
  const before = [...tags.value]
  removing.value = new Set([...removing.value, tag])
  try {
    const after = await call('crm.api.tags.remove_tag', {
      doctype: props.doctype,
      name: props.name,
      tag,
    })
    setTags(after)
  } catch (error) {
    setTags(before)
    toast.error(error.messages?.[0] || __('Could not remove the tag.'))
  } finally {
    const next = new Set(removing.value)
    next.delete(tag)
    removing.value = next
  }
}

function onOpenChange(open) {
  if (open) {
    query.value = ''
    if (!known.value.length) loadVocabulary()
  }
}

watch(picked, (value) => {
  if (value) add(value)
})

watch(
  () => [props.doctype, props.name],
  () => {
    expanded.value = false
    tags.value = splitTags(props.initial)
    load()
  },
  { immediate: true },
)

watch(
  () => props.initial,
  (value) => {
    if (value !== null) tags.value = splitTags(value)
  },
)
</script>
