<template>
  <LayoutHeader>
    <template #left-header>
      <ViewBreadcrumbs routeName="WhatsApp" />
    </template>
    <template #right-header>
      <Tooltip :text="__('Refresh conversations')">
        <Button
          icon="lucide-refresh-ccw"
          :loading="conversations.loading"
          @click="conversations.reload()"
        />
      </Tooltip>
      <Button
        v-if="selected"
        variant="solid"
        :label="referenceLabel"
        @click="openReference()"
      >
        <template #prefix>
          <span class="lucide-arrow-up-right size-4" aria-hidden="true" />
        </template>
      </Button>
    </template>
  </LayoutHeader>

  <div class="flex min-h-0 flex-1 overflow-hidden bg-surface-base">
    <!-- LEFT: conversation list -->
    <aside
      class="flex w-[320px] shrink-0 flex-col border-r border-outline-gray-1 xl:w-[368px]"
    >
      <div class="shrink-0 px-4 pb-3 pt-4">
        <p class="text-tiny-semibold uppercase tracking-wide text-ink-gray-4">
          {{ __('Conversations') }}
        </p>
        <div class="mb-3 mt-1 flex items-center gap-2">
          <h1 class="text-xl-semibold text-ink-gray-9">
            {{ __('Unified inbox') }}
          </h1>
          <span
            v-if="allConversations.length"
            class="rounded-full bg-surface-blue-2 px-2 py-0.5 text-xs-medium text-ink-blue-8"
          >
            {{ allConversations.length }}
          </span>
        </div>

        <div
          v-if="connectedAccount.data"
          class="mb-3 flex items-center gap-1.5 text-xs text-ink-gray-6"
        >
          <span class="size-1.5 rounded-full bg-[#25d366]" />
          <span>
            {{ __('Connected') }}:
            <span class="font-medium text-ink-gray-8">{{
              connectedAccount.data.name
            }}</span>
          </span>
        </div>
        <div
          v-else-if="connectedAccount.fetched"
          class="mb-3 flex items-center gap-1.5 text-xs text-ink-amber-9"
        >
          <span class="size-1.5 rounded-full bg-amber-500" />
          {{ __('No WhatsApp account connected') }}
        </div>

        <!-- Scope toggle as a segmented control. Managers only: the server
             answers "mine" to everyone else however they ask. -->
        <div
          v-if="canSwitchScope"
          class="mb-3 flex items-center gap-0.5 rounded-lg bg-surface-gray-2 p-0.5"
        >
          <button
            v-for="button in scopeButtons"
            :key="button.value"
            type="button"
            class="h-7 flex-1 rounded-[7px] text-sm transition-all"
            :class="
              scope === button.value
                ? 'bg-surface-base font-medium text-ink-gray-9 shadow-sm'
                : 'text-ink-gray-5 hover:text-ink-gray-7'
            "
            @click="scope = button.value"
          >
            {{ button.label }}
          </button>
        </div>

        <TextInput
          v-model="search"
          type="text"
          :placeholder="__('Search name, number or message')"
        >
          <template #prefix>
            <span
              class="lucide-search size-4 text-ink-gray-4"
              aria-hidden="true"
            />
          </template>
        </TextInput>
      </div>

      <FadedScrollableDiv class="flex flex-1 flex-col overflow-y-auto pb-3">
        <div
          v-if="conversations.loading && !allConversations.length"
          class="flex flex-1 flex-col items-center justify-center gap-2 text-ink-gray-4"
        >
          <LoadingIndicator class="size-5" />
          <span class="text-sm">{{ __('Loading...') }}</span>
        </div>

        <div
          v-else-if="!visibleConversations.length"
          class="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center"
        >
          <span
            class="grid size-12 place-items-center rounded-full bg-surface-gray-2"
          >
            <span
              class="lucide-message-circle size-6 text-ink-gray-4"
              aria-hidden="true"
            />
          </span>
          <p class="text-base text-ink-gray-5">
            {{
              search
                ? __('No conversation matches your search')
                : __('No WhatsApp conversations yet')
            }}
          </p>
        </div>

        <div v-else class="flex flex-col divide-y divide-outline-gray-1">
          <button
            v-for="conversation in visibleConversations"
            :key="conversationKey(conversation)"
            type="button"
            class="flex w-full items-start gap-3 px-4 py-3.5 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-outline-blue-3"
            :class="
              conversationKey(conversation) === selectedKey
                ? 'bg-surface-blue-1 shadow-[inset_3px_0_0_0_var(--surface-blue-6)]'
                : 'hover:bg-surface-gray-1'
            "
            @click="selectConversation(conversation)"
          >
            <span class="relative mt-0.5 shrink-0">
              <span
                class="grid size-10 place-items-center rounded-xl bg-surface-blue-2 text-xs-medium uppercase text-ink-blue-8"
                :class="
                  conversationKey(conversation) === selectedKey
                    ? 'ring-1 ring-outline-blue-2'
                    : ''
                "
              >
                {{ conversationInitials(conversation) }}
              </span>
              <span
                class="absolute -bottom-0.5 -right-0.5 grid size-4 place-items-center rounded-full bg-surface-base"
              >
                <span
                  class="size-2.5 rounded-full bg-[#25d366]"
                  :title="__('WhatsApp')"
                />
              </span>
            </span>

            <span class="flex min-w-0 flex-1 flex-col gap-1">
              <span class="flex items-baseline justify-between gap-2">
                <span
                  class="flex min-w-0 items-center gap-1.5 truncate text-base text-ink-gray-9"
                  :class="
                    conversation.needs_reply ? 'font-semibold' : 'font-medium'
                  "
                >
                  {{ conversation.display_name }}
                  <span
                    v-if="conversation.needs_reply"
                    class="size-2 shrink-0 rounded-full bg-surface-blue-6"
                    :title="__('Awaiting your reply')"
                  />
                </span>
                <Tooltip :text="formatDate(conversation.last_at)">
                  <span class="shrink-0 text-xs text-ink-gray-4">
                    {{ prettyDate(conversation.last_at, true) }}
                  </span>
                </Tooltip>
              </span>

              <span
                class="truncate text-sm"
                :class="
                  conversation.needs_reply
                    ? 'font-medium text-ink-gray-8'
                    : 'text-ink-gray-6'
                "
              >
                {{ conversationPreview(conversation) }}
              </span>

              <span class="mt-1 flex flex-wrap items-center gap-1">
                <Tooltip :text="priorityPill(conversation.priority).title">
                  <span
                    class="rounded-full px-2 py-[3px] text-2xs-medium"
                    :class="priorityPill(conversation.priority).class"
                  >
                    {{ priorityPill(conversation.priority).label }}
                  </span>
                </Tooltip>
                <span
                  v-if="statusPill(conversation.status)"
                  class="rounded-full px-2 py-[3px] text-2xs-medium"
                  :class="statusPill(conversation.status).class"
                >
                  {{ statusPill(conversation.status).label }}
                </span>
                <span
                  v-if="waitingPill(conversation)"
                  class="rounded-full px-2 py-[3px] text-2xs-medium"
                  :class="waitingPill(conversation).class"
                >
                  {{ waitingPill(conversation).label }}
                </span>
                <Tooltip
                  v-if="conversation.assigned_to"
                  class="ml-auto"
                  :text="
                    __('Assigned to {0}', [conversation.assigned_to.full_name])
                  "
                >
                  <UserAvatar :user="conversation.assigned_to.user" size="xs" />
                </Tooltip>
              </span>
            </span>
          </button>
        </div>
      </FadedScrollableDiv>
    </aside>

    <!-- CENTER: thread -->
    <section class="flex min-w-0 flex-1 flex-col">
      <template v-if="selected">
        <header
          class="flex shrink-0 items-center justify-between gap-3 border-b border-outline-gray-1 px-6 py-3.5"
        >
          <div class="flex min-w-0 items-center gap-3">
            <span
              class="grid size-10 shrink-0 place-items-center rounded-xl bg-surface-blue-2 text-xs-medium uppercase text-ink-blue-8"
            >
              {{ conversationInitials(selected) }}
            </span>
            <div class="flex min-w-0 flex-col">
              <span class="truncate text-lg font-semibold text-ink-gray-9">
                {{ selected.display_name }}
              </span>
              <span class="flex min-w-0 items-center gap-1.5 text-xs">
                <span class="size-1.5 shrink-0 rounded-full bg-[#25d366]" />
                <span class="shrink-0 text-ink-green-8">
                  {{ __('WhatsApp') }}
                </span>
                <span class="truncate text-ink-gray-5">
                  · {{ selected.phone }}
                </span>
              </span>
            </div>
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <Tooltip :text="__('Call {0}', [selected.phone])">
              <Button icon="lucide-phone" @click="callNumber(selected.phone)" />
            </Tooltip>
            <Tooltip :text="referenceLabel">
              <Button icon="lucide-arrow-up-right" @click="openReference()" />
            </Tooltip>
          </div>
        </header>

        <!-- Context banner: what the CRM knows about this conversation. -->
        <div
          class="flex shrink-0 items-center gap-2 border-b border-outline-gray-1 bg-surface-blue-1 px-6 py-2 text-xs text-ink-gray-6"
        >
          <span
            class="lucide-sparkles size-3.5 shrink-0 text-ink-blue-7"
            aria-hidden="true"
          />
          <span class="truncate">
            {{ __('Linked to') }}
            <button
              type="button"
              class="font-medium text-ink-blue-7 hover:underline"
              @click="openReference()"
            >
              {{ selected.display_name }}</button
            ><template v-if="selected.status">
              · {{ __(selected.status) }}</template
            ><template v-if="selected.assigned_to">
              · {{ __('assigned to {0}', [selected.assigned_to.full_name]) }}
            </template>
          </span>
        </div>

        <FadedScrollableDiv
          data-whatsapp-thread
          class="crm-chat crm-thread-canvas flex flex-1 flex-col overflow-y-auto"
        >
          <div
            v-if="messages.loading && !messages.data?.length"
            class="flex flex-1 flex-col items-center justify-center gap-2 text-ink-gray-4"
          >
            <LoadingIndicator class="size-5" />
            <span class="text-sm">{{ __('Loading...') }}</span>
          </div>

          <div
            v-else-if="messageGroups.length"
            class="mt-auto flex w-full max-w-[760px] flex-col self-center px-6 pb-2 pt-4 xl:px-8"
          >
            <template
              v-for="(group, index) in messageGroups"
              :key="group.label + index"
            >
              <div class="my-3 flex justify-center">
                <span
                  class="rounded-full border border-outline-gray-2 bg-surface-base px-3 py-1 text-xs font-medium text-ink-gray-5 shadow-sm"
                >
                  {{ __(group.label) }}
                </span>
              </div>
              <WhatsAppArea
                v-model="messages"
                v-model:reply="replyMessage"
                :messages="group.messages"
              />
            </template>
          </div>

          <div
            v-else
            class="flex flex-1 flex-col items-center justify-center gap-3 text-center"
          >
            <span
              class="grid size-14 place-items-center rounded-full bg-surface-base shadow-sm"
            >
              <WhatsAppIcon class="size-7" />
            </span>
            <div class="flex flex-col gap-1">
              <p class="text-base font-medium text-ink-gray-8">
                {{ __('No messages yet') }}
              </p>
              <p class="text-sm text-ink-gray-5">
                {{ __('Start the conversation by sending a message below.') }}
              </p>
            </div>
          </div>
        </FadedScrollableDiv>

        <div class="crm-thread-canvas shrink-0 px-6 pb-5 pt-2">
          <WhatsAppInboxComposer
            v-model="activeDoc"
            v-model:reply="replyMessage"
            v-model:whatsapp="messages"
            class="mx-auto w-full max-w-[760px]"
            :doctype="selected.reference_doctype"
            :showTemplates="whatsappEnabled"
            @openTemplates="showWhatsappTemplates = true"
            @sent="conversations.reload()"
          />
        </div>
      </template>

      <div
        v-else
        class="crm-thread-canvas flex flex-1 flex-col items-center justify-center gap-4 text-center"
      >
        <span
          class="grid size-16 place-items-center rounded-2xl bg-surface-base shadow-sm"
        >
          <WhatsAppIcon class="size-8" />
        </span>
        <div class="flex flex-col gap-1">
          <p class="text-lg font-semibold text-ink-gray-8">
            {{ __('Select a conversation') }}
          </p>
          <p class="max-w-xs text-sm text-ink-gray-5">
            {{
              __(
                'Pick a conversation on the left to read and reply to it here.',
              )
            }}
          </p>
        </div>
      </div>
    </section>

    <!-- RIGHT: contact panel. Three panes need ~1280px; below that the app
         falls back to the two-pane list + thread. -->
    <aside
      v-if="selected"
      class="hidden w-[300px] shrink-0 flex-col overflow-y-auto border-l border-outline-gray-1 xl:flex"
    >
      <div class="flex flex-col items-center gap-3 px-5 pb-5 pt-6 text-center">
        <span
          class="grid size-16 place-items-center rounded-2xl bg-surface-blue-2 text-lg-semibold uppercase text-ink-blue-8"
        >
          {{ conversationInitials(selected) }}
        </span>
        <div class="flex min-w-0 flex-col gap-0.5">
          <span class="truncate text-lg-semibold text-ink-gray-9">
            {{ selected.display_name }}
          </span>
          <span class="truncate text-sm text-ink-gray-5">
            {{ selected.phone }}
          </span>
        </div>

        <div class="flex items-center gap-2">
          <Tooltip :text="__('Call')">
            <Button
              class="!rounded-full"
              icon="lucide-phone"
              @click="callNumber(selected.phone)"
            />
          </Tooltip>
          <Tooltip :text="__('Copy number')">
            <Button
              class="!rounded-full"
              icon="lucide-copy"
              @click="copyNumber(selected.phone)"
            />
          </Tooltip>
          <Tooltip :text="referenceLabel">
            <Button
              class="!rounded-full"
              icon="lucide-arrow-up-right"
              @click="openReference()"
            />
          </Tooltip>
        </div>
      </div>

      <div class="border-t border-outline-gray-1 px-5 py-4">
        <p
          class="mb-3 text-tiny-semibold uppercase tracking-wide text-ink-gray-4"
        >
          {{ referenceSectionLabel }}
        </p>

        <dl class="flex flex-col gap-3">
          <div class="flex items-start gap-2.5">
            <span
              class="lucide-git-branch mt-0.5 size-4 shrink-0 text-ink-gray-4"
              aria-hidden="true"
            />
            <div class="flex min-w-0 flex-1 flex-col gap-1">
              <dt class="text-xs text-ink-gray-5">{{ __('Stage') }}</dt>
              <dd>
                <span
                  v-if="statusPill(selected.status)"
                  class="rounded-full px-2 py-[3px] text-2xs-medium"
                  :class="statusPill(selected.status).class"
                >
                  {{ statusPill(selected.status).label }}
                </span>
                <span v-else class="text-sm text-ink-gray-5">—</span>
              </dd>
            </div>
          </div>

          <div class="flex items-start gap-2.5">
            <span
              class="lucide-user-round mt-0.5 size-4 shrink-0 text-ink-gray-4"
              aria-hidden="true"
            />
            <div class="flex min-w-0 flex-1 flex-col gap-1">
              <dt class="text-xs text-ink-gray-5">{{ __('Owner') }}</dt>
              <dd class="flex min-w-0 items-center gap-1.5">
                <template v-if="selected.assigned_to">
                  <UserAvatar :user="selected.assigned_to.user" size="xs" />
                  <span class="truncate text-sm text-ink-gray-8">
                    {{ selected.assigned_to.full_name }}
                  </span>
                </template>
                <span v-else class="text-sm text-ink-gray-5">
                  {{ __('Unassigned') }}
                </span>
              </dd>
            </div>
          </div>

          <div class="flex items-start gap-2.5">
            <span
              class="lucide-flame mt-0.5 size-4 shrink-0 text-ink-gray-4"
              aria-hidden="true"
            />
            <div class="flex min-w-0 flex-1 flex-col gap-1">
              <dt class="text-xs text-ink-gray-5">{{ __('Priority') }}</dt>
              <dd>
                <span
                  class="rounded-full px-2 py-[3px] text-2xs-medium"
                  :class="priorityPill(selected.priority).class"
                >
                  {{ priorityPill(selected.priority).label }}
                </span>
              </dd>
            </div>
          </div>
        </dl>
      </div>

      <div
        v-if="travelRows.length"
        class="border-t border-outline-gray-1 px-5 py-4"
      >
        <p
          class="mb-3 text-tiny-semibold uppercase tracking-wide text-ink-gray-4"
        >
          {{ __('Trip details') }}
        </p>

        <dl class="flex flex-col gap-3">
          <div
            v-for="row in travelRows"
            :key="row.label"
            class="flex items-start gap-2.5"
          >
            <span
              class="mt-0.5 size-4 shrink-0 text-ink-gray-4"
              :class="row.icon"
              aria-hidden="true"
            />
            <div class="flex min-w-0 flex-1 flex-col gap-1">
              <dt class="text-xs text-ink-gray-5">{{ row.label }}</dt>
              <dd class="truncate text-sm text-ink-gray-8">{{ row.value }}</dd>
            </div>
          </div>
        </dl>
      </div>

      <div class="border-t border-outline-gray-1 px-5 py-4">
        <p
          class="mb-3 text-tiny-semibold uppercase tracking-wide text-ink-gray-4"
        >
          {{ __('Conversation') }}
        </p>

        <dl class="flex flex-col gap-3">
          <div class="flex items-start gap-2.5">
            <span
              class="lucide-messages-square mt-0.5 size-4 shrink-0 text-ink-gray-4"
              aria-hidden="true"
            />
            <div class="flex min-w-0 flex-1 flex-col gap-1">
              <dt class="text-xs text-ink-gray-5">{{ __('Messages') }}</dt>
              <dd class="text-sm text-ink-gray-8">
                {{ selected.message_count }}
              </dd>
            </div>
          </div>

          <div class="flex items-start gap-2.5">
            <span
              class="lucide-clock mt-0.5 size-4 shrink-0 text-ink-gray-4"
              aria-hidden="true"
            />
            <div class="flex min-w-0 flex-1 flex-col gap-1">
              <dt class="text-xs text-ink-gray-5">{{ __('Last activity') }}</dt>
              <dd class="text-sm text-ink-gray-8">
                {{ prettyDate(selected.last_at) }}
              </dd>
            </div>
          </div>

          <div v-if="selected.needs_reply" class="flex items-start gap-2.5">
            <span
              class="lucide-timer mt-0.5 size-4 shrink-0 text-ink-amber-9"
              aria-hidden="true"
            />
            <div class="flex min-w-0 flex-1 flex-col gap-1">
              <dt class="text-xs text-ink-gray-5">
                {{ __('Awaiting reply') }}
              </dt>
              <dd class="text-sm font-medium text-ink-amber-9">
                {{ humanizeAge(selected.waiting_since) }}
              </dd>
            </div>
          </div>
        </dl>

        <button
          type="button"
          class="mt-4 flex items-center gap-1 text-sm-medium text-ink-blue-link hover:underline"
          @click="openReference()"
        >
          {{ referenceLabel }}
          <span class="lucide-arrow-right size-3.5" aria-hidden="true" />
        </button>
      </div>
    </aside>
  </div>

  <WhatsappTemplateSelectorModal
    v-if="whatsappEnabled && selected"
    v-model="showWhatsappTemplates"
    :doctype="selected.reference_doctype"
    @send="(template) => sendTemplate(template)"
  />
