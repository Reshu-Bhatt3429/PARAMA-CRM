<template>
  <Dialog v-model:open="show" :size="'sm'">
    <template #body>
      <div class="p-4 pt-5">
        <div class="flex justify-center">
          <div class="flex flex-col items-center">
            <CRMLogo class="mb-3 size-12 text-ink-gray-9" />
            <h3 class="text-2xl-semibold text-ink-gray-9">PARAMA CRM</h3>
          </div>
        </div>
        <p class="mt-3 px-2 text-center text-sm text-ink-gray-6">
          {{
            __(
              'Travel sales, customer communication, itineraries, and follow-ups in one workspace.',
            )
          }}
        </p>
      </div>
    </template>
  </Dialog>
</template>
<script setup>
import CRMLogo from '@/components/Icons/CRMLogo.vue'

let show = defineModel({ type: Boolean })
</script>
