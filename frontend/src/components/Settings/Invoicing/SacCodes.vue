<template>
  <div class="flex h-full flex-col gap-6 p-6 text-ink-gray-8">
    <div class="flex justify-between px-2 pt-2">
      <div class="flex w-9/12 flex-col gap-1">
        <h2 class="flex h-5 gap-2 text-2xl-semibold leading-none">
          {{ __('SAC Codes') }}
        </h2>
        <p class="text-p-base text-ink-gray-6">
          {{
            __(
              'The service codes and tax rates an invoice line can carry. They ship as placeholders — check every one with your CA before you issue a real invoice.',
            )
          }}
        </p>
      </div>
      <div class="item-center flex w-3/12 justify-end space-x-2">
        <Button
          :label="__('New')"
          icon-left="lucide-plus"
          variant="solid"
          @click="startNew"
        />
      </div>
    </div>

    <!-- The research behind these codes was single-source (design note §Risks).
         Saying so once, at the top, is the honest place for it. -->
    <div
      class="mx-2 flex items-start gap-2 rounded-md border border-outline-amber-1 bg-surface-amber-1 p-3 text-p-sm text-ink-amber-3"
    >
      <span
        class="lucide-triangle-alert mt-0.5 size-4 shrink-0"
        aria-hidden="true"
      />
      <span>
        {{
          __(
            'These codes and rates are editable placeholders. Confirm them with your CA — a wrong SAC or rate is on the tax invoice you send.',
          )
        }}
      </span>
    </div>

    <div class="flex h-full flex-col overflow-y-auto">
      <div
        v-if="codes.loading && !codes.data"
        class="mt-12 flex w-full items-center justify-center"
      >
        <LoadingIndicator class="w-4" />
      </div>

      <div v-else class="w-full">
        <div class="flex items-center p-2 text-sm text-ink-gray-5">
          <div class="w-2/12">{{ __('Code') }}</div>
          <div class="w-6/12">{{ __('Description') }}</div>
          <div class="w-2/12 text-right">{{ __('Tax rate') }}</div>
          <div class="w-2/12 text-right">{{ __('Enabled') }}</div>
        </div>
        <div class="mx-2 h-px border-t border-outline-elevation-2" />

        <template v-for="(row, index) in codes.data || []" :key="row.name">
          <div
            class="flex cursor-pointer items-center rounded px-2 py-3 hover:bg-surface-gray-2"
            @click="startEdit(row)"
          >
            <div class="w-2/12 truncate text-base text-ink-gray-8">
              {{ row.code }}
            </div>
            <div class="w-6/12 truncate text-p-sm text-ink-gray-6">
              {{ row.description || '—' }}
            </div>
            <div class="w-2/12 text-right text-p-sm text-ink-gray-6">
              {{ row.tax_rate }}%
            </div>
            <div class="flex w-2/12 justify-end" @click.stop>
              <Switch
                size="sm"
                :model-value="Boolean(row.enabled)"
                @update:model-value="toggle(row)"
              />
            </div>
          </div>
          <hr v-if="(codes.data || []).length !== index + 1" class="mx-2" />
        </template>

        <div
          v-if="codes.data && !codes.data.length"
          class="py-12 text-center text-base text-ink-gray-5"
        >
          {{ __('No SAC codes yet. Add the ones your CA gave you.') }}
        </div>
      </div>
    </div>
  </div>

  <Dialog v-model="showEditor" :options="{ size: 'md' }">
    <template #body>
      <div class="bg-surface-modal px-4 pb-6 pt-5 sm:px-6">
        <h3 class="text-2xl font-semibold text-ink-gray-9">
          {{ editing.name ? __('Edit SAC code') : __('New SAC code') }}
        </h3>
        <div class="mt-4 flex flex-col gap-3">
          <FormControl
            v-model="editing.code"
            type="text"
            :label="__('SAC code')"
            :placeholder="__('998551')"
            :disabled="Boolean(editing.name)"
            :description="
              editing.name
                ? __(
                    'The code is the record\'s identity and cannot be changed. Disable it and add a new one instead.',
                  )
                : ''
            "
          />
          <FormControl
            v-model="editing.description"
            type="text"
            :label="__('Description')"
          />
          <FormControl
            v-model.number="editing.tax_rate"
            type="number"
            min="0"
            step="0.01"
            :label="__('Tax rate (%)')"
            :description="
              __('Tour-package invoices override this with 5% on the gross.')
            "
          />
          <FormControl
            v-model="editing.verify_note"
            type="textarea"
            :rows="2"
            :label="__('Note')"
            :placeholder="__('Verified with the CA on …')"
          />
        </div>
        <ErrorMessage v-if="error" class="mt-3" :message="error" />
      </div>
      <div
        class="flex flex-col-reverse gap-2 px-4 pb-6 sm:flex-row sm:justify-end sm:px-6"
      >
        <Button :label="__('Cancel')" @click="showEditor = false" />
        <Button
          variant="solid"
          :label="__('Save')"
          :loading="saving"
          :disabled="saving || !String(editing.code || '').trim()"
          @click="save"
        />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
