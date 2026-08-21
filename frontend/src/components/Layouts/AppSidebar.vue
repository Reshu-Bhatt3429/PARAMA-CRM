<template>
  <!-- The notifications panel is absolutely positioned at `left: 100%`, so it
       needs a positioning context that is not the Sidebar itself (Sidebar sets
       overflow-x-hidden, which would clip the panel away).

       It also paints the sidebar surface: Sidebar's own `bg-surface-sidebar` is
       transparent in dark mode, and nothing behind it sets a background, so the
       column falls through to the white page canvas. `.crm-sidebar-shell` sets
       that background directly and deliberately carries no token overrides —
       Notifications is its child and must keep the app's normal palette. The
       dark navy tokens live on `.crm-sidebar` one level in (see index.css). -->
  <div class="crm-sidebar-shell relative flex h-full">
    <Sidebar
      v-model:collapsed="isSidebarCollapsed"
      :disable-collapse="mobile"
      :width="mobile ? '260px' : undefined"
      class="crm-sidebar border-r border-outline-gray-1"
    >
      <div class="flex h-full flex-col p-2">
        <UserDropdown :isCollapsed="isCollapsed" />

        <!-- overflow-y-auto forces overflow-x to clip too, which would slice the
             active row's shadow. Widen the scroll box to the sidebar edges and
             pad the content back in so the shadow has room. -->
        <div class="-mx-2 mt-2 flex flex-1 flex-col gap-1 overflow-y-auto px-2">
          <!-- Search sits above Notifications rather than inside a feature
               group: it is not a page, it opens the Cmd+K palette. -->
          <SidebarItem
            id="search-btn"
            :label="__('Search')"
            @click="openCommandPalette"
          >
            <template #prefix>
              <span
                class="lucide-search size-4 text-ink-gray-7"
                aria-hidden="true"
              />
            </template>
            <template #suffix>
              <span
                v-if="!isCollapsed"
                class="mr-2 text-xs text-ink-gray-5"
                aria-hidden="true"
              >
                {{ searchShortcutLabel }}
              </span>
            </template>
          </SidebarItem>

          <SidebarItem
            id="notifications-btn"
            :label="__('Notifications')"
            :to="mobile ? { name: 'Notifications' } : undefined"
            :active="mobile && activeItem === 'Notifications'"
            @click="onNotificationsClick"
          >
            <template #prefix>
              <span class="relative grid size-4 place-items-center">
                <NotificationsIcon class="size-4 text-ink-gray-7" />
                <span
                  v-if="isCollapsed && unreadNotificationsCount"
                  class="absolute -right-1 -top-1 size-1.5 rounded-full bg-surface-gray-9 ring-1 ring-[var(--surface-gray-1)]"
                />
              </span>
            </template>
            <template #suffix>
              <!-- The stock gray subtle Badge disappears on the navy surface;
                   this is the reference's indigo count pill. -->
              <span
                v-if="unreadNotificationsCount"
                class="crm-sidebar-badge mr-2"
              >
                {{ unreadNotificationsCount }}
              </span>
            </template>
          </SidebarItem>

          <CollapsibleSection
            v-for="section in allViews"
            :key="section.name"
            :label="section.name"
            :opened="section.opened"
          >
            <template #header="{ opened, toggle }">
              <SidebarLabel
                divider
                class="mb-1 mt-4 select-none"
                :class="!isCollapsed && 'cursor-pointer'"
                @click="toggleSection(section, opened, toggle)"
              >
                <span class="flex items-center gap-1.5">
                  <span
                    class="lucide-chevron-right -ml-0.5 size-4 shrink-0 text-ink-gray-9 transition-transform duration-300 ease-in-out"
                    :class="{ 'rotate-90': opened }"
                    aria-hidden="true"
                  />
                  <span class="truncate">{{ __(section.name) }}</span>
                </span>
              </SidebarLabel>
            </template>
            <nav class="flex flex-col gap-1">
              <SidebarItem
                v-for="link in section.views"
                :key="link.key"
                :to="link.to"
                :label="__(link.label)"
                :active="activeItem === link.key"
                @click="onLinkClick($event, link)"
              >
                <template #prefix>
                  <span class="relative grid size-4 place-items-center">
                    <Icon :icon="link.icon" class="size-4 text-ink-gray-7" />
                    <!-- On the collapsed rail there is no room for a pill, so
                         the count becomes a dot, exactly as Notifications does
                         above. -->
                    <span
                      v-if="isCollapsed && link.badge"
                      class="absolute -right-1 -top-1 size-1.5 rounded-full bg-surface-gray-9 ring-1 ring-[var(--surface-gray-1)]"
                    />
                  </span>
                </template>
                <template #suffix>
                  <span
                    v-if="!isCollapsed && link.badge"
                    class="crm-sidebar-badge mr-2"
                  >
                    {{ link.badge }}
                  </span>
                </template>
                <Tooltip
                  :text="__(link.label)"
                  placement="right"
                  :hoverDelay="1.5"
                  :disabled="isCollapsed"
                >
                  <span class="truncate text-sm">{{ __(link.label) }}</span>
                </Tooltip>
              </SidebarItem>
            </nav>
          </CollapsibleSection>
        </div>

        <div v-if="!mobile" class="mt-auto flex flex-col gap-1 pt-2">
          <div class="mb-1 flex flex-col gap-2">
            <GettingStartedBanner
              v-if="!isOnboardingStepsCompleted"
              :isSidebarCollapsed="isCollapsed"
            />
          </div>
          <SidebarItem
            v-if="isManager() && isDemoDataCreated"
            :label="__('Clear Demo Data')"
            class="!text-ink-red-6 hover:!bg-surface-red-2"
            @click="() => clearDemoData()"
          >
            <template #prefix>
              <BrushCleaningIcon class="size-4" />
            </template>
          </SidebarItem>
          <SidebarItem
            :label="isCollapsed ? __('Expand') : __('Collapse')"
            @click="isSidebarCollapsed = !isSidebarCollapsed"
          >
            <template #prefix>
              <CollapseSidebar
                class="size-4 text-ink-gray-7 duration-300 ease-in-out"
                :class="{ '[transform:rotateY(180deg)]': isCollapsed }"
              />
            </template>
          </SidebarItem>
        </div>
      </div>
    </Sidebar>
    <Notifications v-if="!mobile" />
  </div>

  <template v-if="!mobile">
    <Settings v-if="showSettings" />
    <IntermediateStepModal
      v-model="showIntermediateModal"
      :currentStep="currentStep"
    />
  </template>
