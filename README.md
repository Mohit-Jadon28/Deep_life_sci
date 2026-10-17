# Deep Life Sci (React) - biomedical deep-agent harness

FastAPI backend (agent harness, streamed over SSE) + React/Vite UI. Free-tier friendly: Groq models + Tavily search.

## Run on Windows (PowerShell)
Start these commands from the project root (the folder containing `backend` and `frontend`).

```powershell
# 1. Backend
cd .\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# Edit .env and add your GROQ_API_KEY and TAVILY_API_KEY.
# If PowerShell blocks activation, run: Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
uvicorn main:app --reload --port 8000
```

Open a second PowerShell terminal from the project root:

```powershell
# 2. Frontend
cd .\frontend
npm ci
npm run dev
```
Open http://localhost:5173. If PowerShell blocks activation scripts, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` in that terminal and activate again.

For macOS/Linux, start from the project root, use `cd backend`, `python3 -m venv .venv`, `source .venv/bin/activate`, and `cp -n .env.example .env`; use `cd frontend` and `npm ci` in the second terminal.

## Workflow (what happens on every question)
1. **Prime** - BM25 (SQLite FTS5) recalls relevant long-term memory into the system prompt.
2. **Plan** - root agent calls `write_todos`; the plan shows live in the UI.
3. **Act loop** - root calls tools (`pubmed_search`, `ctgov_search`, `web_search`) and **delegates one document per call** to analyst sub-agents (abstract / full-text / trial / figure / document). Sub-agents run in isolated context on the cheap model and return <=160 words.
4. **Compute** - `python_sandbox` builds tables (xlsx/csv) and plots (png); they appear as download/preview cards.
5. **Reflect** - a cheap reviewer checks the draft against the request; one repair pass if something is missing.
6. **Compact** - old tool output is shrunk when context grows.
7. **Finalize + Learn** - answer is saved; up to 2 reusable lessons go to memory (viewable/deletable in the UI).

## Token control (free tier)
- Per-run **token budget** (slider, default 30k) enforced across every model call; at the cap the agent writes its answer from evidence so far.
- Sub-agents use `llama-3.1-8b-instant`; only planning/synthesis uses the 70B model. Change models in `.env`.
- Tool output truncated to 3k chars; PMC/PDF text is reduced to the most relevant passages; sub-agent results are cached per run.
- 429 responses are waited out automatically (status shown in UI).

## Before selling / deploying
- The sandbox is a subprocess with a deny-list and a 60s timeout - **not a security boundary**. Run it in Docker/gVisor/E2B for multi-tenant use.
- Add authentication, per-user session/memory scoping, and rate limiting (all data is currently one SQLite file in `backend/data`).
- Serve the built UI (`npm run build`) behind your reverse proxy with the API.
