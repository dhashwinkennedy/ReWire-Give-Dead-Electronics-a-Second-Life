import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import ErrorBoundary from './components/ErrorBoundary'
import './styles.css'
import logger from './services/logger'

logger.info('System', 'ReWire client application bootstrapping', {
  mode: import.meta.env.MODE,
  baseUrl: import.meta.env.VITE_API_BASE_URL || '(same-origin fallback)',
  userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : 'unknown',
})

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>
)
