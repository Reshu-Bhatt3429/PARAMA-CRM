import { onMounted, onUnmounted } from 'vue'

const STORAGE_KEY = 'app_broadcasts'
const MAX_BROADCASTS = 100

function readBroadcasts() {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    return Array.isArray(value) ? value.slice(-MAX_BROADCASTS) : []
  } catch {
    localStorage.removeItem(STORAGE_KEY)
    return []
  }
}

function writeBroadcasts(broadcasts) {
  try {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(broadcasts.slice(-MAX_BROADCASTS)),
    )
  } catch (error) {
    console.warn('[crm] Could not persist an in-app broadcast.', error)
  }
}

const bus = {
  send(event, payload) {
    window.dispatchEvent(new CustomEvent(event, { detail: payload }))

    const broadcasts = readBroadcasts()
    broadcasts.push({ event, payload, timestamp: Date.now() })
    writeBroadcasts(broadcasts)
  },
  on(event, handler) {
    const listener = (e) => handler(e.detail)
    window.addEventListener(event, listener)
    return listener
  },
  off(event, listener) {
    window.removeEventListener(event, listener)
  },
}

export function useBroadcast() {
  const listeners = []

  function on(event, handler) {
    const listener = bus.on(event, handler)
    listeners.push({ event, listener })

    // check localStorage for missed broadcasts on init
    onMounted(() => {
      const broadcasts = readBroadcasts()
      const missed = broadcasts.filter((b) => b.event === event)
      if (missed.length) {
        missed.forEach((b) => handler(b.payload))
        // clear handled broadcasts
        const remaining = broadcasts.filter((b) => b.event !== event)
        writeBroadcasts(remaining)
      }
    })
  }

  onUnmounted(() => {
    listeners.forEach(({ event, listener }) => bus.off(event, listener))
  })

  return { on, send: bus.send }
}
