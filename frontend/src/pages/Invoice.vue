<template>
  <LayoutHeader>
    <template #left-header>
      <Breadcrumbs :items="breadcrumbs" />
    </template>
    <template #right-header>
      <Tooltip
        v-if="invoice"
        :text="statusPillView.tooltip"
        :disabled="!statusPillView.tooltip"
      >
        <Badge
          :theme="statusPillView.theme"
          :variant="statusPillView.variant"
          :label="statusPillView.label"
        />
      </Tooltip>

      <template v-if="invoice && editing">
        <Button :label="__('Cancel')" :disabled="busy" @click="cancelEdit" />
        <Button
          variant="solid"
          :label="__('Save')"
          :loading="working === 'save'"
          :disabled="busy"
          @click="saveDraft"
        />
      </template>

      <template v-else-if="invoice">
        <Button
          v-if="actions.canEdit"
          :label="__('Edit')"
          :disabled="busy"
          @click="startEdit"
        />
        <Button
          v-if="actions.canIssue"
          variant="solid"
          :label="__('Issue')"
          :loading="working === 'issue'"
          :disabled="busy"
          @click="issue"
        />
        <Button
          v-if="actions.canRecordPayment"
          variant="solid"
          :label="__('Record payment')"
          :disabled="busy"
          @click="showPaymentModal = true"
        />
        <Button
          v-if="actions.canSend"
          :label="__('Send')"
          :loading="working === 'email'"
          :disabled="busy"
          @click="sendEmail"
        />
        <Button
          v-if="actions.canShare"
          :label="__('Share link')"
          :loading="working === 'share'"
          :disabled="busy"
          @click="shareLink"
        />
        <Dropdown v-if="moreOptions.length" :options="moreOptions">
          <Button icon="lucide-more-horizontal" :disabled="busy" />
        </Dropdown>
      </template>
    </template>
  </LayoutHeader>

  <div v-if="loading" class="flex h-64 items-center justify-center">
    <LoadingIndicator class="h-5 w-5 text-ink-gray-5" />
  </div>

  <ErrorMessage v-else-if="loadError" class="m-5" :message="loadError" />

  <div v-else-if="invoice" class="flex-1 overflow-y-auto px-3 pb-16 sm:px-5">
    <!-- A voided invoice keeps its number, its amounts and its history. The
         banner says so at the top rather than leaving the reader to notice a
         grey pill. -->
    <div
      v-if="actions.isVoid"
      class="mt-4 rounded-lg border border-outline-red-1 bg-surface-red-1 px-4 py-3"
    >
      <div class="text-base font-medium text-ink-red-3">
        {{ __('This invoice is void.') }}
      </div>
      <div class="mt-0.5 text-p-sm text-ink-red-2">
        {{
          invoice.void_reason ||
          __('It is excluded from every revenue figure and takes no payment.')
        }}
      </div>
    </div>

    <!-- Rule 47: non-blocking. The invoice was still issued; the agent is told
         so they can answer for it, not stopped. -->
    <div
      v-if="invoice.rule_47_warning"
      class="mt-4 flex items-start gap-2 rounded-lg border border-outline-amber-1 bg-surface-amber-1 px-4 py-2.5 text-base text-ink-amber-3"
    >
      <span
        class="lucide-triangle-alert mt-0.5 size-4 shrink-0"
        aria-hidden="true"
      />
      <span>{{ invoice.rule_47_warning }}</span>
    </div>

    <ErrorMessage v-if="actionError" class="mt-4" :message="actionError" />

    <div
      v-if="sendHint"
      class="mt-4 rounded-lg border border-outline-amber-1 bg-surface-amber-1 px-4 py-2.5 text-base text-ink-amber-3"
    >
      {{ sendHint }}
    </div>

    <!-- Minting a new link retires the last one, so the live URL is shown
         rather than left in the clipboard alone. -->
    <div
      v-if="sharedLink"
      class="mt-4 flex flex-wrap items-center gap-2 rounded-lg border border-outline-gray-2 bg-surface-gray-1 px-4 py-2.5 text-base"
    >
      <span class="text-ink-gray-5">{{ __('Customer link') }}</span>
      <span class="min-w-0 flex-1 truncate text-ink-gray-8">
        {{ sharedLink.link_url }}
      </span>
      <span class="text-p-sm text-ink-gray-5">
        {{
          __('Expires {0}', [
            formatDate(sharedLink.expires_at, DATETIME_FORMAT),
          ])
        }}
      </span>
      <Button
        :label="__('Copy')"
        @click="copyToClipboard(sharedLink.link_url)"
      />
    </div>

    <!-- invoice facts and recipient -->
    <section class="mt-5 grid grid-cols-1 gap-6 lg:grid-cols-2">
      <div>
        <h2 class="text-lg font-semibold text-ink-gray-8">
          {{ __('Invoice') }}
        </h2>
        <div v-if="!editing" class="mt-3 flex flex-col gap-1.5 text-base">
          <InvoiceFact :label="__('Number')">
            {{ invoice.invoice_number || __('Draft — not yet issued') }}
          </InvoiceFact>
          <InvoiceFact :label="__('Invoice date')">
            {{
              invoice.invoice_date
                ? formatDate(invoice.invoice_date, DATE_FORMAT)
                : '—'
            }}
          </InvoiceFact>
          <InvoiceFact :label="__('Due date')">
            {{
              invoice.due_date ? formatDate(invoice.due_date, DATE_FORMAT) : '—'
            }}
          </InvoiceFact>
          <InvoiceFact :label="__('Service date')">
            {{
              invoice.service_date
                ? formatDate(invoice.service_date, DATE_FORMAT)
                : '—'
            }}
          </InvoiceFact>
          <InvoiceFact :label="__('Mode')">{{ __(invoice.mode) }}</InvoiceFact>
          <InvoiceFact :label="__('Reverse charge')">
            {{ invoice.reverse_charge ? __('Yes') : __('No') }}
          </InvoiceFact>
          <InvoiceFact :label="__('Place of supply')">
            {{ invoice.place_of_supply || '—' }}
          </InvoiceFact>
          <InvoiceFact :label="__('Deal')">
            <router-link
              v-if="invoice.deal"
              class="text-ink-blue-2 hover:underline"
              :to="{ name: 'Deal', params: { dealId: invoice.deal } }"
            >
              {{ invoice.deal }}
            </router-link>
            <span v-else>—</span>
          </InvoiceFact>
        </div>
        <div v-else class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <FormControl
            v-model="form.invoice_date"
            type="date"
            :label="__('Invoice date')"
          />
          <FormControl
            v-model="form.due_date"
            type="date"
            :label="__('Due date')"
          />
          <FormControl
            v-model="form.service_date"
            type="date"
            :label="__('Service date')"
            :description="
              __('When the travel happens. Rule 47 counts from it.')
            "
          />
          <FormControl
            v-model="form.mode"
            type="select"
            :label="__('Mode')"
            :options="modeOptions"
            :description="
              __(
                'Tour package bills 5% on the gross and prints the prescribed statement.',
              )
            "
          />
          <FormControl
            v-model="form.place_of_supply"
            type="text"
            :label="__('Place of supply (state code)')"
            :placeholder="__('27')"
          />
          <FormControl
            v-model="form.reverse_charge"
            type="checkbox"
            :label="__('Reverse charge')"
          />
        </div>
      </div>

      <div>
        <h2 class="text-lg font-semibold text-ink-gray-8">
          {{ __('Recipient') }}
        </h2>
        <!-- The conversion copies the address off the deal's ORGANISATION only
             (Stage 5.3a §Deviations 6). Where there is none, this is where the
             agent types it, and Draft is the state that allows it. -->
        <div v-if="!editing" class="mt-3 flex flex-col gap-1.5 text-base">
          <InvoiceFact :label="__('Name')">
            {{ invoice.customer.name || '—' }}
          </InvoiceFact>
          <InvoiceFact :label="__('Address')">
            <span class="whitespace-pre-line">
              {{ invoice.customer.address || '—' }}
            </span>
          </InvoiceFact>
          <InvoiceFact :label="__('State')">
            {{ invoice.customer.state || '—' }}
            <span v-if="invoice.customer.state_code" class="text-ink-gray-5">
              ({{ invoice.customer.state_code }})
            </span>
          </InvoiceFact>
          <InvoiceFact :label="__('GSTIN')">
            {{ invoice.customer.gstin || '—' }}
          </InvoiceFact>
          <InvoiceFact :label="__('Email')">
            {{ invoice.customer.email || '—' }}
          </InvoiceFact>
          <InvoiceFact :label="__('Phone')">
            {{ invoice.customer.phone || '—' }}
          </InvoiceFact>
        </div>
        <div v-else class="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <FormControl
            v-model="form.customer_name"
            type="text"
            :label="__('Name')"
            class="sm:col-span-2"
          />
          <FormControl
            v-model="form.customer_address"
            type="textarea"
            :rows="3"
            class="sm:col-span-2"
            :label="__('Address')"
            :description="
              __(
                'Required above ₹50,000 when the customer has no GSTIN. Issue refuses without it.',
              )
            "
          />
          <FormControl
            v-model="form.customer_state"
            type="text"
            :label="__('State')"
          />
          <FormControl
            v-model="form.customer_state_code"
            type="text"
            :label="__('State code')"
            :placeholder="__('27')"
          />
          <FormControl
            v-model="form.customer_gstin"
            type="text"
            :label="__('GSTIN')"
          />
          <FormControl
            v-model="form.customer_email"
            type="text"
            :label="__('Email')"
          />
          <FormControl
            v-model="form.customer_phone"
            type="text"
            :label="__('Phone')"
          />
        </div>
      </div>
    </section>

    <!-- the prescribed tour-package statement -->
    <div
      v-if="invoice.tour_package_statement"
      class="mt-6 rounded-lg border border-outline-gray-2 bg-surface-gray-1 px-4 py-3"
    >
      <div class="text-xs uppercase tracking-wide text-ink-gray-4">
        {{ __('Printed on this invoice') }}
      </div>
      <p class="mt-1 text-base text-ink-gray-7">
        {{ invoice.tour_package_statement }}
      </p>
    </div>

    <!-- items -->
    <section class="mt-8">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold text-ink-gray-8">{{ __('Items') }}</h2>
        <Button
          v-if="editing"
          :label="__('Add line')"
          iconLeft="plus"
          :disabled="busy"
          @click="addItem"
        />
      </div>

      <div class="mt-3 overflow-x-auto">
        <table class="w-full text-base">
          <thead>
            <tr class="border-b text-left text-sm text-ink-gray-5">
              <th class="py-2 pr-3 font-normal">{{ __('Description') }}</th>
              <th class="w-40 py-2 px-3 font-normal">{{ __('SAC') }}</th>
              <th class="w-24 py-2 px-3 text-right font-normal">
                {{ __('Qty') }}
              </th>
              <th class="w-32 py-2 px-3 text-right font-normal">
                {{ __('Rate') }}
              </th>
              <th class="w-24 py-2 px-3 text-right font-normal">
                {{ __('Tax %') }}
              </th>
              <th class="w-32 py-2 pl-3 text-right font-normal">
                {{ __('Amount') }}
              </th>
              <th v-if="editing" class="w-10" />
            </tr>
          </thead>
          <tbody v-if="!editing">
            <tr
              v-for="row in invoice.items"
              :key="row.name"
              class="border-b text-ink-gray-8"
            >
              <td class="py-2.5 pr-3">{{ row.description }}</td>
              <td class="py-2.5 px-3 text-ink-gray-6">{{ row.sac || '—' }}</td>
              <td class="py-2.5 px-3 text-right">{{ row.qty }}</td>
              <td class="py-2.5 px-3 text-right">
                {{ formatMoney(row.rate, invoice.currency) }}
              </td>
              <td class="py-2.5 px-3 text-right text-ink-gray-6">
                {{ row.tax_rate }}%
              </td>
              <td class="py-2.5 pl-3 text-right">
                {{ formatMoney(row.amount, invoice.currency) }}
              </td>
            </tr>
            <tr v-if="!invoice.items.length">
              <td colspan="6" class="py-6 text-center text-ink-gray-5">
                {{ __('This invoice has no lines yet.') }}
              </td>
            </tr>
          </tbody>
          <tbody v-else>
            <tr
              v-for="(row, index) in form.items"
              :key="index"
              class="border-b"
            >
              <td class="py-2 pr-3">
                <TextInput
                  v-model="row.description"
                  class="w-full"
                  type="text"
                  :placeholder="__('What is billed')"
                />
              </td>
              <td class="py-2 px-3">
                <FormControl
                  v-model="row.sac"
                  type="select"
                  :options="sacOptions"
                  @update:modelValue="applySac(row)"
                />
              </td>
              <td class="py-2 px-3">
                <TextInput
                  v-model="row.qty"
                  class="w-full text-right"
                  type="number"
                  min="0"
                  step="0.01"
                />
              </td>
              <td class="py-2 px-3">
                <TextInput
                  v-model="row.rate"
                  class="w-full text-right"
                  type="number"
                  min="0"
                  step="0.01"
                />
              </td>
              <td class="py-2 px-3">
                <TextInput
                  v-model="row.tax_rate"
                  class="w-full text-right"
                  type="number"
                  min="0"
                  step="0.01"
                  :disabled="form.mode === 'Tour Package'"
                />
              </td>
              <td class="py-2 pl-3 text-right text-ink-gray-5">
                {{ formatMoney(lineAmount(row), invoice.currency) }}
              </td>
              <td class="py-2 pl-2">
                <Button
                  icon="trash-2"
                  :tooltip="__('Remove line')"
                  @click="removeItem(index)"
                />
              </td>
            </tr>
            <tr v-if="!form.items.length">
              <td colspan="7" class="py-6 text-center text-ink-gray-5">
                {{ __('Add at least one line before you issue this invoice.') }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <p v-if="editing" class="mt-2 text-p-sm text-ink-gray-5">
        {{
          __(
            'Every figure is recomputed on the server when you save. The amounts above are a preview.',
          )
        }}
      </p>
    </section>

    <!-- totals -->
    <section class="mt-8 flex justify-end">
      <div class="w-full max-w-sm">
        <div
          v-for="row in totals"
          :key="row.key"
          class="flex items-center justify-between border-b border-outline-gray-1 py-2 text-base"
          :class="
            row.strong ? 'text-lg-medium text-ink-gray-9' : 'text-ink-gray-7'
          "
        >
          <span>{{ row.label }}</span>
          <span>{{ formatMoney(row.value, invoice.currency) }}</span>
        </div>
        <div
          class="flex items-center justify-between py-2 text-base text-ink-gray-7"
        >
          <span>{{ __('Paid') }}</span>
          <span>{{
            formatMoney(invoice.totals.paid_total, invoice.currency)
          }}</span>
        </div>
        <div
          class="flex items-center justify-between border-t border-outline-gray-2 py-2 text-lg-medium"
          :class="
            statusPillView.isOverdue ? 'text-ink-red-3' : 'text-ink-gray-9'
          "
        >
          <span>{{ __('Outstanding') }}</span>
          <span>
            {{
              formatMoney(invoice.totals.outstanding_amount, invoice.currency)
            }}
          </span>
        </div>
      </div>
    </section>

    <!-- payment schedule -->
    <section class="mt-8">
      <div class="flex items-center justify-between">
        <h2 class="text-lg font-semibold text-ink-gray-8">
          {{ __('Payment schedule') }}
        </h2>
        <div class="flex items-center gap-3">
          <div
            v-if="invoice.reminders_enabled && !actions.isVoid"
            class="flex items-center gap-2"
          >
            <span class="text-p-sm text-ink-gray-6">{{
              __('Pause reminders')
            }}</span>
            <Switch
              size="sm"
              :model-value="Boolean(invoice.reminders_paused)"
              :disabled="busy"
              @update:model-value="togglePause"
            />
          </div>
          <Button
            v-if="editing"
            :label="__('Add instalment')"
            iconLeft="plus"
            @click="addScheduleRow"
          />
        </div>
      </div>

      <table
        v-if="!editing && invoice.payment_schedule.length"
        class="mt-3 w-full text-base"
      >
        <thead>
          <tr class="border-b text-left text-sm text-ink-gray-5">
            <th class="py-2 pr-3 font-normal">{{ __('Instalment') }}</th>
            <th class="py-2 px-3 font-normal">{{ __('Due') }}</th>
            <th class="py-2 px-3 text-right font-normal">{{ __('Amount') }}</th>
            <th class="py-2 pl-3 font-normal">{{ __('Reminders') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in invoice.payment_schedule"
            :key="row.name"
            class="border-b"
          >
            <td class="py-2.5 pr-3 text-ink-gray-8">{{ row.label }}</td>
            <td class="py-2.5 px-3 text-ink-gray-7">
              {{ row.due_date ? formatDate(row.due_date, DATE_FORMAT) : '—' }}
            </td>
            <td class="py-2.5 px-3 text-right text-ink-gray-7">
              {{ formatMoney(row.amount, invoice.currency) }}
            </td>
            <td class="py-2.5 pl-3">
              <Badge
                variant="subtle"
                :theme="reminderState(row).theme"
                :label="reminderState(row).label"
              />
            </td>
          </tr>
        </tbody>
      </table>

      <div v-else-if="editing" class="mt-3 flex flex-col gap-2">
        <div
          v-for="(row, index) in form.payment_schedule"
          :key="index"
          class="flex flex-wrap items-center gap-2"
        >
          <TextInput
            v-model="row.label"
            class="w-48"
            type="text"
            :placeholder="__('Deposit')"
          />
          <TextInput v-model="row.due_date" class="w-44" type="date" />
          <TextInput
            v-model="row.amount"
            class="w-40"
            type="number"
            min="0"
            step="0.01"
            :placeholder="__('Amount')"
          />
          <Button
            icon="trash-2"
            :tooltip="__('Remove instalment')"
            @click="removeScheduleRow(index)"
          />
        </div>
        <p v-if="!form.payment_schedule.length" class="text-sm text-ink-gray-4">
          {{ __('No instalments. The whole amount is due on the due date.') }}
        </p>
      </div>

      <p v-else class="mt-3 text-sm text-ink-gray-4">
        {{ __('No instalments. The whole amount is due on the due date.') }}
      </p>
    </section>

    <!-- payments: append-only, so there is nothing here to click -->
    <section class="mt-8">
      <h2 class="text-lg font-semibold text-ink-gray-8">
        {{ __('Payments') }}
      </h2>
      <table v-if="invoice.payments.length" class="mt-3 w-full text-base">
        <thead>
          <tr class="border-b text-left text-sm text-ink-gray-5">
            <th class="py-2 pr-3 font-normal">{{ __('Date') }}</th>
            <th class="py-2 px-3 text-right font-normal">{{ __('Amount') }}</th>
            <th class="py-2 px-3 font-normal">{{ __('Mode') }}</th>
            <th class="py-2 px-3 font-normal">{{ __('Reference') }}</th>
            <th class="py-2 pl-3 font-normal">{{ __('Recorded by') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in invoice.payments" :key="row.name" class="border-b">
            <td class="py-2.5 pr-3 text-ink-gray-7">
              {{
                row.payment_date
                  ? formatDate(row.payment_date, DATE_FORMAT)
                  : '—'
              }}
            </td>
            <td
              class="py-2.5 px-3 text-right"
              :class="row.amount < 0 ? 'text-ink-red-3' : 'text-ink-gray-8'"
            >
              {{ formatMoney(row.amount, invoice.currency) }}
            </td>
            <td class="py-2.5 px-3 text-ink-gray-7">{{ row.mode }}</td>
            <td class="py-2.5 px-3 text-ink-gray-6">
              {{ row.reference || '—' }}
              <span v-if="row.note" class="block text-p-sm text-ink-gray-5">
                {{ row.note }}
              </span>
            </td>
            <td class="py-2.5 pl-3 text-ink-gray-5">{{ row.recorded_by }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="mt-3 text-sm text-ink-gray-4">
        {{ __('Nothing has been paid yet.') }}
      </p>
      <p v-if="invoice.payments.length" class="mt-2 text-p-sm text-ink-gray-5">
        {{
          __(
            'Payments are never edited or removed. A mistake is corrected by a negative amount with a note.',
          )
        }}
      </p>
    </section>

    <!-- status history -->
    <section class="mt-8">
      <h2 class="text-lg font-semibold text-ink-gray-8">{{ __('History') }}</h2>
      <table v-if="invoice.status_log.length" class="mt-3 w-full text-base">
        <thead>
          <tr class="border-b text-left text-sm text-ink-gray-5">
            <th class="py-2 pr-3 font-normal">{{ __('When') }}</th>
            <th class="py-2 px-3 font-normal">{{ __('Change') }}</th>
            <th class="py-2 px-3 font-normal">{{ __('By') }}</th>
            <th class="py-2 pl-3 font-normal">{{ __('Note') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(row, index) in invoice.status_log"
            :key="index"
            class="border-b"
          >
            <td class="whitespace-nowrap py-2.5 pr-3 text-ink-gray-7">
              {{
                row.changed_at
                  ? formatDate(row.changed_at, DATETIME_FORMAT)
                  : '—'
              }}
            </td>
            <td class="py-2.5 px-3 text-ink-gray-8">
              {{ row.from_status || '—' }} → {{ row.to_status }}
            </td>
            <td class="py-2.5 px-3 text-ink-gray-5">{{ row.changed_by }}</td>
            <td class="py-2.5 pl-3 text-ink-gray-6">{{ row.note || '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="mt-3 text-sm text-ink-gray-4">
        {{ __('Nothing has happened to this invoice yet.') }}
      </p>
    </section>

    <!-- terms -->
    <section class="mt-8">
      <h2 class="text-lg font-semibold text-ink-gray-8">{{ __('Terms') }}</h2>
      <FormControl
        v-if="editing"
        v-model="form.terms"
        class="mt-3"
        type="textarea"
        :rows="4"
        :description="__('One line becomes one bullet on the PDF.')"
      />
      <p v-else class="mt-3 whitespace-pre-line text-base text-ink-gray-7">
        {{ invoice.terms || __('No terms on this invoice.') }}
      </p>
    </section>
  </div>

  <RecordPaymentModal
    v-if="invoice && showPaymentModal"
    v-model="showPaymentModal"
    :invoice="invoice"
    @recorded="apply"
  />

  <Dialog v-model="showVoidModal" :options="{ size: 'md' }">
    <template #body>
      <div class="bg-surface-modal px-4 pb-6 pt-5 sm:px-6">
        <h3 class="text-2xl font-semibold text-ink-gray-9">
          {{ __('Void this invoice') }}
        </h3>
        <p class="mt-1 text-p-sm text-ink-gray-5">
          {{
            __(
              'The number, the amounts and the history stay. It is excluded from every revenue figure and can never be issued again.',
            )
          }}
        </p>
        <FormControl
          v-model="voidReason"
          class="mt-4"
          type="textarea"
          :rows="3"
          :label="__('Reason')"
          :placeholder="__('Why this invoice is being cancelled')"
        />
      </div>
      <div
        class="flex flex-col-reverse gap-2 px-4 pb-6 sm:flex-row sm:justify-end sm:px-6"
      >
        <Button :label="__('Cancel')" @click="showVoidModal = false" />
        <Button
          variant="solid"
          theme="red"
          :label="__('Void invoice')"
          :loading="working === 'void'"
          :disabled="!voidReason.trim() || busy"
          @click="voidInvoice"
        />
      </div>
    </template>
  </Dialog>
</template>

<script setup>
/**
 * One invoice (master spec §5, item 29).
 *
 * Every figure on this page comes from `crm.api.invoices.get_invoice`, which is
 * the same payload the print format reads. Nothing is added up in the browser:
 * a totals block that agreed with the agent while the PDF agreed with the server
 * would send the customer the second one.
 *
 * The state decides the actions, and `@/utils/invoices.invoiceActions` is where
 * that mapping lives so it can be tested without mounting anything. Every one of
 * those actions is refused again on the server — hiding a button decides what is
 * worth offering, never what is allowed.
 *
 * A Draft is fully editable, including the recipient block. That is not a
 * convenience: `customer_snapshot` reads an address only through the deal's
 * organisation (Stage 5.3a §Deviations 6), so a deal with no linked Address
 * produces an invoice whose address block is empty, and this editor is where the
 * agent fills it in before pressing Issue.
 *
 * The Draft is saved through `frappe.client.set_value`, which runs the ordinary
 * document save and therefore every refusal the controller owns — the locked
 * fields, the append-only payments and the recompute from the item rows.
 */
import { h } from 'vue'
import LucideDownload from '~icons/lucide/download'
import LucideBan from '~icons/lucide/ban'
import LayoutHeader from '@/components/LayoutHeader.vue'
import RecordPaymentModal from '@/components/Modals/RecordPaymentModal.vue'
import InvoiceFact from '@/components/Invoice/InvoiceFact.vue'
import WhatsAppIcon from '@/components/Icons/WhatsAppIcon.vue'
import { copyToClipboard, formatDate } from '@/utils'
import {
  DATETIME_FORMAT,
  DATE_FORMAT,
  formatMoney,
  invoiceActions,
  scheduleReminderState,
  statusPill,
  todayString,
  totalsRows,
} from '@/utils/invoices'
import { usersStore } from '@/stores/users'
import { whatsappEnabled } from '@/composables/whatsapp'
import {
  Badge,
  Breadcrumbs,
  Dialog,
  Dropdown,
  FormControl,
  LoadingIndicator,
  Switch,
  Tooltip,
  call,
  createListResource,
  toast,
} from 'frappe-ui'
import { computed, reactive, ref } from 'vue'

const props = defineProps({
  invoiceId: { type: String, required: true },
})

const { isManager } = usersStore()

const invoice = ref(null)
const loading = ref(true)
const loadError = ref('')
const actionError = ref('')
const sendHint = ref('')
const working = ref('')
const editing = ref(false)
const showPaymentModal = ref(false)
const showVoidModal = ref(false)
const voidReason = ref('')
// The last link this page minted. Kept on screen rather than only in the
// clipboard: minting a new one retires the old, so the agent has to be able to
// see which URL is the live one.
const sharedLink = ref(null)

const form = reactive({
  invoice_date: '',
  due_date: '',
  service_date: '',
  mode: 'Commission',
  reverse_charge: false,
  place_of_supply: '',
  customer_name: '',
  customer_address: '',
  customer_state: '',
  customer_state_code: '',
  customer_gstin: '',
  customer_email: '',
  customer_phone: '',
  terms: '',
  items: [],
  payment_schedule: [],
})

const busy = computed(() => Boolean(working.value))

const sacCodes = createListResource({
  doctype: 'CRM SAC Code',
  fields: ['name', 'code', 'description', 'tax_rate'],
  filters: { enabled: 1 },
  pageLength: 100,
  auto: true,
})

const sacOptions = computed(() => [
  { label: __('No SAC'), value: '' },
  ...(sacCodes.data || []).map((row) => ({
    label: row.description ? `${row.code} — ${row.description}` : row.code,
    value: row.name,
  })),
])

const modeOptions = [
  { label: __('Commission'), value: 'Commission' },
  { label: __('Tour Package'), value: 'Tour Package' },
]

const statusPillView = computed(() => statusPill(invoice.value, todayString()))

const actions = computed(() =>
  invoiceActions(invoice.value, { isManager: isManager() }),
)

const totals = computed(() => totalsRows(invoice.value))

const breadcrumbs = computed(() => [
  { label: __('Invoices'), route: { name: 'Invoices' } },
  {
    label: invoice.value?.invoice_number || __('Draft'),
    route: { name: 'Invoice', params: { invoiceId: props.invoiceId } },
  },
])

// Secondary actions live behind one button so the header stays a header and not
// a shelf (UX §2.13).
const moreOptions = computed(() => {
  const options = []
  if (actions.value.canDownload) {
    options.push({
      label: __('Download PDF'),
      icon: () => h(LucideDownload, { class: 'size-4' }),
      onClick: downloadPdf,
    })
  }
  if (actions.value.canSend && whatsappEnabled.value) {
    options.push({
      label: __('Send on WhatsApp'),
      icon: () => h(WhatsAppIcon, { class: 'size-4' }),
      onClick: sendOnWhatsApp,
    })
  }
  if (actions.value.canVoid) {
    options.push({
      label: __('Void invoice'),
      icon: () => h(LucideBan, { class: 'size-4' }),
      onClick: () => {
        voidReason.value = ''
        showVoidModal.value = true
      },
    })
  }
  return options
})

function reminderState(row) {
  return scheduleReminderState(row, invoice.value, todayString())
}

function lineAmount(row) {
  const qty = parseFloat(row.qty)
  const rate = parseFloat(row.rate)
  if (!Number.isFinite(qty) || !Number.isFinite(rate)) return 0
  return Math.round((qty * rate + Number.EPSILON) * 100) / 100
}

function messageOf(error, fallback) {
  return error?.messages?.[0] || error?.message || fallback
}

function apply(payload) {
  invoice.value = payload
  editing.value = false
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    apply(
      await call('crm.api.invoices.get_invoice', { invoice: props.invoiceId }),
    )
  } catch (error) {
    loadError.value = messageOf(error, __('Could not open this invoice.'))
  } finally {
    loading.value = false
  }
}

// --- the draft editor -----------------------------------------------------

function startEdit() {
  const doc = invoice.value
  Object.assign(form, {
    invoice_date: doc.invoice_date || '',
    due_date: doc.due_date || '',
    service_date: doc.service_date || '',
    mode: doc.mode || 'Commission',
    reverse_charge: Boolean(doc.reverse_charge),
    place_of_supply: doc.place_of_supply || '',
    customer_name: doc.customer.name || '',
    customer_address: doc.customer.address || '',
    customer_state: doc.customer.state || '',
    customer_state_code: doc.customer.state_code || '',
    customer_gstin: doc.customer.gstin || '',
    customer_email: doc.customer.email || '',
    customer_phone: doc.customer.phone || '',
    terms: doc.terms || '',
    // The child-row `name` travels so an edited row is updated in place rather
    // than deleted and recreated with a new id.
    items: doc.items.map((row) => ({
      name: row.name,
      description: row.description,
      sac: row.sac || '',
      qty: row.qty,
      rate: row.rate,
      tax_rate: row.tax_rate,
    })),
    payment_schedule: doc.payment_schedule.map((row) => ({
      name: row.name,
      label: row.label,
      due_date: row.due_date,
      amount: row.amount,
    })),
  })
  actionError.value = ''
  editing.value = true
}

function cancelEdit() {
  editing.value = false
  actionError.value = ''
}

function addItem() {
  form.items.push({ description: '', sac: '', qty: 1, rate: 0, tax_rate: 18 })
}

function removeItem(index) {
  form.items.splice(index, 1)
}

function addScheduleRow() {
  form.payment_schedule.push({
    label: '',
    due_date: form.due_date || '',
    amount: 0,
  })
}

function removeScheduleRow(index) {
  form.payment_schedule.splice(index, 1)
}

// The SAC master carries the rate. Picking a code fills it in so the agent does
// not have to remember which band the code sits in.
function applySac(row) {
  const sac = (sacCodes.data || []).find((entry) => entry.name === row.sac)
  if (sac && sac.tax_rate !== undefined && sac.tax_rate !== null) {
    row.tax_rate = sac.tax_rate
  }
}

async function saveDraft() {
  actionError.value = ''
  working.value = 'save'
  try {
    await call('frappe.client.set_value', {
      doctype: 'CRM Invoice',
      name: props.invoiceId,
      fieldname: {
        invoice_date: form.invoice_date || null,
        due_date: form.due_date || null,
        service_date: form.service_date || null,
        mode: form.mode,
        reverse_charge: form.reverse_charge ? 1 : 0,
        place_of_supply: form.place_of_supply,
        customer_name: form.customer_name,
        customer_address: form.customer_address,
        customer_state: form.customer_state,
        customer_state_code: form.customer_state_code,
        customer_gstin: form.customer_gstin,
        customer_email: form.customer_email,
        customer_phone: form.customer_phone,
        terms: form.terms,
        items: form.items,
        payment_schedule: form.payment_schedule,
      },
    })
    // Re-read rather than trust the save's echo: the controller recomputes every
    // figure from the item rows and the browser must show what it wrote.
    apply(
      await call('crm.api.invoices.get_invoice', { invoice: props.invoiceId }),
    )
    toast.success(__('Draft saved'))
  } catch (error) {
    actionError.value = messageOf(error, __('Could not save the draft.'))
  } finally {
    working.value = ''
  }
}

// --- issuing and voiding --------------------------------------------------

async function issue() {
  actionError.value = ''
  working.value = 'issue'
  try {
    const result = await call('crm.api.invoices.finalize', {
      invoice: props.invoiceId,
    })
    apply(result.invoice)
    toast.success(__('Invoice {0} issued', [result.invoice.invoice_number]))
  } catch (error) {
    actionError.value = messageOf(error, __('Could not issue this invoice.'))
  } finally {
    working.value = ''
  }
}

async function voidInvoice() {
  actionError.value = ''
  working.value = 'void'
  try {
    apply(
      await call('crm.api.invoices.void_invoice', {
        invoice: props.invoiceId,
        reason: voidReason.value,
      }),
    )
    showVoidModal.value = false
    toast.success(__('Invoice voided'))
  } catch (error) {
    actionError.value = messageOf(error, __('Could not void this invoice.'))
  } finally {
    working.value = ''
  }
}

async function togglePause(next) {
  actionError.value = ''
  working.value = 'pause'
  try {
    const result = await call('crm.api.invoices.set_reminders_paused', {
      invoice: props.invoiceId,
      paused: next ? 1 : 0,
    })
    invoice.value = {
      ...invoice.value,
      reminders_paused: result.reminders_paused,
    }
  } catch (error) {
    actionError.value = messageOf(error, __('Could not change the reminders.'))
  } finally {
    working.value = ''
  }
}

// --- delivery -------------------------------------------------------------

async function downloadPdf() {
  actionError.value = ''
  working.value = 'pdf'
  try {
    const result = await call('crm.api.invoices.download_invoice', {
      invoice: props.invoiceId,
    })
    // The file is PRIVATE and the agent is signed in, so the ordinary file URL
    // is the right way to hand it over. Only the CUSTOMER ever gets a token.
    window.open(result.file_url, '_blank')
  } catch (error) {
    actionError.value = messageOf(error, __('Could not build the PDF.'))
  } finally {
    working.value = ''
  }
}

async function shareLink() {
  actionError.value = ''
  working.value = 'share'
  try {
    const result = await call('crm.api.invoices.share_invoice', {
      invoice: props.invoiceId,
    })
    sharedLink.value = result
    // `copyToClipboard` raises its own "Copied to Clipboard" toast.
    copyToClipboard(result.link_url)
  } catch (error) {
    actionError.value = messageOf(error, __('Could not build the link.'))
  } finally {
    working.value = ''
  }
}

async function sendEmail() {
  actionError.value = ''
  sendHint.value = ''
  working.value = 'email'
  try {
    const result = await call('crm.api.invoices.send_invoice_email', {
      invoice: props.invoiceId,
    })
    if (result.success) {
      toast.success(__('Invoice sent to {0}', [result.to]))
    } else {
      actionError.value = result.error
    }
  } catch (error) {
    actionError.value = messageOf(error, __('Could not send the invoice.'))
  } finally {
    working.value = ''
  }
}

async function sendOnWhatsApp() {
  actionError.value = ''
  sendHint.value = ''
  working.value = 'whatsapp'
  try {
    const result = await call('crm.api.invoices.send_invoice_on_whatsapp', {
      invoice: props.invoiceId,
    })
    if (result.success) {
      toast.success(__('Invoice sent to {0}', [result.to]))
    } else {
      actionError.value = result.error
      sendHint.value = result.hint || ''
    }
  } catch (error) {
    actionError.value = messageOf(error, __('Could not send the invoice.'))
  } finally {
    working.value = ''
  }
}

load()
</script>
