<template>
  <CreateDocumentModal
    v-if="showCreateDocumentModal"
    v-model="showCreateDocumentModal"
    :doctype="createDocumentDoctype"
    :data="createDocumentData"
    @callback="(data) => createDocumentCallback(data)"
  />
  <QuickEntryModal
    v-if="showQuickEntryModal"
    v-model="showQuickEntryModal"
    v-bind="quickEntryProps"
  />
  <ChangePasswordModal
    v-if="showChangePasswordModal"
    v-model="showChangePasswordModal"
  />
  <AboutModal v-if="showAboutModal" v-model="showAboutModal" />
  <FieldLayoutDialogContainer />
  <!-- Mounted here, once, because it is reached from three places that live in
       different trees: the desktop sidebar button, the mobile top-bar button
       and the global Cmd/Ctrl+K shortcut. -->
  <CommandPalette v-if="showCommandPalette" />
</template>
<script setup>
import { defineAsyncComponent } from 'vue'
import { useKeyboardShortcuts } from '@/composables/useKeyboardShortcuts'
import { showCommandPalette } from '@/composables/commandPalette'
import {
  showCreateDocumentModal,
  createDocumentDoctype,
  createDocumentData,
  createDocumentCallback,
} from '@/composables/document'
import {
  showQuickEntryModal,
  quickEntryProps,
  showAboutModal,
  showChangePasswordModal,
} from '@/composables/modals'

const FieldLayoutDialogContainer = defineAsyncComponent(
  () => import('@/components/Modals/FieldLayoutDialogContainer.vue'),
)
const ChangePasswordModal = defineAsyncComponent(
  () => import('@/components/Modals/ChangePasswordModal.vue'),
)
const CreateDocumentModal = defineAsyncComponent(
  () => import('@/components/Modals/CreateDocumentModal.vue'),
)
const QuickEntryModal = defineAsyncComponent(
  () => import('@/components/Modals/QuickEntryModal.vue'),
)
const AboutModal = defineAsyncComponent(
  () => import('@/components/Modals/AboutModal.vue'),
)
const CommandPalette = defineAsyncComponent(
  () => import('@/components/CommandPalette.vue'),
)

useKeyboardShortcuts({
  // Cmd/Ctrl+K is global, including while the cursor sits in a filter box.
  ignoreTyping: false,
  shortcuts: [
    {
      match: (event) =>
        (event.metaKey || event.ctrlKey) && event.key?.toLowerCase() === 'k',
      action: () => (showCommandPalette.value = !showCommandPalette.value),
    },
  ],
})
</script>
