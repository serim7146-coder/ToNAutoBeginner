import { useState } from 'react'
import { useRounds } from './hooks/useRounds'
import { terrorName } from './terrors'
import './App.css'

const COLORS = {
  "Classic": "#6495ED", "Classic.exe": "#4169E1", "Alternate": "#D8D8D8",
  "Midnight": "#8B0000", "Bloodbath": "#FF2020", "Bloodbath EX": "#FF4040",
  "Double Trouble": "#9370DB", "Ghost": "#87CEEB", "Fog": "#808080",
  "Punished": "#FFD700", "Randomizer": "#FFD700", "Sabotage": "#32CD32",
  "8 Pages": "#DEB887", "Unbound": "#FF8C00", "Cracked": "#FF6347",
  "Run": "#FF69B4", "Special": "#C0C0C0",
}
const colorFor = r => {
  for (const [k, v] of Object.entries(COLORS)) if (r.includes(k)) return v
  return "#555577"
}

function StatCard({ title, value, sub }) {
  return (
    <div className="stat-card">
      <h3>{title}</h3>
      <div className="value">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  )
}

function BarChart({ counts, color }) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 30)
  const max = entries[0]?.[1] || 1
  return (
    <div className="bar-list">
      {entries.map(([name, count]) => (
        <div key={name} className="bar-item">
          <span className="bar-label" title={name}>{name}</span>
          <div className="bar-track">
            <div className="bar-fill" style={{ width: `${count / max * 100}%`, background: color }} />
          </div>
          <span className="bar-count">{count.toLocaleString()}</span>
        </div>
      ))}
    </div>
  )
}

export default function App() {
  const [input, setInput] = useState('')
  const [playerHash, setPlayerHash] = useState(null)
  const [excluded, setExcluded] = useState(new Set(['Classic', 'Run']))  // デフォルト除外
  const { rows, loading, error } = useRounds(playerHash)

  // 全ラウンド名リスト
  const allRounds = [...new Set(rows.map(r => r.round))].sort()

  // 除外フィルタ適用
  const filteredRows = rows.filter(r => !excluded.has(r.round))

  const toggleExclude = (round) => {
    setExcluded(prev => {
      const next = new Set(prev)
      if (next.has(round)) next.delete(round)
      else next.add(round)
      return next
    })
  }

  // 集計はfilteredRowsを使う
  const roundCounts = {}
  const terrorCounts = {}
  const mapCounts = {}
  filteredRows.forEach(r => {
    roundCounts[r.round] = (roundCounts[r.round] || 0) + 1
    if (Array.isArray(r.terror_ids)) {
      r.terror_ids.forEach(id => {
        const n = terrorName(id)
        terrorCounts[n] = (terrorCounts[n] || 0) + 1
      })
    }
    const k = r.map_id != null ? `Map ${r.map_id}` : 'Unknown'
    mapCounts[k] = (mapCounts[k] || 0) + 1
  })

  const top = Object.entries(roundCounts).sort((a, b) => b[1] - a[1])[0]
  const dates = rows.map(r => String(r.date)).sort()
  const fmt = d => `${d.slice(0,4)}/${d.slice(4,6)}/${d.slice(6,8)}`

  return (
    <div className="container">
      <header>
        <h1>TERRORS OF NOWHERE</h1>
        <div className="divider" />
        <p>Round Statistics Dashboard</p>
      </header>

      <div className="filter-bar">
        <div className="round-filter">
          {allRounds.map(round => (
            <label key={round} className="filter-chip"
              style={{ opacity: excluded.has(round) ? 0.4 : 1 }}>
              <input type="checkbox"
                checked={!excluded.has(round)}
                onChange={() => toggleExclude(round)} />
              <span style={{ color: colorFor(round) }}>■</span>
              {round}
            </label>
          ))}
        </div>
        <label>Player Hash</label>
        <input
          type="number"
          value={input}
          onChange={e => setInput(e.target.value)}
          placeholder="ハッシュ値（空=全員）"
        />
        <button onClick={() => setPlayerHash(input ? Number(input) : null)}>読み込む</button>
        <button onClick={() => { setInput(''); setPlayerHash(null) }}>全員表示</button>
        <span className="status">{loading ? '読み込み中…' : `${rows.length.toLocaleString()}件`}</span>
      </div>

      {error && <p style={{ color: '#c44' }}>エラー: {error}</p>}

      <div className="stats-row">
        <StatCard title="総ラウンド数" value={rows.length.toLocaleString()} />
        <StatCard title="最多ラウンド" value={top?.[0] ?? '—'} sub={top ? `${top[1].toLocaleString()} rounds` : ''} />
        <StatCard title="ユニークラウンド" value={new Set(rows.map(r => r.round)).size} />
        <StatCard title="記録期間" value={dates.length ? `${fmt(dates[0])} 〜 ${fmt(dates[dates.length-1])}` : '—'} />
      </div>

      <div className="grid-2">
        <div className="panel">
          <h2>テラー遭遇ランキング</h2>
          <BarChart counts={terrorCounts} color="#a07bd4" />
        </div>
        <div className="panel">
          <h2>ラウンド別詳細</h2>
          <BarChart counts={roundCounts} color="#c9a84c" />
        </div>
      </div>

      <div className="grid-2">
        <div className="panel">
          <h2>マップ別ラウンド数</h2>
          <BarChart counts={mapCounts} color="#4a9a8a" />
        </div>
      </div>
    </div>
  )
}