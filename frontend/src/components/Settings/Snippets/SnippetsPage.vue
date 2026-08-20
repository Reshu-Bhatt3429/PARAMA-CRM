<template>
  <div class="flex h-full flex-col gap-6 p-6 text-ink-gray-8">
    <!-- Header -->
    <div class="flex justify-between px-2 pt-2">
      <div class="flex w-9/12 flex-col gap-1">
        <h2 class="flex h-5 gap-2 text-2xl-semibold leading-none">
          {{ __('Snippets') }}
        </h2>
        <p class="text-p-base text-ink-gray-6">
          {{
            __(
              'Short pieces of text you insert into an email or a WhatsApp reply by typing / in the composer.',
            )
          }}
        </p>
      </div>
      <div class="item-center flex w-3/12 justify-end space-x-2">
        <Button
          :label="__('New')"
          icon-left="lucide-plus"
          variant="solid"
          @click="startCreate()"
        />
      </div>
    </div>

    <!-- Loading -->
    <div
      v-if="snippets.loading"
      class="mt-28 flex h-full w-full justify-between"
    >
      <Button :loading="true" variant="ghost" class="w-full" size="2xl" />
    </div>

    <!-- Empty -->
    <div
      v-else-if="!snippets.data?.length"
      class="flex flex-1 flex-col items-center justify-center gap-3 text-ink-gray-5"
    >
      <div class="text-lg">{{ __('No snippets yet') }}</div>
      <Button :label="__('Create one')" @click="startCreate()" />
    </div>

    <!-- List -->
    <div v-else class="flex flex-col gap-2 overflow-y-auto px-2">
      <div
        v-for="snippet in snippets.data"
        :key="snippet.name"
        class="flex items-center gap-3 rounded-lg border p-3"
      >
        <div class="flex min-w-0 flex-1 flex-col gap-1">
          <div class="flex items-center gap-2">
            <span class="truncate text-base-semibold text-ink-gray-8">
              {{ snippet.title }}
            </span>
            <span class="shrink-0 text-sm text-ink-gray-5">
              /{{ snippet.shortcut }}
            </span>
            <Badge
              v-if="snippet.shared"
              :label="__('Shared')"
              variant="subtle"
              theme="blue"
            />
            <Badge
              v-if="!snippet.enabled"
              :label="__('Disabled')"
              variant="subtle"
              theme="gray"
            />
          </div>
          <div class="line-clamp-2 text-sm text-ink-gray-5">
            {{ htmlToText(snippet.body) }}
          </div>
        </div>
        <Button
          v-if="canEdit(snippet)"
          :tooltip="__('Edit')"
          icon="lucide-pencil"
          variant="ghost"
          @click="startEdit(snippet)"
        />
        <Button
          v-if="canEdit(snippet)"
          :tooltip="__('Delete')"
          icon="lucide-trash-2"
          variant="ghost"
          @click="remove(snippet)"
        />
      </div>
    </div>

    <!-- Create / edit -->
    <Dialog
      v-model:open="showForm"
      :title="editing?.name ? __('Edit Snippet') : __('New Snippet')"
      :size="'2xl'"
    >
      <template #body-content>
        <div class="flex flex-col gap-4">
          <FormControl
            v-model="form.title"
            :label="__('Title')"
            :placeholder="__('Booking confirmation')"
          />
          <FormControl
            v-model="form.shortcut"
            :label="__('Shortcut')"
            :placeholder="__('booking')"
            :description="
              __('Typed after / in a composer. Letters, digits, - and _ only.')
            "
          />
          <div class="flex flex-col gap-1.5">
            <span class="text-xs text-ink-gray-5">{{ __('Body') }}</span>
            <TextEditor
              :content="form.body"
              :editable="true"
              editor-class="prose-sm max-w-none min-h-32 rounded-b border-t-0 p-2 focus:outline-none"
              class="rounded border"
              @change="(value) => (form.body = value)"
            />
            <!-- Built in the script: a literal {{ }} inside an interpolation
                 is not something the template compiler can parse. -->
            <span class="text-xs text-ink-gray-5" v-text="tokenHint" />
          </div>
          <div class="flex items-center gap-4">
            <FormControl
              v-model="form.enabled"
              type="checkbox"
              :label="__('Enabled')"
            />
            <FormControl
              v-if="isManager"
              v-model="form.shared"
              type="checkbox"
              :label="__('Share with the team')"
            />
          </div>
          <ErrorMessage :message="error" />
        </div>
      </template>
      <template #actions>
        <div class="flex justify-end gap-2">
          <Button :label="__('Cancel')" @click="showForm = false" />
          <Button
            variant="solid"
            :label="__('Save')"
            :loading="saving"
            @click="save()"
          />
        </div>
      </template>
    </Dialog>
  </div>
