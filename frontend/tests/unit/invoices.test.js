import {
  OPEN_STATUSES,
  PAYMENT_MODES,
  STATUS_DRAFT,
  STATUS_PAID,
  STATUS_PARTIALLY_PAID,
  STATUS_SENT,
  STATUS_VOID,
  dateString,
  formatMoney,
  invoiceActions,
  isIntraState,
  isOverdue,
  listRow,
  paymentArgs,
  paymentDefaults,
  paymentErrors,
  remainingAmount,
  scheduleReminderState,
  statusPill,
  totalsRows,
  unsettledScheduleRows,
} from '@/utils/invoices'

const TODAY = '2026-08-19'

function invoice(overrides = {}) {
  return {
    name: 'abc123',
    invoice_number: 'INV/25-26/0001',
    status: STATUS_SENT,
    due_date: '2026-08-25',
    currency: 'INR',
    intra_state: 1,
    is_locked: 1,
    reminders_enabled: true,
    reminders_paused: 0,
    totals: {
      taxable_total: 10000,
      cgst_amount: 250,
      sgst_amount: 250,
      igst_amount: 0,
      tax_total: 500,
      rounding_adjustment: 0,
      grand_total: 10500,
      paid_total: 0,
      outstanding_amount: 10500,
    },
    payment_schedule: [],
    ...overrides,
  }
}

describe('dateString', () => {
  it('trims a datetime to its date half', () => {
    expect(dateString('2026-08-19 14:30:00')).toBe('2026-08-19')
    expect(dateString('2026-08-19')).toBe('2026-08-19')
  })

  it('reads a Date in the local calendar, not in UTC', () => {
    // Built from local parts on purpose: `new Date('2026-08-19')` is midnight
    // UTC and would read back as the 18th west of Greenwich.
    const local = new Date(2026, 7, 19, 23, 30)
    expect(dateString(local)).toBe('2026-08-19')
  })

  it('gives an empty string for anything unusable', () => {
    expect(dateString(null)).toBe('')
    expect(dateString(undefined)).toBe('')
    expect(dateString('')).toBe('')
    expect(dateString(new Date('nonsense'))).toBe('')
    expect(dateString(42)).toBe('')
  })
})

describe('isOverdue', () => {
  it('is true for an open invoice whose due date has passed', () => {
    expect(isOverdue(invoice({ due_date: '2026-08-18' }), TODAY)).toBe(true)
    expect(
      isOverdue(
        invoice({ status: STATUS_PARTIALLY_PAID, due_date: '2026-01-01' }),
        TODAY,
      ),
    ).toBe(true)
  })

  it('gives the customer the whole due day', () => {
    expect(isOverdue(invoice({ due_date: TODAY }), TODAY)).toBe(false)
  })

  it('is false for every status that is not Sent or Partially Paid', () => {
    for (const status of [STATUS_DRAFT, STATUS_PAID, STATUS_VOID]) {
      expect(
        isOverdue(invoice({ status, due_date: '2020-01-01' }), TODAY),
      ).toBe(false)
    }
  })

  it('is false with no due date at all', () => {
    expect(isOverdue(invoice({ due_date: null }), TODAY)).toBe(false)
    expect(isOverdue(null, TODAY)).toBe(false)
  })

  it('only ever holds one of the five real statuses as OPEN', () => {
    expect(OPEN_STATUSES).toEqual([STATUS_SENT, STATUS_PARTIALLY_PAID])
  })
})

describe('statusPill', () => {
  it('gives each stored status its own tint', () => {
    expect(statusPill(invoice({ status: STATUS_DRAFT }), TODAY)).toMatchObject({
      label: 'Draft',
      theme: 'gray',
      isOverdue: false,
    })
    expect(statusPill(invoice(), TODAY)).toMatchObject({
      label: 'Sent',
      theme: 'blue',
    })
    expect(
      statusPill(invoice({ status: STATUS_PARTIALLY_PAID }), TODAY),
    ).toMatchObject({ label: 'Partially Paid', theme: 'orange' })
    expect(statusPill(invoice({ status: STATUS_PAID }), TODAY)).toMatchObject({
      label: 'Paid',
      theme: 'green',
    })
  })

  it('keeps red for Overdue alone, so Void cannot be mistaken for it', () => {
    const void_ = statusPill(invoice({ status: STATUS_VOID }), TODAY)
    expect(void_.theme).toBe('gray')
    expect(void_.variant).toBe('outline')
    expect(void_.isOverdue).toBe(false)
  })

  it('replaces the label and turns red when the due date has passed', () => {
    const pill = statusPill(invoice({ due_date: '2026-07-01' }), TODAY)
    expect(pill.label).toBe('Overdue')
    expect(pill.theme).toBe('red')
    expect(pill.isOverdue).toBe(true)
  })

  it('carries the real stored status even while it shows Overdue', () => {
    const pill = statusPill(
      invoice({ status: STATUS_PARTIALLY_PAID, due_date: '2026-07-01' }),
      TODAY,
    )
    expect(pill.status).toBe(STATUS_PARTIALLY_PAID)
    expect(pill.tooltip).toContain('Partially Paid')
    expect(pill.tooltip).toContain('2026-07-01')
  })
})