</template>

<script setup>
import LayoutHeader from '@/components/LayoutHeader.vue'
import ViewBreadcrumbs from '@/components/ViewBreadcrumbs.vue'
import FadedScrollableDiv from '@/components/FadedScrollableDiv.vue'
import LoadingIndicator from '@/components/Icons/LoadingIndicator.vue'
import WhatsAppIcon from '@/components/Icons/WhatsAppIcon.vue'
import WhatsAppArea from '@/components/Activities/WhatsAppArea.vue'
import WhatsAppInboxComposer from '@/components/Activities/WhatsAppInboxComposer.vue'
import WhatsappTemplateSelectorModal from '@/components/Modals/WhatsappTemplateSelectorModal.vue'
import UserAvatar from '@/components/UserAvatar.vue'
import { globalStore } from '@/stores/global'
import { usersStore } from '@/stores/users'
import { whatsappEnabled } from '@/composables/whatsapp'
import { formatDate, prettyDate } from '@/utils'
import { formatCurrency } from '@/utils/numberFormat.js'
import {
  conversationInitials,
  conversationKey,
  conversationPreview,
  filterConversations,
  groupMessagesByDay,
  groupSizeLabel,
  humanizeAge,
  isSameConversation,
  priorityPill,
  statusPill,
  travelWindowLabel,
  waitingPill,
} from '@/utils/whatsappInbox'
import { useTelemetry } from 'frappe-ui/frappe'
import {
  Button,
  TextInput,
  Tooltip,
  createResource,
  toast,
  usePageMeta,
} from 'frappe-ui'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const { $socket } = globalStore()
const { isManager } = usersStore()
const { capture } = useTelemetry()

