import React, { useEffect, useState } from 'react'
import networkStatus from '../services/network'
import './NetworkStatusBanner.css'

export default function NetworkStatusBanner() {
  const [online, setOnline] = useState(networkStatus.isOnline())
  const [probing, setProbing] = useState(false)
  const [restoredNotice, setRestoredNotice] = useState(false)

  useEffect(() => {
    let restoredTimer = null
    const unsubscribe = networkStatus.subscribe((isOnline) => {
      setOnline((prev) => {
        if (!prev && isOnline) {
          // Transitioned from offline to online
          setRestoredNotice(true)
          restoredTimer = setTimeout(() => setRestoredNotice(false), 3500)
        }
        return isOnline
      })
    })

    return () => {
      unsubscribe()
      if (restoredTimer) clearTimeout(restoredTimer)
    }
  }, [])

  const handleCheckConnection = async () => {
    setProbing(true)
    try {
      await networkStatus.probe()
    } finally {
      setProbing(false)
    }
  }

  if (online && !restoredNotice) {
    return null
  }

  if (restoredNotice) {
    return (
      <aside className="network-banner restored" role="status" aria-live="polite">
        <div className="network-banner-content">
          <span className="network-badge restored-badge">✓ ONLINE</span>
          <span className="network-text">Connection restored. Live AI-ReWire and image recognition are available.</span>
        </div>
      </aside>
    )
  }

  return (
    <aside className="network-banner offline" role="alert" aria-live="assertive">
      <div className="network-banner-content">
        <span className="network-badge offline-badge">⚡ OFFLINE</span>
        <span className="network-text">
          You are currently offline. Local catalog search remains available, but AI-ReWire and photo identification require internet.
        </span>
        <button
          type="button"
          className="network-probe-btn"
          onClick={handleCheckConnection}
          disabled={probing}
        >
          {probing ? 'Checking…' : 'Check Connection'}
        </button>
      </div>
    </aside>
  )
}
