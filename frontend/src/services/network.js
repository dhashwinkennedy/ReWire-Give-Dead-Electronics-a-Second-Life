import logger from './logger'

const log = logger.forModule('Network')

class NetworkStatusService {
  constructor() {
    this.online = typeof navigator !== 'undefined' ? navigator.onLine : true
    this.listeners = new Set()

    if (typeof window !== 'undefined') {
      window.addEventListener('online', this._handleOnline)
      window.addEventListener('offline', this._handleOffline)
    }
  }

  _handleOnline = () => {
    this.online = true
    log.info('Network connectivity restored: device is online', {
      timestamp: new Date().toISOString(),
    })
    this._notify()
  }

  _handleOffline = () => {
    this.online = false
    log.warn('Network connectivity lost: device is offline', {
      timestamp: new Date().toISOString(),
    })
    this._notify()
  }

  _notify() {
    for (const listener of this.listeners) {
      try {
        listener(this.online)
      } catch (err) {
        log.error('Error in network status listener', { error: err.message })
      }
    }
  }

  isOnline() {
    return this.online
  }

  subscribe(listener) {
    this.listeners.add(listener)
    // Immediately call with current state
    try {
      listener(this.online)
    } catch {
      // ignore
    }
    return () => this.listeners.delete(listener)
  }

  /**
   * Actively probe the backend or origin to verify actual HTTP reachability.
   */
  async probe(url = null) {
    const targetUrl = url || (import.meta.env.VITE_API_BASE_URL || window.location.origin)
    try {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), 4000)
      const res = await fetch(targetUrl, {
        method: 'HEAD',
        cache: 'no-store',
        signal: controller.signal,
      })
      clearTimeout(timer)
      const reachable = res.ok || res.status < 500
      if (reachable !== this.online) {
        this.online = reachable
        this._notify()
      }
      return reachable
    } catch {
      if (this.online) {
        this.online = false
        this._notify()
      }
      return false
    }
  }

  cleanup() {
    if (typeof window !== 'undefined') {
      window.removeEventListener('online', this._handleOnline)
      window.removeEventListener('offline', this._handleOffline)
    }
    this.listeners.clear()
  }
}

export const networkStatus = new NetworkStatusService()
export default networkStatus
