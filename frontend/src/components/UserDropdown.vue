<template>
  <Dropdown :options="dropdownItems" v-bind="$attrs">
    <template #default="{ open }">
      <button
        class="flex h-12 items-center rounded-md py-2 duration-300 ease-in-out"
        :class="
          isCollapsed
            ? 'w-auto px-0'
            : open
              ? 'w-full px-2 bg-surface-elevation-3 shadow-sm'
              : 'w-full px-2 hover:bg-surface-gray-2'
        "
      >
        <BrandLogo v-model="brand" class="h-8 max-w-16 flex-shrink-0" />
        <div
          class="flex flex-1 flex-col text-left duration-300 ease-in-out truncate"
          :class="
            isCollapsed
              ? 'ml-0 w-0 overflow-hidden opacity-0'
              : 'ml-2 w-auto opacity-100'
          "
        >
          <div class="text-base-medium leading-none text-ink-gray-9 truncate">
            {{ __(brand.name || 'PARAMA CRM') }}
          </div>
          <div class="mt-1 text-sm leading-none text-ink-gray-7 truncate">
            {{ user.full_name }}
          </div>
        </div>
        <div
          class="duration-300 ease-in-out"
          :class="
            isCollapsed
              ? 'ml-0 w-0 overflow-hidden opacity-0'
              : 'ml-2 w-auto opacity-100'
          "
        >
          <span
            class="lucide-chevron-down size-4 text-ink-gray-5"
            aria-hidden="true"
          />
        </div>
      </button>
    </template>
  </Dropdown>
</template>

<script setup>
import BrandLogo from '@/components/BrandLogo.vue'
import AppsIcon from '@/components/Icons/AppsIcon.vue'
import { sessionStore } from '@/stores/session'
import { usersStore } from '@/stores/users'
import { getSettings } from '@/stores/settings'
import { showSettings, isMobileView } from '@/composables/settings'
import { showAboutModal } from '@/composables/modals'
import { sanitizeHTML } from '@/utils'
import { openSafeUrl } from '@/utils/safeUrl'
import { createResource, Dropdown } from 'frappe-ui'
import { computed, h, markRaw } from 'vue'

defineProps({
  isCollapsed: { type: Boolean, default: false },
})

const { settings, brand } = getSettings()
const { logout } = sessionStore()
const { getUser } = usersStore()

const user = computed(() => getUser() || {})

const apps = createResource({
  url: 'frappe.apps.get_apps',
  cache: 'apps',
  auto: true,
  transform: (data) => crmSiblingApps(data),
})

const dropdownItems = computed(() => {
  if (!settings.value?.dropdown_items) return []

  let items = settings.value.dropdown_items

  let _dropdownItems = [
    {
      group: 'Dropdown Items',
      hideLabel: true,
      items: [],
    },
  ]

  items.forEach((item) => {
    if (item.hidden) return
    if (item.type !== 'Separator') {
      const dropdownItem = dropdownItemObj(item)
      if (dropdownItem) {
        _dropdownItems[_dropdownItems.length - 1].items.push(dropdownItem)
      }
    } else {
      _dropdownItems.push({
        group: '',
        hideLabel: true,
        items: [],
      })
    }
  })

  return _dropdownItems
})

function dropdownItemObj(item) {
  let _item = JSON.parse(JSON.stringify(item))
  let icon = _item.icon || 'external-link'
  if (typeof icon === 'string' && icon.startsWith('<svg')) {
    // The icon markup comes from a doc field, so it is sanitized before it is
    // injected: `startsWith('<svg')` alone lets scripted SVG attributes through.
    icon = markRaw(h('div', { innerHTML: sanitizeHTML(icon) }))
  }
  _item.icon = icon

  if (_item.is_standard) {
    return getStandardItem(_item)
  }

  if (isRemovedDestination(_item.route)) return null

  return {
    icon: _item.icon,
    label: __(_item.label),
    onClick: () =>
      openSafeUrl(_item.route, {
        target: _item.open_in_new_window ? '_blank' : '_self',
      }),
  }
}

function getStandardItem(item) {
  switch (item.name1) {
    case 'app_selector':
      return {
        icon: markRaw(AppsIcon),
        label: __(item.label),
        submenu: appMenuItems(),
        condition: () => Boolean(apps.data?.length),
      }
    case 'settings':
      return {
        icon: item.icon,
        label: __(item.label),
        onClick: () => (showSettings.value = true),
        condition: () => !isMobileView.value,
      }
    case 'about':
      return {
        icon: item.icon,
        label: __(item.label),
        onClick: () => (showAboutModal.value = true),
      }
    case 'logout':
      return {
        icon: item.icon,
        label: __(item.label),
        onClick: () => logout.submit(),
      }
  }
  return null
}

function appMenuItems() {
  return (apps.data || []).map((app) => ({
    label: app.title,
    onClick: () => openSafeUrl(app.route, { target: '_self' }),
    slots: {
      prefix: () => h('img', { class: 'size-5 rounded', src: app.logo }),
    },
  }))
}

function crmSiblingApps(data) {
  return data
    .filter(
      (app) =>
        !['crm', 'frappe'].includes(app.name) &&
        !String(app.title || '')
          .toLowerCase()
          .includes('frappe'),
    )
    .map((app) => ({
      name: app.name,
      logo: app.logo,
      title: __(app.title),
      route: app.route,
    }))
}

function isRemovedDestination(route) {
  const value = String(route || '').toLowerCase()
  return (
    value.includes('frappe.io') ||
    value.includes('frappecloud.com') ||
    value.includes('github.com/frappe/')
  )
}
</script>