</template>

<script setup>
import { htmlToText } from '@/utils/snippets'
import { usersStore } from '@/stores/users'
import {
  Badge,
  Dialog,
  ErrorMessage,
  FormControl,
  TextEditor,
  call,
  createListResource,
  toast,
} from 'frappe-ui'
import { computed, reactive, ref } from 'vue'

const { getUser, isManager: userIsManager } = usersStore()

// Sharing is a manager decision, and the server enforces it
// (`CRMSnippet.check_shared_is_a_manager_decision`). Hiding the toggle keeps a
// Sales User from meeting an error they could not have predicted.
const isManager = computed(() => Boolean(userIsManager()))
const sessionUser = computed(() => getUser().email)

// The token examples carry braces, so they are assembled here rather than in
// the template, where `{{ }}` is interpolation syntax.
const tokenHint = computed(() =>
  __('Use {0} to pull in the record, for example {1} or {2}.', [
    '{{ field }}',
    '{{ lead_name }}',
    '{{ user.full_name }}',
  ]),
)

const snippets = createListResource({
  type: 'list',
  doctype: 'CRM Snippet',
  cache: 'crm-snippet-settings',
  fields: ['name', 'title', 'shortcut', 'body', 'shared', 'enabled', 'owner'],
  orderBy: 'modified desc',
  pageLength: 100,
  auto: true,
})

const showForm = ref(false)
const editing = ref(null)
const saving = ref(false)
const error = ref('')
const form = reactive({
  title: '',
  shortcut: '',
  body: '',
  shared: false,
  enabled: true,
})

/** Write access mirrors `has_snippet_permission`: own rows, or anything if manager. */
function canEdit(snippet) {
  return isManager.value || snippet.owner === sessionUser.value
}

function startCreate() {
  editing.value = null
  error.value = ''
  Object.assign(form, {
    title: '',
    shortcut: '',
    body: '',
    shared: false,
    enabled: true,
  })
  showForm.value = true
}

function startEdit(snippet) {
  editing.value = snippet
  error.value = ''
  Object.assign(form, {
    title: snippet.title || '',
    shortcut: snippet.shortcut || '',
    body: snippet.body || '',
    shared: Boolean(snippet.shared),
    enabled: Boolean(snippet.enabled),
  })
  showForm.value = true
}

async function save() {
  error.value = ''
  if (!form.title?.trim() || !form.shortcut?.trim()) {
    error.value = __('A snippet needs a title and a shortcut.')
    return
  }

  saving.value = true
  const values = {
    title: form.title.trim(),
    shortcut: form.shortcut.trim(),
    body: form.body,
    shared: form.shared ? 1 : 0,
    enabled: form.enabled ? 1 : 0,
  }

  try {
    if (editing.value?.name) {
      await snippets.setValue.submit({ name: editing.value.name, ...values })
    } else {
      await snippets.insert.submit(values)
    }
    showForm.value = false
    await snippets.reload()
    toast.success(__('Snippet saved'))
  } catch (e) {
    error.value =
      e?.messages?.[0] || e?.message || __('Could not save the snippet')
  } finally {
    saving.value = false
  }
}

async function remove(snippet) {
  try {
    await call('frappe.client.delete', {
      doctype: 'CRM Snippet',
      name: snippet.name,
    })
    await snippets.reload()
    toast.success(__('Snippet deleted'))
  } catch (e) {
    toast.error(e?.messages?.[0] || __('Could not delete the snippet'))
  }
}
</script>
