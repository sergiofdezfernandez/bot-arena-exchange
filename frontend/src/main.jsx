import React, { useEffect, useMemo, useState, useRef, useCallback } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'

const WS_URL =
  import.meta.env.VITE_WS_URL ||
  (import.meta.env.VITE_API_BASE_URL
    ? import.meta.env.VITE_API_BASE_URL.replace(/^http/, 'ws') + '/ws/stream'
    : 'ws://127.0.0.1:8000/ws/stream')

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000'

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
  return response.json()
}

// ---------------------------------------------------------------------------
// Neon palette for the PnL area chart
// ---------------------------------------------------------------------------
const NEON_PALETTE = [
  '#22c55e', // green
  '#06b6d4', // cyan
  '#d946ef', // magenta
  '#f97316', // orange
  '#fbbf24', // amber
  '#ef4444', // red
  '#a78bfa', // violet
  '#2dd4bf', // teal
]

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------
function App() {
  const [markets, setMarkets] = useState({ VENUE_1: null, VENUE_2: null })
  const [recentTrades, setRecentTrades] = useState([])
  const [wsStatus, setWsStatus] = useState('connecting')
  const [config, setConfig] = useState(null)
  const [tournaments, setTournaments] = useState([])
  const [error, setError] = useState('')

  const [leaderboard, setLeaderboard] = useState([])
  const [pnlHistory, setPnlHistory] = useState([])

  // Master-Detail: selected trader for the detail chart
  const [selectedTrader, setSelectedTrader] = useState(null)

  const tournamentId = useMemo(
    () => config?.tournament_id || tournaments[0]?.tournament_id,
    [config, tournaments],
  )

  // ---- WebSocket connection ----
  const wsRef = useRef(null)
  const reconnectTimer = useRef(null)

  const connectWs = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return
    setWsStatus('connecting')

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setWsStatus('open')
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = null
      }
    }

    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data)
        const type = data.event_type || data.type

        switch (type) {
          case 'snapshot': {
            const marketData = data.market || data.payload
            const venue = marketData?.venue || data.payload?.venue
            if (venue) {
              setMarkets((prev) => ({ ...prev, [venue]: marketData }))
            }
            break
          }
          case 'ORDERBOOK_UPDATE': {
            const venue = data.payload?.venue
            if (venue) {
              setMarkets((prev) => ({ ...prev, [venue]: data.payload }))
            }
            break
          }
          case 'FILL':
            setRecentTrades((prev) => [data, ...prev].slice(0, 10))
            break
          case 'LEADERBOARD_UPDATE': {
            const lb = data.payload.leaderboard || []
            setLeaderboard(lb)
            setPnlHistory((prev) => {
              const snapshot = { timestamp: Date.now(), scores: lb }
              const next = [...prev, snapshot]
              return next.length > 100 ? next.slice(-100) : next
            })
            break
          }
          default:
            break
        }
      } catch {
        // ignore malformed messages
      }
    }

    ws.onclose = () => {
      setWsStatus('closed')
      if (!reconnectTimer.current) {
        reconnectTimer.current = setTimeout(() => {
          reconnectTimer.current = null
          connectWs()
        }, 3000)
      }
    }

    ws.onerror = () => { /* onclose will fire */ }
  }, [])

  useEffect(() => {
    refreshRestState().catch((err) => setError(`API unavailable: ${err.message}`))
    connectWs()
    return () => {
      if (wsRef.current) wsRef.current.close()
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
    }
  }, [connectWs])

  async function refreshRestState() {
    setError('')
    const [nextConfig, nextTournaments] = await Promise.all([
      api('/config'),
      api('/tournaments'),
    ])
    setConfig(nextConfig)
    setTournaments(nextTournaments)
  }

  // ---- Mid-price & Cross-spread calculations (with strict null guards) ----
  const v1 = markets.VENUE_1
  const v2 = markets.VENUE_2
  const v1Bid = v1?.best_bid ?? null
  const v1Ask = v1?.best_ask ?? null
  const v2Bid = v2?.best_bid ?? null
  const v2Ask = v2?.best_ask ?? null

  const v1Mid = (v1Bid != null && v1Ask != null) ? ((v1Bid + v1Ask) / 2) : null
  const v2Mid = (v2Bid != null && v2Ask != null) ? ((v2Bid + v2Ask) / 2) : null

  const crossSpread1 = (v1Bid != null && v2Ask != null) ? (v1Bid - v2Ask) : null
  const crossSpread2 = (v2Bid != null && v1Ask != null) ? (v2Bid - v1Ask) : null
  const crossSpread =
    crossSpread1 != null && crossSpread2 != null
      ? Math.max(crossSpread1, crossSpread2)
      : crossSpread1 ?? crossSpread2
  const hasArb = crossSpread != null && crossSpread > 0
  const tourneyStatus = tournaments[0]?.status || 'ACTIVE'

  const handleSelectTrader = useCallback((id) => {
    setSelectedTrader((prev) => (prev === id ? null : id))
  }, [])

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-left">
          <h1 className="app-title">Bot Arena Exchange</h1>
        </div>
        <div className="topbar-center">
          <span className={`ws-badge ${wsStatus === 'open' ? 'ws-live' : 'ws-dead'}`}>
            <span className="ws-dot" />
            {wsStatus === 'open' ? 'Connected' : wsStatus === 'connecting' ? 'Connecting…' : 'Disconnected'}
          </span>
        </div>
        <div className="topbar-right">
          <span className="user-avatar" title="web-user">W</span>
          <span className="user-name">web-user</span>
        </div>
      </header>

      <section className="metrics-bar">
        <div className="metric">
          <span className="metric-label">Tourney</span>
          <span className="metric-value">{tournamentId || '—'}</span>
          <span className={`metric-tag ${tourneyStatus === 'ACTIVE' ? 'tag-active' : ''}`}>{tourneyStatus}</span>
        </div>
        <div className="metric">
          <span className="metric-label">V1 MID</span>
          <span className="metric-value metric-mid">{v1Mid != null ? v1Mid.toFixed(2) : '—'}</span>
        </div>
        <div className="metric">
          <span className="metric-label">V2 MID</span>
          <span className="metric-value metric-mid">{v2Mid != null ? v2Mid.toFixed(2) : '—'}</span>
        </div>
        <div className={`metric ${hasArb ? 'metric-green-flash' : ''}`}>
          <span className="metric-label">CROSS-SPREAD</span>
          <span className={`metric-value ${hasArb ? 'metric-green' : ''}`}>
            {crossSpread != null ? crossSpread.toFixed(2) : '—'}
          </span>
        </div>
      </section>

      {error && <div className="error-banner">{error}</div>}

      <section className="split-main">
        <div className="left-col">
          <TraderDetailPanel
            selectedTrader={selectedTrader}
            history={pnlHistory}
            leaderboard={leaderboard}
          />
          <LeaderboardPanel
            leaderboard={leaderboard}
            history={pnlHistory}
            selectedTrader={selectedTrader}
            onSelectTrader={handleSelectTrader}
          />
        </div>
        <div className="right-col">
          <div className="ob-dual-container">
            <OrderBookPanel market={markets.VENUE_1} title="VENUE 1 (Base)" />
            <OrderBookPanel market={markets.VENUE_2} title="VENUE 2 (Lagging)" />
          </div>
          <CompactTradesPanel trades={recentTrades} />
        </div>
      </section>
    </div>
  )
}

