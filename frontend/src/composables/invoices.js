/**
 * Whether the invoice module is switched on for this site.
 *
 * Read once and shared: the sidebar entry, the two routes, the deal's Create
 * invoice action and the dashboard tiles all ask the same question, and one
 * request answers it for all of them.
 *
 * It starts FALSE and stays false if the read fails. Acceptance criterion 10
 * asks that a switched-off module leave nothing visible anywhere, and a client
 * that guessed "probably on" while the server was unreachable would break it.
 * The endpoints refuse independently — `crm.api.invoices.require_module` is the
 * lock, this ref only decides what is worth showing.
 *
 * `frappe.client.get_single_value` is used rather than a new endpoint: every CRM
 * role already has read on FCRM Settings, and Stage 5.2's WorkflowRules panel
 * reads its own flag exactly this way.
 */

import { call, createResource } from 'frappe-ui'
import { computed, ref } from 'vue'

const enabled = ref(false)
const remindersEnabled = ref(false)

export const invoicesEnabled = computed(() => enabled.value)
export const invoiceRemindersEnabled = computed(() => remindersEnabled.value)

export const invoicesFlag = createResource({
  url: 'frappe.client.get_single_value',
  params: { doctype: 'FCRM Settings', field: 'invoices_enabled' },
  // Deliberately uncached. A manager who switches the module on wants the
  // sidebar entry on the next page load, not after a cache eviction.
  auto: true,
  onSuccess: (value) => {
    enabled.value = Boolean(value)
  },
  onError: () => {
    enabled.value = false
  },
})

export const invoiceRemindersFlag = createResource({
  url: 'frappe.client.get_single_value',
  params: { doctype: 'FCRM Settings', field: 'invoice_reminders_enabled' },
  auto: true,
  onSuccess: (value) => {
    remindersEnabled.value = Boolean(value)
  },
  onError: () => {
    remindersEnabled.value = false
  },
})

/**
 * Ask the server again, for a caller that cannot wait for the auto fetch.
 *
 * The route guard uses this on a direct URL load, where the flag may simply not
 * have arrived yet and bouncing a legitimate visit would be wrong.
 */
export async function refreshInvoicesFlag() {
  try {
    enabled.value = Boolean(
      await call('frappe.client.get_single_value', {
        doctype: 'FCRM Settings',
        field: 'invoices_enabled',
      }),
    )
  } catch {
    enabled.value = false
  }
  return enabled.value
}
