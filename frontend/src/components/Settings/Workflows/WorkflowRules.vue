<template>
  <div class="flex h-full flex-col gap-6 p-6 text-ink-gray-8">
    <div class="flex justify-between px-2 pt-2">
      <div class="flex flex-col gap-1 w-9/12">
        <h2 class="flex gap-2 text-2xl-semibold leading-none h-5">
          {{ __('Workflow Rules') }}
        </h2>
        <p class="text-p-base text-ink-gray-6">
          {{
            __(
              'When a lead or a deal changes, do something about it: send a template, make a task, notify somebody, set a field.',
            )
          }}
        </p>
      </div>
      <div class="flex item-center space-x-2 w-3/12 justify-end">
        <Button
          :label="__('New')"
          icon-left="lucide-plus"
          variant="solid"
          @click="updateStep('view', null)"
        />
      </div>
    </div>

    <!-- The master switch. A rule that looks enabled but cannot fire is the
         single most confusing state this feature has, so it is said once, at
         the top, and only while it is true. -->
    <div
      v-if="flag.data !== undefined && !flag.data"
      class="mx-2 flex items-start gap-2 rounded-md border border-outline-gray-2 bg-surface-gray-1 p-3 text-p-sm text-ink-gray-6"
    >
      <span class="lucide-info size-4 mt-0.5 shrink-0" aria-hidden="true" />
      <span>
        {{
          __(
            'Workflow rules are switched off site-wide. Rules can be written and saved, but none of them will fire until "Workflow rules" is turned on in Settings, General, Feature Flags.',
          )
        }}
      </span>
    </div>

    <div class="flex h-full overflow-y-auto">
      <div
        v-if="rules.loading && !rules.data"
        class="flex items-center justify-center mt-12 w-full"
      >
        <LoadingIndicator class="w-4" />
      </div>
      <EmptyState
        v-else-if="rules.data?.length === 0"
        name="Workflow Rules"
        :title="__('No workflow rules yet')"
        :description="__('Add one to get started.')"
        :icon="h(WorkflowIcon)"
      />
      <div v-else class="w-full">
        <div class="flex items-center p-2 text-sm text-ink-gray-5">
          <div class="w-5/12">{{ __('Rule') }}</div>
          <div class="w-3/12">{{ __('When') }}</div>
          <div class="w-2/12">{{ __('Runs today') }}</div>
          <div class="w-2/12 text-right">{{ __('Enabled') }}</div>
        </div>
        <div class="h-px border-t mx-2 border-outline-elevation-2" />
        <div class="overflow-y-auto">
          <template v-for="(rule, i) in rules.data" :key="rule.name">
            <div
              class="flex items-center px-2 py-3 cursor-pointer hover:bg-surface-gray-2 rounded"
              @click="updateStep('view', rule.name)"
            >
              <div class="w-5/12 truncate text-base text-ink-gray-8">
                {{ rule.title }}
              </div>
              <div class="w-3/12 truncate text-p-sm text-ink-gray-6">
                {{ describeTrigger(rule) }}
              </div>
              <div class="w-2/12 text-p-sm text-ink-gray-6">
                {{ rule.runs_today }}
                <span v-if="rule.daily_action_cap > 0" class="text-ink-gray-4">
                  / {{ rule.daily_action_cap }}
                </span>
              </div>
              <div class="w-2/12 flex justify-end" @click.stop>
                <Switch
                  size="sm"
                  :model-value="Boolean(rule.enabled)"
                  @update:model-value="toggle(rule)"
                />
              </div>
            </div>
            <hr v-if="rules.data.length !== i + 1" class="mx-2" />
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import {
  Button,
  LoadingIndicator,
  Switch,
  call,
  createResource,
  toast,
} from 'frappe-ui'
import { h, inject } from 'vue'
import WorkflowIcon from '~icons/lucide/workflow'
import EmptyState from '@/components/ListViews/EmptyState.vue'
import { describeTrigger } from '@/utils/workflows'

const updateStep = inject('updateStep')

const rules = createResource({
  url: 'crm.workflows.get_rules',
  auto: true,
})

// Read-only, and only so the banner above can be honest about why nothing
// fires. The switch itself lives in Settings, General.
const flag = createResource({
  url: 'frappe.client.get_single_value',
  params: { doctype: 'FCRM Settings', field: 'workflow_rules_enabled' },
  auto: true,
})

async function toggle(rule) {
  const next = rule.enabled ? 0 : 1
  try {
    await call('crm.workflows.set_rule_enabled', {
      name: rule.name,
      enabled: next,
    })
    rule.enabled = next
    toast.success(next ? __('Rule enabled') : __('Rule disabled'))
  } catch (error) {
    toast.error(error?.messages?.[0] || __('Could not change the rule'))
  }
}
</script>
