import { io } from 'socket.io-client'
import { getCachedListResource, getCachedResource } from 'frappe-ui'

export function initSocket() {
  let socketio_port = window.socketio_port || 9000
  let host = window.location.hostname
  let siteName = window.site_name
  let port = window.location.port ? `:${socketio_port}` : ''
  let protocol = port ? 'http' : 'https'
  let url = `${protocol}://${host}${port}/${siteName}`

  let socket = io(url, {
    withCredentials: true,
    // No attempt cap on purpose. With `reconnectionAttempts: 5` a single bench
    // restart exhausted the retries and realtime stayed dead until the user
    // reloaded the page — the "it worked, then it went stale" report.
    reconnection: true,
    reconnectionAttempts: Infinity,
    reconnectionDelay: 1000,
    reconnectionDelayMax: 10000,
    // Spreads the retries of many open tabs so they do not all hit the socket
    // server in the same instant after a restart.
    randomizationFactor: 0.5,
  })
  // socket.io-client deliberately does NOT auto-reconnect when the server hangs
  // up, which is exactly what a bench restart does. Without the handler below,
  // the unlimited retry settings above never come into play for that case.
  //
  // The retry needs its own backoff. `socket.connect()` is not the managed
  // reconnect path — in socket.io-client 4.8.3 it runs
  // `if (!this.io["_reconnecting"]) this.io.open()`, going straight to the
  // manager's open() and skipping reconnectionDelay entirely. Calling it from
  // the disconnect handler with no delay makes a server that keeps rejecting
  // the session (logout in another tab, expired cookie) spin every open tab at
  // one handshake per round trip.
  const REJOIN_BASE_DELAY = 2000
  const REJOIN_MAX_DELAY = 30000
  const REJOIN_MAX_ATTEMPTS = 10
  // A session shorter than this was a rejection, not a working connection.
  const REJOIN_STABLE_MS = 10000

  let rejoinAttempts = 0
  let rejoinTimer = null
  let connectedAt = 0

  function cancelRejoin() {
    if (rejoinTimer) {
      clearTimeout(rejoinTimer)
      rejoinTimer = null
    }
  }

  socket.on('connect', () => {
    connectedAt = Date.now()
    cancelRejoin()
  })

  socket.on('disconnect', (reason) => {
    // Any new disconnect supersedes a retry that has not fired yet.
    cancelRejoin()

    // Every other reason is already handled by the manager's own backoff.
    if (reason !== 'io server disconnect') return

    // Reset only after a connection that actually held. Resetting on the bare
    // `connect` event would let a connect-then-immediately-dropped flap loop
    // forever at the base delay, which is the storm this code exists to stop.
    if (connectedAt && Date.now() - connectedAt >= REJOIN_STABLE_MS) {
      rejoinAttempts = 0
    }

    if (rejoinAttempts >= REJOIN_MAX_ATTEMPTS) {
      console.warn(
        `[crm] The realtime server closed the connection ${REJOIN_MAX_ATTEMPTS} times; giving up. Reload the page to retry.`,
      )
      return
    }

    const delay = Math.min(
      REJOIN_BASE_DELAY * 2 ** rejoinAttempts,
      REJOIN_MAX_DELAY,
    )
    rejoinAttempts += 1
    rejoinTimer = setTimeout(() => {
      rejoinTimer = null
      socket.connect()
    }, delay)
  })
  socket.on('refetch_resource', (data) => {
    if (data.cache_key) {
      let resource =
        getCachedResource(data.cache_key) ||
        getCachedListResource(data.cache_key)
      if (resource) {
        resource.reload()
      }
    }
  })
  return socket
}
