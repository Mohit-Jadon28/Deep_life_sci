import { useEffect, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from './api.js'

export const ANALYSTS = [
  ['abstract-analyst', 'One PubMed abstract'], ['full-text-analyst', 'One paper, in full'], ['trial-analyst', 'One registry record'],
  ['figure-analyst', 'One figure image'], ['document-analyst', 'One uploaded doc'],
]
export const SOURCES = [
  ['pubmed', 'PubMed', '29M abstracts'], ['pmc', 'PMC', '8M open-access papers'], ['ctgov', 'ClinicalTrials.gov', '600k trials'],
  ['web', 'Web search', 'The open web'], ['sandbox', 'Python sandbox', 'Tables, stats, plots'],
]
const EXAMPLES = [
  'Find every published phase 3 trial with an adalimumab arm in moderate-to-severe plaque psoriasis. For each, pull PASI 75 at week 16, arm size and baseline PASI into one table, then plot the response rates with confidence intervals.',
  'Run enrichment analysis on my proteomics screen (attach a CSV), then for each significant hit search the literature for prior evidence linking it to ALS. Separate well-described hits from novel ones.',
  'Summarise the evidence that GLP-1 receptor agonists reduce cardiovascular events, citing the pivotal trials with hazard ratios.',
]

export function Sidebar({ sessions, sid, onOpen, onNew, onDelete, mem, onForget, cfg }) {
  const [tab, setTab] = useState('chats')
  return (
    <aside className="side">
      <button className="new" onClick={onNew}>New research</button>
      <div className="tabs" role="tablist">
        <button className={tab === 'chats' ? 'on' : ''} onClick={() => setTab('chats')}>Sessions</button>
        <button className={tab === 'mem' ? 'on' : ''} onClick={() => setTab('mem')}>Memory <b>{mem.length}</b></button>
      </div>
      <div className="list">
        {tab === 'chats' && (sessions.length === 0 ? <p className="hint">Your research sessions appear here.</p> :
          sessions.map(s => (
            <div key={s.id} className={'row' + (s.id === sid ? ' on' : '')}>
              <button className="grow" onClick={() => onOpen(s.id)}>{s.title}</button>
              <button className="x" onClick={() => onDelete(s.id)} aria-label="Delete session">×</button>
            </div>)))}
        {tab === 'mem' && (mem.length === 0 ? <p className="hint">Lessons and preferences the agent saves will show here. You can delete any of them.</p> :
          mem.map(x => (
            <div key={x.id} className="row mem"><span className="grow">{x.text}</span>
              <button className="x" onClick={() => onForget(x.id)} aria-label="Forget">×</button></div>)))}
      </div>
      {cfg && <div className="keys">
        <span className={cfg.groq ? 'ok' : 'bad'}>{cfg.groq ? 'Groq key set' : 'Groq key missing'}</span>
        <span className={cfg.tavily ? 'ok' : 'bad'}>{cfg.tavily ? 'Tavily key set' : 'Tavily key missing'}</span>
        <small>{cfg.root_model}</small>
      </div>}
    </aside>
  )
}

export function Empty({ onPick }) {
  return (
    <div className="empty">
      <h2>What do you want to find out?</h2>
      <p>The agent plans the work, searches the literature and trial registries, hands each paper to a specialist, and returns a cited answer with downloadable tables and charts.</p>
      <div className="ex">{EXAMPLES.map(t => <button key={t} onClick={() => onPick(t)}>{t}</button>)}</div>
    </div>
  )
}

function FileCard({ f, sid }) {
  const [p, setP] = useState(null)
  const ext = f.name.split('.').pop().toLowerCase()
  useEffect(() => { if (['xlsx', 'csv'].includes(ext)) api.preview(sid, f.name).then(setP).catch(() => {}) }, [f.name, sid])
  if (['png', 'jpg', 'jpeg', 'svg'].includes(ext)) return (
    <figure className="fig"><img src={f.url} alt={f.name} /><figcaption>{f.name} <a href={f.url} download>Download</a></figcaption></figure>)
  return (
    <div className="file">
      <div className="fh"><b>{f.name}</b><span>{(f.size / 1024).toFixed(1)} KB</span><a className="dl" href={f.url} download>Download</a></div>
      {p && <div className="tw"><table><thead><tr>{p.columns.map(c => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>{p.rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody></table>
        {p.total > p.rows.length && <small>Showing {p.rows.length} of {p.total} rows</small>}</div>}
    </div>)
}

function Trace({ items }) {
  if (!items.length) return null
  return (
    <details className="trace" open>
      <summary>{items.length} steps</summary>
      {items.map(t => (
        <div key={t.id} className={'step ' + t.state + (t.sub ? ' sub' : '')}>
          <code>{t.name}</code><span>{Object.values(t.args || {}).join(' · ').slice(0, 110)}</span>
          {t.out && <em>{t.out}</em>}
        </div>))}
    </details>)
}

export function Message({ m, sid, hide, live }) {
  if (m.role === 'user') return (
    <div className="user"><div>{m.content}{m.attach?.length > 0 && <small>Attached: {m.attach.join(', ')}</small>}</div></div>)
  return (
    <div className="bot">
      {live && !m.content && !m.error && <div className="think"><i />{m.status}…</div>}
      {!hide && <Trace items={m.trace} />}
      {m.content && <div className="md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown></div>}
      {m.error && <div className="err"><b>The run stopped.</b> {m.error}</div>}
      {m.files.map(f => <FileCard key={f.name} f={f} sid={sid} />)}
      {m.usage && m.done && <small className="used">{m.usage.used.toLocaleString()} tokens used</small>}
    </div>)
}

export function Harness({ m, running }) {
  const nodes = m?.nodes || {}, act = m?.active || []
  const Node = ([id, a, b]) => (
    <div key={id} className={'node' + (act.includes(id) ? ' act' : '') + (nodes[id] ? ' used' : '')}>
      <div><b>{b ? a : id}</b><span>{b || a}</span></div>{nodes[id] > 0 && <em>{nodes[id]}</em>}
    </div>)
  const pct = m?.usage ? Math.min(100, (m.usage.used / m.usage.limit) * 100) : 0
  return (
    <aside className="rail">
      <h3>Agent harness</h3>
      <div className="root"><b>Root agent</b><span>{running ? m?.status : 'Delegates each document, synthesises the answer'}</span></div>
      <div className="cols">
        <div><h4>Analyst sub-agents</h4>{ANALYSTS.map(([id, d]) => Node([id, d]))}</div>
        <div><h4>Tools</h4>{SOURCES.map(([id, n, d]) => Node([id, n, d]))}</div>
      </div>
      {m?.plan?.length > 0 && <><h4>Plan</h4><ul className="plan">{m.plan.map((t, i) => <li key={i} className={t.status}>{t.text}</li>)}</ul></>}
      <h4>Token budget</h4>
      <div className="meter"><div style={{ width: pct + '%' }} className={pct > 85 ? 'hot' : ''} /></div>
      <small>{m?.usage ? `${m.usage.used.toLocaleString()} of ${m.usage.limit.toLocaleString()}` : 'Not used yet'}</small>
    </aside>)
}