// =========================================================================
// TraderDetailPanel — Master detail view for selected trader
// =========================================================================
function TraderDetailPanel({ selectedTrader, history, leaderboard }) {
  // Extract the selected trader's series from history
  const points = useMemo(() => {
    if (!selectedTrader || !history.length) return []
    const result = []
    for (let i = 0; i < history.length; i++) {
      const scores = history[i].scores || []
      const entry = scores.find(s => s.trader_id === selectedTrader)
      result.push({
        index: i,
        pnl: entry ? (entry.realized_pnl ?? entry.adjusted_score ?? 0) : null,
        score: entry ? (entry.adjusted_score ?? 0) : null,
      })
    }
    return result
  }, [selectedTrader, history])

  // Current stats for the selected trader
  const currentStats = useMemo(() => {
    if (!leaderboard.length || !selectedTrader) return null
    return leaderboard.find(r => r.trader_id === selectedTrader) || null
  }, [leaderboard, selectedTrader])

  if (!selectedTrader) {
    return (
      <div className="detail-panel">
        <div className="panel-header">
          <h2>Trader Detail</h2>
        </div>
        <div className="detail-placeholder">
          Select a trader from the leaderboard
        </div>
      </div>
    )
  }

  const validPoints = points.filter(p => p.pnl != null && !isNaN(p.pnl))
  const lastPnl = validPoints.length ? validPoints[validPoints.length - 1].pnl : 0
  const pnlPositive = lastPnl >= 0
  const color = pnlPositive ? '#22c55e' : '#ef4444'

  return (
    <div className="detail-panel">
      <div className="panel-header">
        <h2>{selectedTrader}</h2>
      </div>

      {/* Metric cards */}
      <div className="detail-cards">
        <div className={`detail-card ${pnlPositive ? 'card-positive' : 'card-negative'}`}>
          <span className="detail-card-label">PNL</span>
          <span className="detail-card-value">{lastPnl.toFixed(2)}</span>
        </div>
        <div className="detail-card">
          <span className="detail-card-label">SCORE</span>
          <span className="detail-card-value">
            {currentStats ? currentStats.adjusted_score?.toFixed(2) ?? '—' : '—'}
          </span>
        </div>
        <div className="detail-card">
          <span className="detail-card-label">RANK</span>
          <span className="detail-card-value">
            #{currentStats ? currentStats.rank ?? '—' : '—'}
          </span>
        </div>
      </div>

      {/* Detail area chart */}
      <div className="detail-chart-body">
        {validPoints.length < 2 ? (
          <div className="detail-chart-empty">Collecting data…</div>
        ) : (
          <DetailChart points={validPoints} color={color} />
        )}
      </div>
    </div>
  )
}