describe('formatMoney', () => {
  it('groups rupees the Indian way', () => {
    // A western grouping would read 123,456.00 and an Indian invoice does not.
    expect(formatMoney(123456, 'INR')).toBe('₹1,23,456.00')
  })

  it('always shows the paise', () => {
    expect(formatMoney(10500, 'INR')).toBe('₹10,500.00')
    expect(formatMoney(0, 'INR')).toBe('₹0.00')
  })

  it('defaults to INR, which is the only currency v1 issues', () => {
    expect(formatMoney(1)).toBe(formatMoney(1, 'INR'))
  })

  it('reads the numeric strings a JSON payload can carry', () => {
    expect(formatMoney('10500.5', 'INR')).toBe('₹10,500.50')
  })

  it('never throws on a value it cannot read', () => {
    expect(formatMoney(null, 'INR')).toBe('₹0.00')
    expect(formatMoney(undefined, 'INR')).toBe('₹0.00')
    expect(formatMoney('nonsense', 'INR')).toBe('₹0.00')
  })

  it('falls back rather than throwing on an unknown currency code', () => {
    expect(formatMoney(12, 'NOTACURRENCY')).toBe('NOTACURRENCY 12.00')
  })
})

describe('totalsRows', () => {
  it('shows CGST and SGST for an intra-state supply and no IGST', () => {
    const keys = totalsRows(invoice()).map((row) => row.key)
    expect(keys).toEqual([
      'taxable_total',
      'cgst_amount',
      'sgst_amount',
      'grand_total',
    ])
  })

  it('shows IGST alone for an inter-state supply', () => {
    const inter = invoice({
      intra_state: 0,
      totals: {
        taxable_total: 10000,
        cgst_amount: 0,
        sgst_amount: 0,
        igst_amount: 500,
        rounding_adjustment: 0,
        grand_total: 10500,
        outstanding_amount: 10500,
      },
    })
    const keys = totalsRows(inter).map((row) => row.key)
    expect(keys).toEqual(['taxable_total', 'igst_amount', 'grand_total'])
  })

  it('hides the rounding line when Section 170 changed nothing', () => {
    const keys = totalsRows(invoice()).map((row) => row.key)
    expect(keys).not.toContain('rounding_adjustment')
  })

  it('shows the rounding line in either direction', () => {
    const up = invoice({
      totals: { ...invoice().totals, rounding_adjustment: 0.4 },
    })
    const down = invoice({
      totals: { ...invoice().totals, rounding_adjustment: -0.35 },
    })
    expect(totalsRows(up).map((r) => r.key)).toContain('rounding_adjustment')
    expect(totalsRows(down).map((r) => r.key)).toContain('rounding_adjustment')
  })

  it('reads the amounts the server sent and derives nothing', () => {
    const rows = totalsRows(invoice())
    expect(rows.find((row) => row.key === 'cgst_amount').value).toBe(250)
    expect(rows.find((row) => row.key === 'grand_total').value).toBe(10500)
    expect(rows.find((row) => row.key === 'grand_total').strong).toBe(true)
  })

  it('falls back to the filled side when no intra_state flag arrived', () => {
    expect(isIntraState({ totals: { igst_amount: 500 } })).toBe(false)
    expect(isIntraState({ totals: { igst_amount: 0 } })).toBe(true)
  })
})

