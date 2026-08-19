<template>
  <!--
    Master spec §2.15: the timeline gets ONE dismissible Brief card. Not a stack
    of AI cards, and not a panel that is always there — it appears because
    somebody asked for it and it goes away when they are done reading it.
  -->
  <div
    class="mb-5 rounded-lg border border-outline-blue-1 bg-surface-blue-1 px-4 py-3"
    data-testid="ai-brief-card"
  >
    <div class="flex items-center gap-2">
      <LucideSparkles class="size-4 text-ink-blue-7" aria-hidden="true" />
      <span class="text-base-medium text-ink-blue-8">{{ __('AI Brief') }}</span>
      <TimelineTimestamp
        v-if="brief?.generated_at"
        :date="brief.generated_at"
        class-name="text-sm text-ink-gray-5"
      />
      <div class="ml-auto flex items-center gap-1">
        <Button
          variant="ghost"
          :tooltip="__('Regenerate')"
          :loading="loading"
          icon="refresh-cw"
          @click="emit('regenerate')"
        />
        <Button
          variant="ghost"
          :tooltip="__('Save as note')"
          :loading="saving"
          :icon="NoteIcon"
          @click="emit('save-note')"
        />
        <Button
          variant="ghost"
          :tooltip="__('Dismiss')"
          icon="x"
          @click="emit('dismiss')"
        />
      </div>
    </div>

    <ul class="mt-2.5 flex flex-col gap-1.5">
      <li
        v-for="(bullet, i) in brief?.bullets || []"
        :key="i"
        class="flex gap-2 text-base text-ink-gray-8"
      >
        <span class="mt-2 size-1 shrink-0 rounded-full bg-ink-gray-5" />
        <span>{{ bullet }}</span>
      </li>
    </ul>

    <div
      v-if="brief?.next_step?.description"
      class="mt-3 flex flex-wrap items-center gap-2 border-t border-outline-blue-1 pt-2.5"
    >
      <span class="text-base text-ink-gray-8">
        <span class="text-ink-gray-6">{{ __('Suggested next step') }}:</span>
        {{ brief.next_step.description }}
      </span>
      <!--
        C6: this opens the task modal with the fields filled in. The task exists
        after the agent presses Create in that modal, and not before.
      -->
      <Button
        class="ml-auto"
        :label="__('Create task')"
        iconLeft="plus"
        @click="emit('create-task')"
      />
    </div>

    <!--
      Item 15, as amended: tone is shown only when the customer has actually
      written something. The server sends null otherwise, so there is no
      "Tone: unknown" row to explain away.
    -->
    <div v-if="tone" class="mt-2 text-sm text-ink-gray-6">
      {{ __('Tone') }}: {{ __(toneLabels[tone]) }}
    </div>
  </div>
</template>

<script setup>
import NoteIcon from '@/components/Icons/NoteIcon.vue'
import TimelineTimestamp from '@/components/Activities/TimelineTimestamp.vue'
import LucideSparkles from '~icons/lucide/sparkles'
import { normalizeTone } from '@/utils/aiBrief'
import { Button } from 'frappe-ui'
import { computed } from 'vue'

const props = defineProps({
  brief: { type: Object, default: () => ({}) },
  loading: { type: Boolean, default: false },
  saving: { type: Boolean, default: false },
})

const emit = defineEmits(['regenerate', 'dismiss', 'create-task', 'save-note'])

const toneLabels = {
  positive: 'Positive',
  neutral: 'Neutral',
  negative: 'Negative',
  frustrated: 'Frustrated',
}

const tone = computed(() => normalizeTone(props.brief?.tone))
</script>