// Only a manager may look past their own conversations; the server enforces
// this too and quietly answers "mine" to anyone else who asks for "all".
const canSwitchScope = computed(() => isManager())
const scope = ref('mine')
const scopeButtons = [
  { label: __('My inbox'), value: 'mine' },
  { label: __('All conversations'), value: 'all' },
]

const search = ref('')
const selected = ref(null)
const replyMessage = ref({})
const showWhatsappTemplates = ref(false)

// The composer reads `name` and `mobile_no` off its doc model to address the send.
const activeDoc = ref({})

const connectedAccount = createResource({
  url: 'crm.api.whatsapp.get_connected_whatsapp_account',
  cache: 'whatsapp_connected_account',
  auto: true,
})

const conversations = createResource({
  url: 'crm.api.whatsapp.get_whatsapp_conversations',
  cache: 'whatsapp_conversations',
  makeParams: () => ({ scope: scope.value }),
  auto: true,
})

watch(scope, (value) => {
  conversations.reload()
  capture('whatsapp_inbox_switch_scope', { scope: value })
})

const allConversations = computed(() => conversations.data || [])

const visibleConversations = computed(() =>
  filterConversations(allConversations.value, search.value),
)

const selectedKey = computed(() => conversationKey(selected.value))

// `selected` holds a row from the previous fetch, so after a realtime refresh
// the contact panel would keep showing a stale stage, assignee and preview.
// Re-point it at the matching row of the new list. A conversation that left the
// list (scope switched away from it) stays open on its last known state rather
// than closing under the reader.
watch(allConversations, (rows) => {
  if (!selected.value) return

  const fresh = rows.find((row) => conversationKey(row) === selectedKey.value)
  if (fresh) selected.value = fresh
})

