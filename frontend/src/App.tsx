import { useEffect, useState } from 'react'
import './App.css'

interface HealthData {
  status: string
  db: string
  version: string
}

function App() {
  const [health, setHealth] = useState<HealthData | null>(null)
  const [loading, setLoading] = useState<boolean>(true)
  const [error, setError] = useState<string | null>(null)

  const checkHealth = async () => {
    setLoading(true)
    setError(null)
    try {
      const response = await fetch('http://127.0.0.1:8000/api/health')
      if (!response.ok) {
        throw new Error(`Backend returned status ${response.status}`)
      }
      const data: HealthData = await response.json()
      setHealth(data)
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Unknown error'
      setError(message)
      setHealth(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    checkHealth()
  }, [])

  return (
    <div className="container">
      <header className="header">
        <h1>ClassMail J</h1>
        <p className="subtitle">Local-first AI Email &amp; Classroom Assistant</p>
      </header>

      <main className="card">
        <h2>Backend Service Status</h2>

        {loading && <p className="status loading">Checking backend health...</p>}

        {!loading && error && (
          <div className="status-box error">
            <div className="status-indicator error-dot"></div>
            <div>
              <h3>Backend Offline / Unhealthy</h3>
              <p className="error-message">Could not connect to http://127.0.0.1:8000/api/health ({error})</p>
            </div>
          </div>
        )}

        {!loading && health && (
          <div className="status-box success">
            <div className="status-indicator success-dot"></div>
            <div>
              <h3>Backend Online</h3>
              <p>Status: <strong>{health.status}</strong></p>
              <p>Database: <strong>{health.db}</strong></p>
              <p>API Version: <strong>{health.version}</strong></p>
            </div>
          </div>
        )}

        <button className="btn" onClick={checkHealth} disabled={loading}>
          {loading ? 'Checking...' : 'Refresh Health Check'}
        </button>
      </main>
    </div>
  )
}

export default App
