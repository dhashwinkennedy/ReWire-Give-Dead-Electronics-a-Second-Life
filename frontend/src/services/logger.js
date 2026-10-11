/**
 * Advanced Client-Side Logger for ReWire Platform.
 *
 * Features:
 * - Leveled logging: DEBUG, INFO, PERF, WARN, ERROR, FATAL
 * - Beautiful styled console formatting with ANSI/CSS color tags and module scopes
 * - High-precision performance timing (logger.time / logger.timeEnd)
 * - In-memory circular ring buffer with real-time reactive subscriber pipeline
 * - Automatic uncaught error & unhandled promise rejection interception
 * - Network API request/response lifecycle logging & timing
 * - Remote telemetry beacon: flush client errors to backend /api/v1/logs/client
 * - Export utilities: Download JSON logs, plain-text logs, or copy to clipboard
 */

const LOG_LEVELS = {
  DEBUG: 10,
  INFO: 20,
  PERF: 25,
  WARN: 30,
  ERROR: 40,
  FATAL: 50,
}

const LEVEL_COLORS = {
  DEBUG: { bg: '#083344', fg: '#38bdf8', badge: '#0284c7' },
  INFO:  { bg: '#064e3b', fg: '#34d399', badge: '#059669' },
  PERF:  { bg: '#3b0764', fg: '#c084fc', badge: '#9333ea' },
  WARN:  { bg: '#451a03', fg: '#fbbf24', badge: '#d97706' },
  ERROR: { bg: '#450a0a', fg: '#f87171', badge: '#dc2626' },
  FATAL: { bg: '#4c0519', fg: '#fb7185', badge: '#e11d48' },
}

class AdvancedLogger {
  constructor() {
    this.buffer = []
    this.maxBufferSize = 500
    this.listeners = new Set()
    this.sessionStartTime = Date.now()
    this.logCounter = 0
    this.activeTimers = new Map()
    this.isFlushing = false

    // Default log level: DEBUG in dev, INFO otherwise; can be overridden via localStorage
    const storedLevel = typeof window !== 'undefined' ? window.localStorage?.getItem('rewire_log_level') : null
    this.currentLevel = storedLevel && LOG_LEVELS[storedLevel]
      ? LOG_LEVELS[storedLevel]
      : (import.meta.env?.DEV ? LOG_LEVELS.DEBUG : LOG_LEVELS.INFO)

    this.consoleEnabled = true
    this.remoteSyncEnabled = true
    this.remoteEndpoint = '/api/v1/logs/client'

    // Bind global error handlers if in browser
    if (typeof window !== 'undefined') {
      this.attachGlobalErrorHandlers()
    }
  }

  setLevel(levelName) {
    const upper = String(levelName).toUpperCase()
    if (LOG_LEVELS[upper] !== undefined) {
      this.currentLevel = LOG_LEVELS[upper]
      if (typeof window !== 'undefined' && window.localStorage) {
        window.localStorage.setItem('rewire_log_level', upper)
      }
      this.info('System', `Logger level set to ${upper}`)
    }
  }

  getLevelName() {
    for (const [name, val] of Object.entries(LOG_LEVELS)) {
      if (val === this.currentLevel) return name
    }
    return 'INFO'
  }

  attachGlobalErrorHandlers() {
    window.addEventListener('error', (event) => {
      this.error('Window', event.message || 'Uncaught browser error', {
        filename: event.filename,
        lineno: event.lineno,
        colno: event.colno,
        stack: event.error?.stack,
      })
    })

    window.addEventListener('unhandledrejection', (event) => {
      const reason = event.reason
      const message = typeof reason === 'object' && reason?.message ? reason.message : String(reason)
      this.error('Promise', `Unhandled Promise Rejection: ${message}`, {
        stack: reason?.stack,
        detail: reason,
      })
    })
  }

