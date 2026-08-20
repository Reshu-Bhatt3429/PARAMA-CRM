<template>
  <div
    v-if="title !== 'Data'"
    class="flex items-center justify-between text-lg-medium sm:mx-10 sm:mb-4 sm:mt-8"
  >
    <div class="flex h-8 items-center text-2xl-semibold text-ink-gray-8">
      {{ __(title) }}
    </div>
    <Button
      v-if="title == 'Emails'"
      variant="solid"
      :label="__('New Email')"
      iconLeft="plus"
      @click="emailBox.show = true"
    />
    <Button
      v-else-if="title == 'Comments'"
      variant="solid"
      :label="__('New Comment')"
      iconLeft="plus"
      @click="emailBox.showComment = true"
    />
    <MultiActionButton
      v-else-if="title == 'Calls'"
      variant="solid"
      :options="callActions"
    />
    <Button
      v-else-if="title == 'Events'"
      variant="solid"
      @click="modalRef.showEvent()"
    >
      <template #prefix>
        <EventIcon class="h-4 w-4" />
      </template>
      <span>{{ __('Schedule an Event') }}</span>
    </Button>
    <Button
      v-else-if="title == 'Notes'"
      variant="solid"
      :label="__('New Note')"
      iconLeft="plus"
      @click="modalRef.showNote()"
    />
    <Button
      v-else-if="title == 'Tasks'"
      variant="solid"
      :label="__('New Task')"
      iconLeft="plus"
      @click="modalRef.showTask()"
    />
    <Button
      v-else-if="title == 'Attachments'"
      variant="solid"
      :label="__('Upload Attachment')"
      iconLeft="plus"
      @click="showFilesUploader = true"
    />
    <div v-else-if="title == 'WhatsApp'" class="flex gap-2 shrink-0">
      <Button
        :label="__('Send Template')"
        @click="showWhatsappTemplates = true"
      />
      <Button
        variant="solid"
        :label="__('New Message')"
        iconLeft="plus"
        @click="whatsappBox.show()"
      />
    </div>
    <div v-else class="flex items-center gap-2 shrink-0">
      <!--
        Master spec §2.14/§2.15: the timeline gets ONE sparkle, and this is it.
        Item 13/28/15's Brief card is the only AI on this surface.
      -->
      <Button
        v-if="aiReady"
        :tooltip="__('Summarize this record')"
        :loading="briefLoading"
        @click="emit('summarize')"
      >
        <template #prefix>
          <LucideSparkles class="size-4" aria-hidden="true" />
        </template>
        <span>{{ __('Summarize') }}</span>
      </Button>
      <!--
        AI off: a popover that says where to switch it on. Not an error toast —
        nothing has gone wrong, the feature simply is not configured yet.
      -->
      <Popover v-else-if="aiReady === false" placement="bottom-end">
        <template #target="{ togglePopover }">
          <Button
            :tooltip="__('Summarize this record')"
            @click="togglePopover()"
          >
            <template #prefix>
              <LucideSparkles
                class="size-4 text-ink-gray-5"
                aria-hidden="true"
              />
            </template>
            <span>{{ __('Summarize') }}</span>
          </Button>
        </template>
        <template #body>
          <div
            class="w-64 rounded-lg bg-surface-modal p-3 shadow-2xl text-base text-ink-gray-7"
          >
            <div class="text-base-medium text-ink-gray-8">
              {{ __('AI is not set up yet') }}
            </div>
            <p class="mt-1">
              {{
                __(
                  'Add an AI provider and key in Settings → AI & Follow-ups, then this button writes a short brief of the record.',
                )
              }}
            </p>
          </div>
        </template>
      </Popover>
      <Dropdown :options="defaultActions" @click.stop>
        <template #default="{ open }">
          <Button
            variant="solid"
            class="flex items-center gap-1"
            :label="__('New')"
            iconLeft="plus"
            :iconRight="open ? 'chevron-up' : 'chevron-down'"
          />
        </template>
      </Dropdown>
    </div>
  </div>
