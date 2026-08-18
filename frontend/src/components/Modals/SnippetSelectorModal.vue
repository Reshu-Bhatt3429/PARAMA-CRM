<template>
  <Dialog v-model:open="show" :title="__('Snippets')" :size="'3xl'">
    <template #default>
      <div class="flex items-center gap-2">
        <TextInput
          ref="searchInput"
          v-model="search"
          class="w-full"
          type="text"
          :placeholder="__('Search by name or shortcut')"
          @keydown.enter="applyFirst"
        >
          <template #prefix>
            <span
              class="lucide-search h-4 w-4 text-ink-gray-4"
              aria-hidden="true"
            />
          </template>
        </TextInput>
        <Button
          :label="__('Manage')"
          icon-left="lucide-settings"
          @click="manage"
        />
      </div>
      <div
        v-if="visibleSnippets.length"
        class="mt-4 flex max-h-96 flex-col gap-1 overflow-y-auto"
      >
        <button
          v-for="snippet in visibleSnippets"
          :key="snippet.name"
          type="button"
          class="flex cursor-pointer flex-col gap-1 rounded-lg border p-3 text-left hover:bg-surface-gray-2"
          @click="apply(snippet)"
        >
          <div class="flex items-center gap-2">
            <span class="text-base-semibold text-ink-gray-8">
              {{ snippet.title }}
            </span>
            <span class="text-sm text-ink-gray-5">/{{ snippet.shortcut }}</span>
            <Badge
              v-if="snippet.shared"
              :label="__('Shared')"
              variant="subtle"
              theme="blue"
            />
          </div>
          <div class="line-clamp-2 text-sm text-ink-gray-5">
            {{ preview(snippet.body) }}
          </div>
        </button>
      </div>
      <div v-else class="mt-2">
        <div class="flex h-40 flex-col items-center justify-center">
          <div class="text-lg text-ink-gray-4">
            {{ __('No snippets found') }}
          </div>
          <Button :label="__('Create one')" class="mt-4" @click="manage" />
        </div>
      </div>
    </template>
  </Dialog>
</template>

<script setup>
import { showSettings, activeSettingsPage } from '@/composables/settings'
import { filterSnippets, htmlToText } from '@/utils/snippets'
import {
  Badge,
  Dialog,
  TextInput,
  call,
  createResource,
  toast,
} from 'frappe-ui'
import { ref, computed, nextTick, watch } from 'vue'

const props = defineProps({
  // The record the tokens merge against. Both are optional: a snippet with no
  // record tokens is still useful in a composer that is not on a record.
  doctype: { type: String, default: '' },
  docname: { type: String, default: '' },
  // WhatsApp takes plain text, the email composer takes the HTML as authored.
  plainText: { type: Boolean, default: false },
  // Prefills the search box from what the user already typed after "/".
  query: { type: String, default: '' },
})

const show = defineModel({ type: Boolean })

const emit = defineEmits(['apply'])

const search = ref('')
const searchInput = ref(null)

const snippets = createResource({
  url: 'crm.api.snippets.get_snippets',
  cache: 'crm-snippets',
  auto: true,
})

const visibleSnippets = computed(() =>
  filterSnippets(snippets.data ?? [], search.value),
)

function preview(body) {
  return htmlToText(body).slice(0, 160)
}

function manage() {
  show.value = false
  showSettings.value = true
  activeSettingsPage.value = 'Snippets'
}

/**
 * Merge on the SERVER, then hand the result up. The endpoint resolves the
 * tokens against the record after checking that this user may read it, so the
 * composer never has to hold — or be trusted with — the record's fields.
 */
async function apply(snippet) {
  try {
    const rendered = await call('crm.api.snippets.render', {
      snippet: snippet.name,
      doctype: props.doctype || undefined,
      docname: props.docname || undefined,
    })
    const body = props.plainText ? htmlToText(rendered.body) : rendered.body
    emit('apply', { snippet, body })
    show.value = false
  } catch (error) {
    toast.error(error?.messages?.[0] || __('Could not insert the snippet'))
  }
}

function applyFirst() {
  const first = visibleSnippets.value[0]
  if (first) apply(first)
}

watch(show, (value) => {
  if (!value) return
  search.value = props.query || ''
  snippets.reload()
  nextTick(() => searchInput.value?.el?.focus())
})
</script>
