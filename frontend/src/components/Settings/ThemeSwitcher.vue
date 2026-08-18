<template>
  <div class="flex gap-3">
    <div
      class="flex-1 rounded-lg border cursor-pointer"
      :class="
        theme == 'light'
          ? 'border-outline-gray-7'
          : 'border-outline-elevation-2'
      "
      @click="theme = 'light'"
    >
      <div class="pl-5 pt-3.5 bg-surface-gray-2 rounded-t-[10.5px]">
        <div class="theme-preview-light bg-surface-base rounded-tl-sm">
          <div
            class="flex gap-[3px] py-[3px] px-1 border-b border-outline-gray-1"
          >
            <div class="size-1.5 bg-[#FF5F57] rounded-full"></div>
            <div class="size-1.5 bg-[#FEBC2D] rounded-full"></div>
            <div class="size-1.5 bg-[#28C840] rounded-full"></div>
          </div>
          <div class="flex items-start justify-between gap-2 p-2.5 pr-0 pb-1">
            <div
              class="flex items-center flex-1 gap-1 text-xs-semibold text-ink-gray-5"
            >
              <img
                v-if="typeof logo == 'string'"
                :src="logo"
                class="size-5 object-cover"
              />
              <component :is="logo" v-else class="size-5 shrink-0 rounded" />
              <div>{{ __(name) }}</div>
            </div>
            <div class="flex flex-col flex-1 gap-[5px]">
              <div class="bg-surface-blue-6 w-full h-1.5"></div>
              <div class="bg-surface-gray-3 w-full h-1.5"></div>
              <div class="bg-surface-gray-3 w-full h-1.5"></div>
            </div>
          </div>
        </div>
      </div>
      <div class="flex items-center justify-between px-3 py-2 border-t">
        <div class="text-base text-ink-gray-7">
          {{ __('Light') }}
        </div>
        <div
          class="rounded-full size-3.5"
          :class="
            theme == 'light'
              ? 'border-4 border-outline-gray-7'
              : 'border  border-outline-gray-4'
          "
        />
      </div>
    </div>
    <div
      class="flex-1 rounded-lg border cursor-pointer"
      :class="
        theme == 'dark' ? 'border-outline-gray-7' : 'border-outline-elevation-2'
      "
      @click="theme = 'dark'"
    >
      <div class="pl-5 pt-3.5 bg-surface-gray-2 rounded-t-[10.5px]">
        <div class="theme-preview-dark bg-surface-base rounded-tl-sm">
          <div
            class="flex gap-[3px] py-[3px] px-1 border-b border-outline-gray-1"
          >
            <div class="size-1.5 bg-[#FF5F57] rounded-full"></div>
            <div class="size-1.5 bg-[#FEBC2D] rounded-full"></div>
            <div class="size-1.5 bg-[#28C840] rounded-full"></div>
          </div>
          <div class="flex items-start justify-between gap-2 p-2.5 pr-0 pb-1">
            <div
              class="flex items-center flex-1 gap-1 text-xs-semibold text-ink-gray-5"
            >
              <img
                v-if="typeof logo == 'string'"
                :src="logo"
                class="size-5 object-cover"
              />
              <component :is="logo" v-else class="size-5 shrink-0 rounded" />
              <div>{{ __(name) }}</div>
            </div>
            <div class="flex flex-col flex-1 gap-[5px]">
              <div class="bg-surface-blue-6 w-full h-1.5"></div>
              <div class="bg-surface-gray-3 w-full h-1.5"></div>
              <div class="bg-surface-gray-3 w-full h-1.5"></div>
            </div>
          </div>
        </div>
      </div>
      <div class="flex items-center justify-between px-3 py-2 border-t">
        <div class="text-base text-ink-gray-7">
          {{ __('Dark') }}
        </div>
        <div
          class="rounded-full size-3.5"
          :class="
            theme == 'dark'
              ? 'border-4 border-outline-gray-7'
              : 'border border-outline-gray-4'
          "
        />
      </div>
    </div>
    <div
      class="flex-1 rounded-lg border cursor-pointer"
      :class="
        theme == 'system'
          ? 'border-outline-gray-7'
          : 'border-outline-elevation-2'
      "
      @click="theme = 'system'"
    >
      <div class="flex">
        <div
          class="flex flex-1 pl-5 pt-3.5 bg-surface-gray-2 rounded-tl-[10.5px]"
        >
          <div class="theme-preview-light bg-surface-base rounded-tl-sm w-full">
            <div
              class="flex gap-[3px] py-[3px] px-1 border-b border-outline-gray-1"
            >
              <div class="size-1.5 bg-[#FF5F57] rounded-full"></div>
              <div class="size-1.5 bg-[#FEBC2D] rounded-full"></div>
              <div class="size-1.5 bg-[#28C840] rounded-full"></div>
            </div>
            <div class="flex items-start justify-between gap-2 p-2.5 pr-0 pb-1">
              <div
                class="flex items-center flex-1 gap-1 text-xs-semibold text-ink-gray-5"
              >
                <img
                  v-if="typeof logo == 'string'"
                  :src="logo"
                  class="size-5 object-cover"
                />
                <component :is="logo" v-else class="size-5 shrink-0 rounded" />
                <div>{{ __(name) }}</div>
              </div>
            </div>
          </div>
        </div>
        <div
          class="flex flex-1 pl-5 pt-3.5 bg-surface-gray-3 rounded-tr-[10.5px]"
        >
          <div class="theme-preview-dark bg-surface-base rounded-tl-sm w-full">
            <div
              class="flex gap-[3px] py-[3px] px-1 border-b border-outline-gray-1"
            >
              <div class="size-1.5 bg-[#FF5F57] rounded-full"></div>
              <div class="size-1.5 bg-[#FEBC2D] rounded-full"></div>
              <div class="size-1.5 bg-[#28C840] rounded-full"></div>
            </div>
            <div class="flex items-start justify-between gap-2 p-2.5 pb-3 pr-0">
              <div
                class="flex items-center flex-1 gap-1 text-xs-semibold text-ink-gray-5"
              >
                <img
                  v-if="typeof logo == 'string'"
                  :src="logo"
                  class="size-5 object-cover"
                />
                <component :is="logo" v-else class="size-5 shrink-0 rounded" />
                <div>{{ __(name) }}</div>
              </div>
            </div>
          </div>
        </div>
      </div>
      <div class="flex items-center justify-between px-3 py-2 border-t">
        <div class="text-base text-ink-gray-7">
          {{ __('System') }}
        </div>
        <div
          class="rounded-full size-3.5"
          :class="
            theme == 'system'
              ? 'border-4 border-outline-gray-7'
              : 'border border-outline-gray-4'
          "
        ></div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useTheme } from 'frappe-ui'
