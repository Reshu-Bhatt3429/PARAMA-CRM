<template>
  <div class="flex h-full flex-col gap-6 py-8 px-6 text-ink-gray-8">
    <div class="flex flex-col gap-1 px-2">
      <h2 class="flex gap-2 text-2xl-semibold leading-none h-5">
        {{ __('General Settings') }}
      </h2>
      <p class="text-p-base text-ink-gray-6">
        {{ __('Configure general settings for your application') }}
      </p>
    </div>

    <div class="flex-1 flex flex-col overflow-y-auto">
      <div class="flex items-center justify-between py-3 px-2">
        <div class="flex flex-col">
          <div class="text-p-base-medium text-ink-gray-7 truncate">
            {{ __('Update timestamp on new communication') }}
          </div>
          <div class="text-p-sm text-ink-gray-5 truncate">
            {{
              __(
                'Update the modified timestamp on new email communication & comments for leads & deals',
              )
            }}
          </div>
        </div>
        <div>
          <Switch
            v-model="settings.doc.update_timestamp_on_new_communication"
            size="sm"
            @click.stop="toggle('update_timestamp_on_new_communication')"
          />
        </div>
      </div>
      <div class="h-px border-t mx-2 border-outline-elevation-2" />
      <div class="flex gap-4 items-center justify-between py-3 px-2">
        <div class="flex flex-col">
          <div class="text-p-base-medium text-ink-gray-7 truncate">
            {{ __('Mark lead/deal as replied on response') }}
          </div>
          <div class="text-p-sm text-ink-gray-5">
            {{
              __(
                'Automatically sets communication status to “Replied” for the lead or deal when a response is received. Applies only when SLA is enabled',
              )
            }}
          </div>
        </div>
        <div>
          <Switch
            v-model="settings.doc.auto_mark_replied_on_response"
            size="sm"
            @click.stop="toggle('auto_mark_replied_on_response')"
          />
        </div>
      </div>
      <div class="h-px border-t mx-2 border-outline-elevation-2" />
      <div class="flex gap-4 items-center justify-between py-3 px-2">
        <div class="flex flex-col">
          <div class="text-p-base-medium text-ink-gray-7 truncate">
            {{ __('Reopen lead/deal on new communication') }}
          </div>
          <div class="text-p-sm text-ink-gray-5">
            {{
              __(
                'Automatically sets communication status to “Open” for the lead or deal when a new communication is created. Applies only when SLA is enabled',
              )
            }}
          </div>
        </div>
        <div>
          <Switch
            v-model="settings.doc.auto_reopen_on_new_communication"
            size="sm"
            @click.stop="toggle('auto_reopen_on_new_communication')"
          />
        </div>
      </div>
      <div class="h-px border-t mx-2 border-outline-elevation-2" />
      <div class="flex gap-4 items-center justify-between py-3 px-2">
        <div class="flex flex-col">
          <div class="text-p-base font-medium text-ink-gray-7 truncate">
            {{ __('Timeline timestamp format') }}
          </div>
          <div class="text-p-sm text-ink-gray-5">
            {{
              __(
                'Show timestamps in the activity timeline as relative time (5 mins ago) or an exact date & time',
              )
            }}
          </div>
        </div>
        <div>
          <FormControl
            v-model="settings.doc.crm_timeline_timestamp_format"
            type="select"
            class="w-40"
            :options="timestampFormatOptions"
            :placeholder="__('Relative')"
            @update:modelValue="save()"
          />
        </div>
      </div>
      <div class="h-px border-t mx-2 border-outline-elevation-2" />
      <div class="flex gap-4 items-center justify-between py-3 px-2">
        <div class="flex flex-col">
          <div class="text-p-base font-medium text-ink-gray-7 truncate">
            {{ __('Timeline sort order') }}
          </div>
          <div class="text-p-sm text-ink-gray-5">
            {{
              __(
                'Order of activities, emails, comments and calls in the timeline',
              )
            }}
          </div>
        </div>
        <div>
          <FormControl
            v-model="settings.doc.crm_timeline_sort_order"
            type="select"
            class="w-40"
            :options="sortOrderOptions"
            :placeholder="__('Oldest First')"
            @update:modelValue="save()"
          />
        </div>
      </div>

      <!-- Feature Flags. Every expansion feature ships behind a named switch
           that is OFF by default (master spec C5). The list below is the whole
           of `crm/feature_flags.py::FLAGS`, in registry order: Stage 5.1 and
           Stage 5.2 both point a manager here, so every flag has to be here. -->
      <div
        class="mt-6 px-2 pb-1 text-xs-medium uppercase tracking-wide text-ink-gray-5"
      >
        {{ __('Feature Flags') }}
      </div>
      <div class="text-p-sm text-ink-gray-5 px-2 pb-2">
        {{
          __(
            'Every switch added by the feature expansion, in one place. All of them are OFF until somebody turns them on. The registry that must agree with this section is crm/feature_flags.py.',
          )
        }}
      </div>
      <template v-for="flag in featureFlags" :key="flag.fieldname">
        <div class="h-px border-t mx-2 border-outline-elevation-2" />
        <div
          class="flex gap-4 items-center justify-between py-3 px-2"
          :class="flag.subordinate && 'pl-6'"
        >
          <div class="flex flex-col">
            <div class="text-p-base-medium text-ink-gray-7 truncate">
              {{ __(flag.label) }}
            </div>
            <div class="text-p-sm text-ink-gray-5">
              {{ __(flag.description) }}
            </div>
          </div>
          <div>
            <Switch
              v-model="settings.doc[flag.fieldname]"
              size="sm"
              @click.stop="toggleFlag(flag.fieldname)"
            />
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { getSettings } from '@/stores/settings'
import { refreshInvoicesFlag } from '@/composables/invoices'
import { FormControl, Switch, toast } from 'frappe-ui'