const isDeal = computed(() => selected.value?.reference_doctype === 'CRM Deal')

const referenceLabel = computed(() =>
  isDeal.value ? __('Open deal') : __('Open lead'),
)

const referenceSectionLabel = computed(() =>
  isDeal.value ? __('Deal details') : __('Lead details'),
)

const messages = createResource({
  url: 'crm.api.whatsapp.get_whatsapp_messages',
  makeParams: () => ({
    reference_doctype: selected.value?.reference_doctype,
    reference_name: selected.value?.reference_name,
  }),
  // Oldest first, so the newest message sits at the bottom like WhatsApp Web.
  transform: (data) =>
    [...data].sort((a, b) => new Date(a.creation) - new Date(b.creation)),
  onSuccess: () => scrollThreadToBottom(),
})

const messageGroups = computed(() => groupMessagesByDay(messages.data || []))

// The travel fields live on CRM Lead only, so a Deal conversation never asks
// for them — `frappe.client.get_value` would throw on the unknown fieldnames.
// The read is permission aware; a lead this user cannot see comes back empty.
const TRAVEL_FIELDS = [
  'destination',
  'travel_start_date',
  'travel_end_date',
  'group_size',
  'budget',
]

const travelDetails = createResource({
  url: 'frappe.client.get_value',
  makeParams: () => ({
    doctype: 'CRM Lead',
    filters: { name: selected.value?.reference_name },
    fieldname: TRAVEL_FIELDS,
  }),
})

