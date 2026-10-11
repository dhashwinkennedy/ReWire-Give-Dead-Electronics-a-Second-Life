import logger from './logger'
import networkStatus from './network'
import {
  aiSearchBreaker,
  visionBreaker,
  CircuitBreakerOpenError,
} from './circuitBreaker'

const log = logger.forModule('API')
const base = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

// Verified ReWire backend routes.
const endpoints = {
  repurpose: '/api/v1/repurpose',
  aiSearch: '/api/v1/ai_search',
  detect: '/detect',
  detectAlias: '/api/v1/detect',
}

/**
 * Standardized API domain error preserving backend RFC 7807 envelope fields.
 */
export class ApiError extends Error {
  constructor(message, status, payload = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status || 500
    this.errorCode = payload.error_code || `HTTP_${status}`
    this.title = payload.title || (this.errorCode ? this.errorCode.replace(/_/g, ' ') : `HTTP ${status}`)
    this.remediation = payload.remediation || 'Please verify request parameters and retry.'
    this.requestId = payload.request_id || null
    this.details = payload.details || null
    this.timestamp = payload.timestamp || new Date().toISOString()
    this.path = payload.path || null
    this.retryAfter = payload.retry_after !== undefined ? Number(payload.retry_after) : null
  }

  get formattedMessage() {
    let msg = this.message
    if (this.requestId) {
      msg += ` (Ref: ${this.requestId})`
    }
    return msg
  }
}

export class NetworkError extends ApiError {
  constructor(message = 'We couldn’t reach the ReWire backend. Check your network connection and verify the server is running.') {
    super(message, 0, {
      error_code: 'NETWORK_ERROR',
      title: 'Network Connection Error',
      remediation: 'Ensure your network connection is active and that the backend server is reachable.',
    })
    this.name = 'NetworkError'
  }
}

export class TimeoutError extends ApiError {
  constructor(message = 'The request timed out while waiting for a response from the ReWire backend.', timeoutMs = 25000) {
    super(message, 408, {
      error_code: 'REQUEST_TIMEOUT',
      title: 'Request Timed Out',
      remediation: `The backend took longer than ${Math.round(timeoutMs / 1000)}s to respond. Please check server load or retry shortly.`,
    })
    this.name = 'TimeoutError'
  }
}

export class ValidationError extends ApiError {
  constructor(message, details = null) {
    super(message || 'Please check your inventory and parameters.', 422, {
      error_code: 'VALIDATION_ERROR',
      title: 'Validation Error',
      remediation: 'Review the items in your request payload to ensure all fields and quantities are valid.',
      details,
    })
    this.name = 'ValidationError'
  }
}

export class RateLimitError extends ApiError {
  constructor(message = 'Too many requests sent in a short time frame.', retryAfter = 5, payload = {}) {
    super(message, 429, {
      error_code: 'RATE_LIMIT_EXCEEDED',
      title: 'Rate Limit Exceeded',
      remediation: `Please wait ${retryAfter} second${retryAfter === 1 ? '' : 's'} before submitting another request.`,
      retry_after: retryAfter,
      ...payload,
    })
    this.name = 'RateLimitError'
    this.retryAfter = retryAfter
  }
}

export { CircuitBreakerOpenError }

