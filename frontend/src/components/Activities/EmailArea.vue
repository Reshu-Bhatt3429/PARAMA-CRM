<template>
  <div
    class="cursor-pointer flex flex-col rounded-md shadow-sm bg-surface-elevation-1 px-3 py-1.5 text-base transition-all duration-300 ease-in-out"
  >
    <div
      class="-mb-0.5 flex items-center justify-between gap-2 truncate text-ink-gray-9"
    >
      <div class="flex items-center gap-2 truncate">
        <span>{{ activity.data.sender_full_name }}</span>
        <span class="sm:flex hidden text-sm text-ink-gray-5">
          {{ '<' + activity.data.sender + '>' }}
        </span>
        <Badge
          v-if="activity.communication_type == 'Automated Message'"
          :label="__('Notification')"
          variant="subtle"
          theme="green"
        />
        <!-- Item 21: says the sequence sent this, not the agent. Nothing else
             on the card changes: a sequence email IS an ordinary email. -->
        <Badge
          v-if="sequenceChip"
          :label="sequenceChip"
          variant="subtle"
          theme="gray"
        />
      </div>
      <div class="flex items-center gap-2 shrink-0">
        <Tooltip v-if="indicator" :text="indicator.tooltip">
          <span
            class="flex items-center gap-1 text-sm"
            :class="indicator.className"
          >
            <component :is="indicator.icon" class="size-3.5" />
            <span v-if="indicator.label">{{ indicator.label }}</span>
          </span>
        </Tooltip>
        <TimelineTimestamp :date="activity.communication_date" />
        <div class="flex gap-0.5">
          <Button
            :tooltip="__('Reply')"
            variant="ghost"
            class="text-ink-gray-7"
            :icon="ReplyIcon"
            @click="reply(activity.data)"
          />
          <Button
            :tooltip="__('Reply All')"
            variant="ghost"
            :icon="ReplyAllIcon"
            class="text-ink-gray-7"
            @click="reply(activity.data, true)"
          />
          <Button
            :tooltip="__('Forward')"
            variant="ghost"
            :icon="LucideForward"
            class="text-ink-gray-7"
            @click="forward()"
          />
        </div>
      </div>
    </div>
    <div class="flex flex-col gap-1 text-base leading-5 text-ink-gray-8">
      <div>{{ activity.data.subject }}</div>
      <div>
        <span class="mr-1 text-ink-gray-5"> {{ __('To') }}: </span>
        <span>{{ activity.data.recipients }}</span>
        <span v-if="activity.data.cc">, </span>
        <span v-if="activity.data.cc" class="mr-1 text-ink-gray-5">
          {{ __('CC') }}:
        </span>
        <span v-if="activity.data.cc">{{ activity.data.cc }}</span>
        <span v-if="activity.data.bcc">, </span>
        <span v-if="activity.data.bcc" class="mr-1 text-ink-gray-5">
          {{ __('BCC') }}:
        </span>
        <span v-if="activity.data.bcc">{{ activity.data.bcc }}</span>
      </div>
    </div>
    <div class="border-0 border-t mt-3 mb-1 border-outline-elevation-2" />
    <EmailContent :content="activity.data.content" />
    <div v-if="activity.data?.attachments?.length" class="flex flex-wrap gap-2">
      <AttachmentItem
        v-for="a in activity.data.attachments"
        :key="a.file_url"
        :label="a.file_name"
        :url="a.file_url"
      />
    </div>
  </div>
</template>
<script setup>
import LucideForward from '~icons/lucide/forward'
import LucideCheck from '~icons/lucide/check'
import LucideCheckCheck from '~icons/lucide/check-check'
import LucideClock from '~icons/lucide/clock'
import LucideAlertCircle from '~icons/lucide/alert-circle'
import ReplyIcon from '@/components/Icons/ReplyIcon.vue'
import ReplyAllIcon from '@/components/Icons/ReplyAllIcon.vue'
import AttachmentItem from '@/components/AttachmentItem.vue'
import EmailContent from '@/components/Activities/EmailContent.vue'
import { Badge, Tooltip } from 'frappe-ui'
import TimelineTimestamp from '@/components/Activities/TimelineTimestamp.vue'
import {
  emailState,
  emailStateTimestamp,
  showEmailState,
} from '@/utils/emailStatus'
import { sequenceChipLabel } from '@/utils/emailSequence'
import { formatDate, timeAgo } from '@/utils'
import { reactive, computed } from 'vue'

