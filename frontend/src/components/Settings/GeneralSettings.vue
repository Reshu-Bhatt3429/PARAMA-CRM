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
           that is OFF by default (master spec C5, crm/feature_flags.py). -->
      <div
        class="mt-6 px-2 pb-1 text-xs-medium uppercase tracking-wide text-ink-gray-5"
      >
        {{ __('Feature Flags') }}
      </div>
      <div class="h-px border-t mx-2 border-outline-elevation-2" />
      <div class="flex gap-4 items-center justify-between py-3 px-2">
        <div class="flex flex-col">
          <div class="text-p-base-medium text-ink-gray-7 truncate">
            {{ __('Invoices') }}
          </div>
          <div class="text-p-sm text-ink-gray-5">
            {{
              __(
                'The Invoices page, the Create invoice action on a deal, and every invoice endpoint. While this is off each of them refuses and the customer link answers like a dead token. Fill in Settings → Invoicing → Company Profile before you turn it on.',
              )
            }}
          </div>
        </div>
        <div>
          <Switch
            v-model="settings.doc.invoices_enabled"
            size="sm"
            @click.stop="toggleInvoices()"
          />
        </div>
      </div>
      <div class="h-px border-t mx-2 border-outline-elevation-2" />
      <div class="flex gap-4 items-center justify-between py-3 px-2">
        <div class="flex flex-col">
          <div class="text-p-base-medium text-ink-gray-7 truncate">
            {{ __('Invoice reminders') }}
          </div>
          <div class="text-p-sm text-ink-gray-5">
            {{
              __(
                'Chases unpaid invoices: one reminder ladder per instalment, on the due date, then after seven days, then after fourteen. Delivery also needs the outbound engine. Separate from Invoices on purpose — an agency may raise invoices long before it wants the app to chase them.',
              )
            }}
          </div>
        </div>
        <div>
          <Switch
            v-model="settings.doc.invoice_reminders_enabled"
            size="sm"
            @click.stop="toggle('invoice_reminders_enabled')"
          />
        </div>
      </div>
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

// The sidebar entry and the deal action read a cached copy of this flag. Refresh
// it here so the module appears and disappears without a page reload.
function toggleInvoices() {
  settings.save.submit(null, {
    onSuccess: () => {
      refreshInvoicesFlag()
      toast.success(
        settings.doc.invoices_enabled
          ? __('Setting enabled successfully')
          : __('Setting disabled successfully'),
      )
    },
  })
}
</script>
