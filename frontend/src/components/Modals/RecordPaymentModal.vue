<template>
  <Dialog v-model="show" :options="{ size: 'lg' }">
    <template #body>
      <div class="bg-surface-modal px-4 pb-6 pt-5 sm:px-6">
        <div class="mb-5 flex items-start justify-between">
          <div>
            <h3 class="text-2xl font-semibold text-ink-gray-9">
              {{ __('Record payment') }}
            </h3>
            <p class="mt-1 text-p-sm text-ink-gray-5">
              {{
                __('{0} outstanding on {1}', [
                  formatMoney(remaining, invoice.currency),
                  invoice.invoice_number || invoice.name,
                ])
              }}
            </p>
          </div>
          <Button variant="ghost" icon="x" @click="show = false" />
        </div>

        <div class="flex flex-col gap-4">
          <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <FormControl
              v-model.number="form.amount"
              type="number"
              step="0.01"
              :label="__('Amount')"
              :description="
                __(
                  'Defaults to the whole remaining balance. A refund is a negative amount with a note.',
                )
              "
            />
            <FormControl
              v-model="form.mode"
              type="select"
              :label="__('Mode')"
              :options="modeOptions"
            />
          </div>

          <FormControl
            v-model="form.reference"
            type="text"
            :label="__('Reference')"
            :placeholder="__('UTR, cheque number, receipt number')"
          />

          <FormControl
            v-if="scheduleOptions.length > 1"
            v-model="form.schedule_row"
            type="select"
            :label="__('Instalment')"
            :options="scheduleOptions"
            :description="
              __(
                'Link the payment to one instalment and only that instalment stops being chased.',
              )
            "
          />

          <FormControl
            v-model="form.note"
            type="textarea"
            :rows="2"
            :label="__('Note')"
            :placeholder="__('Required only on a correction')"
          />

          <div class="flex items-start justify-between gap-4">
            <div class="flex flex-col">
              <div class="text-p-base-medium text-ink-gray-7">
                {{ __('Send a thank-you email') }}
              </div>
              <div class="text-p-sm text-ink-gray-5">
                {{
                  __('Goes to {0}. Off by default.', [
                    invoice.customer?.email || __('no address on file'),
                  ])
                }}
              </div>
            </div>
            <Switch
              v-model="form.send_thank_you"
              size="sm"
              :disabled="!invoice.customer?.email"
            />
          </div>

          <ErrorMessage v-if="errors.length" :message="errors[0]" />
        </div>
      </div>

      <div
        class="flex flex-col-reverse gap-2 px-4 pb-6 sm:flex-row sm:justify-end sm:px-6"
      >
        <Button :label="__('Cancel')" @click="show = false" />
        <Button
          variant="solid"
          :label="__('Record payment')"
          :loading="saving"
          :disabled="saving || errors.length > 0"
          @click="submit"
        />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
/**
 * Record one payment (design note §Flow).
 *
 * Every field here is an argument of `crm.api.invoices.record_payment` and
 * nothing else travels: the status maths, the over-payment refusal and the
 * append-only rule all live on the server, and this form deliberately does not
 * repeat them. The amount opens on the whole remaining balance, which is the
 * only default that cannot be wrong by a paisa.
 *
 * There is no edit and no delete affordance anywhere for a recorded payment. A
 * mistake is corrected by a NEGATIVE amount with a note, which is what the
 * history is supposed to read like.
 */
import {
  DATE_FORMAT,
  PAYMENT_MODES,
  formatMoney,
  paymentArgs,
  paymentDefaults,
  paymentErrors,
  remainingAmount,
  unsettledScheduleRows,
} from '@/utils/invoices'
import { formatDate } from '@/utils'
import { Button, Dialog, FormControl, Switch, call, toast } from 'frappe-ui'
import { computed, reactive, ref, watch } from 'vue'

const props = defineProps({
  invoice: { type: Object, required: true },
})

const emit = defineEmits(['recorded'])

const show = defineModel({ type: Boolean, default: false })

const saving = ref(false)
const form = reactive(paymentDefaults(props.invoice))

const remaining = computed(() => remainingAmount(props.invoice))
const errors = computed(() => paymentErrors(form, props.invoice))

const modeOptions = PAYMENT_MODES.map((mode) => ({
  label: __(mode),
  value: mode,
}))

const scheduleOptions = computed(() => [
  { label: __('Not linked to an instalment'), value: '' },
  ...unsettledScheduleRows(props.invoice).map((row) => ({
    label: `${row.label} · ${formatMoney(row.amount, props.invoice.currency)} · ${
      row.due_date ? formatDate(row.due_date, DATE_FORMAT) : ''
    }`,
    value: row.name,
  })),
])

// Re-open on the balance as it stands now, not as it stood when the page loaded.
watch(
  show,
  (open) => {
    if (open) Object.assign(form, paymentDefaults(props.invoice))
  },
  { immediate: true },
)

async function submit() {
  if (errors.value.length) return
  saving.value = true
  try {
    const payload = await call(
      'crm.api.invoices.record_payment',
      paymentArgs(props.invoice, form),
    )
    if (payload.thank_you?.sent) {
      toast.success(__('Payment recorded. Thank-you email sent.'))
    } else {
      toast.success(__('Payment recorded'))
    }
    show.value = false
    emit('recorded', payload)
  } catch (error) {
    toast.error(
      error?.messages?.[0] ||
        error?.message ||
        __('Could not record the payment'),
    )
  } finally {
    saving.value = false
  }
}
</script>
