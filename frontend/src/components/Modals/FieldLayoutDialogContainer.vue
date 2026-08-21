<template>
  <FieldLayoutDialog
    v-for="dialog in fieldLayoutDialogs"
    :key="dialog.key"
    v-bind="dialogProps(dialog)"
    @resolve="dialog.props.onResolve"
  />
</template>

<script setup>
import { fieldLayoutDialogs } from '@/utils/renderFieldLayoutDialog'
import { defineAsyncComponent } from 'vue'

const FieldLayoutDialog = defineAsyncComponent(
  () => import('@/components/Modals/FieldLayoutDialog.vue'),
)

function dialogProps(dialog) {
  // Extract onResolve so it's only attached via @resolve, not doubled via v-bind
  const { onResolve: _onResolve, ...rest } = dialog.props
  return rest
}
</script>
