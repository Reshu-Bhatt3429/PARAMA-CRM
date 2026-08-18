<template>
  <div
    class="flex flex-col rounded-md border border-dashed border-outline-gray-3 bg-surface-gray-1 px-3 py-1.5 text-base"
  >
    <div class="-mb-0.5 flex items-center justify-between gap-2 truncate">
      <div class="flex items-center gap-2 truncate text-ink-gray-7">
        <LucideClock class="size-3.5 shrink-0 text-ink-gray-5" />
        <span class="truncate">{{ __('Scheduled') }}</span>
        <Tooltip :text="exactTime">
          <span class="text-sm text-ink-gray-5">{{ relativeTime }}</span>
        </Tooltip>
      </div>
      <div class="flex shrink-0 items-center gap-0.5">
        <Button
          :loading="busy === 'send'"
          :disabled="Boolean(busy)"
          variant="ghost"
          class="text-ink-gray-7"
          :label="__('Send now')"
          @click="sendNow"
        />
        <Button
          v-if="canCancel"
          :loading="busy === 'cancel'"
          :disabled="Boolean(busy)"
          variant="ghost"
          class="text-ink-gray-7"
          :label="__('Cancel')"
          @click="cancel"
        />
        <Tooltip v-else :text="__('It is already on its way')">
          <span class="px-2 text-sm text-ink-gray-4">{{ __('Sending') }}</span>
        </Tooltip>
      </div>
    </div>

    <div class="flex flex-col gap-1 text-base leading-5 text-ink-gray-8">
      <div class="truncate">{{ job.subject }}</div>
      <div class="truncate">
        <span class="mr-1 text-ink-gray-5">{{ __('To') }}:</span>
        <span>{{ job.recipients }}</span>
      </div>
      <div v-if="job.attachment_count" class="text-sm text-ink-gray-5">
        {{ __('{0} attachment(s)', [job.attachment_count]) }}
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * A not-yet-sent email in the timeline (master spec item 5).
 *
 * Dashed border, gray ground, a clock: it has to be legible at a glance as
 * something that HAS NOT happened yet, sitting among messages that have.
 *
 * The two actions live inline rather than behind a menu because both are
 * time-critical -- the whole reason to look at this card is that the plan
 * changed. `Cancel` disappears once the job is Claimed; the state machine
 * refuses a cancel past that point, and a button that could only fail is worse
 * than no button.
 */
import LucideClock from '~icons/lucide/clock'
import { canCancelJob } from '@/utils/sendLater'
import { formatDate, timeAgo } from '@/utils'
import { Button, Tooltip, call, toast } from 'frappe-ui'
import { computed, ref } from 'vue'

const props = defineProps({
  job: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['reload'])

const busy = ref('')

const canCancel = computed(() => canCancelJob(props.job))
const relativeTime = computed(() => __(timeAgo(props.job.scheduled_at)))
const exactTime = computed(() => formatDate(props.job.scheduled_at))

async function sendNow() {
  busy.value = 'send'
  try {
    await call('crm.api.email.send_scheduled_email_now', {
      job: props.job.name,
    })
    toast.success(__('Email sent'))
    emit('reload')
  } catch (error) {
    toast.error(error?.messages?.[0] || __('Could not send the email'))
  } finally {
    busy.value = ''
  }
}

async function cancel() {
  busy.value = 'cancel'
  try {
    await call('crm.api.email.cancel_scheduled_email', { job: props.job.name })
    toast.success(__('Scheduled email cancelled'))
    emit('reload')
  } catch (error) {
    toast.error(error?.messages?.[0] || __('Could not cancel the email'))
  } finally {
    busy.value = ''
  }
}
</script>