// =========================================================================
// DetailChart — Single-trader area chart with gradient
// =========================================================================
function DetailChart({ points, color }) {
  const W = 600
  const H = 200
  const PL = 8
  const PR = 8
  const PT = 8
  const PB = 24

  const pw = W - PL - PR
  const ph = H - PT - PB

  const { yMin, yMax, yRange } = useMemo(() => {
    const vals = points.map(p => p.pnl).filter(v => !isNaN(v))
    if (!vals.length) return { yMin: -1, yMax: 1, yRange: 2 }
    let min = Math.min(...vals)
    let max = Math.max(...vals)
    const span = Math.abs(max - min)
    if (span < 0.001) { const c = min; min = c - 1; max = c + 1; }
    else { const pad = Math.max(span * 0.1, 1); min -= pad; max += pad; }
    return { yMin: min, yMax: max, yRange: Math.max(max - min, 0.001) }
  }, [points])

  const scaleY = useCallback(
    (v) => PT + ph - ((v - yMin) / yRange) * ph,
    [yMin, yRange, PT, ph],
  )

  const areaPath = useMemo(() => {
    if (points.length < 2) return ''
    let d = `M ${PL} ${scaleY(points[0].pnl)}`
    for (let i = 1; i < points.length; i++) {
      const x = PL + (i / (points.length - 1)) * pw
      d += ` L ${x} ${scaleY(points[i].pnl)}`
    }
    d += ` L ${PL + pw} ${PT + ph} L ${PL} ${PT + ph} Z`
    return d
  }, [points, pw, ph, PL, PT, scaleY])

  const linePoints = useMemo(() => {
    return points.map((p, i) => {
      const x = PL + (i / Math.max(points.length - 1, 1)) * pw
      return `${x},${scaleY(p.pnl)}`
    }).join(' ')
  }, [points, pw, PL, scaleY])

  // Grid lines
  const gridLines = useMemo(() => {
    const lines = []
    const steps = 4
    for (let i = 0; i <= steps; i++) {
      const v = yMin + (yRange / steps) * i
      lines.push({ y: scaleY(v), label: v.toFixed(0) })
    }
    return lines
  }, [yMin, yRange, scaleY])

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="detail-svg" preserveAspectRatio="xMidYMid meet">
      <defs>
        <linearGradient id="detail-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.35" />
          <stop offset="100%" stopColor={color} stopOpacity="0.0" />
        </linearGradient>
      </defs>

      {gridLines.map((gl, i) => (
        <line key={`dg-${i}`} x1={PL} y1={gl.y} x2={PL + pw} y2={gl.y} className="chart-grid-line" />
      ))}

      <path d={areaPath} fill="url(#detail-grad)" />
      <polyline points={linePoints} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

// =========================================================================
// Leaderboard Panel with sparklines & click-to-select
// =========================================================================
function LeaderboardPanel({ leaderboard, history, selectedTrader, onSelectTrader }) {
  const prevRanksRef = useRef({})
  const [flashMap, setFlashMap] = useState({})

  useEffect(() => {
    const changes = {}
    for (const row of leaderboard) {
      const prev = prevRanksRef.current[row.trader_id]
      if (prev != null && prev !== row.rank) {
        changes[row.trader_id] = prev < row.rank ? 'down' : 'up'
      }
    }
    const next = {}
    for (const row of leaderboard) {
      next[row.trader_id] = row.rank
    }
    prevRanksRef.current = next

    if (Object.keys(changes).length > 0) {
      setFlashMap(changes)
      const timer = setTimeout(() => setFlashMap({}), 1200)
      return () => clearTimeout(timer)
    }
  }, [leaderboard])

  // Build per-trader sparkline data from history
  const sparkData = useMemo(() => {
    if (!history.length) return {}
    const map = {}
    for (const row of leaderboard) {
      const id = row.trader_id
      map[id] = []
    }
    for (const snap of history) {
      const scores = snap.scores || []
      for (const s of scores) {
        if (map[s.trader_id]) {
          map[s.trader_id].push(s.realized_pnl ?? s.adjusted_score ?? 0)
        }
      }
    }
    return map
  }, [history, leaderboard])

  const rows = leaderboard.length ? leaderboard : []

  return (
    <div className="lb-panel">
      <div className="panel-header">
        <h2>Leaderboard</h2>
        <span className="lb-count">{rows.length} traders</span>
      </div>
      <div className="lb-table-wrap">
        <table className="lb-table">
          <thead>
            <tr>
              <th>#</th>
              <th>Trader</th>
              <th>PnL</th>
              <th>Score</th>
              <th>Trend</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const flash = flashMap[row.trader_id]
              const flashClass = flash === 'up' ? 'rank-flash-up' : flash === 'down' ? 'rank-flash-down' : ''
              const isSelected = selectedTrader === row.trader_id
              const data = sparkData[row.trader_id] || []
              return (
                <tr
                  key={row.trader_id}
                  className={`lb-row ${flashClass} ${isSelected ? 'lb-row-selected' : ''}`}
                  onClick={() => onSelectTrader(row.trader_id)}
                >
                  <td className="lb-rank">{row.rank}</td>
                  <td className="lb-trader">{row.trader_id}</td>
                  <td className={`lb-pnl ${row.realized_pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>
                    {row.realized_pnl != null ? row.realized_pnl.toFixed(2) : '—'}
                  </td>
                  <td className="lb-score">{row.adjusted_score != null ? row.adjusted_score.toFixed(2) : '—'}</td>
                  <td className="lb-spark">
                    <SparkLine data={data} width={70} height={20} />
                  </td>
                </tr>
              )
            })}
            {!rows.length && (
              <tr>
                <td colSpan="5" className="lb-empty">No leaderboard data yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// =========================================================================
// SparkLine — tiny inline SVG sparkline (no axes)
// =========================================================================
function SparkLine({ data, width, height }) {
  const pad = 1
  const pw = width - pad * 2
  const ph = height - pad * 2

  const linePath = useMemo(() => {
    if (!data.length) return ''
    const vals = data.filter(v => typeof v === 'number' && !isNaN(v))
    if (vals.length < 2) return ''
    const min = Math.min(...vals)
    const max = Math.max(...vals)
    const range = max - min || 1
    const sy = (v) => pad + ph - ((v - min) / range) * ph
    let d = `M ${pad} ${sy(vals[0])}`
    for (let i = 1; i < vals.length; i++) {
      const x = pad + (i / (vals.length - 1)) * pw
      d += ` L ${x} ${sy(vals[i])}`
    }
    return d
  }, [data, pw, ph, pad])

  const lastVal = data.length ? data[data.length - 1] : 0
  const positive = lastVal >= 0
  const stroke = positive ? '#22c55e' : '#ef4444'

  if (!linePath) {
    return (
      <svg viewBox={`0 0 ${width} ${height}`} className="spark-svg">
        <line x1={pad} y1={height / 2} x2={width - pad} y2={height / 2} stroke="rgba(255,255,255,0.1)" strokeWidth="1" />
      </svg>
    )
  }

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="spark-svg">
      <path d={linePath} fill="none" stroke={stroke} strokeWidth="1.2" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

// =========================================================================
// Order Book Panel — with depth bars
// =========================================================================
function OrderBookPanel({ market, title }) {
  const snapshot = market?.snapshot
  const bids = snapshot?.bids || []
  const asks = snapshot?.asks || []

  const maxDepth = useMemo(() => {
    let cumBid = 0
    let cumAsk = 0
    for (const b of bids) cumBid += b.quantity
    for (const a of asks) cumAsk += a.quantity
    return Math.max(cumBid, cumAsk, 1)
  }, [bids, asks])

  const askRows = useMemo(() => {
    let cum = 0
    return [...asks].reverse().map((lvl) => {
      cum += lvl.quantity
      return { ...lvl, side: 'ASK', cumQty: cum }
    })
  }, [asks])

  const bidRows = useMemo(() => {
    let cum = 0
    return bids.map((lvl) => {
      cum += lvl.quantity
      return { ...lvl, side: 'BID', cumQty: cum }
    })
  }, [bids])

  const bestBid = market?.best_bid
  const bestAsk = market?.best_ask
  const spread = bestAsk != null && bestBid != null ? (bestAsk - bestBid).toFixed(2) : null

  return (
    <div className="panel ob-panel ob-panel-dual">
      <div className="panel-header">
        <h2>{title || 'Order Book'}</h2>
        {spread != null && <span className="spread-badge">Spread: {spread}</span>}
      </div>
      <table className="ob-table">
        <thead>
          <tr>
            <th>Side</th>
            <th>Qty</th>
            <th>Price</th>
            <th>Asks</th>
            <th>Qty</th>
            <th>Depth</th>
          </tr>
        </thead>
        <tbody>
          {askRows.map((row, i) => (
            <tr key={`ask-${i}`} className="ob-row ob-row-ask">
              <td className="ob-side"></td>
              <td></td>
              <td className="ob-price ob-price-ask">{row.price}</td>
              <td className="ob-asks">{row.quantity}</td>
              <td></td>
              <td className="ob-depth-cell">
                <div className="ob-depth-bar ob-depth-ask" style={{ width: `${(row.cumQty / maxDepth) * 100}%` }} />
              </td>
            </tr>
          ))}
          {spread != null && (
            <tr className="ob-spread-row">
              <td colSpan="6" className="ob-spread-cell">Spread: {spread}</td>
            </tr>
          )}
          {bidRows.map((row, i) => (
            <tr key={`bid-${i}`} className="ob-row ob-row-bid">
              <td className="ob-side">{row.quantity}</td>
              <td></td>
              <td className="ob-price ob-price-bid">{row.price}</td>
              <td className="ob-asks"></td>
              <td>{row.quantity}</td>
              <td className="ob-depth-cell">
                <div className="ob-depth-bar ob-depth-bid" style={{ width: `${(row.cumQty / maxDepth) * 100}%` }} />
              </td>
            </tr>
          ))}
          {!bids.length && !asks.length && (
            <tr>
              <td colSpan="6" className="ob-empty">Waiting for market data…</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

// =========================================================================
// Compact Recent Trades — only last 10 trades, minimal columns
// =========================================================================
function CompactTradesPanel({ trades }) {
  const prevPriceRef = useRef(null)

  const getTrendClass = (price) => {
    if (prevPriceRef.current == null || price == null) return ''
    const cls = price > prevPriceRef.current ? 'trade-up' : price < prevPriceRef.current ? 'trade-down' : ''
    prevPriceRef.current = price
    return cls
  }

  return (
    <div className="panel ct-panel">
      <div className="panel-header">
        <h2>Recent Trades</h2>
        <span className="ct-count">{trades.length} trades</span>
      </div>
      <div className="ct-table-wrap">
        <table className="ct-table">
          <thead>
            <tr>
              <th>Venue</th>
              <th>Time</th>
              <th>Price</th>
              <th>Qty</th>
              <th>Buyer</th>
              <th>Seller</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((ev, i) => {
              const t = ev.payload || {}
              const rawTs = t.timestamp || ev.timestamp || ''
              let time = '-'
              try {
                const ts = typeof rawTs === 'number'
                  ? new Date(rawTs * 1000).toISOString()
                  : typeof rawTs === 'string'
                    ? rawTs
                    : ''
                if (ts.length >= 19) {
                  time = ts.slice(11, 19)
                } else if (ts.length > 0) {
                  time = ts.slice(0, 19)
                }
              } catch {
                time = '-'
              }
              const price = t.price
              const trendClass = i === 0 ? getTrendClass(price) : ''
              const isBotBuyer = (t.buyer_id || '').startsWith('Bot_')
              const isBotSeller = (t.seller_id || '').startsWith('Bot_')
              const venue = t.venue || ''
              const venueLabel = venue === 'VENUE_2' ? 'V2' : venue === 'VENUE_1' ? 'V1' : (venue || '—')
              const venueClass = venue === 'VENUE_2' ? 'venue-v2' : venue === 'VENUE_1' ? 'venue-v1' : ''

              return (
                <tr key={`ct${ev.sequence || i}`} className="ct-row">
                  <td className="ct-venue">
                    <span className={`venue-badge ${venueClass}`}>{venueLabel}</span>
                  </td>
                  <td className="ct-time">{time}</td>
                  <td className={`ct-price ${trendClass}`}>
                    <span className="rt-arrow">{trendClass === 'trade-up' ? '▲' : trendClass === 'trade-down' ? '▼' : ''}</span>
                    {price != null ? price : '-'}
                  </td>
                  <td className="ct-qty">{t.quantity ?? '-'}</td>
                  <td className={`ct-trader ${isBotBuyer ? 'bot-highlight' : ''}`}>{t.buyer_id || '-'}</td>
                  <td className={`ct-trader ${isBotSeller ? 'bot-highlight' : ''}`}>{t.seller_id || '-'}</td>
                </tr>
              )
            })}
            {!trades.length && (
              <tr>
                <td colSpan="6" className="ct-empty">No trades yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

createRoot(document.getElementById('root')).render(<App />)