<template>
  <div class="media-field">
    <span class="field-label">{{ label }}</span>
    <div v-if="value" class="preview" :class="{ logo }">
      <img :src="value" :alt="label" />
    </div>
    <div class="actions">
      <FileUploader
        file-types="image/*"
        :upload-args="uploadArgs"
        @success="(file) => emit('upload', file.file_url)"
      >
        <template #default="{ openFileSelector, uploading, progress }">
          <Button
            :label="uploading ? __('Uploading {0}%', [progress]) : __('Upload')"
            iconLeft="image-up"
            :loading="uploading"
            @click="openFileSelector"
          />
        </template>
      </FileUploader>
      <Button
        v-if="editableValue || value"
        :label="__('Remove')"
        @click="emit('remove')"
      />
    </div>
  </div>
</template>

<script setup>
import { Button, FileUploader } from 'frappe-ui'

defineProps({
  label: { type: String, required: true },
  value: { type: String, default: '' },
  editableValue: { type: String, default: undefined },
  logo: { type: Boolean, default: false },
  uploadArgs: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['upload', 'remove'])
</script>

<style scoped>
.media-field {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 8px;
}
.field-label {
  color: var(--ink-gray-6);
  font-size: 12px;
  font-weight: 500;
}
.preview {
  height: 120px;
  overflow: hidden;
  border-radius: 8px;
  background: var(--surface-gray-2);
}
.preview img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.preview.logo {
  display: grid;
  place-items: center;
}
.preview.logo img {
  width: auto;
  max-width: 80%;
  height: auto;
  max-height: 74px;
  object-fit: contain;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
</style>
