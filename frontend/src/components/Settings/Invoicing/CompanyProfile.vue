<template>
  <div class="flex h-full flex-col gap-6 px-6 py-8 text-ink-gray-8">
    <div class="flex justify-between px-2">
      <div class="flex w-9/12 flex-col gap-1">
        <h2 class="flex h-5 gap-2 text-2xl-semibold leading-none">
          {{ __('Company Profile') }}
        </h2>
        <p class="text-p-base text-ink-gray-6">
          {{
            __(
              'The supplier half of every GST invoice. An invoice cannot be issued while the legal name, address, state code or GSTIN is missing.',
            )
          }}
        </p>
      </div>
      <div class="item-center flex w-3/12 justify-end space-x-2">
        <Button
          v-if="profile.isDirty"
          :label="__('Save')"
          variant="solid"
          :loading="profile.save.loading"
          @click="save"
        />
      </div>
    </div>

    <div
      v-if="profile.get.loading && !profile.doc"
      class="flex items-center justify-center py-12"
    >
      <LoadingIndicator class="w-4" />
    </div>

    <div
      v-else-if="profile.doc"
      class="flex flex-1 flex-col gap-6 overflow-y-auto px-2"
    >
      <section>
        <h3 class="text-p-base-medium text-ink-gray-7">{{ __('Agency') }}</h3>
        <div class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <FormControl
            v-model="profile.doc.legal_name"
            type="text"
            :label="__('Legal name')"
          />
          <FormControl
            v-model="profile.doc.trade_name"
            type="text"
            :label="__('Trade name')"
          />
          <FormControl
            v-model="profile.doc.gstin"
            type="text"
            :label="__('GSTIN')"
            :description="
              __(
                '15 characters. Its first two digits must match the state code below.',
              )
            "
          />
          <FormControl
            v-model="profile.doc.phone"
            type="text"
            :label="__('Phone')"
          />
          <FormControl
            v-model="profile.doc.email"
            type="text"
            :label="__('Email')"
          />
        </div>
      </section>

      <section>
        <h3 class="text-p-base-medium text-ink-gray-7">
          {{ __('Registered address') }}
        </h3>
        <div class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <FormControl
            v-model="profile.doc.address"
            class="sm:col-span-2"
            type="textarea"
            :rows="3"
            :label="__('Address')"
          />
          <FormControl
            v-model="profile.doc.state"
            type="text"
            :label="__('State')"
          />
          <FormControl
            v-model="profile.doc.state_code"
            type="text"
            :label="__('State code')"
            :description="
              __(
                'Two digits. It decides CGST + SGST against IGST on every invoice.',
              )
            "
          />
        </div>
      </section>

      <section>
        <h3 class="text-p-base-medium text-ink-gray-7">
          {{ __('Invoicing') }}
        </h3>
        <div class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <FormControl
            v-model="profile.doc.invoice_number_prefix"
            type="text"
            :label="__('Invoice number prefix')"
            :description="
              __(
                'Checked against Rule 46(b) as you save: the whole number must fit 16 characters.',
              )
            "
          />
          <FormControl
            v-model="profile.doc.default_sac"
            type="select"
            :label="__('Default SAC code')"
            :options="sacOptions"
            :description="__('The code a new invoice line starts on.')"
          />
          <FormControl
            v-model="profile.doc.upi_vpa"
            type="text"
            :label="__('UPI ID (VPA)')"
            :description="
              __('Prints a QR on the invoice for the amount still outstanding.')
            "
          />
          <div class="flex items-start justify-between gap-4 pt-6">
            <div class="flex flex-col">
              <div class="text-p-base-medium text-ink-gray-7">
                {{ __('Default to tour-package mode') }}
              </div>
              <div class="text-p-sm text-ink-gray-5">
                {{
                  __(
                    '5% on the gross, and the prescribed statement is printed.',
                  )
                }}
              </div>
            </div>
            <Switch
              size="sm"
              :model-value="Boolean(profile.doc.tour_package_mode_default)"
              @update:model-value="
                (value) =>
                  (profile.doc.tour_package_mode_default = value ? 1 : 0)
              "
            />
          </div>
        </div>
      </section>

      <section>
        <h3 class="text-p-base-medium text-ink-gray-7">
          {{ __('Default terms') }}
        </h3>
        <FormControl
          v-model="profile.doc.terms_default"
          class="mt-3"
          type="textarea"
          :rows="4"
          :description="
            __('One line becomes one bullet. A new invoice starts with these.')
          "
        />
      </section>

      <section>
        <h3 class="text-p-base-medium text-ink-gray-7">
          {{ __('E-invoicing') }}
        </h3>
        <div class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <FormControl
            v-model.number="profile.doc.einvoice_threshold"
            type="number"
            :label="__('Turnover threshold')"
          />
        </div>
        <!-- Read-only and written by the server. The figure behind it is a
             recorded DEFAULT and not a verified 2026 fact (Stage 5.3a §The
             e-invoicing threshold), which is exactly why the note names its
             source and tells the reader to confirm it with their CA. -->
        <p
          v-if="profile.doc.einvoice_note"
          class="mt-3 flex items-start gap-2 rounded-md border border-outline-gray-2 bg-surface-gray-1 p-3 text-p-sm text-ink-gray-6"
        >
          <span class="lucide-info mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>{{ profile.doc.einvoice_note }}</span>
        </p>
      </section>
    </div>

    <ErrorMessage v-if="error" class="px-2" :message="error" />
  </div>
</template>

<script setup>
/**
 * Company Profile (design note item 29, §Data model).
 *
 * A manager-only page. `CRM Company Profile` grants a Sales User read and
 * nothing else, and this component is only mounted for a manager, so the two
 * agree — but the doctype is the lock and this is only the door.
 *
 * The e-invoicing note is READ-ONLY and comes from the server. It says what this
 * module does not do (no IRN, no portal) and names the notification the
 * threshold was last read from, so a reader can judge how old the figure is.
 * Nothing here presents that number as verified.
 */
import {
  Button,
  ErrorMessage,
  FormControl,
  LoadingIndicator,
  Switch,
  createDocumentResource,
  createListResource,
  toast,
} from 'frappe-ui'
import { computed, ref } from 'vue'

const error = ref('')

const profile = createDocumentResource({
  doctype: 'CRM Company Profile',
  name: 'CRM Company Profile',
  auto: true,
})

const sacCodes = createListResource({
  doctype: 'CRM SAC Code',
  fields: ['name', 'code', 'description'],
  filters: { enabled: 1 },
  pageLength: 100,
  auto: true,
})

const sacOptions = computed(() => [
  { label: __('None'), value: '' },
  ...(sacCodes.data || []).map((row) => ({
    label: row.description ? `${row.code} — ${row.description}` : row.code,
    value: row.name,
  })),
])

function save() {
  error.value = ''
  profile.save.submit(null, {
    onSuccess: () => {
      // Re-read: the controller validates the GSTIN, the state code and the
      // number prefix, and it rewrites the e-invoicing note.
      profile.reload()
      toast.success(__('Company Profile saved'))
    },
    onError: (err) => {
      error.value =
        err?.messages?.[0] || err?.message || __('Could not save the profile.')
    },
  })
}
</script>