describe('invoiceActions', () => {
  it('lets a Draft be edited and issued and nothing else', () => {
    const actions = invoiceActions(
      invoice({ status: STATUS_DRAFT, is_locked: 0 }),
      { isManager: true },
    )
    expect(actions.canEdit).toBe(true)
    expect(actions.canIssue).toBe(true)
    expect(actions.canRecordPayment).toBe(false)
    expect(actions.canShare).toBe(false)
    expect(actions.canVoid).toBe(false)
    expect(actions.readOnly).toBe(false)
  })

  it('opens payment, send, share and void once the invoice is issued', () => {
    const actions = invoiceActions(invoice(), { isManager: true })
    expect(actions.canEdit).toBe(false)
    expect(actions.canIssue).toBe(false)
    expect(actions.canRecordPayment).toBe(true)
    expect(actions.canSend).toBe(true)
    expect(actions.canShare).toBe(true)
    expect(actions.canVoid).toBe(true)
    expect(actions.readOnly).toBe(true)
  })

  it('offers Void to a manager only', () => {
    expect(invoiceActions(invoice(), { isManager: false }).canVoid).toBe(false)
    expect(invoiceActions(invoice(), {}).canVoid).toBe(false)
  })

  it('leaves a Paid invoice read-only with the share link', () => {
    const actions = invoiceActions(invoice({ status: STATUS_PAID }), {
      isManager: true,
    })
    expect(actions.readOnly).toBe(true)
    expect(actions.canShare).toBe(true)
    expect(actions.canRecordPayment).toBe(false)
    expect(actions.canVoid).toBe(false)
  })

  it('leaves a Void invoice with no action that changes it', () => {
    const actions = invoiceActions(invoice({ status: STATUS_VOID }), {
      isManager: true,
    })
    expect(actions.isVoid).toBe(true)
    expect(actions.canEdit).toBe(false)
    expect(actions.canIssue).toBe(false)
    expect(actions.canRecordPayment).toBe(false)
    expect(actions.canSend).toBe(false)
    expect(actions.canShare).toBe(false)
    expect(actions.canVoid).toBe(false)
  })

  it('does not offer to edit a Draft that already carries a locked number', () => {
    const actions = invoiceActions(
      invoice({ status: STATUS_DRAFT, is_locked: 1 }),
      { isManager: true },
    )
    expect(actions.canEdit).toBe(false)
  })
})

describe('payment schedule', () => {
  const rows = [
    { name: 'r1', label: 'Deposit', due_date: '2026-08-01', settled: 1 },
    { name: 'r2', label: 'Balance', due_date: '2026-09-01', settled: 0 },
  ]

  it('offers only the rows that are not settled', () => {
    expect(
      unsettledScheduleRows(invoice({ payment_schedule: rows })).map(
        (row) => row.name,
      ),
    ).toEqual(['r2'])
  })

  it('calls a settled row settled whatever the flags say', () => {
    const state = scheduleReminderState(
      rows[0],
      invoice({ reminders_enabled: false }),
      TODAY,
    )
    expect(state.key).toBe('settled')
  })

  it('names the switch that is in the way', () => {
    expect(
      scheduleReminderState(
        rows[1],
        invoice({ reminders_enabled: false }),
        TODAY,
      ).key,
    ).toBe('disabled')
    expect(
      scheduleReminderState(
        rows[1],
        invoice({ reminders_enabled: true, reminders_paused: 1 }),
        TODAY,
      ).key,
    ).toBe('paused')
    expect(
      scheduleReminderState(
        { ...rows[1], reminders_paused: 1 },
        invoice({ reminders_enabled: true }),
        TODAY,
      ).key,
    ).toBe('paused')
  })

  it('says a live ladder is chasing once its own due date has passed', () => {
    expect(
      scheduleReminderState(
        { ...rows[1], due_date: '2026-08-01' },
        invoice(),
        TODAY,
      ).key,
    ).toBe('chasing')
    expect(scheduleReminderState(rows[1], invoice(), TODAY).key).toBe(
      'scheduled',
    )
  })
})

describe('paymentDefaults', () => {
  it('opens on the whole remaining balance', () => {
    expect(paymentDefaults(invoice()).amount).toBe(10500)
  })

  it('follows the balance down after a part payment', () => {
    const partly = invoice({
      status: STATUS_PARTIALLY_PAID,
      totals: {
        ...invoice().totals,
        paid_total: 4000,
        outstanding_amount: 6500,
      },
    })
    expect(remainingAmount(partly)).toBe(6500)
    expect(paymentDefaults(partly).amount).toBe(6500)
  })

  it('starts on UPI with nothing else filled in', () => {
    const form = paymentDefaults(invoice())
    expect(form.mode).toBe(PAYMENT_MODES[0])
    expect(form.mode).toBe('UPI')
    expect(form.reference).toBe('')
    expect(form.note).toBe('')
    expect(form.schedule_row).toBe('')
    expect(form.send_thank_you).toBe(false)
  })
})

