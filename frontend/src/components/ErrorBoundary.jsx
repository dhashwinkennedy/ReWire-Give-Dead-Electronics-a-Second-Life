import React from 'react'
import logger from '../services/logger'
import './ErrorBoundary.css'

export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
      copied: false,
    }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, errorInfo) {
    this.setState({ errorInfo })
    logger.fatal('ErrorBoundary', error.message || 'React rendering crashed', {
      stack: error.stack,
      componentStack: errorInfo?.componentStack,
    })
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null, copied: false })
  }

  handleReload = () => {
    window.location.reload()
  }

  handleCopyReport = () => {
    const report = {
      timestamp: new Date().toISOString(),
      error: this.state.error?.message,
      stack: this.state.error?.stack,
      componentStack: this.state.errorInfo?.componentStack,
      url: window.location.href,
      userAgent: navigator.userAgent,
    }
    if (navigator.clipboard) {
      navigator.clipboard.writeText(JSON.stringify(report, null, 2))
      this.setState({ copied: true })
      setTimeout(() => this.setState({ copied: false }), 2500)
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-boundary-wrapper" role="alert">
          <div className="error-boundary-card">
            <div className="error-boundary-header">
              <div className="error-boundary-icon">⚠</div>
              <div>
                <h2>Workbench Interruption</h2>
                <p>An unexpected UI rendering error occurred. The workbench state was captured safely.</p>
              </div>
            </div>

            <div className="error-boundary-message">
              <b>Error: </b>
              {this.state.error?.message || 'Unknown render exception'}
            </div>

            {this.state.error?.stack && (
              <details className="error-boundary-details">
                <summary>View Technical Diagnostic Trace</summary>
                <pre>{this.state.error.stack}</pre>
                {this.state.errorInfo?.componentStack && (
                  <pre style={{ color: '#93c5fd', marginTop: '8px' }}>
                    {this.state.errorInfo.componentStack}
                  </pre>
                )}
              </details>
            )}

            <div className="error-boundary-actions">
              <button
                type="button"
                className="btn-recover-primary"
                onClick={this.handleReset}
              >
                Try Again
              </button>
              <button
                type="button"
                className="btn-recover-secondary"
                onClick={this.handleReload}
              >
                Reload Workbench
              </button>
              <button
                type="button"
                className="btn-recover-secondary"
                onClick={this.handleCopyReport}
              >
                {this.state.copied ? '✓ Report Copied' : 'Copy Error Report'}
              </button>
            </div>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
