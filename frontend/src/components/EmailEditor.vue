<template>
  <Editor
    ref="textEditor"
    v-model="content"
    :extensions="extensions"
    :placeholder="placeholder"
    :editable="editable"
    :upload-function="(file) => uploadFile(file, doctype, modelValue.name)"
  >
    <div class="relative w-full">
      <div class="flex flex-col gap-3">
        <div
          v-if="from.length"
          class="mx-4 flex items-center gap-2 border-t pt-2.5 h-10"
        >
          <span class="text-xs text-ink-gray-4">{{ __('FROM') }}:</span>
          <FormControl
            v-model="fromEmail"
            type="select"
            variant="ghost"
            class="w-full"
            :placeholder="__('')"
            :options="from"
          />
        </div>
        <div
          class="mx-4 flex items-center gap-2"
          :class="from.length ? '' : 'border-t pt-2.5'"
        >
          <span class="text-xs text-ink-gray-4 mr-2">{{ __('TO') }}:</span>
          <EmailMultiSelect
            ref="toInput"
            v-model="toEmails"
            class="flex-1"
            variant="ghost"
            :validate="validateEmail"
            :fetchContacts="true"
            :error-message="
              (value) => __('{0} is an invalid email address', [value])
            "
          />
          <div class="flex gap-1.5">
            <Button
              :label="__('CC')"
              variant="ghost"
              :class="[
                cc
                  ? '!bg-surface-gray-4 hover:bg-surface-gray-3'
                  : '!text-ink-gray-4',
              ]"
              @click="toggleCC()"
            />
            <Button
              :label="__('BCC')"
              variant="ghost"
              :class="[
                bcc
                  ? '!bg-surface-gray-4 hover:bg-surface-gray-3'
                  : '!text-ink-gray-4',
              ]"
              @click="toggleBCC()"
            />
          </div>
        </div>
        <div v-if="cc" class="mx-4 flex items-center gap-2">
          <span class="text-xs text-ink-gray-4">{{ __('CC') }}:</span>
          <EmailMultiSelect
            ref="ccInput"
            v-model="ccEmails"
            class="flex-1"
            variant="ghost"
            :fetchContacts="true"
            :validate="validateEmail"
            :error-message="
              (value) => __('{0} is an invalid email address', [value])
            "
          />
        </div>
        <div v-if="bcc" class="mx-4 flex items-center gap-2">
          <span class="text-xs text-ink-gray-4">{{ __('BCC') }}:</span>
          <EmailMultiSelect
            ref="bccInput"
            v-model="bccEmails"
            class="flex-1"
            variant="ghost"
            :fetchContacts="true"
            :validate="validateEmail"
            :error-message="
              (value) => __('{0} is an invalid email address', [value])
            "
          />
        </div>
        <div class="mx-4 flex items-center gap-2 pb-2.5">
          <span class="text-xs text-ink-gray-4">{{ __('SUBJECT') }}:</span>
          <input
            v-model="subject"
            class="flex-1 border-none text-ink-gray-9 text-base bg-surface-base hover:bg-surface-base focus:border-none focus:!shadow-none focus-visible:!ring-0"
          />
        </div>
      </div>
      <EditorContent
        :class="[
          'prose-sm max-w-none [&_p.reply-to-content]:hidden',
          editable && 'mx-4 max-h-[35vh] overflow-y-auto border-t py-3',
        ]"
      />
      <EditorTableMenu />
      <div v-if="editable" class="flex flex-col gap-2">
        <div class="flex flex-wrap gap-2 px-4">
          <AttachmentItem
            v-for="a in attachments"
            :key="a.file_url"
            :label="a.file_name"
          >
            <template #suffix>
              <span
                class="lucide-x h-3.5"
                aria-hidden="true"
                @click.stop="removeAttachment(a)"
              />
            </template>
          </AttachmentItem>
        </div>
        <div
          class="flex justify-between gap-2 overflow-hidden border-t px-4 py-2.5"
        >
          <div class="flex gap-1 items-center overflow-x-auto">
            <Button
              :tooltip="__('Insert Email Template')"
              variant="ghost"
              :icon="EmailTemplateIcon"
              @click="showEmailTemplateSelectorModal = true"
            />
            <Button
              :tooltip="__('Insert Snippet')"
              variant="ghost"
              :icon="LucideTextQuote"
              @click="showSnippetSelectorModal = true"
            />
            <!--
              Item 14. Master spec §2.14 allows the composer ONE sparkle, and
              this is it; the snippet icon above is Stage 2B's and stays. The
              popover is inline rather than a modal (§2, "inline over modal")
              and it never sends anything (C6) — it writes into the editor the
              agent is already in.
            -->
            <Popover placement="top-start" @open="onDraftPopoverOpen">
              <template #target="{ togglePopover }">
                <Button
                  :tooltip="__('Draft with AI')"
                  variant="ghost"
                  :loading="draftLoading"
                  @click="togglePopover()"
                >
                  <template #icon>
                    <LucideSparkles class="size-4" aria-hidden="true" />
                  </template>
                </Button>
              </template>
              <template #body="{ close }">
                <div
                  class="w-80 rounded-lg bg-surface-modal p-3 shadow-2xl text-base text-ink-gray-7"
                >
                  <template v-if="aiReady === false">
                    <div class="text-base-medium text-ink-gray-8">
                      {{ __('AI is not set up yet') }}
                    </div>
                    <p class="mt-1">
                      {{
                        __(
                          'Add an AI provider and key in Settings → AI & Follow-ups, then this button drafts a reply for you to edit.',
                        )
                      }}
                    </p>
                  </template>
                  <template v-else>
                    <div class="text-base-medium text-ink-gray-8">
                      {{ __('Draft with AI') }}
                    </div>
                    <div class="mt-2 flex flex-wrap gap-1.5">
                      <Button
                        v-for="preset in draftPresets"
                        :key="preset.key"
                        :label="__(preset.label)"
                        :disabled="draftLoading"
                        @click="runDraft(__(preset.instruction), close)"
                      />
                    </div>
                    <FormControl
                      v-model="draftInstruction"
                      class="mt-2"
                      type="textarea"
                      :rows="2"
                      :placeholder="__('Or say what this email should do')"
                      @keydown.enter.exact.prevent="
                        runDraft(draftInstruction, close)
                      "
                    />
                    <p class="mt-2 text-sm text-ink-gray-5">
                      {{ draftDisclosure }}
                    </p>
                    <div class="mt-2 flex justify-end">
                      <Button
                        variant="solid"
                        :label="__('Draft')"
                        :loading="draftLoading"
                        :disabled="!draftInstruction.trim()"
                        @click="runDraft(draftInstruction, close)"
                      />
                    </div>
                  </template>
                </div>
              </template>
            </Popover>
            <FileUploader
              :upload-args="{
                doctype: doctype,
                docname: modelValue.name,
                private: true,
              }"
              @success="(f) => attachments.push(f)"
            >
              <template #default="{ openFileSelector }">
                <Button
                  :tooltip="__('Attach a File')"
                  :icon="AttachmentIcon"
                  variant="ghost"
                  @click="openFileSelector()"
                />
              </template>
            </FileUploader>
            <EditorFixedMenu :items="fullToolbar" />
            <IconPicker
              v-slot="{ togglePopover }"
              v-model="emoji"
              @update:modelValue="() => appendEmoji()"
            >
              <Button
                :tooltip="__('Insert Emoji')"
                :icon="SmileIcon"
                variant="ghost"
                @click="togglePopover()"
              />
            </IconPicker>
          </div>
          <div class="mt-2 flex items-center justify-end space-x-2 sm:mt-0">
            <Button v-bind="discardButtonProps || {}" :label="__('Discard')" />
            <!-- Item 5: Send is a split button. Send Later exists ONLY behind
                 the caret (spec §2.14) so the composer keeps three visible
                 actions and one primary. -->
            <div class="flex items-stretch">
              <Button
                variant="solid"
                v-bind="submitButtonProps || {}"
                class="rounded-r-none"
                :label="`${__('Send')} (${submitShortcutLabel})`"
              />
              <Popover placement="top-end">
                <template #target="{ togglePopover }">
                  <Button
                    variant="solid"
                    class="rounded-l-none border-l border-outline-white"
                    :disabled="submitButtonProps?.disabled"
                    :tooltip="__('Send later')"
                    icon="chevron-up"
                    @click="togglePopover()"
                  />
                </template>
                <template #body="{ close }">
                  <SendLaterPopover
                    @schedule="
                      (payload) => {
                        close()
                        scheduleSend(payload)
                      }
                    "
                  />
                </template>
              </Popover>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Editor>
  <EmailTemplateSelectorModal
    v-model="showEmailTemplateSelectorModal"
    :doctype="doctype"
    @apply="applyEmailTemplate"
  />
  <SnippetSelectorModal
    v-model="showSnippetSelectorModal"
    :doctype="doctype"
    :docname="modelValue?.name"
    @apply="applySnippet"
  />
