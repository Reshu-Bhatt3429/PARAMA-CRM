<template>
  <SettingsLayoutBase
    v-if="user.doc"
    :title="__('Preferences')"
    :description="
      __(
        'Choose how you want to use the application by setting your preferences.',
      )
    "
  >
    <template #content>
      <div>
        <div class="flex items-center justify-between">
          <div class="flex gap-2 items-center">
            <div class="text-base-semibold text-ink-gray-9">
              {{ __('Appearance') }}
            </div>
          </div>
        </div>
        <div class="flex flex-col gap-4 my-6">
          <div class="flex flex-col gap-1">
            <span class="text-base-medium text-ink-gray-8">
              {{ __('Theme') }}
            </span>
            <span class="text-p-sm text-ink-gray-6">
              {{ __('Switch between light, dark, or system theme') }}
            </span>
          </div>
          <ThemeSwitcher
            :logo="brand.logo || CRMLogo"
            :name="brand.name || 'PARAMA CRM'"
          />
        </div>
        <div class="flex items-center justify-between">
          <div class="flex gap-2 items-center h-7">
            <div class="text-base-semibold text-ink-gray-9">
              {{ __('Language & Time') }}
            </div>
            <Badge
              v-if="isDirty"
              :variant="'subtle'"
              :theme="'orange'"
              size="sm"
              :label="__('Not Saved')"
            />
          </div>
          <Button
            v-if="isDirty"
            :label="__('Save')"
            :loading="user.save.loading"
            @click="save()"
          />
        </div>
        <div class="flex items-center justify-between mt-6">
          <div class="flex flex-col gap-1">
            <span class="text-base-medium text-ink-gray-8">
              {{ __('Language') }}
            </span>
            <span class="text-p-sm text-ink-gray-6">
              {{ __('Change language of the application.') }}
            </span>
          </div>
          <Link v-model="user.doc.language" doctype="Language" class="w-40" />
        </div>
        <div class="flex items-center justify-between mt-6">
          <div class="flex flex-col gap-1">
            <span class="text-base-medium text-ink-gray-8">
              {{ __('Timezone') }}
            </span>
            <span class="text-p-sm text-ink-gray-6">
              {{ __('Change timezone of the application.') }}
            </span>
          </div>
          <Combobox
            v-model="user.doc.time_zone"
            class="w-40"
            :options="getTimezoneOptions()"
          />
        </div>

        <!--
          Per-user notification switches (master spec §5 item 22). They live on
          this page because they are this person's choice, not the agency's —
          the site-wide digest settings stay in Settings → AI & Follow-ups.
          Each switch saves on change; there is nothing to press Save for.
        -->
        <div v-if="preferenceRows.length" class="mt-8">
          <div class="text-base-semibold text-ink-gray-9">
            {{ __('Notifications') }}
          </div>
          <div
            v-for="row in preferenceRows"
            :key="row.key"
            class="flex items-center justify-between gap-8 mt-6"
          >
            <div class="flex flex-col gap-1">
              <span class="text-base-medium text-ink-gray-8">
                {{ row.label }}
              </span>
              <span class="text-p-sm text-ink-gray-6">
                {{ row.description }}
              </span>
            </div>
            <FormControl
              type="checkbox"
              :modelValue="row.value"
              @update:modelValue="(value) => savePreference(row.key, value)"
            />
          </div>
        </div>
      </div>
    </template>
  </SettingsLayoutBase>
</template>

<script setup>
import CRMLogo from '@/components/Icons/CRMLogo.vue'
import ThemeSwitcher from '@/components/Settings/ThemeSwitcher.vue'
import SettingsLayoutBase from '@/components/Layouts/SettingsLayoutBase.vue'
import Link from '@/components/Controls/Link.vue'
import { useKeyboardShortcuts } from '@/composables/useKeyboardShortcuts'
import { getSettings } from '@/stores/settings'
import {
  Combobox,
  Badge,
  FormControl,
  call,
  toast,
  createResource,
  createDocumentResource,
} from 'frappe-ui'
import { ref, computed, inject } from 'vue'

const refreshRequired = ref(false)

const { user: sessionUser } = inject('session')

const { brand } = getSettings()
const user = createDocumentResource({ doctype: 'User', name: sessionUser })

function save() {
  refreshRequired.value =
    user.doc.language !== user.originalDoc?.language ||
    user.doc.time_zone !== user.originalDoc?.time_zone

  user.save.submit(null, {
    onSuccess: () => {
      toast.success(__('Preferences updated successfully'))
      if (refreshRequired.value) {
        window.location.reload()
      }
    },
    onError: (err) => {
      toast.error(err.message + ': ' + err.messages[0])
    },
  })
}

const isDirty = computed(() => {
  return JSON.stringify(user.doc) !== JSON.stringify(user.originalDoc)
})

const timeZones = createResource({
  url: 'frappe.core.doctype.user.user.get_timezones',
  cache: 'TimeZones',
  auto: true,
})

function getTimezoneOptions() {
  return timeZones.data?.timezones.map((tz) => ({ label: tz, value: tz })) || []
}

// --- per-user preferences --------------------------------------------------
//
// The list is built from the server's registry rather than hard-coded here, so
// the next preference someone adds to `CRM User Preference` appears on this page
// without a second edit.

const preferences = createResource({
  url: 'crm.fcrm.doctype.crm_user_preference.crm_user_preference.get_my_preferences',
  auto: true,
})

const preferenceRows = computed(() => {
  const data = preferences.data
  if (!data?.registry) return []
  return Object.entries(data.registry).map(([key, entry]) => ({
    key,
    label: entry.label,
    description: entry.description,
    value: Boolean(data.values?.[key]),
  }))
})

async function savePreference(key, value) {
  // Paint first, then persist: a switch that waits for a round trip before it
  // moves feels broken. A failure puts it back and says so.
  const previous = preferences.data.values[key]
  preferences.data.values[key] = Boolean(value)
  try {
    await call(
      'crm.fcrm.doctype.crm_user_preference.crm_user_preference.set_my_preference',
      { key, value: value ? 1 : 0 },
    )
  } catch (error) {
    preferences.data.values[key] = previous
    toast.error(error.messages?.[0] || __('Could not save this preference'))
  }
}

useKeyboardShortcuts({
  ignoreTyping: false,
  shortcuts: [
    {
      match: (e) => (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's',
      action: () => {
        if (isDirty.value) {
          save()
        }
      },
    },
  ],
})
</script>
