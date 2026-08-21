<template>
  <span>
    <a
      :href="isShowable ? null : safeUrl"
      target="_blank"
      rel="noopener noreferrer"
    >
      <Button
        :label="label"
        theme="gray"
        variant="outline"
        :iconLeft="getIcon()"
        @click="toggleDialog()"
      >
        <template #suffix>
          <slot name="suffix" />
        </template>
      </Button>
    </a>
    <Dialog v-model:open="showDialog" :title="label" :size="'4xl'">
      <template #default>
        <div
          v-if="isText"
          class="prose prose-sm max-w-none whitespace-pre-wrap"
        >
          {{ content }}
        </div>
        <img
          v-if="isImage && safeUrl"
          :src="safeUrl"
          class="m-auto rounded border"
        />
      </template>
    </Dialog>
  </span>
</template>

<script setup>
import { computed, ref } from 'vue'
import mime from 'mime'
import { getSafeHttpUrl } from '@/utils/safeUrl'
import FileTypeIcon from '@/components/Icons/FileTypeIcon.vue'
import FileImageIcon from '@/components/Icons/FileImageIcon.vue'
import FileTextIcon from '@/components/Icons/FileTextIcon.vue'
import FileSpreadsheetIcon from '@/components/Icons/FileSpreadsheetIcon.vue'
import FileIcon from '@/components/Icons/FileIcon.vue'

const props = defineProps({
  label: { type: String, default: null },
  url: { type: String, default: null },
})

const showDialog = ref(false)
const mimeType = mime.getType(props.label) || ''
const isImage = mimeType.startsWith('image/')
const isPdf = mimeType === 'application/pdf'
const isSpreadsheet = mimeType.includes('spreadsheet')
const isText = mimeType === 'text/plain'
const safeUrl = computed(() => getSafeHttpUrl(props.url))
const isShowable = computed(() => safeUrl.value && (isText || isImage))
const content = ref('')

function getIcon() {
  if (isText) return FileTypeIcon
  else if (isImage) return FileImageIcon
  else if (isPdf) return FileTextIcon
  else if (isSpreadsheet) return FileSpreadsheetIcon
  else return FileIcon
}

function toggleDialog() {
  if (!isShowable.value) return
  if (isText) {
    fetch(safeUrl.value).then((res) =>
      res.text().then((text) => (content.value = text)),
    )
  }
  showDialog.value = !showDialog.value
}
</script>