</template>

<script setup>
import BrushCleaningIcon from '~icons/lucide/brush-cleaning'
import LucideImport from '~icons/lucide/import'
import LucideLayoutDashboard from '~icons/lucide/layout-dashboard'
import LucideMap from '~icons/lucide/map'
import LucideReceipt from '~icons/lucide/receipt'
import LucideSun from '~icons/lucide/sun'
import InviteIcon from '@/components/Icons/InviteIcon.vue'
import ConvertIcon from '@/components/Icons/ConvertIcon.vue'
import CommentIcon from '@/components/Icons/CommentIcon.vue'
import EmailIcon from '@/components/Icons/EmailIcon.vue'
import StepsIcon from '@/components/Icons/StepsIcon.vue'
import CollapsibleSection from '@/components/CollapsibleSection.vue'
import Icon from '@/components/Icon.vue'
import PinIcon from '@/components/Icons/PinIcon.vue'
import UserDropdown from '@/components/UserDropdown.vue'
import SquareAsterisk from '@/components/Icons/SquareAsterisk.vue'
import LeadsIcon from '@/components/Icons/LeadsIcon.vue'
import DealsIcon from '@/components/Icons/DealsIcon.vue'
import ContactsIcon from '@/components/Icons/ContactsIcon.vue'
import OrganizationsIcon from '@/components/Icons/OrganizationsIcon.vue'
import NoteIcon from '@/components/Icons/NoteIcon.vue'
import TaskIcon from '@/components/Icons/TaskIcon.vue'
import CalendarIcon from '@/components/Icons/CalendarIcon.vue'
import PhoneIcon from '@/components/Icons/PhoneIcon.vue'
import WhatsAppIcon from '@/components/Icons/WhatsAppIcon.vue'
import CollapseSidebar from '@/components/Icons/CollapseSidebar.vue'
import NotificationsIcon from '@/components/Icons/NotificationsIcon.vue'
import SettingsIcon from '@/components/Icons/SettingsIcon.vue'
import Notifications from '@/components/Notifications.vue'
import { viewsStore } from '@/stores/views'
import {
  unreadNotificationsCount,
  notificationsStore,
} from '@/stores/notifications'
import { usersStore } from '@/stores/users'
import { sessionStore } from '@/stores/session'
import {
  showSettings,
  activeSettingsPage,
  mobileSidebarOpened,
} from '@/composables/settings'
import { showChangePasswordModal } from '@/composables/modals'
import { openCommandPalette } from '@/composables/commandPalette'
import { isWhatsappInstalled } from '@/composables/whatsapp'
import { canUseItineraries } from '@/composables/itinerary'
import { invoicesEnabled } from '@/composables/invoices'
import { todayCount } from '@/composables/today'
import { useBroadcast } from '@/composables/useBroadcast.js'
import { call, Sidebar, SidebarItem, SidebarLabel, Tooltip } from 'frappe-ui'
import {
  GettingStartedBanner,
  useOnboarding,
  minimize,
  IntermediateStepModal,
  useTelemetry,
} from 'frappe-ui/frappe'
import router from '@/router'
import { useStorage } from '@vueuse/core'
import { useDemoData } from '@/composables/demoData'
import {
  ref,
  reactive,
  computed,
  defineAsyncComponent,
  markRaw,
  onMounted,
  watch,
} from 'vue'
import { useRoute } from 'vue-router'