</template>

<script setup>
import IconPicker from '@/components/IconPicker.vue'
import SmileIcon from '@/components/Icons/SmileIcon.vue'
import EmailTemplateIcon from '@/components/Icons/EmailTemplateIcon.vue'
import AttachmentIcon from '@/components/Icons/AttachmentIcon.vue'
import AttachmentItem from '@/components/AttachmentItem.vue'
import EmailMultiSelect from '@/components/Controls/EmailMultiSelect.vue'
import EmailTemplateSelectorModal from '@/components/Modals/EmailTemplateSelectorModal.vue'
import SnippetSelectorModal from '@/components/Modals/SnippetSelectorModal.vue'
import SendLaterPopover from '@/components/SendLaterPopover.vue'
import LucideTextQuote from '~icons/lucide/text-quote'
import LucideSparkles from '~icons/lucide/sparkles'
import {
  buildEditorExtensions,
  fullToolbar,
  uploadFile,
} from '@/components/editor/config'
import { aiReady, loadAiReady } from '@/composables/ai'
import { DRAFT_PRESETS, bodyToHtml, disclosureLine } from '@/utils/aiDraft'
import { FileUploader, call, FormControl, Popover, toast } from 'frappe-ui'
import {
  Editor,
  EditorContent,
  EditorFixedMenu,
  EditorTableMenu,
} from 'frappe-ui/editor'
import { useTelemetry } from 'frappe-ui/frappe'
import { useDocument } from '@/data/document'
import { validateEmail, submitShortcutLabel } from '@/utils'
import Paragraph from '@tiptap/extension-paragraph'
import { ref, computed, nextTick, inject, watch } from 'vue'

