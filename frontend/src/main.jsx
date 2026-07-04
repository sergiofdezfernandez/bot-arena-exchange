import React, { useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'

const botSource = `
class Bot:
    def on_tick(self, api):
        return None


def create_bot():
    return Bot()
`

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
  return response.json()
}

function App() {
  const [config, setConfig] = useState(null)
  const [tournaments, setTournaments] = useState([])
  const [submission, setSubmission] = useState(null)
  const [entry, setEntry] = useState(null)
  const [runResult, setRunResult] = useState(null)
  const [leaderboard, setLeaderboard] = useState([])
  const [market, setMarket] = useState(null)
  const [events, setEvents] = useState([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const tournamentId = useMemo(() => config?.tournament_id || tournaments[0]?.tournament_id, [config, tournaments])

  async function refresh() {
    setError('')
    const [nextConfig, nextTournaments, nextLeaderboard, nextMarket, nextEvents] = await Promise.all([
      api('/config'),
      api('/tournaments'),
      api('/leaderboard'),
      api('/market'),
      api('/events'),
    ])
    setConfig(nextConfig)
    setTournaments(nextTournaments)
    setLeaderboard(nextLeaderboard)
    setMarket(nextMarket)
    setEvents(nextEvents.slice(-10).reverse())
  }

  async function runStep(label, action) {
    setLoading(true)
    setError('')
    try {
      await action()
      await refresh()
    } catch (err) {
      setError(`${label} failed: ${err.message}`)
    } finally {
      setLoading(false)
    }
  }

  async function submitBot() {
    const result = await api('/bots/submit', {
      method: 'POST',
      body: JSON.stringify({
        owner_id: 'web-user',
        bot_name: 'react-demo-bot',
        files: { 'bot.py': botSource },
      }),
    })
    setSubmission(result)
  }

  async function enterTournament() {
    const version = submission?.version || 1
    const result = await api(`/tournaments/${tournamentId}/entries`, {
      method: 'POST',
      body: JSON.stringify({ owner_id: 'web-user', bot_name: 'react-demo-bot', version }),
    })
    setEntry(result)
  }

  async function runTournament() {
    const result = await api(`/tournaments/${tournamentId}/run`, { method: 'POST' })
    setRunResult(result)
  }

  useEffect(() => {
    refresh().catch((err) => setError(`API unavailable: ${err.message}`))
  }, [])

  return (
    <main>
      <header className="hero">
        <div>
          <p className="eyebrow">Simulated trading competition</p>
          <h1>Bot Arena Exchange</h1>
          <p>Submit a bot, enter a scheduled tournament, run the exchange loop, and inspect the leaderboard.</p>
        </div>
        <button onClick={() => runStep('Refresh', refresh)} disabled={loading}>Refresh</button>
      </header>

      {error && <section className="error">{error}</section>}

      <section className="grid cards">
        <Card title="Tournament" value={tournamentId || 'Loading'} detail={tournaments[0]?.status || 'Unknown'} />
        <Card title="Best bid" value={market?.best_bid ?? '-'} detail="Top of book" />
        <Card title="Best ask" value={market?.best_ask ?? '-'} detail="Top of book" />
        <Card title="Pending orders" value={market?.pending_orders ?? 0} detail={`Tick ${market?.tick ?? 0}`} />
      </section>

      <section className="panel">
        <h2>Competition flow</h2>
        <div className="actions">
          <button onClick={() => runStep('Submit bot', submitBot)} disabled={loading}>1. Submit Bot</button>
          <button onClick={() => runStep('Enter tournament', enterTournament)} disabled={loading || !tournamentId}>2. Enter Tournament</button>
          <button onClick={() => runStep('Run tournament', runTournament)} disabled={loading || !tournamentId}>3. Run Tournament</button>
        </div>
        <div className="status-grid">
          <Status title="Submission" data={submission} />
          <Status title="Entry" data={entry} />
          <Status title="Run" data={runResult && { status: runResult.status, leaderboard_size: runResult.leaderboard?.length }} />
        </div>
      </section>

      <section className="panel">
        <h2>Leaderboard</h2>
        <table>
          <thead>
            <tr><th>Rank</th><th>Trader</th><th>Raw PnL</th><th>Delta</th><th>Penalty</th><th>Adjusted</th><th>Status</th></tr>
          </thead>
          <tbody>
            {leaderboard.map((row) => (
              <tr key={`${row.rank}-${row.trader_id}`}>
                <td>{row.rank}</td>
                <td>{row.trader_id}</td>
                <td>{row.raw_pnl}</td>
                <td>{row.delta_exposure}</td>
                <td>{row.liquidation_penalty}</td>
                <td>{row.adjusted_score}</td>
                <td>{row.status}</td>
              </tr>
            ))}
            {!leaderboard.length && <tr><td colSpan="7">No results yet. Run a tournament.</td></tr>}
          </tbody>
        </table>
      </section>

      <section className="grid">
        <section className="panel">
          <h2>Recent events</h2>
          <pre>{JSON.stringify(events, null, 2)}</pre>
        </section>
        <section className="panel">
          <h2>Market state</h2>
          <pre>{JSON.stringify(market, null, 2)}</pre>
        </section>
      </section>
    </main>
  )
}

function Card({ title, value, detail }) {
  return <article className="card"><span>{title}</span><strong>{value}</strong><small>{detail}</small></article>
}

function Status({ title, data }) {
  return <article className="status"><h3>{title}</h3><pre>{JSON.stringify(data || { status: 'not started' }, null, 2)}</pre></article>
}

createRoot(document.getElementById('root')).render(<App />)