const props = defineProps({
  activity: { type: Object, default: () => ({}) },
  emailBox: { type: Object, default: () => ({}) },
})

const emailBox = reactive(props.emailBox)

// Item 21. Null for every message a follow-up sequence did not send, which is
// every message on a site that does not use them.
const sequenceChip = computed(() => sequenceChipLabel(props.activity))

function reply(email, reply_all = false) {
  emailBox.show = true
  let editor = emailBox.editor
  let message = email.content
  let recipients = email.recipients.split(',').map((r) => r.trim())
  editor.fromEmail = email.sender
  editor.toEmails = [email.sender]
  editor.cc = editor.bcc = false
  editor.ccEmails = []
  editor.bccEmails = []

  if (!email.subject.startsWith('Re:')) {
    editor.subject = `Re: ${email.subject}`
  } else {
    editor.subject = email.subject
  }

  if (reply_all) {
    let cc = email.cc?.split(',').map((r) => r.trim())
    let bcc = email.bcc?.split(',').map((r) => r.trim())

    if (cc?.length) {
      recipients = recipients.filter((r) => !cc?.includes(r))
      cc.push(...recipients)
    } else {
      cc = recipients
    }

    editor.cc = cc ? true : false
    editor.bcc = bcc ? true : false

    editor.ccEmails = cc
    editor.bccEmails = bcc
  }

  let repliedMessage = `<blockquote>${message}</blockquote>`

  editor.editor
    .chain()
    .clearContent()
    .updateAttributes('paragraph', { class: 'reply-to-content' })
    .insertContent(repliedMessage)
    .focus('all')
    .insertContentAt(0, { type: 'paragraph' })
    .focus('start')
    .run()
}

/**
 * Forward the whole message: subject, quoted body, original attachments.
 *
 * The composer does the work, not this card. `CommunicationArea` owns the
 * attachment model and the draft, so it exposes `forward()` and this button
 * hands it the message; doing it here would need EmailArea to reach into a
 * model it does not own.
 */
function forward() {
  emailBox.forward({
    ...props.activity.data,
    communication_date: props.activity.communication_date,
  })
}

/**
 * Item 19: one quiet mark beside the timestamp.
 *
 * It replaces a coloured `Badge` that printed the raw `delivery_status` word --
 * "Sent", "Sending", "Error" -- on every message. That was a badge shelf
 * (spec §2.13) and it said nothing an agent needed: what they want to know is
 * whether the customer OPENED it. So: one gray tick for sent, two for opened
 * with the relative time beside them, and the exact timestamp on hover.
 *
 * No new tracking. `read_by_recipient` is the framework's own read receipt and
 * `delivery_status` is the Email Queue's own verdict; both were already in the
 * activities payload before this stage.
 */
const indicator = computed(() => {
  if (!showEmailState(props.activity)) return null

  const state = emailState(props.activity.data)
  const openedAt = emailStateTimestamp(props.activity.data)
  const sentAt = props.activity.communication_date

  switch (state) {
    case 'opened':
      return {
        icon: LucideCheckCheck,
        // The one place a word is worth the room: "sent" is the default
        // assumption, "opened" is the news.
        label: openedAt
          ? __('Opened · {0}', [timeAgo(openedAt)])
          : __('Opened'),
        tooltip: openedAt
          ? __('Opened on {0}', [formatDate(openedAt)])
          : __('Opened by the recipient'),
        className: 'text-ink-gray-5',
      }
    case 'sent':
      return {
        icon: LucideCheck,
        label: '',
        tooltip: __('Sent on {0}', [formatDate(sentAt)]),
        className: 'text-ink-gray-4',
      }
    case 'pending':
      return {
        icon: LucideClock,
        label: '',
        tooltip: __('Queued for delivery'),
        className: 'text-ink-gray-4',
      }
    case 'failed':
      return {
        icon: LucideAlertCircle,
        label: '',
        tooltip: __('Delivery failed'),
        className: 'text-ink-red-3',
      }
    default:
      return null
  }
})
</script>