const { _settings: settings } = getSettings()

const timestampFormatOptions = [
  { label: __('Relative'), value: 'Relative' },
  { label: __('Exact'), value: 'Exact' },
]
const sortOrderOptions = [
  { label: __('Oldest First'), value: 'Oldest First' },
  { label: __('Newest First'), value: 'Newest First' },
]

/**
 * Every flag in `crm/feature_flags.py::FLAGS`, in the order the registry lists
 * them.
 *
 * The label and the description are COPIED VERBATIM from the matching Check
 * field in `crm/fcrm/doctype/fcrm_settings/fcrm_settings.json`. That file is the
 * UI half of the registry contract, so nothing here is written twice in
 * different words. Adding a flag means adding a registry entry, a Check field
 * with its description, and one row here.
 */
const featureFlags = [
  {
    fieldname: 'outbound_engine_enabled',
    label: 'Outbound engine',
    description:
      'Lets the scheduler claim and process CRM Outbound Jobs (Send Later, mass email, sequences, scheduled reports). While this is off the outbound sweep returns without reading a single job, so nothing can be sent through the outbound engine. Turning it on does not by itself create any job.',
  },
  {
    fieldname: 'task_reminders_enabled',
    label: 'Task due-date reminders',
    description:
      'Lets the scheduler remind assignees about tasks that are about to fall due. While this is off the reminder sweep returns without reading a single task row, so no notification and no email is ever produced.',
  },
  {
    fieldname: 'email_sequences_enabled',
    label: 'Email sequences',
    description:
      'Lets a follow-up stage set to the Email channel build and schedule its message. While this is off every email stage parks with a stated reason and no outbound job is created. Delivery also needs the outbound engine above.',
  },
  {
    fieldname: 'deal_health_enabled',
    label: 'Deal health flags',
    description:
      'Lets the nightly sweep work out which open deals need attention (close date passed, stalled, awaiting a reply) and store the answer on the deal. While this is off the sweep reads no deal row, no deal carries a Needs attention chip, and the manager digest says nothing about deal health.',
  },
  {
    fieldname: 'workflow_rules_enabled',
    label: 'Workflow rules',
    description:
      'Lets a workflow rule fire when a lead or a deal is created or changes. While this is off the engine stops on one cached read and no rule can act, whatever its own Enabled switch says. Both switches must be on. A rule that writes a field NEVER triggers another rule.',
  },
  {
    fieldname: 'invoices_enabled',
    label: 'Invoices',
    description:
      'Lets the invoice module be used at all: the Invoices page, Convert to invoice on a deal, and every endpoint in crm.api.invoices. While this is off those endpoints refuse and a tokenised customer link answers exactly like a dead token.',
  },
  {
    // Indented under Invoices: the ladder does not exist while the module is
    // off, so the two switches are not siblings even though both must be on.
    fieldname: 'invoice_reminders_enabled',
    label: 'Invoice payment reminders',
    subordinate: true,
    description:
      'Lets the hourly sweep chase unpaid invoices: one ladder per payment-schedule row, on the due date, then +7 days, then +14. Suppression-checked and quiet-hours aware, and pausable per invoice. Separate from Invoices above on purpose. Delivery also needs the Outbound engine switch.',
  },
]

function toggle(settingKey) {
  settings.save.submit(null, {
    onSuccess: () => {
      toast.success(
        settings.doc[settingKey]
          ? __('Setting enabled successfully')
          : __('Setting disabled successfully'),
      )
    },
  })
}

function save() {
  settings.save.submit(null, {
    onSuccess: () => toast.success(__('Setting updated successfully')),
  })
}

/**
 * Save one feature flag.
 *
 * `invoices_enabled` needs one extra thing: the sidebar entry, the deal action
 * and the dashboard tiles read a cached copy of it, so the copy is refreshed
 * here and the module appears and disappears without a page reload.
 */
function toggleFlag(fieldname) {
  settings.save.submit(null, {
    onSuccess: () => {
      if (fieldname === 'invoices_enabled') refreshInvoicesFlag()
      toast.success(
        settings.doc[fieldname]
          ? __('Setting enabled successfully')
          : __('Setting disabled successfully'),
      )
    },
  })
}
</script>
