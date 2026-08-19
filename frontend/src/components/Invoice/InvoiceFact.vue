<template>
  <div class="flex items-start gap-3">
    <span class="w-36 shrink-0 text-ink-gray-5">{{ label }}</span>
    <span class="min-w-0 flex-1 text-ink-gray-8"><slot /></span>
  </div>
</template>

<script setup>
/**
 * One label and one value on the invoice's read-only face.
 *
 * A component rather than a repeated pair of spans so the label column is one
 * width everywhere and the two blocks line up across the fold.
 */
defineProps({
  label: { type: String, required: true },
})
</script>
