<template>
  <div class="list-editor">
    <div class="list-header">
      <strong>{{ label }}</strong>
      <Button icon="plus" :tooltip="__('Add item')" @click="add" />
    </div>
    <div v-for="(_, index) in model" :key="index" class="list-row">
      <TextInput
        v-model="model[index]"
        type="text"
        :placeholder="placeholder"
        @change="emit('save')"
      />
      <Button
        icon="arrow-up"
        :tooltip="__('Move up')"
        :disabled="index === 0"
        @click="move(index, -1)"
      />
      <Button
        icon="arrow-down"
        :tooltip="__('Move down')"
        :disabled="index === model.length - 1"
        @click="move(index, 1)"
      />
      <Button icon="trash-2" :tooltip="__('Remove')" @click="remove(index)" />
    </div>
    <p v-if="!model.length">{{ __('No items yet.') }}</p>
  </div>
</template>

<script setup>
import { Button, TextInput } from 'frappe-ui'

defineProps({
  label: { type: String, required: true },
  placeholder: { type: String, default: '' },
})
const model = defineModel({ type: Array, default: () => [] })
const emit = defineEmits(['save'])

function add() {
  model.value.push('')
}
function remove(index) {
  model.value.splice(index, 1)
  emit('save')
}
function move(index, offset) {
  const target = index + offset
  if (target < 0 || target >= model.value.length) return
  ;[model.value[index], model.value[target]] = [
    model.value[target],
    model.value[index],
  ]
  emit('save')
}
</script>

<style scoped>
.list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  color: var(--ink-gray-7);
  font-size: 12px;
}
.list-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto auto;
  gap: 5px;
  margin-bottom: 7px;
}
p {
  padding: 16px 0;
  color: var(--ink-gray-4);
  font-size: 12px;
}
</style>
