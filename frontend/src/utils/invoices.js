/**
 * Pure view logic for the invoice module (master spec §5, item 29).
 *
 * It lives here, and not in the components, for the reason every other
 * Stage-2/3/5 helper does: this repo has no component tests, so the decisions
 * worth testing have to be reachable without mounting anything.
 *
 * Four rules from the design note are encoded here, and each one is a rule the
 * backend also holds:
 *
 * * **Overdue is COMPUTED, never stored.** `crm.invoicing.is_overdue` says an
 *   invoice is overdue when it is Sent or Partially Paid AND its due date has
 *   passed. The status column only ever holds one of the five real statuses, so
 *   this file derives the overdue tint rather than reading it.
 * * **No figure is recomputed here.** Every amount comes off
 *   `crm.api.invoices.read_invoice`, which reads what the controller wrote from
 *   the item rows. A browser that added its own totals would show the agent one
 *   number and the customer another.
 * * **The tax split follows `intra_state`.** CGST + SGST for an intra-state
 *   supply, IGST for an inter-state one — never both, because the backend only
 *   ever fills one side.
 * * **A payment defaults to the whole remaining balance.** It is the only
 *   default that cannot be wrong by a paisa; the backend applies the same one
 *   when the amount is omitted.
 */

export const STATUS_DRAFT = 'Draft'
export const STATUS_SENT = 'Sent'
export const STATUS_PARTIALLY_PAID = 'Partially Paid'
export const STATUS_PAID = 'Paid'
export const STATUS_VOID = 'Void'

/** The statuses that still owe money, and so can be overdue or take a payment. */
export const OPEN_STATUSES = [STATUS_SENT, STATUS_PARTIALLY_PAID]

export const PAYMENT_MODES = ['UPI', 'Bank', 'Cash', 'Other']

/**
 * How a date reads on the invoice screens.
 *
 * The same shape the print format uses (`d MMM yyyy`), so the screen and the PDF
 * a customer holds do not disagree about what a date looks like. The app's
 * default format carries a time, and a Date field has no time to carry.
 */
export const DATE_FORMAT = 'D MMM YYYY'
export const DATETIME_FORMAT = 'D MMM YYYY, h:mm a'

function toNumber(value) {
  const number = typeof value === 'number' ? value : parseFloat(value)
  return Number.isFinite(number) ? number : 0
}

function round2(value) {
  return Math.round((toNumber(value) + Number.EPSILON) * 100) / 100
}

/**
 * One date, as the `YYYY-MM-DD` string the server speaks.
 *
 * Comparison is done on the STRING and not on a `Date`. `new Date('2026-08-19')`
 * is midnight UTC, so east of Greenwich it reads back as the 19th and west of it
 * as the 18th — an invoice would fall overdue a day early or a day late
 * depending on where the agent sat.
 */
export function dateString(value) {
  if (!value) return ''
  if (typeof value === 'string') return value.slice(0, 10)
  if (value instanceof Date && !Number.isNaN(value.getTime())) {
    const month = String(value.getMonth() + 1).padStart(2, '0')
    const day = String(value.getDate()).padStart(2, '0')
    return `${value.getFullYear()}-${month}-${day}`
  }
  return ''
}

/** Today, in the reader's own calendar. */
export function todayString() {
  return dateString(new Date())
}

/**
 * Is this invoice overdue right now?
 *
 * Open status plus a due date STRICTLY in the past. An invoice due today is not
 * overdue today — the customer still has the day to pay.
 */
export function isOverdue(invoice, today) {
  if (!invoice) return false
  if (!OPEN_STATUSES.includes(invoice.status)) return false
  const due = dateString(invoice.due_date)
  if (!due) return false
  return due < (dateString(today) || todayString())
}

const STATUS_THEMES = {
  [STATUS_DRAFT]: { theme: 'gray', variant: 'subtle' },
  [STATUS_SENT]: { theme: 'blue', variant: 'subtle' },
  [STATUS_PARTIALLY_PAID]: { theme: 'orange', variant: 'subtle' },
  [STATUS_PAID]: { theme: 'green', variant: 'subtle' },
  // Outline rather than a filled red: red belongs to Overdue alone, so the two
  // terminal-looking states cannot be confused at a glance down the list.
  [STATUS_VOID]: { theme: 'gray', variant: 'outline' },
}

