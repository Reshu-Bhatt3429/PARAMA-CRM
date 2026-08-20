<template>
  <div class="rounded-lg border border-outline-gray-2 bg-surface-white">
    <div
      class="flex items-center justify-between border-b border-outline-gray-2 p-3"
    >
      <div class="flex flex-col">
        <span class="text-base-medium text-ink-gray-8">{{
          __('Recent runs')
        }}</span>
        <span class="text-p-sm text-ink-gray-5">
          {{ __('Read-only. Kept for 90 days.') }}
        </span>
      </div>
      <Button
        variant="ghost"
        icon-left="lucide-refresh-cw"
        size="sm"
        :label="__('Refresh')"
        :loading="runs.loading"
        @click="runs.reload()"
      />
    </div>

    <div v-if="runs.loading && !runs.data" class="flex justify-center p-6">
      <LoadingIndicator class="w-4" />
    </div>
    <div v-else-if="!runs.data?.length" class="p-4 text-p-sm text-ink-gray-5">
      {{ __('This rule has not run yet.') }}
    </div>
    <div v-else class="divide-y divide-outline-gray-2">
      <div
        v-for="run in runs.data"
        :key="run.name"
        class="flex items-center gap-3 px-3 py-2"
      >
        <Badge
          :theme="runTheme(run.status)"
          variant="subtle"
          size="sm"
          :label="__(run.status)"
        />
        <span class="truncate text-p-sm text-ink-gray-7">
          {{ run.action_type }}
        </span>
        <span class="truncate text-p-sm text-ink-gray-5">
          {{ run.reference_docname }}
        </span>
        <Tooltip v-if="run.reason" :text="run.reason">
          <span class="lucide-info size-4 text-ink-gray-4" aria-hidden="true" />
        </Tooltip>
        <span class="ml-auto shrink-0 text-p-sm text-ink-gray-4">
          {{ formatDate(run.executed_at || run.creation, 'D MMM, h:mm a') }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * The read-only half of the editor. It never writes: a Sales Manager has no
 * delete permission on `CRM Workflow Execution Log` and the panel offers no
 * action that would need one. A log an operator can edit is not a record of
 * what happened.
 */
import {
  Badge,
  Button,
  LoadingIndicator,
  Tooltip,
  createResource,
} from 'frappe-ui'
import { watch } from 'vue'
import { formatDate } from '@/utils'
import { runTheme } from '@/utils/workflows'

const props = defineProps({
  rule: { type: String, default: '' },
})

const runs = createResource({
  url: 'crm.workflows.get_recent_runs',
  params: { rule: props.rule, limit: 20 },
  auto: Boolean(props.rule),
})

watch(
  () => props.rule,
  (rule) => rule && runs.submit({ rule, limit: 20 }),
)
</script>
