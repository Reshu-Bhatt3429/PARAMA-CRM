<template>
  <div class="rounded-lg border border-outline-gray-2 bg-surface-white">
    <div class="flex items-center gap-2 p-3">
      <span
        class="flex size-6 shrink-0 items-center justify-center rounded-full bg-surface-gray-2 text-xs-medium text-ink-gray-6"
      >
        {{ position }}
      </span>
      <Select
        v-model="action.action_type"
        class="w-56"
        size="sm"
        :options="ACTION_OPTIONS"
        @update:model-value="onTypeChange"
      />
      <span class="truncate text-p-sm text-ink-gray-5">
        {{ summariseAction(action) }}
      </span>
      <div class="ml-auto flex items-center gap-1">
        <Button
          variant="ghost"
          :icon-left="expanded ? 'lucide-chevron-up' : 'lucide-chevron-down'"
          :label="expanded ? __('Hide') : __('Edit')"
          size="sm"
          @click="expanded = !expanded"
        />
        <Button
          variant="ghost"
          icon="lucide-trash-2"
          size="sm"
          :label="__('Remove action')"
          @click="emit('remove')"
        />
      </div>
    </div>

    <!-- Inline, never a modal (§2 UX principle 4 and the design note: the
         HubSpot canvas simplified to a linear stack). -->
    <div v-if="expanded" class="border-t border-outline-gray-2 p-3">
      <div
        v-if="action.action_type === ACTION_EMAIL"
        class="grid grid-cols-2 gap-4"
      >
        <div class="flex flex-col gap-1.5">
          <FormLabel :label="__('Email template')" />
          <Link
            v-model="action.email_template"
            doctype="Email Template"
            :placeholder="__('Choose a template')"
          />
        </div>
        <div class="flex flex-col gap-1.5">
          <FormLabel :label="__('Send to')" />
          <Select
            v-model="action.recipient_mode"
            :options="RECIPIENT_OPTIONS"
          />
        </div>
        <FormControl
          v-if="action.recipient_mode === RECIPIENT_SPECIFIC"
          v-model="action.recipient_address"
          type="text"
          size="sm"
          variant="subtle"
          :label="__('Address')"
          placeholder="someone@example.com"
        />
        <p class="col-span-2 text-p-sm text-ink-gray-5">
          {{
            __(
              'Every address is checked against the opt-out ledger before anything is queued. A customer who opted out is logged as skipped and never mailed.',
            )
          }}
        </p>
      </div>

      <div
        v-else-if="action.action_type === ACTION_TASK"
        class="grid grid-cols-2 gap-4"
      >
        <FormControl
          v-model="action.task_title"
          type="text"
          size="sm"
          variant="subtle"
          :label="__('Task title')"
          :placeholder="__('Call the customer back')"
        />
        <div class="flex flex-col gap-1.5">
          <FormLabel :label="__('Priority')" />
          <Select
            v-model="action.task_priority"
            :options="TASK_PRIORITIES.map((p) => ({ label: __(p), value: p }))"
          />
        </div>
        <FormControl
          v-model="action.task_due_offset_days"
          type="number"
          size="sm"
          min="0"
          variant="subtle"
          :label="__('Due in (days)')"
        />
      </div>

      <div
        v-else-if="action.action_type === ACTION_NOTIFY"
        class="grid grid-cols-2 gap-4"
      >
        <div class="flex flex-col gap-1.5">
          <FormLabel :label="__('Notify')" />
          <Select v-model="action.notify_mode" :options="NOTIFY_OPTIONS" />
        </div>
        <div
          v-if="action.notify_mode === NOTIFY_SPECIFIC"
          class="flex flex-col gap-1.5"
        >
          <FormLabel :label="__('User')" />
          <Link
            v-model="action.notify_user"
            doctype="User"
            :placeholder="__('Choose a user')"
          />
        </div>
        <div
          v-if="action.notify_mode === NOTIFY_ROLE"
          class="flex flex-col gap-1.5"
        >
          <FormLabel :label="__('Role')" />
          <Link
            v-model="action.notify_role"
            doctype="Role"
            :placeholder="__('Choose a role')"
          />
        </div>
      </div>

      <div v-else class="grid grid-cols-2 gap-4">
        <div class="flex flex-col gap-1.5">
          <FormLabel :label="__('Field')" />
          <Autocomplete
            :options="fieldOptions"
            :modelValue="action.update_field"
            :placeholder="__('Choose a field')"
            @update:modelValue="
              (option) => (action.update_field = option?.value || '')
            "
          />
        </div>
        <FormControl
          v-model="action.update_value"
          type="text"
          size="sm"
          variant="subtle"
          :label="__('New value')"
        />
        <p class="col-span-2 text-p-sm text-ink-gray-5">
          {{
            __(
              'A field this rule writes never triggers another workflow rule. The chain stops here, on purpose.',
            )
          }}
        </p>
      </div>

      <ErrorMessage v-if="errors.length" class="mt-3" :message="errors[0]" />
    </div>
  </div>
</template>

<script setup>
/**
 * One "Then" card. Collapsed it is a sentence; expanded it is the fields that
 * sentence needs, inline. No nested dialog: the design note's whole point is a
 * linear stack a manager can read top to bottom.
 */
import {
  Autocomplete,
  Button,
  ErrorMessage,
  FormControl,
  FormLabel,
  Select,
} from 'frappe-ui'
import Link from '@/components/Controls/Link.vue'
import { computed, ref } from 'vue'
import {
  ACTION_EMAIL,
  ACTION_NOTIFY,
  ACTION_OPTIONS,
  ACTION_TASK,
  NOTIFY_OPTIONS,
  NOTIFY_SPECIFIC,
  NOTIFY_ROLE,
  PROTECTED_FIELDS,
  RECIPIENT_OPTIONS,
  RECIPIENT_SPECIFIC,
  TASK_PRIORITIES,
  emptyAction,
  summariseAction,
  validateAction,
} from '@/utils/workflows'

const props = defineProps({
  action: { type: Object, required: true },
  position: { type: Number, required: true },
  fields: { type: Array, default: () => [] },
  startExpanded: { type: Boolean, default: false },
})

const emit = defineEmits(['remove'])

const action = props.action
const expanded = ref(props.startExpanded)

const errors = computed(() => validateAction(action, props.position))

/** Writable fields only: the framework's own columns are never offered. */
const fieldOptions = computed(() =>
  (props.fields || [])
    .filter((field) => !PROTECTED_FIELDS.includes(field.fieldname))
    .filter(
      (field) =>
        !['Section Break', 'Column Break', 'Table', 'HTML'].includes(
          field.fieldtype,
        ),
    )
    .map((field) => ({
      label: field.label || field.fieldname,
      value: field.fieldname,
    })),
)

/**
 * Changing the type resets the fields the old type owned. Leaving them behind
 * saves a template name on a task action, which reads like a bug to whoever
 * opens the rule next.
 */
function onTypeChange(type) {
  const fresh = emptyAction(type)
  Object.keys(fresh).forEach((key) => {
    if (key !== 'action_type') action[key] = fresh[key]
  })
  expanded.value = true
}
</script>
