<template>
  <div class="flex flex-col pt-5">
    <div class="flex flex-col gap-1">
      <span class="text-lg-semibold text-ink-gray-8">
        {{ __('Automatic reply') }}
      </span>
      <span class="text-p-sm text-ink-gray-6">
        {{
          __(
            'Answer every submission of this form straight away. Sent once per submission, never to an address that has opted out.',
          )
        }}
      </span>
    </div>

    <div class="mt-3.5 flex items-start justify-between gap-4">
      <div class="flex flex-col gap-0.5">
        <span class="text-base text-ink-gray-8">
          {{ __('Send automatic reply') }}
        </span>
        <span class="text-p-sm text-ink-gray-5">
          {{ __('Off by default. Nothing is sent until you turn this on.') }}
        </span>
      </div>
      <Switch
        :modelValue="Boolean(model.enabled)"
        @update:modelValue="(v) => update('enabled', v ? 1 : 0)"
      />
    </div>

    <div v-if="model.enabled" class="mt-5 flex flex-col gap-4">
      <div>
        <div class="mb-1.5 flex items-end justify-between gap-2">
          <span class="text-sm text-ink-gray-5">{{ __('Subject') }}</span>
          <Dropdown
            :options="fieldOptions((token) => insertIntoSubject(token))"
          >
            <Button
              variant="ghost"
              class="!h-6"
              :label="__('Insert field')"
              iconRight="chevron-down"
            />
          </Dropdown>
        </div>
        <TextInput
          type="text"
          :modelValue="model.subject"
          :placeholder="__('Thanks for getting in touch, {{ first_name }}')"
          @update:modelValue="(v) => update('subject', v)"
        />
      </div>

      <div>
        <div class="mb-1.5 flex items-end justify-between gap-2">
          <span class="text-sm text-ink-gray-5">{{ __('Message') }}</span>
          <Dropdown :options="fieldOptions((token) => insertIntoBody(token))">
            <Button
              variant="ghost"
              class="!h-6"
              :label="__('Insert field')"
              iconRight="chevron-down"
            />
          </Dropdown>
        </div>
        <div
          class="auto-response-editor rounded border border-outline-gray-2 bg-surface-base px-3 py-2"
        >
          <TextEditor
            ref="bodyEditor"
            :content="model.message"
            :extensions="extensions"
            :editable="true"
            :placeholder="
              __(
                'Hi {{ first_name }}, thanks for your enquiry. We will be in touch shortly.',
              )
            "
            editor-class="prose-sm min-h-[9rem] max-w-none focus:outline-none"
            @change="(value) => update('message', value)"
          />
        </div>
        <p class="mt-1.5 text-p-sm text-ink-gray-5">
          {{
            __(
              'A field you insert becomes one token. It is replaced with the value the visitor submitted, or with nothing when they left it blank.',
            )
          }}
        </p>
      </div>

      <div class="flex items-center gap-3">
        <Button
          :loading="testing"
          :label="__('Send test email')"
          @click="sendTest"
        />
        <span class="text-p-sm text-ink-gray-5">
          {{ __('The test goes to your own address, with sample values.') }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * The form builder's "Auto-response" tab (master spec item 4).
 *
 * Per FORM, not per site: two forms answer two audiences, and one site-wide
 * reply would be wrong for at least one of them. The setting therefore lives
 * here, beside the form it belongs to, and not in FCRM Settings.
 *
 * The panel owns no state of its own beyond the test-send flag. The parent
 * holds `auto_response` on the form model and saves it with everything else, so
 * a half-written reply is discarded by the same Cancel that discards a
 * half-written field layout.
 */
import { MergeField } from '@/components/editor/mergeField'
import {
  Button,
  Dropdown,
  Switch,
  TextEditor,
  TextInput,
  call,
  createResource,
  toast,
} from 'frappe-ui'
import { ref } from 'vue'

const props = defineProps({
  formName: { type: String, default: '' },
  // A save is needed before a test can be sent: the server reads the STORED
  // reply, not the one on screen.
  dirty: { type: Boolean, default: false },
})

const model = defineModel({ type: Object, default: () => ({}) })
const emit = defineEmits(['change', 'requestSave'])

// `TextEditor` already supplies its own kit; this is the ONE extension added on
// top. Handing it a second StarterKit would register every node twice.
const extensions = [MergeField]

const bodyEditor = ref(null)
const testing = ref(false)

const mergeFields = createResource({
  url: 'crm.api.form.get_auto_response_fields',
  auto: true,
})

function update(key, value) {
  model.value = { ...model.value, [key]: value }
  emit('change')
}

/** The "Insert field" menu, built once from the server's own vocabulary. */
function fieldOptions(onPick) {
  return (mergeFields.data || []).map((field) => ({
    label: field.label,
    onClick: () => onPick(field.token),
  }))
}

/**
 * The subject is a plain input, so a token there is literal text. Appended
 * rather than inserted at the caret: a plain `TextInput` does not report one,
 * and guessing a position is worse than a predictable append.
 */
function insertIntoSubject(token) {
  const current = model.value.subject || ''
  const separator = current && !current.endsWith(' ') ? ' ' : ''
  update('subject', `${current}${separator}{{ ${token} }}`)
}

/** The body is a rich-text editor, so a token there is one non-editable atom. */
function insertIntoBody(token) {
  const editor = bodyEditor.value?.editor
  if (!editor) return
  editor.chain().focus().insertMergeField(token).run()
}

async function sendTest() {
  if (!props.formName) return
  if (props.dirty) {
    // The server sends the STORED reply. Testing an unsaved draft would show
    // the previous version and quietly convince the author it was broken.
    emit('requestSave')
    return
  }

  testing.value = true
  try {
    const result = await call('crm.api.form.send_auto_response_test', {
      name: props.formName,
    })
    toast.success(__('Test sent to {0}', [result.sent_to]))
  } catch (error) {
    toast.error(error?.messages?.[0] || __('Could not send the test'))
  } finally {
    testing.value = false
  }
}
</script>

<style scoped>
/* The pill. `contenteditable=false` comes from the atom node itself; this is
   only what makes it look like a token rather than like stray text. */
.auto-response-editor :deep(.merge-field) {
  display: inline-block;
  border-radius: 0.25rem;
  background: var(--surface-gray-2, #f3f4f6);
  color: var(--ink-gray-7, #374151);
  padding: 0 0.25rem;
  font-size: 0.8125rem;
  white-space: nowrap;
}
</style>
