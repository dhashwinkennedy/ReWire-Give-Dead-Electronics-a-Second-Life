import React, { useEffect, useState } from 'react'
import './ActionableErrorNotice.css'

export default function ActionableErrorNotice({
  error,
  onRetry,
  onFallback,
  fallbackLabel = '✦ Run Standard ReWire',
  onDismiss,
}) {
  const [copied, setCopied] = useState(false)
  const [countdown, setCountdown] = useState(error?.retryAfter || 0)

  // Sync countdown timer if error has retryAfter (for 429 / cool-down)
  useEffect(() => {
    if (!error?.retryAfter) {
      setCountdown(0)
      return
    }
    setCountdown(error.retryAfter)
    const interval = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          clearInterval(interval)
          return 0
        }
        return prev - 1
      })
    }, 1000)
    return () => clearInterval(interval)
  }, [error])

  if (!error) return null

  const errorCode = error.errorCode || error.error_code || 'ERROR'
  const title = error.title || errorCode.replace(/_/g, ' ')
  const message = error.message || error.detail || 'An unexpected issue occurred.'
  const remediation = error.remediation || null
  const requestId = error.requestId || error.request_id || null
  const status = error.status || null
  const details = error.details || null

  const handleCopyId = () => {
    if (requestId && navigator.clipboard) {
      navigator.clipboard.writeText(requestId)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }

  const getTone = () => {
    if (status === 429) return 'rate-limit'
    if (status === 503 || errorCode.includes('CIRCUIT_BREAKER')) return 'circuit'
    if (status === 413 || status === 400 || status === 422) return 'client-error'
    if (status === 0 || errorCode.includes('NETWORK')) return 'network'
    return 'server-error'
  }

  return (
    <div className={`actionable-error-notice ${getTone()}`} role="alert" aria-live="assertive">
      <div className="error-notice-header">
        <div className="error-title-group">
          <span className="error-code-badge">{errorCode}</span>
          {status ? <span className="error-status-badge">HTTP {status}</span> : null}
          <h4 className="error-title-text">{title}</h4>
        </div>
        {onDismiss && (
          <button
            type="button"
            className="error-dismiss-btn"
            onClick={onDismiss}
            aria-label="Dismiss notice"
          >
            ×
          </button>
        )}
      </div>

      <p className="error-message-text">{message}</p>

      {remediation && (
        <div className="error-remediation-box">
          <span className="remediation-icon" aria-hidden="true">💡</span>
          <div className="remediation-content">
            <b>Recommended Action:</b>
            <span>{remediation}</span>
          </div>
        </div>
      )}

      {requestId && (
        <div className="error-ref-row">
          <span className="ref-label">Correlation ID:</span>
          <code className="ref-code">{requestId}</code>
          <button
            type="button"
            className="ref-copy-btn"
            onClick={handleCopyId}
            title="Copy Request ID for troubleshooting"
          >
            {copied ? '✓ Copied' : 'Copy ID'}
          </button>
        </div>
      )}

      {details && (
        <details className="error-technical-details">
          <summary>View Diagnostic Details</summary>
          <pre>{typeof details === 'object' ? JSON.stringify(details, null, 2) : String(details)}</pre>
        </details>
      )}

      <div className="error-action-row">
        {onRetry && (
          <button
            type="button"
            className="error-action-btn btn-retry"
            onClick={onRetry}
            disabled={countdown > 0}
          >
            {countdown > 0 ? `Retry in ${countdown}s` : '↻ Retry Request'}
          </button>
        )}

        {onFallback && (
          <button
            type="button"
            className="error-action-btn btn-fallback"
            onClick={onFallback}
          >
            {fallbackLabel}
          </button>
        )}

        {onDismiss && (
          <button
            type="button"
            className="error-action-btn btn-dismiss"
            onClick={onDismiss}
          >
            Dismiss
          </button>
        )}
      </div>
    </div>
  )
}
