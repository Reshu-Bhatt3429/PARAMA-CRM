<template>
  <div class="flex border-b pr-5">
    <div id="app-header" class="flex-1"></div>
    <div class="flex items-center justify-center">
      <CallUI v-if="callEnabled" />
    </div>
  </div>
</template>

<script setup>
import { callEnabled } from '@/composables/telephony'
import { defineAsyncComponent } from 'vue'

const CallUI = defineAsyncComponent(
  () => import('@/components/Telephony/CallUI.vue'),
)
</script>