const userMessage = (message = '', status = 500) => {
  const lower = String(message).toLowerCase()
  if (lower.includes('ai search is not configured') || lower.includes('ai_model'))
    return 'AI-ReWire is currently unavailable because the backend AI model is not configured.'
  if (lower.includes('tavily'))
    return 'AI-ReWire’s research provider is unavailable. Please try again later.'
  if (lower.includes('ollama'))
    return 'The local AI model is unavailable. Check that Ollama is running and try again.'
  if (status === 413 || lower.includes('exceeds maximum allowed upload size'))
    return message || 'The selected file exceeds the 15 MB upload size limit.'
  if (status === 429 || lower.includes('rate limit exceeded'))
    return message || 'Too many requests sent. Please pause for a moment.'
  if (status === 422)
    return message || 'Please check your inventory items and try again.'
  return message || 'We couldn’t complete that request right now. Please try again.'
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

/**
 * Determines whether an error is transient and retriable.
 */
function isRetriableError(error, status) {
  if (error instanceof TimeoutError || error instanceof NetworkError) return true
  if (status === 429 || status === 502 || status === 503 || status === 504) return true
  return false
}

/**
 * Single-shot fetch execution with timeout and header correlation.
 */
async function performSingleFetch(path, options, timeoutMs) {
  const method = options.method || 'GET'
  const startTime = performance.now()
  const controller = new AbortController()
  const hasTimeout = typeof timeoutMs === 'number' && timeoutMs > 0 && timeoutMs !== Infinity
  const timeoutId = hasTimeout ? setTimeout(() => controller.abort(), timeoutMs) : null

  // Ensure correlation headers exist
  const headers = new Headers(options.headers || {})
  if (!headers.has('X-Request-ID')) {
    const randomRef = 'fe-' + Math.random().toString(36).substring(2, 10)
    headers.set('X-Request-ID', randomRef)
  }

  let response
  try {
    response = await fetch(`${base}${path}`, {
      ...options,
      headers,
      signal: hasTimeout ? controller.signal : (options.signal || controller.signal),
    })
  } catch (err) {
    if (timeoutId) clearTimeout(timeoutId)
    const duration = performance.now() - startTime

    if (err.name === 'AbortError') {
      log.error(`Request to ${path} timed out after ${timeoutMs}ms`)
      throw new TimeoutError(`Request to ${path} timed out after ${timeoutMs / 1000}s.`, timeoutMs)
    }

    log.error(`Network failure reaching ${path}`, {
      durationMs: duration,
      error: err.message,
    })
    throw new NetworkError()
  } finally {
    if (timeoutId) clearTimeout(timeoutId)
  }

  const duration = performance.now() - startTime
  const raw = await response.text()
  let data
  try {
    data = raw ? JSON.parse(raw) : {}
  } catch {
    data = { detail: raw }
  }

  if (!response.ok) {
    const rawDetail = Array.isArray(data.detail)
      ? data.detail.map((item) => item.msg || item.message || JSON.stringify(item)).filter(Boolean).join('. ')
      : data.detail || data.message || `HTTP ${response.status} Error`

    const message = userMessage(rawDetail, response.status)
    const retryAfterHeader = response.headers.get('Retry-After')
    const retryAfter = retryAfterHeader ? parseInt(retryAfterHeader, 10) : data.retry_after

    log.warn(`HTTP ${response.status} on ${method} ${path}`, {
      status: response.status,
      durationMs: duration,
      errorCode: data.error_code,
      requestId: data.request_id || headers.get('X-Request-ID'),
      detail: rawDetail,
    })

    if (response.status === 429) {
      throw new RateLimitError(message, retryAfter || 5, data)
    }

    if (response.status === 422) {
      throw new ValidationError(message, data.detail || data.details)
    }

    throw new ApiError(message, response.status, {
      ...data,
      request_id: data.request_id || headers.get('X-Request-ID'),
      retry_after: retryAfter,
    })
  }

  log.perf(`HTTP ${response.status} ${method} ${path}`, duration, {
    status: response.status,
    payloadSize: raw.length,
    requestId: response.headers.get('X-Request-ID') || data.request_id,
  })

  return data
}

/**
 * Resilient API fetch wrapper with:
 * - Exponential backoff + jitter retry
 * - Rate limit (429) Retry-After header parsing
 * - Circuit breaker protection
 * - Fast-fail on offline status
 * - Structured logging & RFC 7807 envelope preservation
 */
export async function apiFetch(path, options = {}, config = {}) {
  const {
    timeoutMs = 25000,
    retries = 2,
    retryDelayMs = 800,
    circuitBreaker = null,
    onRetry = null,
  } = typeof config === 'number' ? { timeoutMs: config } : config

  // Check offline status first to fast-fail if not connected
  if (!networkStatus.isOnline()) {
    log.warn(`Blocked API request to ${path} because device is offline`)
    throw new NetworkError('You are currently offline. Local catalog search remains available, but server actions require internet access.')
  }

  const runWithRetries = async () => {
    let attempt = 0
    while (true) {
      try {
        return await performSingleFetch(path, options, timeoutMs)
      } catch (error) {
        const status = error.status || 0
        const retriable = isRetriableError(error, status)

        if (attempt >= retries || !retriable) {
          throw error
        }

        attempt += 1

        let backoffMs = retryDelayMs * Math.pow(2, attempt - 1) + Math.random() * 200
        if (status === 429 && error.retryAfter) {
          backoffMs = Math.min(10000, error.retryAfter * 1000)
        }

        log.info(`Retrying ${path} (attempt ${attempt}/${retries}) in ${Math.round(backoffMs)}ms due to ${error.name || error.message}`)
        if (typeof onRetry === 'function') {
          onRetry({ attempt, maxRetries: retries, delayMs: backoffMs, error })
        }

        await sleep(backoffMs)
      }
    }
  }

  if (circuitBreaker) {
    return circuitBreaker.execute(runWithRetries)
  }

  return runWithRetries()
}

const requestFor = (items) => {
  if (!Array.isArray(items) || items.length === 0) {
    throw new ValidationError('Inventory must contain at least one item.')
  }

  return {
    items: items.map(({ id, kind, quantity, condition, defective_components }) => ({
      name: id,
      kind,
      quantity: Math.max(1, parseInt(quantity, 10) || 1),
      condition: condition || 'working',
      ...(kind === 'device' && condition === 'defective' && defective_components?.length
        ? { defective_components }
        : {}),
    })),
    options: {
      max_results: 10,
      min_score: 40,
      max_difficulty: 'high',
      detail: 'full',
      include_best_combination: true,
    },
  }
}

const postInventory = (path, items, config = {}) => {
  log.info(`Sending inventory payload to ${path}`, { itemCount: items.length })
  return apiFetch(
    path,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(requestFor(items)),
    },
    config
  )
}

