import './index.css'

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createDialog } from './utils/dialogs'
import { initSocket } from './socket'
import { ensureCacheBelongsToSessionUser } from './stores/session'
import router from './router'
import translationPlugin from './translation'
import App from './App.vue'

import {
  FrappeUI,
  Button,
  Input,
  TextInput,
  FormControl,
  ErrorMessage,
  Dialog,
  Alert,
  Badge,
  setConfig,
  frappeRequest,
  FeatherIcon,
} from 'frappe-ui'

import { telemetryPlugin } from 'frappe-ui/frappe'
// injects the lucide SVG sprite into the DOM so the IconPicker and lucide Icons
// (used for view icons) can render from it
import { spritePlugin } from 'frappe-ui/icons'

let globalComponents = {
  Button,
  TextInput,
  Input,
  FormControl,
  ErrorMessage,
  Dialog,
  Alert,
  Badge,
  FeatherIcon,
}

// create a pinia instance
let pinia = createPinia()

let app = createApp(App)

setConfig('resourceFetcher', frappeRequest)
app.use(FrappeUI)
app.use(spritePlugin)
app.use(pinia)
app.use(router)
app.use(translationPlugin)
for (let key in globalComponents) {
  app.component(key, globalComponents[key])
}
app.use(telemetryPlugin, { app_name: 'crm' })

app.config.globalProperties.$dialog = createDialog

let socket

function mountApp() {
  socket = initSocket()
  app.config.globalProperties.$socket = socket
  app.mount('#app')
}

// Runs before the app mounts so the clear can never race a cached resource
// reading IndexedDB. No eagerly imported module creates a cached resource
// today; the ordering guarantee is for lazy route chunks and for future code,
// which is cheap to keep now and expensive to retrofit later.
// The guard settles within its own timeout, so this cannot stall the boot.
ensureCacheBelongsToSessionUser()
  .catch((error) => {
    console.error('[crm] Cache ownership check failed; mounting anyway.', error)
  })
  .then(() => {
    if (import.meta.env.DEV) {
      return frappeRequest({
        url: '/api/method/crm.www.crm.get_context_for_dev',
      }).then((values) => {
        for (let key in values) {
          window[key] = values[key]
        }
        mountApp()
      })
    }
    mountApp()
  })
  .catch((error) => {
    // Without this the app fails to mount with a blank page and no console
    // trace, which is the hardest possible failure to diagnose on a demo box.
    console.error('[crm] The app failed to mount.', error)
  })

if (import.meta.env.DEV) {
  window.$dialog = createDialog
}
