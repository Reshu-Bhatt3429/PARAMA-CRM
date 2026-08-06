<template>
  <LayoutHeader>
    <template #left-header>
      <ViewBreadcrumbs routeName="WhatsApp" />
    </template>
    <template #right-header>
      <TabButtons
        v-if="canSwitchScope"
        v-model="scope"
        :buttons="scopeButtons"
        class="mr-2"
      />
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

  <div class="flex min-h-0 flex-1 overflow-hidden">
    <!-- Conversation list -->
    <aside
      class="flex w-[300px] shrink-0 flex-col border-r border-outline-gray-1 bg-surface-white xl:w-[360px]"
    >
      <div class="shrink-0 px-4 pb-3 pt-4">
        <div class="mb-3 flex items-baseline justify-between gap-2">
          <h1 class="text-lg-semibold text-ink-gray-9">
            {{ __('Conversations') }}
          </h1>
          <span v-if="allConversations.length" class="text-xs text-ink-gray-5">
            {{ allConversations.length }}
          </span>
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

      <FadedScrollableDiv
        class="flex flex-1 flex-col gap-0.5 overflow-y-auto px-2 pb-3"
      >
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
          class="flex w-full items-start gap-3 rounded-lg px-2.5 py-2.5 text-left transition-colors hover:bg-surface-gray-2 focus:outline-none focus-visible:ring-2 focus-visible:ring-outline-gray-3"
          :class="
            conversationKey(conversation) === selectedKey
              ? 'bg-surface-gray-3'
              : ''
          "
          @click="selectConversation(conversation)"
        >
          <span
            class="mt-0.5 grid size-9 shrink-0 place-items-center rounded-full bg-surface-gray-3 text-xs-medium uppercase text-ink-gray-7"
          >
            {{ conversationInitials(conversation) }}
          </span>
          <span class="flex min-w-0 flex-1 flex-col gap-0.5">
            <span class="flex items-center justify-between gap-2">
              <span class="flex min-w-0 items-center gap-1.5">
                <Tooltip :text="priorityMeta(conversation.priority).label">
                  <span
                    class="size-2 shrink-0 rounded-full"
                    :class="priorityMeta(conversation.priority).dotClass"
                  />
                </Tooltip>
                <span class="truncate text-base-medium text-ink-gray-9">
                  {{ conversation.display_name }}
                </span>
              </span>
              <Tooltip :text="formatDate(conversation.last_at)">
                <span class="shrink-0 text-xs text-ink-gray-5">
                  {{ prettyDate(conversation.last_at, true) }}
                </span>
              </Tooltip>
            </span>
            <span class="flex items-center gap-1.5">
              <span class="truncate text-xs text-ink-gray-5">
                {{ conversation.phone }}
              </span>
              <span
                v-if="waitingLabel(conversation)"
                class="shrink-0 rounded bg-surface-amber-2 px-1.5 text-xs text-ink-amber-3"
              >
                {{ waitingLabel(conversation) }}
              </span>
            </span>
            <span class="flex items-center gap-1.5">
              <OutboundCallIcon
                v-if="conversation.last_message_type === 'Outgoing'"
                class="size-3.5 shrink-0 text-ink-blue-3"
              />
              <InboundCallIcon
                v-else
                class="size-3.5 shrink-0 text-ink-green-3"
              />
              <span class="truncate text-sm text-ink-gray-6">
                {{ conversationPreview(conversation) }}
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

    <!-- Thread -->
    <section class="flex min-w-0 flex-1 flex-col bg-surface-base">
      <template v-if="selected">
        <header
          class="flex shrink-0 items-center justify-between gap-3 border-b border-outline-gray-1 px-5 py-3"
        >
          <div class="flex min-w-0 items-center gap-3">
            <span
              class="grid size-9 shrink-0 place-items-center rounded-full bg-surface-gray-3 text-xs-medium uppercase text-ink-gray-7"
            >
              {{ conversationInitials(selected) }}
            </span>
            <div class="flex min-w-0 flex-col">
              <span class="truncate text-base-medium text-ink-gray-9">
                {{ selected.display_name }}
              </span>
              <span class="truncate text-xs text-ink-gray-5">
                {{ selected.phone }} ·
                {{ __('{0} messages', [selected.message_count]) }}
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
            <Button
              :label="
                selected.reference_doctype === 'CRM Deal'
                  ? __('Open deal')
                  : __('Open lead')
              "
              @click="openReference()"
            >
              <template #prefix>
                <span class="lucide-arrow-up-right size-4" aria-hidden="true" />
              </template>
            </Button>
          </div>
        </header>

        <FadedScrollableDiv
          data-whatsapp-thread
          class="flex flex-1 flex-col overflow-y-auto py-4"
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
            class="px-5 xl:px-10"
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

        <WhatsAppBox
          v-model="activeDoc"
          v-model:reply="replyMessage"
          v-model:whatsapp="messages"
          class="shrink-0 border-t border-outline-gray-1"
          :doctype="selected.reference_doctype"
        />
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
import InboundCallIcon from '@/components/Icons/InboundCallIcon.vue'
import OutboundCallIcon from '@/components/Icons/OutboundCallIcon.vue'
import WhatsAppArea from '@/components/Activities/WhatsAppArea.vue'
import WhatsAppBox from '@/components/Activities/WhatsAppBox.vue'
import WhatsappTemplateSelectorModal from '@/components/Modals/WhatsappTemplateSelectorModal.vue'
import UserAvatar from '@/components/UserAvatar.vue'
import { globalStore } from '@/stores/global'
import { usersStore } from '@/stores/users'
import { whatsappEnabled } from '@/composables/whatsapp'
import { formatDate, prettyDate } from '@/utils'
import {
  conversationInitials,
  conversationKey,
  conversationPreview,
  filterConversations,
  isSameConversation,
  priorityMeta,
  waitingLabel,
} from '@/utils/whatsappInbox'
import { useTelemetry } from 'frappe-ui/frappe'
import {
  Button,
  TabButtons,
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
