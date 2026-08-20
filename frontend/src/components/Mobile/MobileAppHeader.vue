<template>
  <div class="flex pr-3">
    <div class="z-20 ml-2 flex items-center justify-center">
      <Button
        class="size-7"
        variant="ghost"
        @click="sidebarOpened = !sidebarOpened"
      >
        <MenuIcon class="h-4 text-ink-gray-9" />
      </Button>
    </div>
    <div id="app-header" class="flex-1" />
    <!-- Spec §2.18: on mobile the palette opens from a top-bar search button,
         because there is no keyboard shortcut to reach it with. -->
    <div class="z-20 flex items-center justify-center">
      <Button
        class="size-7"
        variant="ghost"
        :aria-label="__('Search')"
        @click="openCommandPalette"
      >
        <span class="lucide-search size-4 text-ink-gray-9" aria-hidden="true" />
      </Button>
    </div>
  </div>
  <CallUI class="mr-3 mt-2" />
</template>

<script setup>
import MenuIcon from '@/components/Icons/MenuIcon.vue'
import CallUI from '@/components/Telephony/CallUI.vue'
import { mobileSidebarOpened as sidebarOpened } from '@/composables/settings'
import { openCommandPalette } from '@/composables/commandPalette'
</script>
