<!-- eslint-disable vue/no-v-html -->
<template>
  <div
    class="overflow-hidden rounded-2xl border border-outline-gray-2 bg-surface-base shadow-[0_1px_3px_rgba(16,24,40,0.08),0_1px_2px_rgba(16,24,40,0.04)]"
  >
    <!-- Quoted reply -->
    <div
      v-if="reply?.message"
      class="flex items-center gap-2 border-b border-outline-gray-1 px-4 pt-3"
    >
      <div
        class="mb-2 flex-1 cursor-pointer rounded-lg border-0 border-l-4 bg-surface-gray-2 p-2 text-sm text-ink-gray-5"
        :class="
          reply.type == 'Incoming' ? 'border-green-500' : 'border-blue-400'
        "
      >
        <div
          class="mb-0.5 text-sm-bold"
          :class="
            reply.type == 'Incoming' ? 'text-ink-green-5' : 'text-ink-blue-link'
          "
        >
          {{ reply.from_name || __('You') }}
        </div>
        <div
          class="max-h-12 overflow-hidden"
          v-html="sanitizeHTML(reply.message)"
        />
      </div>
      <Button variant="ghost" icon="lucide-x" @click="reply = {}" />
    </div>

    <!-- Toolbar -->
    <div
      class="flex h-11 items-center gap-1 border-b border-outline-gray-1 px-3"
    >
      <FileUploader @success="(file) => uploadFile(file)">
        <template #default="{ openFileSelector }">
          <Dropdown :options="uploadOptions(openFileSelector)">
            <button
              type="button"
              class="flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-sm text-ink-gray-6 transition-colors hover:bg-surface-gray-2"
            >
              <span class="lucide-paperclip size-4" aria-hidden="true" />
              {{ __('Attach') }}
            </button>
          </Dropdown>
        </template>
      </FileUploader>

      <button
        v-if="showTemplates"
        type="button"
        class="flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-sm text-ink-gray-6 transition-colors hover:bg-surface-gray-2"
        @click="emit('openTemplates')"
      >
        <span class="lucide-layout-template size-4" aria-hidden="true" />
        {{ __('Templates') }}
      </button>

      <IconPicker
        v-slot="{ togglePopover }"
        v-model="emoji"
        @update:modelValue="
          () => {
            content += emoji
            textareaEl()?.focus()
            capture('whatsapp_emoji_added')
          }
        "
      >
        <button
          type="button"
          class="flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-sm text-ink-gray-6 transition-colors hover:bg-surface-gray-2"
          @click="togglePopover"
        >
          <SmileIcon class="size-4" />
          {{ __('Emoji') }}
        </button>
      </IconPicker>
    </div>

    <!-- Input -->
    <textarea
      ref="textareaRef"
      v-model="content"
      rows="3"
      class="max-h-48 w-full resize-none border-0 bg-transparent px-4 py-3 text-base text-ink-gray-9 placeholder-ink-gray-4 focus:ring-0"
      :placeholder="__('Reply via WhatsApp…')"
      @keydown.enter.exact.prevent="send()"
    />

    <!-- Footer -->
    <div class="flex h-12 items-center justify-between px-4 pb-1">
      <span class="text-xs text-ink-gray-4">
        {{ content.length }}/4096
        <span class="hidden sm:inline">
          · {{ __('Enter to send, Shift+Enter for a new line') }}
        </span>
      </span>
      <Button
        variant="solid"
        :label="__('Send')"
        :disabled="!content.trim()"
        @click="send()"
      >
        <template #prefix>
          <span class="lucide-send-horizontal size-4" aria-hidden="true" />
        </template>
      </Button>
    </div>
  </div>
</template>

<script setup>
import IconPicker from '@/components/IconPicker.vue'
import SmileIcon from '@/components/Icons/SmileIcon.vue'
import { sanitizeHTML } from '@/utils'
import { useTelemetry } from 'frappe-ui/frappe'
import {
  createResource,
  FileUploader,
  Dropdown,
  Button,
  toast,
} from 'frappe-ui'
import { ref } from 'vue'

const props = defineProps({
  doctype: { type: String, default: '' },
  showTemplates: { type: Boolean, default: false },
})

const emit = defineEmits(['openTemplates', 'sent'])

const doc = defineModel({ type: Object, default: () => ({}) })
const whatsapp = defineModel('whatsapp', { type: Object, default: () => ({}) })
const reply = defineModel('reply', { type: Object, default: () => ({}) })

const { capture } = useTelemetry()

const textareaRef = ref(null)
const emoji = ref('')
const content = ref('')
const fileType = ref('')

function textareaEl() {
  return textareaRef.value
}

function uploadFile(file) {
  sendMessage({ attach: file.file_url, content_type: fileType.value })
  capture('whatsapp_upload_file')
}

function send() {
  if (!content.value.trim()) return
  sendMessage({ attach: '', content_type: 'text' })
  capture('whatsapp_send_message')
}

function sendMessage({ attach, content_type }) {
  const args = {
    reference_doctype: props.doctype,
    reference_name: doc.value.name,
    message: content.value,
    to: doc.value.mobile_no,
    attach,
    reply_to: reply.value?.name || '',
    content_type,
  }
  content.value = ''
  fileType.value = ''
  reply.value = {}
  createResource({
    url: 'crm.api.whatsapp.create_whatsapp_message',
    params: args,
    auto: true,
    onSuccess: () => {
      whatsapp.value.reload()
      emit('sent')
    },
    onError: (error) => {
      toast.error(error.messages?.[0] || __('Failed to send WhatsApp message'))
    },
  })
}

function uploadOptions(openFileSelector) {
  return [
    {
      label: __('Upload Document'),
      icon: 'file',
      onClick: () => {
        fileType.value = 'document'
        openFileSelector()
      },
    },
    {
      label: __('Upload Image'),
      icon: 'image',
      onClick: () => {
        fileType.value = 'image'
        openFileSelector('image/*')
      },
    },
    {
      label: __('Upload Video'),
      icon: 'video',
      onClick: () => {
        fileType.value = 'video'
        openFileSelector('video/*')
      },
    },
  ]
}
</script>
