import logger from './logger'

const log = logger.forModule('CircuitBreaker')

export class CircuitBreakerOpenError extends Error {
  constructor(serviceName, resetTimeoutMs) {
    const seconds = Math.ceil(resetTimeoutMs / 1000)
    super(`The ${serviceName} service is temporarily unavailable to protect system stability. Please retry in ${seconds}s.`)
    this.name = 'CircuitBreakerOpenError'
    this.status = 503
    this.errorCode = 'CIRCUIT_BREAKER_OPEN'
    this.serviceName = serviceName
    this.resetTimeoutMs = resetTimeoutMs
    this.remediation = `Service is in protective cool-down. Please retry after ${seconds} seconds or use fallback options.`
  }
}

/**
 * Resilient client-side Circuit Breaker pattern with states CLOSED, OPEN, HALF_OPEN.
 * Protects downstream AI and inference endpoints from cascading failures and excessive user retries.
 */
export class CircuitBreaker {
  constructor(name, options = {}) {
    this.name = name
    this.failureThreshold = options.failureThreshold || 3
    this.resetTimeoutMs = options.resetTimeoutMs || 20000
    this.halfOpenSuccessThreshold = options.halfOpenSuccessThreshold || 1

    this.state = 'CLOSED'
    this.failureCount = 0
    this.successCount = 0
    this.lastStateChange = Date.now()
    this.listeners = new Set()
  }

  getState() {
    this._checkHalfOpenTransition()
    return this.state
  }

  _checkHalfOpenTransition() {
    if (this.state === 'OPEN') {
      const elapsed = Date.now() - this.lastStateChange
      if (elapsed >= this.resetTimeoutMs) {
        this._transition('HALF_OPEN')
      }
    }
  }

  isOpen() {
    this._checkHalfOpenTransition()
    return this.state === 'OPEN'
  }

  _transition(nextState) {
    const prev = this.state
    if (prev === nextState) return

    this.state = nextState
    this.lastStateChange = Date.now()
    if (nextState === 'CLOSED') {
      this.failureCount = 0
      this.successCount = 0
    } else if (nextState === 'HALF_OPEN') {
      this.successCount = 0
    }

    log.warn(`[${this.name}] State transition: ${prev} -> ${nextState}`, {
      service: this.name,
      previousState: prev,
      newState: nextState,
      timestamp: new Date().toISOString(),
    })

    for (const listener of this.listeners) {
      try {
        listener(nextState, prev)
      } catch (err) {
        log.error(`[${this.name}] Listener error during state transition`, { error: err.message })
      }
    }
  }

  recordSuccess() {
    this._checkHalfOpenTransition()
    if (this.state === 'HALF_OPEN') {
      this.successCount += 1
      if (this.successCount >= this.halfOpenSuccessThreshold) {
        this._transition('CLOSED')
      }
    } else if (this.state === 'CLOSED') {
      this.failureCount = 0
    }
  }

  recordFailure(error) {
    this._checkHalfOpenTransition()
    this.failureCount += 1

    log.warn(`[${this.name}] Failure recorded (#${this.failureCount}/${this.failureThreshold})`, {
      service: this.name,
      state: this.state,
      errorMessage: error?.message,
    })

    if (this.state === 'HALF_OPEN') {
      this._transition('OPEN')
    } else if (this.state === 'CLOSED' && this.failureCount >= this.failureThreshold) {
      this._transition('OPEN')
    }
  }

  async execute(fn) {
    if (this.isOpen()) {
      const remainingMs = Math.max(0, this.resetTimeoutMs - (Date.now() - this.lastStateChange))
      throw new CircuitBreakerOpenError(this.name, remainingMs)
    }

    try {
      const result = await fn()
      this.recordSuccess()
      return result
    } catch (error) {
      // Only trip for server or connection failures, not user validation errors (422, 400)
      const status = error.status || 0
      const isClientError = status >= 400 && status < 500 && status !== 429
      if (!isClientError) {
        this.recordFailure(error)
      }
      throw error
    }
  }

  reset() {
    this._transition('CLOSED')
    this.failureCount = 0
    this.successCount = 0
  }

  onStateChange(listener) {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }
}

export const aiSearchBreaker = new CircuitBreaker('AI Search', {
  failureThreshold: 3,
  resetTimeoutMs: 25000,
})

export const visionBreaker = new CircuitBreaker('Vision Inference', {
  failureThreshold: 3,
  resetTimeoutMs: 20000,
})

export default CircuitBreaker