const props = defineProps({
  mobile: { type: Boolean, default: false },
})

const Settings = defineAsyncComponent(
  () => import('@/components/Settings/Settings.vue'),
)

const route = useRoute()

const { user } = sessionStore()
const { getPinnedViews, getPublicViews } = viewsStore()
const { toggle: toggleNotificationPanel } = notificationsStore()
const { capture } = useTelemetry()
const { clearDemoData, isDemoDataCreated } = useDemoData()
const { send } = useBroadcast()

const isSidebarCollapsed = useStorage('isSidebarCollapsed', false)

// Which sidebar groups this user has collapsed, as { [group name]: true }.
// One browser can hold sessions for several sites and several users, so the key
// carries both; anything missing from the map counts as open.
const collapsedGroupsKey = `crm:sidebar-groups:${
  window.site_name || window.location.hostname
}:${user || 'guest'}`
const collapsedGroups = useStorage(collapsedGroupsKey, {})

// The mobile drawer pins the sidebar open, so it is never visually collapsed
// even when the stored rail state says otherwise.
const isCollapsed = computed(() => isSidebarCollapsed.value && !props.mobile)

// The palette answers to both modifiers; the hint shows the one this machine
// actually uses.
const searchShortcutLabel =
  typeof navigator !== 'undefined' &&
  /Mac|iPhone|iPad/.test(navigator.platform || '')
    ? '⌘K'
    : 'Ctrl K'