const travelRows = computed(() => {
  const lead = travelDetails.data || {}
  const rows = []

  if (lead.destination) {
    rows.push({
      icon: 'lucide-map-pin',
      label: __('Destination'),
      value: lead.destination,
    })
  }

  const dates = travelWindowLabel(
    formatDate(lead.travel_start_date, '', true),
    formatDate(lead.travel_end_date, '', true),
  )
  if (dates) {
    rows.push({
      icon: 'lucide-calendar-range',
      label: __('Travel dates'),
      value: dates,
    })
  }

  const group = groupSizeLabel(lead.group_size)
  if (group) {
    rows.push({ icon: 'lucide-users', label: __('Group size'), value: group })
  }

  if (lead.budget) {
    rows.push({
      icon: 'lucide-wallet',
      label: __('Budget'),
      // A budget is a round planning figure; trailing decimals only add noise.
      value: formatCurrency(lead.budget, '', siteCurrency(), 0),
    })
  }

  return rows
})

function siteCurrency() {
  return window.sysdefaults?.currency || 'USD'
}

function selectConversation(conversation) {
  if (conversationKey(conversation) === selectedKey.value) return

  selected.value = conversation
  replyMessage.value = {}
  activeDoc.value = {
    name: conversation.reference_name,
    doctype: conversation.reference_doctype,
    mobile_no: conversation.phone,
  }
  messages.reset()
  messages.fetch()

  travelDetails.reset()
  if (conversation.reference_doctype === 'CRM Lead') {
    travelDetails.fetch()
  }

  capture('whatsapp_inbox_open_conversation')
}

