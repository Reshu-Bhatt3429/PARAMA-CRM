<template>
  <div v-if="loading" class="flex h-full items-center justify-center">
    <LoadingIndicator class="w-4" />
  </div>
  <div v-else class="flex h-full flex-col gap-6 px-6 py-8 text-ink-gray-8">
    <!-- Header -->
    <div class="flex w-full justify-between px-2">
      <div class="flex items-center gap-2">
        <Button
          variant="ghost"
          icon-left="lucide-chevron-left"
          :label="rule.title || __('New workflow rule')"
          size="md"
          class="-ml-4 max-w-96 cursor-pointer justify-start !pr-0 text-2xl-semibold hover:bg-transparent hover:opacity-70"
          @click="goBack()"
        />
        <Badge
          v-if="isDirty"
          variant="subtle"
          theme="orange"
          size="sm"
          :label="__('Not Saved')"
        />
      </div>
      <div class="flex items-center gap-4">
        <div
          class="flex h-7 items-center gap-2"
          @click="rule.enabled = rule.enabled ? 0 : 1"
        >
          <Switch size="sm" :model-value="Boolean(rule.enabled)" />
          <span class="text-sm text-ink-gray-7">{{ __('Enabled') }}</span>
        </div>
        <Button
          :label="__('Save')"
          theme="gray"
          variant="solid"
          :loading="saving"
          @click="save()"
        />
      </div>
    </div>

    <div class="overflow-y-auto px-2">
      <!-- One vertical stack of connected cards. No nested modal anywhere:
           the design note's UI research is that HubSpot's canvas simplifies to
           a linear When / If / Then read. -->
      <div class="mx-auto flex max-w-3xl flex-col">
        <!-- WHEN -->
        <section
          class="rounded-lg border border-outline-gray-2 bg-surface-white p-4"
        >
          <div class="mb-3 flex items-center gap-2">
            <span
              class="rounded bg-surface-gray-3 px-2 py-0.5 text-xs-medium text-ink-gray-7"
            >
              {{ __('When') }}
            </span>
            <span class="text-p-sm text-ink-gray-5">{{
              describeTrigger(rule)
            }}</span>
          </div>

          <div class="grid grid-cols-2 gap-4">
            <FormControl
              v-model="rule.title"
              type="text"
              size="sm"
              variant="subtle"
              maxlength="140"
              :label="__('Rule title')"
              :placeholder="__('Qualified leads get a call-back task')"
            />
            <div class="flex flex-col gap-1.5">
              <FormLabel :label="__('Runs on')" />
              <Select v-model="rule.apply_on" :options="APPLY_ON_OPTIONS" />
            </div>
            <div class="flex flex-col gap-1.5">
              <FormLabel :label="__('Event')" />
              <Select v-model="rule.event" :options="EVENT_OPTIONS" />
            </div>
            <div
              v-if="rule.event === EVENT_FIELD_CHANGED"
              class="flex flex-col gap-1.5"
            >
              <FormLabel :label="__('Watched field')" />
              <Autocomplete
                :options="watchableFields"
                :modelValue="rule.watched_field"
                :placeholder="__('Choose a field')"
                @update:modelValue="
                  (option) => (rule.watched_field = option?.value || '')
                "
              />
            </div>
          </div>
          <p class="mt-3 text-p-sm text-ink-gray-5">
            {{
              __(
                'A stage or field rule fires only on a real change. Re-saving a record that did not move does nothing.',
              )
            }}
          </p>
        </section>

        <div class="ml-6 h-5 w-px bg-outline-gray-2" aria-hidden="true" />

        <!-- IF -->
        <section
          class="rounded-lg border border-outline-gray-2 bg-surface-white p-4"
        >
          <div class="mb-3 flex items-center gap-2">
            <span
              class="rounded bg-surface-gray-3 px-2 py-0.5 text-xs-medium text-ink-gray-7"
            >
              {{ __('If') }}
            </span>
            <span class="text-p-sm text-ink-gray-5">
              {{
                rule.condition_json.length
                  ? __('only records that match')
                  : __('every record — add a condition to narrow it')
              }}
            </span>
          </div>
          <AssignmentRulesSection
            :key="rule.apply_on"
            :conditions="rule.condition_json"
            name="workflowCondition"
            :errors="''"
            :doctype="rule.apply_on"
          />
        </section>

        <div class="ml-6 h-5 w-px bg-outline-gray-2" aria-hidden="true" />

        <!-- THEN -->
        <section
          class="rounded-lg border border-outline-gray-2 bg-surface-white p-4"
        >
          <div class="mb-3 flex items-center gap-2">
            <span
              class="rounded bg-surface-gray-3 px-2 py-0.5 text-xs-medium text-ink-gray-7"
            >
              {{ __('Then') }}
            </span>
            <span class="text-p-sm text-ink-gray-5">
              {{ __('in this order, after the save is committed') }}
            </span>
          </div>

          <div class="flex flex-col gap-2">
            <template v-for="(action, index) in rule.actions" :key="index">
              <WorkflowActionCard
                :action="action"
                :position="index + 1"
                :fields="fields"
                :start-expanded="rule.actions.length === 1"
                @remove="rule.actions.splice(index, 1)"
              />
              <div
                v-if="index < rule.actions.length - 1"
                class="ml-6 h-3 w-px bg-outline-gray-2"
                aria-hidden="true"
              />
            </template>
          </div>

          <Button
            class="mt-3"
            :label="__('Add action')"
            icon-left="lucide-plus"
            @click="rule.actions.push(emptyAction())"
          />

          <div
            class="mt-4 grid grid-cols-2 gap-4 border-t border-outline-gray-2 pt-4"
          >
            <FormControl
              v-model="rule.daily_action_cap"
              type="number"
              size="sm"
              min="0"
              variant="subtle"
              :label="__('Daily action cap')"
              :description="
                __(
                  '0 means no cap. The owner is told once a day when it is reached.',
                )
              "
            />
          </div>
        </section>
      </div>

      <div v-if="errors.length" class="mx-auto mt-4 max-w-3xl">
        <ErrorMessage
          v-for="message in errors"
          :key="message"
          :message="message"
        />
      </div>

      <div v-if="rule.name" class="mx-auto mt-8 max-w-3xl">
        <WorkflowRecentRuns :rule="rule.name" />
      </div>

      <div v-if="rule.name" class="mx-auto mt-4 flex max-w-3xl justify-end">
        <Button
          theme="red"
          variant="subtle"
          :label="__('Delete rule')"
          icon-left="lucide-trash-2"
          @click="confirmDelete()"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