// Every feature page lives in one of these labelled groups, and every group is
// open on first load, so a new user sees the whole app without expanding
// anything. A group whose entries are all conditioned away is dropped entirely
// rather than left as an empty header.
const linkGroups = [
  {
    name: 'Sales',
    links: [
      {
        // Top of the group on purpose (master spec §5, item 24): it is the
        // Sales User's landing page, so it is also the first thing they see in
        // the nav. `badge` names the ref the suffix slot renders.
        label: 'Today',
        icon: LucideSun,
        to: 'Today',
        badge: 'today',
      },
      {
        label: 'Dashboard',
        icon: LucideLayoutDashboard,
        to: 'Dashboard',
        condition: () => !props.mobile,
      },
      {
        label: 'Leads',
        icon: LeadsIcon,
        to: 'Leads',
      },
      {
        label: 'Deals',
        icon: DealsIcon,
        to: 'Deals',
      },
      {
        label: 'Contacts',
        icon: ContactsIcon,
        to: 'Contacts',
      },
      {
        label: 'Organizations',
        icon: OrganizationsIcon,
        to: 'Organizations',
      },
    ],
  },
  {
    name: 'Work',
    links: [
      {
        label: 'Tasks',
        icon: TaskIcon,
        to: 'Tasks',
      },
      {
        label: 'Notes',
        icon: NoteIcon,
        to: 'Notes',
      },
      {
        label: 'Calendar',
        icon: CalendarIcon,
        to: 'Calendar',
        condition: () => !props.mobile,
      },
      {
        label: 'Call Logs',
        icon: PhoneIcon,
        to: 'Call Logs',
      },
    ],
  },
  {
    name: 'Channels',
    links: [
      {
        label: 'WhatsApp',
        icon: WhatsAppIcon,
        to: 'WhatsApp',
        // The shared inbox is meaningless without the frappe_whatsapp app.
        condition: () => isWhatsappInstalled.value,
      },
    ],
  },
  {
    name: 'Travel',
    links: [
      {
        label: 'Itineraries',
        icon: LucideMap,
        to: 'Itineraries',
        // Hidden from anyone who cannot read an itinerary. The list is row
        // filtered on the server as well, so this only decides what is worth
        // showing.
        condition: () => canUseItineraries.value,
      },
      {
        label: 'Invoices',
        icon: LucideReceipt,
        to: 'Invoices',
        // The module is behind a default-OFF flag (design note item 29,
        // criterion 10). The endpoints refuse on their own while it is off, so
        // this only decides what is worth showing.
        condition: () => invoicesEnabled.value,
      },
    ],
  },
  {
    name: 'More',
    links: [
      {
        label: 'Data Import',
        icon: LucideImport,
        to: 'DataImportList',
      },
      {
        label: 'Settings',
        icon: SettingsIcon,
        key: 'Settings',
        // The settings modal is desktop only — it is not mounted on mobile, and
        // UserDropdown hides its entry there for the same reason.
        condition: () => !props.mobile,
        onClick: () => (showSettings.value = true),
      },
    ],
  },
]

const allViews = computed(() => {
  let _views = linkGroups
    .map((group) => ({
      name: group.name,
      // Feature groups remember their open state; saved-view sections do not.
      storageKey: group.name,
      opened: !collapsedGroups.value[group.name],
      views: group.links
        .filter((link) => {
          if (link.condition) {
            return link.condition()
          }
          return true
        })
        .map((link) => ({
          label: link.label,
          icon: link.icon,
          key: link.key || link.to,
          to: link.to ? { name: link.to } : undefined,
          onClick: link.onClick,
          // 0 renders nothing: a badge that always says "0" is noise, and UX
          // §2.13 keeps the nav from becoming a badge shelf.
          badge: link.badge === 'today' ? todayCount.value : 0,
        })),
    }))
    .filter((group) => group.views.length)

  if (getPublicViews().length) {
    _views.push({
      name: 'Public Views',
      opened: true,
      views: parseView(getPublicViews()),
    })
  }

  if (getPinnedViews().length) {
    _views.push({
      name: 'Pinned Views',
      opened: true,
      views: parseView(getPinnedViews()),
    })
  }
  return _views
})

