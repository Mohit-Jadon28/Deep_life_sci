import { useEffect, useRef, useState } from 'react'
import { api, streamChat } from './api.js'
import { Sidebar, Harness, Message, Empty } from './components.jsx'

const NODE_OF = (name, a = {}) => ({
  pubmed_search: ['pubmed'], ctgov_search: ['ctgov'], web_search: ['web'], python_sandbox: ['sandbox'], memory_save: ['memory'],
  delegate: [a.agent, { 'abstract-analyst': 'pubmed', 'full-text-analyst': 'pmc', 'trial-analyst': 'ctgov' }[a.agent]].filter(Boolean),
}[name] || [])

const blank = () => ({ role: 'assistant', content: '', trace: [], plan: [], files: [], nodes: {}, active: [], status: 'Starting', usage: null, done: false })

function reduce(m, e) {
  switch (e.type) {
    case 'status': return { ...m, status: e.text }
    case 'plan': return { ...m, plan: e.todos }
    case 'usage': return { ...m, usage: { used: e.used, limit: e.limit } }
    case 'tool_call': {
      const ns = NODE_OF(e.name, e.args), nodes = { ...m.nodes }
      ns.forEach(n => { nodes[n] = (nodes[n] || 0) + 1 })
      return { ...m, nodes, active: ns, status: `Running ${e.name.replace('_', ' ')}`, trace: [...m.trace, { id: e.id, name: e.name, args: e.args, state: 'run' }] }
    }
    case 'tool_result': return { ...m, trace: m.trace.map(t => t.id === e.id ? { ...t, state: 'done', out: e.preview } : t) }
    case 'sub_start': return { ...m, trace: [...m.trace, { id: e.id, name: e.agent, args: { target: e.target }, state: 'run', sub: true }] }
    case 'sub_end': return { ...m, trace: m.trace.map(t => t.id === e.id ? { ...t, state: 'done', out: e.summary } : t) }
    case 'file': return { ...m, files: m.files.some(f => f.name === e.name) ? m.files : [...m.files, e] }
    case 'final': return { ...m, content: e.text, active: [], status: 'Done' }
    case 'error': return { ...m, error: e.text, active: [], status: 'Stopped' }
    case 'done': return { ...m, done: true, active: [] }
    default: return m
  }
}

export default function App() {
  const [cfg, setCfg] = useState(null)
  const [sessions, setSessions] = useState([])
  const [sid, setSid] = useState(null)
  const [msgs, setMsgs] = useState([])
  const [mem, setMem] = useState([])
  const [running, setRunning] = useState(false)
  const [hide, setHide] = useState(true)
  const [limit, setLimit] = useState(30000)
  const [input, setInput] = useState('')
  const [files, setFiles] = useState([])
  const abort = useRef(null), end = useRef(null), fileIn = useRef(null)

  const refresh = () => { api.sessions().then(setSessions); api.memory().then(setMem) }
  useEffect(() => { api.config().then(c => { setCfg(c); setLimit(c.default_limit) }); refresh() }, [])
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth' }) }, [msgs])

  const open = async id => {
    setSid(id)
    const rows = await api.messages(id)
    setMsgs(rows.map(r => ({ ...blank(), role: r.role, content: r.content, files: r.meta?.files || [], usage: r.meta?.usage || null, done: true })))
  }
  const fresh = () => { setSid(null); setMsgs([]) }
  const remove = async id => { await api.delSession(id); if (id === sid) fresh(); refresh() }

  const send = async text => {
    text = (text ?? input).trim()
    if (!text || running) return
    let id = sid
    if (!id) { id = (await api.newSession()).id; setSid(id) }
    const names = []
    for (const f of files) names.push((await api.upload(id, f)).name)
    setInput(''); setFiles([]); setRunning(true)
    setMsgs(p => [...p, { role: 'user', content: text, attach: names }, blank()])
    abort.current = new AbortController()
    const upd = e => setMsgs(p => { const q = [...p]; q[q.length - 1] = reduce(q[q.length - 1], e); return q })
    try { await streamChat({ session_id: id, message: text, attachments: names, token_limit: limit }, upd, abort.current.signal) }
    catch (err) { if (err.name !== 'AbortError') upd({ type: 'error', text: String(err) }) }
    upd({ type: 'done' }); setRunning(false); refresh()
  }

  const last = [...msgs].reverse().find(m => m.role === 'assistant')
  return (
    <div className="app">
      <Sidebar sessions={sessions} sid={sid} onOpen={open} onNew={fresh} onDelete={remove} mem={mem}
        onForget={async id => { await api.delMemory(id); refresh() }} cfg={cfg} />
      <main className="main">
        <header className="top"><h1>Deep Life Sci</h1><span className="sub">Biomedical research agent · PubMed · PMC · ClinicalTrials.gov</span></header>
        <div className="thread">
          {msgs.length === 0 ? <Empty onPick={send} /> : (
            <div className="col">
              {msgs.map((m, i) => <Message key={i} m={m} sid={sid} hide={hide} live={running && i === msgs.length - 1} />)}
              <div ref={end} />
            </div>
          )}
        </div>
        <div className="composer-wrap"><div className="composer">
          {files.length > 0 && <div className="chips">{files.map((f, i) => <span key={i} className="chip">{f.name}<button onClick={() => setFiles(files.filter((_, j) => j !== i))} aria-label="Remove file">×</button></span>)}</div>}
          <textarea value={input} placeholder="Ask a research question or attach a paper, figure or dataset…" rows={2}
            onChange={e => setInput(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} />
          <div className="bar">
            <label className="toggle"><input type="checkbox" checked={hide} onChange={e => setHide(e.target.checked)} /><i />Hide tool calls</label>
            <button className="ghost" onClick={() => fileIn.current.click()}>+ Attach files</button>
            <input ref={fileIn} type="file" multiple hidden onChange={e => { setFiles([...files, ...e.target.files]); e.target.value = '' }} />
            <label className="budget" title="Maximum tokens this run may use (free-tier friendly)">Budget {Math.round(limit / 1000)}k
              <input type="range" min="8000" max="80000" step="2000" value={limit} onChange={e => setLimit(+e.target.value)} /></label>
            {running ? <button className="send stop" onClick={() => abort.current?.abort()}>Stop</button>
              : <button className="send" disabled={!input.trim()} onClick={() => send()}>Send</button>}
          </div>
        </div></div>
      </main>
      <Harness m={last} running={running} />
    </div>
  )
}