import { computed } from 'vue'

defineProps({
  logo: { type: [String, Object], default: '' },
  name: { type: String, default: '' },
})

const { currentTheme, setTheme } = useTheme()

const theme = computed({
  get() {
    if (currentTheme.value === 'light') return 'light'
    if (currentTheme.value === 'dark') return 'dark'
    return 'system'
  },
  set(value) {
    setTheme(value)
  },
})
</script>

<style scoped>
/* The two mini-windows must show the light palette AND the dark palette at the
   same time, whatever theme the app is currently in, so they cannot read the
   global tokens (those flip together). Instead each preview redefines the
   handful of tokens it draws with, exactly as `.crm-sidebar` and `.crm-chat` do
   in index.css. Every value below is copied from the matching declaration in
   src/index.css, so the preview always shows the real product palette. */
.theme-preview-light {
  --surface-base: #ffffff; /* index.css: light --surface-base stays white */
  --surface-gray-3: #ebebf5; /* index.css light --surface-gray-3 */
  --surface-blue-6: #4f46e5; /* index.css light --surface-blue-6 (indigo primary) */
  --ink-gray-5: #787882; /* index.css light --ink-gray-5 */
  --outline-gray-1: #e8e8f3; /* index.css light --outline-gray-1 */
}

.theme-preview-dark {
  --surface-base: #16171b; /* index.css dark --surface-base */
  --surface-gray-3: #36373f; /* index.css dark --surface-gray-3 */
  --surface-blue-6: #818cf8; /* index.css dark indigo accent (--ink-blue-link) */
  --ink-gray-5: #7a7a7a; /* frappe-ui dark --ink-gray-5 (not re-themed) */
  --outline-gray-1: #242424; /* frappe-ui dark --outline-gray-1 (not re-themed) */
}
</style>