</template>
<script setup>
import MultiActionButton from '@/components/MultiActionButton.vue'
import Email2Icon from '@/components/Icons/Email2Icon.vue'
import CommentIcon from '@/components/Icons/CommentIcon.vue'
import EventIcon from '@/components/Icons/EventIcon.vue'
import PhoneIcon from '@/components/Icons/PhoneIcon.vue'
import NoteIcon from '@/components/Icons/NoteIcon.vue'
import TaskIcon from '@/components/Icons/TaskIcon.vue'
import AttachmentIcon from '@/components/Icons/AttachmentIcon.vue'
import WhatsAppIcon from '@/components/Icons/WhatsAppIcon.vue'
import LucideSparkles from '~icons/lucide/sparkles'
import { globalStore } from '@/stores/global'
import { whatsappEnabled } from '@/composables/whatsapp'
import { callEnabled } from '@/composables/telephony'
import { aiReady, loadAiReady } from '@/composables/ai'
import { Dropdown, Popover } from 'frappe-ui'
import { computed, h, onMounted } from 'vue'

const props = defineProps({
  tabs: { type: Array, default: () => [] },
  title: { type: String, default: '' },
  doc: { type: Object, default: () => ({}) },
  modalRef: { type: Object, default: () => ({}) },
  whatsappBox: { type: Object, default: () => ({}) },
  briefLoading: { type: Boolean, default: false },
})

const emit = defineEmits(['summarize'])

// Asked once per session and shared. The button must know before it is clicked
// which of its two behaviours it has.
onMounted(() => loadAiReady())

const { makeCall } = globalStore()

const tabIndex = defineModel({ type: Number })
const showWhatsappTemplates = defineModel('showWhatsappTemplates', {
  type: Boolean,
})
const showFilesUploader = defineModel('showFilesUploader', { type: Boolean })
const emailBox = defineModel('emailBox', { type: Object, default: () => ({}) })

const defaultActions = computed(() => {
  let actions = [
    {
      icon: h(Email2Icon, { class: 'h-4 w-4' }),
      label: __('Email'),
      onClick: () => (emailBox.value.show = true),
    },
    {
      icon: h(CommentIcon, { class: 'h-4 w-4' }),
      label: __('Comment'),
      onClick: () => (emailBox.value.showComment = true),
    },
    {
      icon: h(EventIcon, { class: 'h-4 w-4' }),
      label: __('Schedule an Event'),
      onClick: () => props.modalRef.showEvent(),
    },
    {
      icon: h(PhoneIcon, { class: 'h-4 w-4' }),
      label: __('Log a Call'),
      onClick: () => props.modalRef.createCallLog(),
    },
    {
      icon: h(PhoneIcon, { class: 'h-4 w-4' }),
      label: __('Make a Call'),
      onClick: () => makeCall(props.doc.mobile_no),
      condition: () => callEnabled.value,
    },
    {
      icon: h(NoteIcon, { class: 'h-4 w-4' }),
      label: __('Note'),
      onClick: () => props.modalRef.showNote(),
    },
    {
      icon: h(TaskIcon, { class: 'h-4 w-4' }),
      label: __('Task'),
      onClick: () => props.modalRef.showTask(),
    },
    {
      icon: h(AttachmentIcon, { class: 'h-4 w-4' }),
      label: __('Upload Attachment'),
      onClick: () => (showFilesUploader.value = true),
    },
    {
      icon: h(WhatsAppIcon, { class: 'h-4 w-4' }),
      label: __('WhatsApp Message'),
      onClick: () => (tabIndex.value = getTabIndex('WhatsApp')),
      condition: () => whatsappEnabled.value,
    },
  ]
  return actions.filter((action) =>
    action.condition ? action.condition() : true,
  )
})

function getTabIndex(name) {
  return props.tabs.findIndex((tab) => tab.name === name)
}

const callActions = computed(() => {
  let actions = [
    {
      label: __('Log a Call'),
      icon: 'plus',
      onClick: () => props.modalRef.createCallLog(),
    },
    {
      label: __('Make a Call'),
      icon: h(PhoneIcon, { class: 'h-4 w-4' }),
      onClick: () => makeCall(props.doc.mobile_no),
      condition: () => callEnabled.value,
    },
  ]

  return actions.filter((action) =>
    action.condition ? action.condition() : true,
  )
})
</script>