  subscribe(listener) {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  notifyListeners(entry) {
    for (const listener of this.listeners) {
      try {
        listener(entry)
      } catch (err) {
        console.error('[Logger Subscriber Error]', err)
      }
    }
  }

  _record(levelName, module, message, data = null, durationMs = null) {
    const levelValue = LOG_LEVELS[levelName] || LOG_LEVELS.INFO
    if (levelValue < this.currentLevel && levelName !== 'PERF') {
      return null
    }

    this.logCounter += 1
    const now = new Date()
    const timeFormatted = now.toTimeString().split(' ')[0] + '.' + String(now.getMilliseconds()).padStart(3, '0')
    const relativeMs = Math.round(performance.now())

    let stack = null
    if (data instanceof Error) {
      stack = data.stack
      data = { name: data.name, message: data.message, stack: data.stack }
    } else if (data && typeof data === 'object' && data.stack) {
      stack = data.stack
    }

    const entry = {
      id: `log-${this.logCounter}`,
      timestamp: now.toISOString(),
      timeFormatted,
      relativeMs,
      level: levelName,
      levelValue,
      module: String(module || 'App'),
      message: String(message),
      data: data !== null ? data : undefined,
      durationMs: durationMs !== null ? Math.round(durationMs * 10) / 10 : undefined,
      stack,
      url: typeof window !== 'undefined' ? window.location.pathname : '',
    }

    // Circular buffer management
    if (this.buffer.length >= this.maxBufferSize) {
      this.buffer.shift()
    }
    this.buffer.push(entry)

    // Formatted Console output
    if (this.consoleEnabled && typeof console !== 'undefined') {
      this.renderConsole(entry)
    }

    // Notify live subscribers
    this.notifyListeners(entry)

    // Flush high severity logs to backend asynchronously
    if (this.remoteSyncEnabled && (levelName === 'ERROR' || levelName === 'FATAL')) {
      this.scheduleRemoteFlush(entry)
    }

    return entry
  }

  renderConsole(entry) {
    const colors = LEVEL_COLORS[entry.level] || LEVEL_COLORS.INFO
    const timeStyle = 'color: #94a3b8; font-family: monospace; font-size: 11px;'
    const levelStyle = `background: ${colors.bg}; color: ${colors.fg}; border: 1px solid ${colors.badge}; padding: 1px 5px; border-radius: 4px; font-weight: bold; font-size: 10px; font-family: monospace;`
    const moduleStyle = 'color: #38bdf8; font-weight: 600; font-family: monospace; font-size: 11px;'
    const msgStyle = entry.level === 'ERROR' || entry.level === 'FATAL'
      ? 'color: #fca5a5; font-weight: 500;'
      : entry.level === 'WARN'
      ? 'color: #fde047; font-weight: 500;'
      : 'color: #e2e8f0;'

    const prefix = `%c${entry.timeFormatted} %c${entry.level.padEnd(5)} %c[${entry.module}] %c${entry.message}`
    const styleArgs = [timeStyle, levelStyle, moduleStyle, msgStyle]

    if (entry.durationMs !== undefined) {
      const durStyle = 'color: #c084fc; font-weight: bold; font-family: monospace;'
      if (entry.data !== undefined) {
        console.log(`${prefix} %c(${entry.durationMs}ms)`, ...styleArgs, durStyle, entry.data)
      } else {
        console.log(`${prefix} %c(${entry.durationMs}ms)`, ...styleArgs, durStyle)
      }
    } else if (entry.data !== undefined) {
      if (entry.level === 'ERROR' || entry.level === 'FATAL') {
        console.error(prefix, ...styleArgs, entry.data)
      } else if (entry.level === 'WARN') {
        console.warn(prefix, ...styleArgs, entry.data)
      } else {
        console.log(prefix, ...styleArgs, entry.data)
      }
    } else {
      if (entry.level === 'ERROR' || entry.level === 'FATAL') {
        console.error(prefix, ...styleArgs)
      } else if (entry.level === 'WARN') {
        console.warn(prefix, ...styleArgs)
      } else {
        console.log(prefix, ...styleArgs)
      }
    }
  }

  debug(module, message, data) {
    return this._record('DEBUG', module, message, data)
  }

  info(module, message, data) {
    return this._record('INFO', module, message, data)
  }

  perf(module, message, durationMs, data) {
    return this._record('PERF', module, message, data, durationMs)
  }

  warn(module, message, data) {
    return this._record('WARN', module, message, data)
  }

  error(module, message, data) {
    return this._record('ERROR', module, message, data)
  }

  fatal(module, message, data) {
    return this._record('FATAL', module, message, data)
  }

  time(label) {
    this.activeTimers.set(label, performance.now())
  }

  timeEnd(label, module = 'Perf', extraData = null) {
    const start = this.activeTimers.get(label)
    if (start !== undefined) {
      const elapsed = performance.now() - start
      this.activeTimers.delete(label)
      this.perf(module, `${label} completed`, elapsed, extraData)
      return elapsed
    }
    return null
  }

  forModule(moduleName) {
    return {
      debug: (msg, data) => this.debug(moduleName, msg, data),
      info:  (msg, data) => this.info(moduleName, msg, data),
      perf:  (msg, ms, data) => this.perf(moduleName, msg, ms, data),
      warn:  (msg, data) => this.warn(moduleName, msg, data),
      error: (msg, data) => this.error(moduleName, msg, data),
      fatal: (msg, data) => this.fatal(moduleName, msg, data),
      time:  (label) => this.time(`${moduleName}:${label}`),
      timeEnd: (label, data) => this.timeEnd(`${moduleName}:${label}`, moduleName, data),
    }
  }

  getLogs({ level = null, module = null, search = null } = {}) {
    let result = [...this.buffer]
    if (level) {
      const lvl = level.toUpperCase()
      result = result.filter((e) => e.level === lvl)
    }
    if (module) {
      const mod = module.toLowerCase()
      result = result.filter((e) => e.module.toLowerCase() === mod)
    }
    if (search) {
      const q = search.toLowerCase()
      result = result.filter(
        (e) =>
          e.message.toLowerCase().includes(q) ||
          e.module.toLowerCase().includes(q) ||
          (e.data && JSON.stringify(e.data).toLowerCase().includes(q))
      )
    }
    return result
  }

  getStats() {
    const counts = { DEBUG: 0, INFO: 0, PERF: 0, WARN: 0, ERROR: 0, FATAL: 0 }
    for (const entry of this.buffer) {
      if (counts[entry.level] !== undefined) {
        counts[entry.level] += 1
      }
    }
    return {
      total: this.buffer.length,
      counts,
      activeTimers: this.activeTimers.size,
      subscribers: this.listeners.size,
      uptimeSeconds: Math.round((Date.now() - this.sessionStartTime) / 1000),
      currentLevel: this.getLevelName(),
    }
  }

  clear() {
    this.buffer = []
    this.activeTimers.clear()
    this.notifyListeners({ type: 'CLEAR' })
  }

  exportJson() {
    const payload = {
      app: 'ReWire E-Waste Repurposing Client',
      exportedAt: new Date().toISOString(),
      stats: this.getStats(),
      logs: this.buffer,
    }
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `rewire-client-logs-${Date.now()}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  exportText() {
    const lines = this.buffer.map((e) => {
      const dur = e.durationMs ? ` (${e.durationMs}ms)` : ''
      const dataStr = e.data ? ` | ${JSON.stringify(e.data)}` : ''
      const stackStr = e.stack ? `\n${e.stack}` : ''
      return `${e.timestamp} | ${e.level.padEnd(5)} | [${e.module}] ${e.message}${dur}${dataStr}${stackStr}`
    })
    const blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `rewire-client-logs-${Date.now()}.log`
    a.click()
    URL.revokeObjectURL(url)
  }

  async copyToClipboard() {
    const text = this.buffer
      .map((e) => `${e.timeFormatted} [${e.level}] [${e.module}] ${e.message}${e.durationMs ? ` (${e.durationMs}ms)` : ''}`)
      .join('\n')
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(text)
      return true
    }
    return false
  }

  scheduleRemoteFlush(entry) {
    if (this.isFlushing) return
    setTimeout(async () => {
      try {
        this.isFlushing = true
        const base = (import.meta.env?.VITE_API_BASE_URL || '').replace(/\/$/, '')
        await fetch(`${base}${this.remoteEndpoint}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            logs: [
              {
                level: entry.level,
                module: entry.module,
                message: entry.message,
                data: entry.data,
                timestamp: entry.timestamp,
                url: entry.url,
                userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : 'browser',
              },
            ],
          }),
        })
      } catch {
        // Silently tolerate if backend is currently unreachable during offline mode
      } finally {
        this.isFlushing = false
      }
    }, 1000)
  }
}

// Global singleton instance
export const logger = new AdvancedLogger()
export default logger