function openReference() {
  if (!selected.value) return
  const { reference_doctype: doctype, reference_name: name } = selected.value
  if (doctype === 'CRM Deal') {
    router.push({ name: 'Deal', params: { dealId: name } })
  } else {
    router.push({ name: 'Lead', params: { leadId: name } })
  }
}

function callNumber(number) {
  window.location.href = `tel:${number}`
}

async function copyNumber(number) {
  try {
    await navigator.clipboard.writeText(number)
    toast.success(__('Number copied'))
  } catch {
    toast.error(__('Could not copy the number'))
  }
}

function sendTemplate(template) {
  showWhatsappTemplates.value = false
  capture('send_whatsapp_template', {
    doctype: selected.value.reference_doctype,
  })
  createResource({
    url: 'crm.api.whatsapp.send_whatsapp_template',
    params: {
      reference_doctype: selected.value.reference_doctype,
      reference_name: selected.value.reference_name,
      to: selected.value.phone,
      template,
    },
    auto: true,
    onError: (error) => {
      toast.error(error.messages?.[0] || __('Failed to send WhatsApp template'))
    },
    onSuccess: () => {
      messages.reload()
      conversations.reload()
    },
  })
}

function scrollThreadToBottom() {
  requestAnimationFrame(() => {
    const thread = document.querySelector('[data-whatsapp-thread]')
    thread?.scrollTo({ top: thread.scrollHeight })
  })
}

function onWhatsAppMessage(data) {
  conversations.reload()
  if (isSameConversation(selected.value, data)) {
    messages.reload()
  }
}

onMounted(() => {
  $socket.on('whatsapp_message', onWhatsAppMessage)
})

onBeforeUnmount(() => {
  $socket.off('whatsapp_message', onWhatsAppMessage)
})

usePageMeta(() => ({ title: __('WhatsApp') }))
</script>
