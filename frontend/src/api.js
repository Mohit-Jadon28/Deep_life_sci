const j = (u, o) => fetch(u, o).then(r => r.json())
export const api = {
  config: () => j('/api/config'),
  sessions: () => j('/api/sessions'),
  newSession: () => j('/api/sessions', { method: 'POST' }),
  delSession: id => fetch(`/api/sessions/${id}`, { method: 'DELETE' }),
  messages: id => j(`/api/sessions/${id}/messages`),
  memory: () => j('/api/memory'),
  delMemory: id => fetch(`/api/memory/${id}`, { method: 'DELETE' }),
  preview: (sid, name) => j(`/api/preview/${sid}/${encodeURIComponent(name)}`),
  upload: (sid, file) => { const f = new FormData(); f.append('file', file); return j(`/api/sessions/${sid}/upload`, { method: 'POST', body: f }) },
}

// POST + read the SSE stream, calling onEvent for each JSON event
export async function streamChat(body, onEvent, signal) {
  const r = await fetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal })
  const reader = r.body.getReader(), dec = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let i
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2)
      if (chunk.startsWith('data: ')) onEvent(JSON.parse(chunk.slice(6)))
    }
  }
}