const props = defineProps({
  placeholder: { type: String, default: null },
  editable: { type: Boolean, default: true },
  doctype: { type: String, default: 'CRM Lead' },
  subject: { type: String, default: __('Email From Lead') },
  editorProps: { type: Object, default: () => ({}) },
  submitButtonProps: { type: Object, default: () => ({}) },
  discardButtonProps: { type: Object, default: () => ({}) },
})

// Item 5. The composer owns the draft, not this editor, so the schedule is
// handed up rather than posted here -- the same division `submitButtonProps`
// already uses for Send.
const emit = defineEmits(['schedule'])

function scheduleSend(payload) {
  if (props.submitButtonProps?.disabled) return
  emit('schedule', payload)
}

const CustomParagraph = Paragraph.extend({
  addAttributes() {
    return {
      class: {
        default: null,
        renderHTML: (attributes) => {
          if (!attributes.class) {
            return {}
          }
          return {
            class: `${attributes.class}`,
          }
        },
      },
    }
  },
})

const modelValue = defineModel({ type: Object })
const attachments = defineModel('attachments', {
  type: Array,
  default: () => [],
})
const content = defineModel('content', { type: String, default: '' })

const { capture } = useTelemetry()
const { user: sessionUser } = inject('session')
const { document: user } = useDocument('User', sessionUser)

const textEditor = ref(null)
const cc = ref(false)
const bcc = ref(false)
const emoji = ref('')

const subject = ref(props.subject)
const fromEmail = ref('')
const toEmails = ref(modelValue.value.email ? [modelValue.value.email] : [])
const ccEmails = ref([])
const bccEmails = ref([])
const toInput = ref(null)
const ccInput = ref(null)
const bccInput = ref(null)

const extensions = buildEditorExtensions({
  starterKit: { paragraph: false },
  extra: [CustomParagraph],
})

const from = computed(() => {
  if (!user.doc || !user.doc.user_emails?.length) return []
  let emails = user.doc.user_emails.map((e) => {
    return {
      label: e.email_account + ' <' + e.email_id + '>',
      value: e.email_id,
    }
  })

  if (emails.length == 1 && emails[0].email_id === sessionUser) return []

  return emails
})

watch(
  from,
  (fromOptions) => {
    if (!fromOptions.find((f) => f.value === fromEmail.value)) {
      fromEmail.value = fromOptions.length ? fromOptions[0].value : ''
    }
  },
  { immediate: true },
)