// CollapsibleSection owns the open/closed ref once it is mounted, so the stored
// value is written here on toggle instead of being watched. `opened` is the
// state *before* the toggle, which is exactly the new collapsed state.
function toggleSection(section, opened, toggle) {
  toggle()
  if (!section.storageKey) return
  collapsedGroups.value = {
    ...collapsedGroups.value,
    [section.storageKey]: opened,
  }
}

function parseView(views) {
  return views.map((view) => {
    return {
      label: view.label,
      icon: getIcon(view.route_name, view.icon),
      key: view.name,
      to: {
        name: view.route_name,
        params: { viewType: view.type || 'list' },
        query: { view: view.name },
      },
    }
  })
}

function getIcon(routeName, icon) {
  if (icon) return icon

  switch (routeName) {
    case 'Leads':
      return LeadsIcon
    case 'Deals':
      return DealsIcon
    case 'Contacts':
      return ContactsIcon
    case 'Organizations':
      return OrganizationsIcon
    case 'Notes':
      return NoteIcon
    case 'Call Logs':
      return PhoneIcon
    default:
      return PinIcon
  }
}

// A saved view's key is its name; a plain nav item's key is its route name.
function currentRouteKey() {
  return route.query.view || route.name
}

// Set the highlight on click rather than waiting for the route, since route
// components are lazily imported and the first visit waits on a chunk fetch.
// Modified clicks open a new tab without navigating this one, so they must not
// move the highlight here.
const activeItem = ref(currentRouteKey())

function selectItem(event, key) {
  if (
    event.metaKey ||
    event.ctrlKey ||
    event.shiftKey ||
    event.altKey ||
    event.button === 1
  ) {
    return
  }
  activeItem.value = key
  // Selecting the row for the route already open leaves the URL unchanged, so
  // the drawer's navigation watcher never fires. Close it here too.
  if (props.mobile) {
    mobileSidebarOpened.value = false
  }
}

// An entry either navigates (saved views and every routed page) or runs an
// action, such as the Settings modal, which must not steal the active highlight.
function onLinkClick(event, link) {
  if (link.onClick) {
    link.onClick()
    if (props.mobile) {
      mobileSidebarOpened.value = false
    }
    return
  }
  selectItem(event, link.key)
}

watch(
  () => [route.name, route.query.view],
  () => (activeItem.value = currentRouteKey()),
)

function onNotificationsClick(event) {
  if (props.mobile) {
    selectItem(event, 'Notifications')
  } else {
    toggleNotificationPanel()
  }
}

// onboarding
const { users, isManager } = usersStore()
const { isOnboardingStepsCompleted, setUp } = useOnboarding('frappecrm')

async function getFirstLead() {
  let firstLead = localStorage.getItem('firstLead' + user)
  if (firstLead) return firstLead
  return await call('crm.api.onboarding.get_first_lead')
}

async function getFirstDeal() {
  let firstDeal = localStorage.getItem('firstDeal' + user)
  if (firstDeal) return firstDeal
  return await call('crm.api.onboarding.get_first_deal')
}

const showIntermediateModal = ref(false)
const currentStep = ref({})

