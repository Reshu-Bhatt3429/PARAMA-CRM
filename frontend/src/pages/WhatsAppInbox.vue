<template>
  <LayoutHeader>
    <template #left-header>
      <ViewBreadcrumbs routeName="WhatsApp" />
    </template>
    <template #right-header>
      <Button
        :label="__('Refresh')"
        :loading="conversations.loading"
        @click="conversations.reload()"
      >
        <template #prefix>
          <span class="lucide-refresh-ccw size-4" aria-hidden="true" />
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
        <p class="text-tiny-semibold text-ink-gray-5">
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

        <!-- The scope toggle as reference-style pills. Managers only: the
             server answers "mine" to everyone else however they ask. -->
        <div v-if="canSwitchScope" class="mb-3 flex items-center gap-1">
          <button
            v-for="button in scopeButtons"
            :key="button.value"
            type="button"
            class="rounded-full px-3 py-1 text-sm transition-colors"
            :class="
              scope === button.value
                ? 'bg-surface-blue-2 font-medium text-ink-blue-8'
                : 'text-ink-gray-6 hover:bg-surface-gray-2'
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

        <p
          v-else-if="!visibleConversations.length"
          class="px-2 py-10 text-center text-base text-ink-gray-5"
        >
          {{
            search
              ? __('No conversation matches your search')
              : __('No WhatsApp conversations yet')
          }}
        </p>

        <button
          v-for="conversation in visibleConversations"
          :key="conversationKey(conversation)"
          type="button"
          class="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-outline-blue-3"
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
            >
              {{ conversationInitials(conversation) }}
            </span>
            <!-- Channel dot. WhatsApp brand green, ringed in the card surface
                 so it reads as a badge rather than a stray pixel. -->
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
              <span class="truncate text-base-medium text-ink-gray-9">
                {{ conversation.display_name }}
              </span>
              <Tooltip :text="formatDate(conversation.last_at)">
                <span class="shrink-0 text-xs text-ink-gray-5">
                  {{ prettyDate(conversation.last_at, true) }}
                </span>
              </Tooltip>
            </span>

            <span class="truncate text-sm text-ink-gray-6">
              {{ conversationPreview(conversation) }}
            </span>

            <span class="mt-0.5 flex flex-wrap items-center gap-1">
              <Tooltip :text="priorityPill(conversation.priority).title">
                <span
                  class="rounded-full px-2 py-0.5 text-2xs-medium"
                  :class="priorityPill(conversation.priority).class"
                >
                  {{ priorityPill(conversation.priority).label }}
                </span>
              </Tooltip>
              <span
                v-if="statusPill(conversation.status)"
                class="rounded-full px-2 py-0.5 text-2xs-medium"
                :class="statusPill(conversation.status).class"
              >
                {{ statusPill(conversation.status).label }}
              </span>
              <span
                v-if="waitingPill(conversation)"
                class="rounded-full px-2 py-0.5 text-2xs-medium"
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
      </FadedScrollableDiv>
    </aside>

    <!-- CENTER: thread -->
    <section class="flex min-w-0 flex-1 flex-col">
      <template v-if="selected">
        <header
          class="flex shrink-0 items-center justify-between gap-3 border-b border-outline-gray-1 px-5 py-3"
        >
          <div class="flex min-w-0 items-center gap-3">
            <span
              class="grid size-10 shrink-0 place-items-center rounded-xl bg-surface-blue-2 text-xs-medium uppercase text-ink-blue-8"
            >
              {{ conversationInitials(selected) }}
            </span>
            <div class="flex min-w-0 flex-col">
              <span class="truncate text-base-medium text-ink-gray-9">
                {{ selected.display_name }}
              </span>
              <span class="flex min-w-0 items-center gap-1.5 text-xs">
                <span class="size-1.5 shrink-0 rounded-full bg-[#25d366]" />
                <span class="shrink-0 text-ink-green-8">
                  {{ __('WhatsApp') }}
                </span>
                <span class="truncate text-ink-gray-5">
                  · {{ selected.phone }} ·
                  {{ __('{0} messages', [selected.message_count]) }}
                </span>
              </span>
            </div>
          </div>
          <div class="flex shrink-0 items-center gap-2">
            <Button
              v-if="whatsappEnabled"
              :label="__('Templates')"
              @click="showWhatsappTemplates = true"
            >
              <template #prefix>
                <span
                  class="lucide-layout-template size-4"
                  aria-hidden="true"
                />
              </template>
            </Button>
            <Button :label="referenceLabel" @click="openReference()">
              <template #prefix>
                <span class="lucide-arrow-up-right size-4" aria-hidden="true" />
              </template>
            </Button>
          </div>
        </header>

        <!-- The thread sits on the lavender page canvas so the white composer
             card and the message bubbles read as raised surfaces. -->
        <FadedScrollableDiv
          data-whatsapp-thread
          class="crm-chat app-canvas flex flex-1 flex-col overflow-y-auto py-4"
        >
          <div
            v-if="messages.loading && !messages.data?.length"
            class="flex flex-1 flex-col items-center justify-center gap-2 text-ink-gray-4"
          >
            <LoadingIndicator class="size-5" />
            <span class="text-sm">{{ __('Loading...') }}</span>
          </div>
          <WhatsAppArea
            v-else-if="messages.data?.length"
            v-model="messages"
            v-model:reply="replyMessage"
            class="px-5 xl:px-8"
            :messages="messages.data"
          />
          <EmptyState
            v-else
            name="WhatsApp Messages"
            :title="__('No messages yet')"
            :description="
              __('Start the conversation by sending a message below.')
            "
            :icon="WhatsAppIcon"
          />
        </FadedScrollableDiv>

        <!-- Composer, wrapped rather than forked: WhatsAppBox keeps its own
             markup and only gains the reference's white rounded card. -->
        <div class="app-canvas shrink-0 px-4 pb-4">
          <div
            class="overflow-hidden rounded-2xl border border-outline-gray-1 bg-surface-base shadow-sm"
          >
            <WhatsAppBox
              v-model="activeDoc"
              v-model:reply="replyMessage"
              v-model:whatsapp="messages"
              :doctype="selected.reference_doctype"
            />
          </div>
        </div>
      </template>

      <EmptyState
        v-else
        name="Conversation"
        :title="__('Select a conversation')"
        :description="
          __('Pick a conversation on the left to read and reply to it here.')
        "
        :icon="WhatsAppIcon"
      />
    </section>

    <!-- RIGHT: contact panel. Three panes need ~1280px; below that the app
         falls back to the two-pane list + thread. -->
    <aside
      v-if="selected"
      class="hidden w-[300px] shrink-0 flex-col overflow-y-auto border-l border-outline-gray-1 xl:flex"
    >
      <div class="flex flex-col items-center gap-2 px-5 pb-5 pt-6 text-center">
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
      </div>

      <div class="border-t border-outline-gray-1 px-5 py-4">
        <p class="mb-3 text-tiny-semibold text-ink-gray-5">
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
                  class="rounded-full px-2 py-0.5 text-2xs-medium"
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
import EmptyState from '@/components/ListViews/EmptyState.vue'
import LoadingIndicator from '@/components/Icons/LoadingIndicator.vue'
import WhatsAppIcon from '@/components/Icons/WhatsAppIcon.vue'
import WhatsAppArea from '@/components/Activities/WhatsAppArea.vue'
import WhatsAppBox from '@/components/Activities/WhatsAppBox.vue'
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
  groupSizeLabel,
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

// WhatsAppBox reads `name` and `mobile_no` off its doc model to address the send.
const activeDoc = ref({})

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
