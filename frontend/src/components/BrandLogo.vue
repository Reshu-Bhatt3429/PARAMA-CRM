<template>
  <div v-if="safeLogo">
    <img :src="safeLogo" class="h-full w-full object-cover" />
  </div>
  <CRMLogo v-else class="size-8 shrink-0 rounded" />
</template>

<script setup>
import CRMLogo from '@/components/Icons/CRMLogo.vue'
import { getSafeHttpUrl } from '@/utils/safeUrl'
import { computed } from 'vue'

const brand = defineModel({ type: Object, default: () => ({}) })
const safeLogo = computed(() => getSafeHttpUrl(brand.value?.logo))
</script>