const steps = reactive([
  {
    name: 'setup_your_password',
    title: __('Setup your password'),
    icon: markRaw(SquareAsterisk),
    completed: false,
    onClick: () => {
      minimize.value = true
      showChangePasswordModal.value = true
      capture('onboarding_step_clicked_setup_password')
    },
  },
  {
    name: 'create_first_lead',
    title: __('Create your first lead'),
    icon: markRaw(LeadsIcon),
    completed: false,
    onClick: () => {
      minimize.value = true
      router.push({ name: 'Leads' })
      send('trigger_lead_create', true)
      capture('onboarding_step_clicked_create_first_lead')
    },
  },
  {
    name: 'invite_your_team',
    title: __('Invite your team'),
    icon: markRaw(InviteIcon),
    completed: false,
    onClick: () => {
      minimize.value = true
      showSettings.value = true
      activeSettingsPage.value = 'Invite User'
      capture('onboarding_step_clicked_invite_your_team')
    },
    condition: () => isManager(),
  },
  {
    name: 'convert_lead_to_deal',
    title: __('Convert lead to deal'),
    icon: markRaw(ConvertIcon),
    completed: false,
    dependsOn: 'create_first_lead',
    onClick: async () => {
      minimize.value = true
      capture('onboarding_step_clicked_convert_lead_to_deal')
      currentStep.value = {
        title: __('Convert lead to deal'),
        buttonLabel: __('Convert'),
        videoURL: '/assets/crm/videos/convertToDeal.mov',
        onClick: async () => {
          showIntermediateModal.value = false
          currentStep.value = {}

          let lead = await getFirstLead()
          if (lead) {
            router.push({ name: 'Lead', params: { leadId: lead } })
          } else {
            router.push({ name: 'Leads' })
          }
        },
      }
      showIntermediateModal.value = true
    },
  },
  {
    name: 'create_first_task',
    title: __('Create your first task'),
    icon: markRaw(TaskIcon),
    completed: false,
    onClick: async () => {
      minimize.value = true
      let deal = await getFirstDeal()
      capture('onboarding_step_clicked_create_first_task')

      if (deal) {
        router.push({
          name: 'Deal',
          params: { dealId: deal },
          hash: '#tasks',
        })
      } else {
        router.push({ name: 'Tasks' })
      }
    },
  },
  {
    name: 'create_first_note',
    title: __('Create your first note'),
    icon: markRaw(NoteIcon),
    completed: false,
    onClick: async () => {
      minimize.value = true
      let deal = await getFirstDeal()
      capture('onboarding_step_clicked_create_first_note')

      if (deal) {
        router.push({
          name: 'Deal',
          params: { dealId: deal },
          hash: '#notes',
        })
      } else {
        router.push({ name: 'Notes' })
      }
    },
  },
  {
    name: 'add_first_comment',
    title: __('Add your first comment'),
    icon: markRaw(CommentIcon),
    completed: false,
    dependsOn: 'create_first_lead',
    onClick: async () => {
      minimize.value = true
      let deal = await getFirstDeal()
      capture('onboarding_step_clicked_add_first_comment')

      if (deal) {
        router.push({
          name: 'Deal',
          params: { dealId: deal },
          hash: '#comments',
        })
      } else {
        router.push({ name: 'Leads' })
      }
    },
  },
  {
    name: 'send_first_email',
    title: __('Send email'),
    icon: markRaw(EmailIcon),
    completed: false,
    dependsOn: 'create_first_lead',
    onClick: async () => {
      minimize.value = true
      let deal = await getFirstDeal()
      capture('onboarding_step_clicked_send_first_email')

      if (deal) {
        router.push({
          name: 'Deal',
          params: { dealId: deal },
          hash: '#emails',
        })
      } else {
        router.push({ name: 'Leads' })
      }
    },
  },
  {
    name: 'change_deal_status',
    title: __('Change deal status'),
    icon: markRaw(StepsIcon),
    completed: false,
    dependsOn: 'convert_lead_to_deal',
    onClick: async () => {
      minimize.value = true
      capture('onboarding_step_clicked_change_deal_status')

      currentStep.value = {
        title: __('Change deal status'),
        buttonLabel: __('Change'),
        videoURL: '/assets/crm/videos/changeDealStatus.mov',
        onClick: async () => {
          showIntermediateModal.value = false
          currentStep.value = {}

          let deal = await getFirstDeal()
          if (deal) {
            router.push({
              name: 'Deal',
              params: { dealId: deal },
              hash: '#activity',
            })
          } else {
            router.push({ name: 'Leads' })
          }
        },
      }
      showIntermediateModal.value = true
    },
  },
])

onMounted(async () => {
  if (props.mobile) return

  await users.promise

  const filteredSteps = steps.filter((step) => {
    if (step.condition) {
      return step.condition()
    }
    return true
  })

  setUp(filteredSteps)
})
</script>