const editor = computed(() => textEditor.value?.editor)

function removeAttachment(attachment) {
  attachments.value = attachments.value.filter((a) => a !== attachment)
}

const showEmailTemplateSelectorModal = ref(false)

async function applyEmailTemplate(template) {
  let data = await call(
    'frappe.email.doctype.email_template.email_template.get_email_template',
    {
      template_name: template.name,
      doc: modelValue.value,
    },
  )

  if (template.subject) {
    subject.value = data.subject
  }

  if (template.response) {
    content.value = data.message
  }
  showEmailTemplateSelectorModal.value = false
  capture('email_template_applied', { doctype: props.doctype })
}

const showSnippetSelectorModal = ref(false)

/**
 * Insert a snippet at the caret. The body arrives already merged from the
 * server, so nothing is resolved here.
 *
 * Insert, never replace: unlike an email template, a snippet is a paragraph
 * inside a message somebody is already writing.
 */
function applySnippet({ body }) {
  editor.value.commands.insertContent(body)
  editor.value.commands.focus()
  capture('snippet_inserted', { doctype: props.doctype })
}

// --- Item 14: draft with AI ------------------------------------------------

const draftPresets = DRAFT_PRESETS
const draftInstruction = ref('')
const draftLoading = ref(false)
const draftFields = ref(null)

// Asked once per session, so the popover already knows which of its two bodies
// to render the first time it is opened.
loadAiReady()

function onDraftPopoverOpen() {
  loadAiReady()
  if (draftFields.value === null) loadDraftFields()
}

/**
 * The field labels the disclosure line names.
 *
 * Read from the server rather than typed here: `crm.api.ai_draft.sent_fields`
 * reads the same whitelist the prompt builder reads, so the line cannot end up
 * describing a set of fields that is no longer what leaves the site.
 */
async function loadDraftFields() {
  try {
    draftFields.value = await call('crm.api.ai_draft.sent_fields', {
      doctype: props.doctype,
    })
  } catch (error) {
    draftFields.value = []
  }
}

const draftDisclosure = computed(() =>
  disclosureLine(draftFields.value || [], {
    prefix: __('Sends to the AI provider'),
    andMessages: __('and the last 10 emails on this record'),
    nothing: __('Sends the last 10 emails on this record to the AI provider'),
  }),
)

/**
 * Draft a body and insert it at the caret.
 *
 * `insertContent` is one editor transaction, so the editor's own history takes
 * the whole draft back out on the first Ctrl+Z — which is what item 14 asks for
 * and why nothing here maintains an undo stack of its own. There is no
 * streaming: `crm.ai.client` does not stream, and faking a typing effect would
 * be a claim the app cannot back.
 */
async function runDraft(instruction, close) {
  const text = (instruction || '').trim()
  if (!text || draftLoading.value) return

  draftLoading.value = true
  try {
    const data = await call('crm.api.ai_draft.generate', {
      doctype: props.doctype,
      name: modelValue.value?.name,
      instruction: text,
    })

    const html = bodyToHtml(data?.body)
    if (!html) {
      toast.error(__('The AI returned an empty draft'))
      return
    }

    editor.value.commands.focus()
    editor.value.commands.insertContent(html)
    capture('ai_email_draft_inserted', { doctype: props.doctype })
    draftInstruction.value = ''
    close?.()
  } catch (error) {
    toast.error(error.messages?.[0] || __('Could not draft this email'))
  } finally {
    draftLoading.value = false
  }
}

function appendEmoji() {
  editor.value.commands.insertContent(emoji.value)
  editor.value.commands.focus()
  emoji.value = ''
  capture('emoji_inserted_in_email', { emoji: emoji.value })
}

function toggleCC() {
  cc.value = !cc.value
  if (cc.value) nextTick(() => ccInput.value.setFocus())
}

function toggleBCC() {
  bcc.value = !bcc.value
  if (bcc.value) nextTick(() => bccInput.value.setFocus())
}

/** Put the caret in the To field. A forward opens with no recipient. */
function focusTo() {
  toInput.value?.setFocus?.()
}

defineExpose({
  editor,
  subject,
  cc,
  bcc,
  fromEmail,
  toEmails,
  ccEmails,
  bccEmails,
  focusTo,
})
</script>
