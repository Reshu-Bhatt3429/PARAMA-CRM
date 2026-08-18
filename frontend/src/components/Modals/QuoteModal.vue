<template>
  <Dialog v-model="show" :options="{ size: '3xl' }">
    <template #body>
      <div class="bg-surface-modal px-4 pb-6 pt-5 sm:px-6">
        <div class="mb-5 flex items-center justify-between">
          <div>
            <h3 class="text-2xl font-semibold text-ink-gray-9">
              {{ __('Create quote') }}
            </h3>
            <p v-if="preview.data?.meta" class="mt-1 text-p-sm text-ink-gray-5">
              {{ preview.data.meta.number }} ·
              {{ __('Valid until {0}', [preview.data.meta.valid_until]) }}
            </p>
          </div>
          <Button variant="ghost" icon="x" @click="show = false" />
        </div>

        <div v-if="preview.loading" class="flex justify-center py-10">
          <LoadingIndicator class="size-5" />
        </div>

        <div v-else-if="preview.data" class="flex flex-col gap-5">
          <!-- who it is for -->
          <div class="rounded border border-outline-gray-2 px-3 py-2 text-base">
            <span class="text-ink-gray-5">{{ __('Prepared for') }}: </span>
            <span class="text-ink-gray-8">
              {{
                preview.data.meta.customer_name ||
                preview.data.meta.organization ||
                __('This deal')
              }}
            </span>
          </div>

          <!-- what is quoted -->
          <div
            v-if="!preview.data.has_products"
            class="rounded border border-dashed border-outline-gray-3 px-3 py-6 text-center text-base text-ink-gray-5"
          >
            {{
              __(
                'This deal has no products yet. Add them in the Products section before you quote.',
              )
            }}
          </div>
          <div v-else class="overflow-x-auto">
            <table class="w-full text-base">
              <thead>
                <tr class="text-left text-sm text-ink-gray-5">
                  <th class="py-1.5 pr-3 font-normal">{{ __('Item') }}</th>
                  <th class="py-1.5 px-3 text-right font-normal">
                    {{ __('Rate') }}
                  </th>
                  <th class="py-1.5 px-3 text-right font-normal">
                    {{ __('Qty') }}
                  </th>
                  <th class="py-1.5 px-3 text-right font-normal">
                    {{ __('Discount') }}
                  </th>
                  <th class="py-1.5 pl-3 text-right font-normal">
                    {{ __('Net amount') }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(row, i) in preview.data.rows"
                  :key="i"
                  class="border-t border-outline-gray-1 text-ink-gray-8"
                >
                  <td class="py-1.5 pr-3">{{ row.name }}</td>
                  <td class="py-1.5 px-3 text-right">{{ row.rate }}</td>
                  <td class="py-1.5 px-3 text-right">{{ row.qty }}</td>
                  <td class="py-1.5 px-3 text-right">{{ row.discount }}</td>
                  <td class="py-1.5 pl-3 text-right">{{ row.net_amount }}</td>
                </tr>
              </tbody>
              <tfoot>
                <tr class="border-t border-outline-gray-2 text-ink-gray-6">
                  <td colspan="4" class="py-1.5 pr-3 text-right">
                    {{ __('Subtotal') }}
                  </td>
                  <td class="py-1.5 pl-3 text-right">
                    {{ preview.data.totals.total }}
                  </td>
                </tr>
                <tr v-if="preview.data.totals.discount" class="text-ink-gray-6">
                  <td colspan="4" class="py-1.5 pr-3 text-right">
                    {{ __('Discount') }}
                  </td>
                  <td class="py-1.5 pl-3 text-right">
                    − {{ preview.data.totals.discount }}
                  </td>
                </tr>
                <tr
                  class="border-t border-outline-gray-3 text-lg-medium text-ink-gray-9"
                >
                  <td colspan="4" class="py-2 pr-3 text-right">
                    {{ __('Total') }}
                  </td>
                  <td class="py-2 pl-3 text-right">
                    {{ preview.data.totals.net_total }}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>

          <!-- terms -->
          <div>
            <div class="mb-1.5 text-sm text-ink-gray-5">
              {{ __('Terms and conditions') }}
            </div>
            <FormControl
              v-model="terms"
              type="textarea"
              :rows="5"
              :placeholder="__('One condition per line')"
            />
            <p class="mt-1.5 text-p-sm text-ink-gray-5">
              {{
                __(
                  'One line becomes one bullet on the PDF. These terms belong to this quote and are not saved onto the deal.',
                )
              }}
            </p>
          </div>
        </div>
      </div>

      <div
        class="flex flex-col-reverse gap-2 px-4 pb-6 sm:flex-row sm:justify-end sm:px-6"
      >
        <Button
          :loading="busy === 'download'"
          :disabled="Boolean(busy) || !preview.data"
          :label="__('Download PDF')"
          @click="download"
        />
        <Button
          v-if="whatsappEnabled"
          variant="solid"
          :loading="busy === 'whatsapp'"
          :disabled="Boolean(busy) || !preview.data?.whatsapp_numbers?.length"
          :tooltip="
            preview.data?.whatsapp_numbers?.length
              ? ''
              : __('This deal has no WhatsApp number')
          "
          :label="__('Send on WhatsApp')"
          @click="sendOnWhatsApp"
        />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
/**
 * The quote preview (master spec item 25).
 *
 * Every figure on this screen comes from `crm.api.quote.get_quote_preview`,
 * which is the SAME code that fills the PDF. Recomputing the totals in the
 * browser would produce a modal that agrees with the agent and a document that
 * agrees with the server, and the customer would receive the second one.
 *
 * The terms are editable here and nowhere else: they belong to the quote, not
 * to the deal, so they are posted with the render and stored on the link rather
 * than written back onto the record.
 *
 * WhatsApp hands Meta a TOKENISED link, not a public file, and the 24-hour
 * service-window rule is Meta's; a refusal comes back as a message the agent
 * can act on rather than as an error.
 */
import { whatsappEnabled } from '@/composables/whatsapp'
import {
  Button,
  Dialog,
  FormControl,
  LoadingIndicator,
  call,
  createResource,
  toast,
} from 'frappe-ui'
import { ref, watch } from 'vue'

const props = defineProps({
  deal: { type: String, required: true },
})

const emit = defineEmits(['sent'])

const show = defineModel({ type: Boolean, default: false })

const terms = ref('')
const busy = ref('')

const preview = createResource({
  url: 'crm.api.quote.get_quote_preview',
  makeParams: () => ({ deal: props.deal }),
  onSuccess: (data) => {
    terms.value = data.terms || ''
  },
})

watch(
  show,
  (open) => {
    if (open) preview.reload()
  },
  { immediate: true },
)

async function download() {
  busy.value = 'download'
  try {
    const result = await call('crm.api.quote.download_quote', {
      deal: props.deal,
      terms: terms.value,
    })
    // The file is PRIVATE and the agent is logged in, so the ordinary file URL
    // is the right way to hand it over. Only the CUSTOMER gets a token.
    window.open(result.file_url, '_blank')
    emit('sent')
  } catch (error) {
    toast.error(error?.messages?.[0] || __('Could not build the quote'))
  } finally {
    busy.value = ''
  }
}

async function sendOnWhatsApp() {
  busy.value = 'whatsapp'
  try {
    const result = await call('crm.api.quote.send_quote_on_whatsapp', {
      deal: props.deal,
      terms: terms.value,
    })
    if (result.success) {
      toast.success(__('Quote sent to {0}', [result.to]))
      show.value = false
      emit('sent')
    } else {
      toast.error(result.error || __('Could not send the quote'))
    }
  } catch (error) {
    toast.error(error?.messages?.[0] || __('Could not send the quote'))
  } finally {
    busy.value = ''
  }
}
</script>
