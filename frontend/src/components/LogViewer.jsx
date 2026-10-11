import { useEffect, useRef, useState } from 'react'
import logger from '../services/logger'
import './LogViewer.css'

export default function LogViewer() {
  const [isOpen, setIsOpen] = useState(false)
  const [logs, setLogs] = useState(() => logger.getLogs())
  const [filterLevel, setFilterLevel] = useState('ALL')
  const [searchQuery, setSearchQuery] = useState('')
  const [autoScroll, setAutoScroll] = useState(true)
  const [expandedId, setExpandedId] = useState(null)
  const [stats, setStats] = useState(() => logger.getStats())
  const streamEndRef = useRef(null)

  // Real-time subscriber to logger events
  useEffect(() => {
    const unsubscribe = logger.subscribe((event) => {
      if (event?.type === 'CLEAR') {
        setLogs([])
        setStats(logger.getStats())
      } else {
        setLogs(logger.getLogs())
        setStats(logger.getStats())
      }
    })
    return () => unsubscribe()
  }, [])

  // Auto-scroll when new logs arrive if enabled
  useEffect(() => {
    if (autoScroll && isOpen && streamEndRef.current) {
      streamEndRef.current.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs, autoScroll, isOpen])

  // Global hotkey: Ctrl + ~ or Alt + L
  useEffect(() => {
    const handleKeyDown = (e) => {
      if ((e.ctrlKey && (e.key === '`' || e.key === '~')) || (e.altKey && (e.key === 'l' || e.key === 'L'))) {
        e.preventDefault()
        setIsOpen((prev) => !prev)
      } else if (e.key === 'Escape' && isOpen) {
        setIsOpen(false)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen])

  // Filter logs by level and search query
  const filteredLogs = logs.filter((entry) => {
    if (filterLevel !== 'ALL' && entry.level !== filterLevel) {
      return false
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      const inMsg = entry.message.toLowerCase().includes(q)
      const inModule = entry.module.toLowerCase().includes(q)
      const inData = entry.data && JSON.stringify(entry.data).toLowerCase().includes(q)
      if (!inMsg && !inModule && !inData) return false
    }
    return true
  })

  const errorCount = stats.counts.ERROR + stats.counts.FATAL
  const warnCount = stats.counts.WARN

  const handleSimulateTestLog = () => {
    const samples = [
      () => logger.info('Catalog', 'Filtered 36 recommendations against user components', { matched: 3 }),
      () => logger.perf('AI Search', 'Ollama local inference response generated', 342.1, { tokens: 184 }),
      () => logger.warn('Inventory', 'Component condition marked as defective', { component: 'potentiometer' }),
      () => logger.error('Vision', 'MobileNetV3 low-confidence detection warning', { confidence: 0.32, threshold: 0.65 }),
    ]
    const pick = samples[Math.floor(Math.random() * samples.length)]
    pick()
  }

  return (
    <>
      {/* Floating launcher badge in bottom-right corner */}
      <button
        type="button"
        className="log-launcher"
        onClick={() => setIsOpen(true)}
        aria-label="Open ReWire LogViewer DevTools Console"
      >
        <span className="launcher-icon">⌁</span>
        <span>Logs</span>
        <span className="count-badge">{stats.total}</span>
        {errorCount > 0 && <span className="error-badge">{errorCount}</span>}
        <span className="shortcut-hint">Ctrl+~</span>
      </button>

      {/* Main Drawer Overlay */}
      {isOpen && (
        <div className="log-modal-overlay" onClick={() => setIsOpen(false)}>
          <div className="log-modal-panel" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="DevTools Console">
            {/* Header */}
            <div className="log-header">
              <div className="log-header-top">
                <div className="log-header-title">
                  <span>ReWire Observability & Log Console</span>
                  <span className="live-indicator">
                    <i /> Live Stream
                  </span>
                </div>
                <div className="log-header-actions">
                  <button type="button" className="log-btn" onClick={handleSimulateTestLog} title="Generate demo log event">
                    + Test Log
                  </button>
                  <button type="button" className="log-btn" onClick={() => logger.copyToClipboard()} title="Copy logs to clipboard">
                    Copy
                  </button>
                  <button type="button" className="log-btn" onClick={() => logger.exportJson()} title="Download structured JSON logs">
                    JSON ↓
                  </button>
                  <button type="button" className="log-btn" onClick={() => logger.exportText()} title="Download plain-text logs">
                    LOG ↓
                  </button>
                  <button type="button" className="log-btn" onClick={() => logger.clear()} title="Clear console buffer">
                    Clear
                  </button>
                  <button type="button" className="log-btn close" onClick={() => setIsOpen(false)} aria-label="Close logs">
                    ✕
                  </button>
                </div>
              </div>

              {/* Toolbar: Search & Level Filters */}
              <div className="log-toolbar">
                <input
                  type="text"
                  className="log-search-input"
                  placeholder="Filter logs or modules..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                />

                <div className="log-filter-chips">
                  <button
                    type="button"
                    className={`chip-btn ${filterLevel === 'ALL' ? 'active' : ''}`}
                    onClick={() => setFilterLevel('ALL')}
                  >
                    ALL ({stats.total})
                  </button>
                  <button
                    type="button"
                    className={`chip-btn chip-error ${filterLevel === 'ERROR' ? 'active' : ''}`}
                    onClick={() => setFilterLevel('ERROR')}
                  >
                    ERRORS ({errorCount})
                  </button>
                  <button
                    type="button"
                    className={`chip-btn chip-warn ${filterLevel === 'WARN' ? 'active' : ''}`}
                    onClick={() => setFilterLevel('WARN')}
                  >
                    WARNS ({warnCount})
                  </button>
                  <button
                    type="button"
                    className={`chip-btn ${filterLevel === 'INFO' ? 'active' : ''}`}
                    onClick={() => setFilterLevel('INFO')}
                  >
                    INFO ({stats.counts.INFO})
                  </button>
                  <button
                    type="button"
                    className={`chip-btn chip-perf ${filterLevel === 'PERF' ? 'active' : ''}`}
                    onClick={() => setFilterLevel('PERF')}
                  >
                    PERF ({stats.counts.PERF})
                  </button>
                  <button
                    type="button"
                    className={`chip-btn ${filterLevel === 'DEBUG' ? 'active' : ''}`}
                    onClick={() => setFilterLevel('DEBUG')}
                  >
                    DEBUG ({stats.counts.DEBUG})
                  </button>
                </div>

                <label style={{ fontSize: '11px', color: '#8da493', display: 'flex', alignItems: 'center', gap: '5px', cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={autoScroll}
                    onChange={(e) => setAutoScroll(e.target.checked)}
                  />
                  Auto-scroll
                </label>
              </div>
            </div>

            {/* Log Stream List */}
            <div className="log-stream">
              {filteredLogs.length === 0 ? (
                <div className="log-empty">
                  <span>No log records match the current filter.</span>
                </div>
              ) : (
                filteredLogs.map((entry) => {
                  const hasDetails = Boolean(entry.data || entry.stack)
                  const isExpanded = expandedId === entry.id
                  return (
                    <div key={entry.id} className={`log-row ${entry.level}`}>
                      <div className="log-row-main">
                        <span className="log-time">{entry.timeFormatted}</span>
                        <span className={`log-badge ${entry.level}`}>{entry.level}</span>
                        <span className="log-module">[{entry.module}]</span>
                        <span className="log-msg">{entry.message}</span>
                        {entry.durationMs !== undefined && (
                          <span className="log-duration">{entry.durationMs}ms</span>
                        )}
                        {hasDetails && (
                          <button
                            type="button"
                            className="log-expand-btn"
                            onClick={() => setExpandedId(isExpanded ? null : entry.id)}
                            title="Toggle details"
                          >
                            {isExpanded ? '▲ Hide' : '▼ View Data'}
                          </button>
                        )}
                      </div>

                      {/* Expandable JSON / Stack View */}
                      {isExpanded && hasDetails && (
                        <div className="log-details">
                          {entry.data && (
                            <pre>{JSON.stringify(entry.data, null, 2)}</pre>
                          )}
                          {entry.stack && (
                            <pre className="log-stack">{entry.stack}</pre>
                          )}
                        </div>
                      )}
                    </div>
                  )
                })
              )}
              <div ref={streamEndRef} />
            </div>

            {/* Footer Metrics */}
            <div className="log-footer">
              <div className="log-footer-stats">
                <span><b>Total:</b> {stats.total}</span>
                <span><b>Errors:</b> {errorCount}</span>
                <span><b>Warnings:</b> {warnCount}</span>
                <span><b>Uptime:</b> {stats.uptimeSeconds}s</span>
              </div>
              <div>
                <span>Telemetry Sink: <b>Active</b></span>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