/**
 * The SAC master (design note item 29, §Risks).
 *
 * Managers only: `CRM SAC Code` grants a Sales User read and nothing more, so an
 * agent can pick a code on an invoice line and cannot change what it means.
 *
 * The code is the record's NAME (`autoname: field:code`), so it is fixed once
 * saved. Renaming would silently rewrite the code on every invoice line that
 * already points at it, which is exactly what a tax document must not do — a
 * wrong code is disabled and replaced, not edited.
 */
import {
  Button,
  Dialog,
  ErrorMessage,
  FormControl,
  LoadingIndicator,
  Switch,
  call,
  createListResource,
  toast,
} from 'frappe-ui'
import { reactive, ref } from 'vue'

const showEditor = ref(false)
const saving = ref(false)
const error = ref('')
const editing = reactive({
  name: '',
  code: '',
  description: '',
  tax_rate: 18,
  verify_note: '',
})

const codes = createListResource({
  doctype: 'CRM SAC Code',
  fields: ['name', 'code', 'description', 'tax_rate', 'enabled', 'verify_note'],
  orderBy: 'code asc',
  pageLength: 100,
  auto: true,
})

function startNew() {
  error.value = ''
  Object.assign(editing, {
    name: '',
    code: '',
    description: '',
    tax_rate: 18,
    verify_note: '',
  })
  showEditor.value = true
}

function startEdit(row) {
  error.value = ''
  Object.assign(editing, {
    name: row.name,
    code: row.code,
    description: row.description || '',
    tax_rate: row.tax_rate,
    verify_note: row.verify_note || '',
  })
  showEditor.value = true
}

async function save() {
  error.value = ''
  saving.value = true
  const values = {
    description: editing.description,
    tax_rate: editing.tax_rate,
    verify_note: editing.verify_note,
  }
  try {
    if (editing.name) {
      await call('frappe.client.set_value', {
        doctype: 'CRM SAC Code',
        name: editing.name,
        fieldname: values,
      })
    } else {
      await call('frappe.client.insert', {
        doc: {
          doctype: 'CRM SAC Code',
          code: String(editing.code).trim(),
          enabled: 1,
          ...values,
        },
      })
    }
    await codes.reload()
    showEditor.value = false
    toast.success(__('SAC code saved'))
  } catch (err) {
    error.value =
      err?.messages?.[0] || err?.message || __('Could not save the SAC code.')
  } finally {
    saving.value = false
  }
}

async function toggle(row) {
  const next = row.enabled ? 0 : 1
  try {
    await call('frappe.client.set_value', {
      doctype: 'CRM SAC Code',
      name: row.name,
      fieldname: { enabled: next },
    })
    row.enabled = next
    toast.success(next ? __('SAC code enabled') : __('SAC code disabled'))
  } catch (err) {
    toast.error(err?.messages?.[0] || __('Could not change the SAC code'))
  }
}
</script>
