/**
 * The one switch that opens the Cmd/Ctrl+K palette (master spec §5, item 10).
 *
 * A module-level ref rather than a provide/inject: the palette is mounted once
 * in `GlobalModals.vue`, and its triggers live in the desktop sidebar and the
 * mobile top bar, which are in different component trees.
 */

import { ref } from 'vue'

export const showCommandPalette = ref(false)

export function openCommandPalette() {
  showCommandPalette.value = true
}

export function closeCommandPalette() {
  showCommandPalette.value = false
}