/**
 * POST /api/v1/repurpose — deterministic recommendation search (20s timeout, 2 retries).
 */
export const repurposeInventory = (items, onRetry = null) =>
  postInventory(endpoints.repurpose, items, {
    timeoutMs: 20000,
    retries: 2,
    retryDelayMs: 800,
    onRetry,
  })

/**
 * POST /api/v1/ai_search — AI-assisted recommendation search with LLM reranking.
 * No client-side timeout: local AI models can take variable time depending on weights, quantization, and hardware.
 */
export const aiRepurposeInventory = (items, onRetry = null) =>
  postInventory(endpoints.aiSearch, items, {
    timeoutMs: 0,
    retries: 0,
    circuitBreaker: aiSearchBreaker,
    onRetry,
  })

const detectAt = (path, file, config = {}) => {
  const form = new FormData()
  form.append('image', file)
  log.info(`Uploading image for vision inference to ${path}`, {
    filename: file.name,
    fileSize: file.size,
    fileType: file.type,
  })
  return apiFetch(
    path,
    { method: 'POST', body: form },
    {
      timeoutMs: 30000,
      retries: 1,
      circuitBreaker: visionBreaker,
      ...config,
    }
  )
}

/**
 * POST /detect, falling back to the documented POST /api/v1/detect alias only
 * when a deployment does not expose the primary path.
 */
export async function detectImage(file, onRetry = null) {
  if (!file) {
    throw new ValidationError('Please select an image file to upload.')
  }
  const MAX_SIZE = 15 * 1024 * 1024
  if (file.size > MAX_SIZE) {
    const sizeMb = (file.size / (1024 * 1024)).toFixed(1)
    throw new ApiError(`Image size (${sizeMb} MB) exceeds maximum allowed upload size of 15 MB.`, 413, {
      error_code: 'IMAGE_SIZE_LIMIT_EXCEEDED',
      title: 'Image Size Limit Exceeded',
      remediation: 'Resize or compress the image to under 15 MB before uploading.',
    })
  }

  const allowedTypes = ['image/jpeg', 'image/png', 'image/webp', 'image/bmp']
  if (file.type && !allowedTypes.includes(file.type)) {
    throw new ApiError(`Unsupported image type '${file.type}'. Allowed: JPEG, PNG, WEBP, BMP.`, 400, {
      error_code: 'UNSUPPORTED_IMAGE_FORMAT',
      title: 'Unsupported Image Format',
      remediation: 'Convert the image to JPEG, PNG, WEBP, or BMP format before uploading.',
    })
  }

  try {
    return await detectAt(endpoints.detect, file, { onRetry })
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) {
      log.warn(`Falling back from ${endpoints.detect} to alias ${endpoints.detectAlias}`)
      return detectAt(endpoints.detectAlias, file, { onRetry })
    }
    throw error
  }
}
