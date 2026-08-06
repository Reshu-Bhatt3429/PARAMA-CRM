<template>
  <div class="flex h-screen w-screen">
    <AppSidebar />
    <!-- app-canvas, not bg-surface-base: `--surface-base` also backs every
         white card and input, so the lavender page tint needs its own token. -->
    <div class="flex-1 flex flex-col h-full overflow-auto app-canvas">
      <AppHeader />
      <slot />
    </div>
    <GlobalModals />
  </div>
</template>
<script setup>
import AppSidebar from '@/components/Layouts/AppSidebar.vue'
import AppHeader from '@/components/Layouts/AppHeader.vue'
import GlobalModals from '@/components/Modals/GlobalModals.vue'
</script>