/**
 * The rule editor: one vertical stack of When / If / Then cards.
 *
 * The condition rows are the assignment-rule builder's own components
 * (`AssignmentRulesSection` -> `CFConditions` -> `CFCondition`), reused rather
 * than reimplemented, so the JSON this page saves is byte-identical in shape to
 * the JSON an assignment rule saves and `crm/workflows.py` has ONE condition
 * format to understand. Those components ask their parent for
 * `validateAssignmentRule` through `inject`, so this page provides it.
 *
 * Changing "Runs on" re-keys the condition card. A condition that names a lead
 * field would silently stop matching on a deal, and a stale field name is worse
 * than an empty one.
 */
import {
  Autocomplete,
  Badge,
  Button,
  ErrorMessage,
  FormControl,
  FormLabel,
  LoadingIndicator,
  Select,
  Switch,
  call,
  createResource,
  toast,
} from 'frappe-ui'
import {
  computed,
  inject,
  onMounted,
  onUnmounted,
  provide,
  ref,
  watch,
} from 'vue'
import AssignmentRulesSection from '../AssignmentRules/AssignmentRulesSection.vue'
import WorkflowActionCard from './WorkflowActionCard.vue'
import WorkflowRecentRuns from './WorkflowRecentRuns.vue'
import { globalStore } from '@/stores/global'
import { disableSettingModalOutsideClick } from '@/composables/settings'
import {
  APPLY_ON_OPTIONS,
  EVENT_FIELD_CHANGED,
  EVENT_OPTIONS,
  PROTECTED_FIELDS,
  emptyAction,
  emptyRule,
  describeTrigger,
  fromServer,
  toPayload,
  validateRule,
} from '@/utils/workflows'

const step = inject('step')
const updateStep = inject('updateStep')
const { $dialog } = globalStore()

const rule = ref(emptyRule())
const initial = ref('')
const loading = ref(false)
const saving = ref(false)

const errors = computed(() => validateRule(rule.value))
const isDirty = computed(() => JSON.stringify(rule.value) !== initial.value)

// The condition components ask for this by name. Workflow conditions are
// optional, so there is nothing to refuse here; the hook exists because the
// builder calls it, and the real check is `validateRule` above.
provide('validateAssignmentRule', () => ({}))

const fieldsResource = createResource({
  url: 'crm.api.doc.get_filterable_fields',
  params: { doctype: rule.value.apply_on },
  auto: true,
})

const fields = computed(() => fieldsResource.data || [])

const watchableFields = computed(() =>
  fields.value
    .filter((field) => !PROTECTED_FIELDS.includes(field.fieldname))
    .map((field) => ({
      label: field.label || field.fieldname,
      value: field.fieldname,
    })),
)

watch(
  () => rule.value.apply_on,
  (doctype) => fieldsResource.submit({ doctype }),
)

watch(isDirty, (dirty) => (disableSettingModalOutsideClick.value = dirty))

onMounted(async () => {
  if (!step.value.data) {
    initial.value = JSON.stringify(rule.value)
    return
  }
  loading.value = true
  try {
    const data = await call('crm.workflows.get_rule', { name: step.value.data })
    rule.value = fromServer(data)
    initial.value = JSON.stringify(rule.value)
  } catch (error) {
    toast.error(error?.messages?.[0] || __('Could not open the rule'))
    updateStep('list', null)
  } finally {
    loading.value = false
  }
})

onUnmounted(() => (disableSettingModalOutsideClick.value = false))

function goBack() {
  if (!isDirty.value) {
    updateStep('list', null)
    return
  }
  $dialog({
    title: __('Unsaved Changes'),
    message: __(
      'Are you sure you want to go back? Unsaved changes will be lost.',
    ),
    variant: 'solid',
    actions: [
      {
        label: __('Go Back'),
        variant: 'solid',
        onClick: (close) => {
          updateStep('list', null)
          close()
        },
      },
    ],
  })
}

async function save() {
  if (errors.value.length) {
    toast.error(errors.value[0])
    return
  }
  saving.value = true
  try {
    const name = await call('crm.workflows.save_rule', {
      rule: toPayload(rule.value),
    })
    rule.value.name = name
    initial.value = JSON.stringify(rule.value)
    toast.success(__('Workflow rule saved'))
  } catch (error) {
    toast.error(error?.messages?.[0] || __('Could not save the rule'))
  } finally {
    saving.value = false
  }
}

function confirmDelete() {
  $dialog({
    title: __('Delete workflow rule'),
    message: __('The rule stops firing at once. Its run history is kept.'),
    variant: 'solid',
    actions: [
      {
        label: __('Delete'),
        theme: 'red',
        variant: 'solid',
        onClick: async (close) => {
          await call('crm.workflows.delete_rule', { name: rule.value.name })
          close()
          updateStep('list', null)
        },
      },
    ],
  })
}
</script>