/**
 * The one pill a row or a header shows.
 *
 * Overdue REPLACES the label and owns the red tint, and the real status travels
 * in `status` and in the tooltip so nothing about the record is hidden. The
 * stored status is never written as "Overdue" anywhere.
 */
export function statusPill(invoice, today) {
  const status = invoice?.status || STATUS_DRAFT
  const overdue = isOverdue(invoice, today)

  if (overdue) {
    return {
      label: __('Overdue'),
      theme: 'red',
      variant: 'subtle',
      isOverdue: true,
      status,
      tooltip: __('{0} — due {1}', [__(status), dateString(invoice.due_date)]),
    }
  }

  const look = STATUS_THEMES[status] || STATUS_THEMES[STATUS_DRAFT]
  return {
    label: __(status),
    theme: look.theme,
    variant: look.variant,
    isOverdue: false,
    status,
    tooltip: '',
  }
}

const CURRENCY_LOCALE = { INR: 'en-IN' }

/**
 * An amount, in the currency the invoice carries.
 *
 * `Intl` is asked for the grouping, so an INR figure groups the Indian way
 * (1,23,456.00) rather than the western way. A currency code the runtime does
 * not know falls back to a plain grouped number with the code in front, which is
 * still readable, rather than throwing inside a render.
 */
export function formatMoney(value, currency = 'INR') {
  const amount = toNumber(value)
  const code = currency || 'INR'
  const locale = CURRENCY_LOCALE[code] || 'en-IN'
  try {
    return new Intl.NumberFormat(locale, {
      style: 'currency',
      currency: code,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(amount)
  } catch {
    return `${code} ${amount.toFixed(2)}`
  }
}

/** What is still owed, off the payload the server computed. */
export function remainingAmount(invoice) {
  return round2(invoice?.totals?.outstanding_amount)
}

/** True when the tax split is CGST + SGST rather than IGST. */
export function isIntraState(invoice) {
  if (invoice?.intra_state === undefined || invoice?.intra_state === null) {
    // No flag on the payload: the side the server filled decides.
    return toNumber(invoice?.totals?.igst_amount) === 0
  }
  return Boolean(toNumber(invoice.intra_state))
}

/**
 * The totals block, in the order the printed invoice shows it.
 *
 * Exactly the backend's fields and nothing derived: taxable value, one tax
 * split, the Section 170 rounding line when it is not zero, and the grand total.
 */
export function totalsRows(invoice) {
  const totals = invoice?.totals || {}
  const rows = [
    {
      key: 'taxable_total',
      label: __('Taxable value'),
      value: round2(totals.taxable_total),
    },
  ]

  if (isIntraState(invoice)) {
    rows.push({
      key: 'cgst_amount',
      label: __('CGST'),
      value: round2(totals.cgst_amount),
    })
    rows.push({
      key: 'sgst_amount',
      label: __('SGST'),
      value: round2(totals.sgst_amount),
    })
  } else {
    rows.push({
      key: 'igst_amount',
      label: __('IGST'),
      value: round2(totals.igst_amount),
    })
  }

  // A zero rounding line is noise. Section 170 only adjusts when the tax total
  // did not land on a whole rupee.
  if (round2(totals.rounding_adjustment) !== 0) {
    rows.push({
      key: 'rounding_adjustment',
      label: __('Rounding adjustment'),
      value: round2(totals.rounding_adjustment),
    })
  }

  rows.push({
    key: 'grand_total',
    label: __('Grand total'),
    value: round2(totals.grand_total),
    strong: true,
  })

  return rows
}

/**
 * Which header actions this state allows.
 *
 * The frontend half of the permission matrix. Every one of these is refused
 * again on the server, so hiding a button decides what is worth offering and
 * never what is allowed.
 */
export function invoiceActions(invoice, options = {}) {
  const status = invoice?.status || STATUS_DRAFT
  const isManager = Boolean(options.isManager)
  const isDraft = status === STATUS_DRAFT
  const isOpen = OPEN_STATUSES.includes(status)
  const isVoid = status === STATUS_VOID

  return {
    isDraft,
    isVoid,
    // A locked number means the document was issued; a Draft that somehow
    // carries one is not editable either.
    canEdit: isDraft && !invoice?.is_locked,
    canIssue: isDraft,
    canRecordPayment: isOpen,
    canSend: isOpen,
    // A draft has no number yet, so there is nothing honest to share.
    canShare: isOpen || status === STATUS_PAID,
    canVoid: isOpen && isManager,
    // The private PDF is the agent's own copy and reads "DRAFT" until issued.
    canDownload: !isVoid || isManager,
    readOnly: !isDraft,
  }
}

/** The schedule rows a payment may still be attached to. */
export function unsettledScheduleRows(invoice) {
  return (invoice?.payment_schedule || []).filter(
    (row) => !toNumber(row.settled),
  )
}

/**
 * What one schedule row's reminder ladder is doing.
 *
 * Three switches can silence a ladder and they are not the same thing, so the
 * row says which one is in the way rather than a bare "off".
 */
export function scheduleReminderState(row, invoice, today) {
  if (toNumber(row?.settled)) {
    return { key: 'settled', label: __('Settled'), theme: 'green' }
  }
  if (!invoice?.reminders_enabled) {
    return { key: 'disabled', label: __('Reminders off'), theme: 'gray' }
  }
  if (toNumber(invoice?.reminders_paused) || toNumber(row?.reminders_paused)) {
    return { key: 'paused', label: __('Paused'), theme: 'gray' }
  }
  const due = dateString(row?.due_date)
  if (due && due < (dateString(today) || todayString())) {
    return { key: 'chasing', label: __('Chasing'), theme: 'orange' }
  }
  return { key: 'scheduled', label: __('Scheduled'), theme: 'blue' }
}

/**
 * What the Record payment form opens on.
 *
 * The amount is the WHOLE remaining balance. Over-payment is refused by the
 * server, so a default that is too large would be refused, and a default that is
 * too small is the commonest data-entry error there is.
 */
export function paymentDefaults(invoice) {
  return {
    amount: remainingAmount(invoice),
    mode: PAYMENT_MODES[0],
    reference: '',
    note: '',
    schedule_row: '',
    send_thank_you: false,
  }
}

/**
 * Anything the form must fix before the server is asked.
 *
 * Deliberately thin. The server owns over-payment, the append-only rule and the
 * mandatory note on a correction; repeating those here would let the two drift.
 * What is checked is only what would make the request meaningless.
 */
export function paymentErrors(form, invoice) {
  const errors = []
  const amount = round2(form?.amount)
  if (!amount) {
    errors.push(__('Enter the amount that was paid.'))
  }
  if (amount < 0 && !String(form?.note || '').trim()) {
    // The backend refuses this too; saying it here saves a round trip on the
    // one path where the agent is correcting a mistake and is already annoyed.
    errors.push(__('A correction needs a note saying what it corrects.'))
  }
  if (amount > 0 && amount - remainingAmount(invoice) > 0.005) {
    errors.push(
      __('That is more than the {0} still outstanding.', [
        formatMoney(remainingAmount(invoice), invoice?.currency),
      ]),
    )
  }
  return errors
}

/**
 * The form, as `crm.api.invoices.record_payment` names its arguments.
 *
 * Named exactly after the whitelist signature so a rename on the server is one
 * grep away, and no key travels that the endpoint does not accept.
 */
export function paymentArgs(invoice, form) {
  return {
    invoice: invoice?.name,
    amount: round2(form?.amount),
    mode: form?.mode || PAYMENT_MODES[0],
    reference: String(form?.reference || ''),
    note: String(form?.note || ''),
    schedule_row: form?.schedule_row || null,
    send_thank_you: form?.send_thank_you ? 1 : 0,
  }
}

/**
 * One list row, ready to render.
 *
 * The list reads `CRM Invoice` directly through the permission-aware list
 * resource, so its rows are flat columns rather than the nested payload
 * `read_invoice` returns. This is where the two shapes are reconciled.
 */
export function listRow(row, today) {
  const invoice = {
    status: row?.status,
    due_date: row?.due_date,
    currency: row?.currency,
  }
  return {
    name: row?.name,
    number: row?.invoice_number || __('Draft'),
    hasNumber: Boolean(row?.invoice_number),
    customer: row?.customer_name || '—',
    deal: row?.deal || '',
    invoiceDate: dateString(row?.invoice_date),
    dueDate: dateString(row?.due_date),
    pill: statusPill(invoice, today),
    grandTotal: round2(row?.grand_total),
    remaining: round2(row?.outstanding_amount),
    currency: row?.currency || 'INR',
  }
}