describe('paymentErrors', () => {
  it('accepts the default form', () => {
    expect(paymentErrors(paymentDefaults(invoice()), invoice())).toEqual([])
  })

  it('refuses a zero or unreadable amount', () => {
    expect(paymentErrors({ amount: 0 }, invoice())).toHaveLength(1)
    expect(paymentErrors({ amount: '' }, invoice())).toHaveLength(1)
    expect(paymentErrors({}, invoice())).toHaveLength(1)
  })

  it('refuses more than the balance', () => {
    const errors = paymentErrors({ amount: 10501 }, invoice())
    expect(errors).toHaveLength(1)
    expect(errors[0]).toContain('outstanding')
  })

  it('accepts exactly the balance', () => {
    expect(paymentErrors({ amount: 10500 }, invoice())).toEqual([])
  })

  it('asks a negative correction for its note', () => {
    expect(paymentErrors({ amount: -500 }, invoice())).toHaveLength(1)
    expect(
      paymentErrors({ amount: -500, note: 'refunded' }, invoice()),
    ).toEqual([])
  })
})

describe('paymentArgs', () => {
  it('names every key exactly as the endpoint does', () => {
    const args = paymentArgs(invoice(), paymentDefaults(invoice()))
    expect(Object.keys(args).sort()).toEqual([
      'amount',
      'invoice',
      'mode',
      'note',
      'reference',
      'schedule_row',
      'send_thank_you',
    ])
  })

  it('sends the invoice name, not the number', () => {
    expect(paymentArgs(invoice(), paymentDefaults(invoice())).invoice).toBe(
      'abc123',
    )
  })

  it('sends an unlinked payment as null rather than an empty string', () => {
    expect(
      paymentArgs(invoice(), paymentDefaults(invoice())).schedule_row,
    ).toBe(null)
    expect(
      paymentArgs(invoice(), {
        ...paymentDefaults(invoice()),
        schedule_row: 'r2',
      }).schedule_row,
    ).toBe('r2')
  })

  it('sends the thank-you toggle as the 0/1 the endpoint reads', () => {
    expect(
      paymentArgs(invoice(), {
        ...paymentDefaults(invoice()),
        send_thank_you: true,
      }).send_thank_you,
    ).toBe(1)
    expect(
      paymentArgs(invoice(), paymentDefaults(invoice())).send_thank_you,
    ).toBe(0)
  })

  it('rounds the amount to paise', () => {
    expect(
      paymentArgs(invoice(), {
        ...paymentDefaults(invoice()),
        amount: '1000.005',
      }).amount,
    ).toBe(1000.01)
  })
})

describe('listRow', () => {
  const row = {
    name: 'abc123',
    invoice_number: 'INV/25-26/0001',
    customer_name: 'Sharma Travels',
    deal: 'deal-1',
    status: STATUS_SENT,
    invoice_date: '2026-08-01',
    due_date: '2026-08-10',
    grand_total: 10500,
    outstanding_amount: 6500,
  }

  it('maps a flat list row onto the columns the table renders', () => {
    const view = listRow(row, TODAY)
    expect(view.number).toBe('INV/25-26/0001')
    expect(view.customer).toBe('Sharma Travels')
    expect(view.deal).toBe('deal-1')
    expect(view.grandTotal).toBe(10500)
    expect(view.remaining).toBe(6500)
  })

  it('computes overdue from the row, without a stored overdue status', () => {
    expect(listRow(row, TODAY).pill.isOverdue).toBe(true)
    expect(
      listRow({ ...row, due_date: '2026-12-01' }, TODAY).pill.isOverdue,
    ).toBe(false)
  })

  it('says Draft where there is no number yet', () => {
    const view = listRow(
      { ...row, invoice_number: null, status: STATUS_DRAFT },
      TODAY,
    )
    expect(view.number).toBe('Draft')
    expect(view.hasNumber).toBe(false)
  })

  it('falls back to INR and an em dash rather than rendering undefined', () => {
    const view = listRow({ name: 'x' }, TODAY)
    expect(view.currency).toBe('INR')
    expect(view.customer).toBe('—')
  })
})
