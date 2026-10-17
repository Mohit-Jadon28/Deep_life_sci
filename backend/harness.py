
import os, json, time, base64
import httpx
from dotenv import load_dotenv
import memory, tools as T

load_dotenv()
GROQ = "https://api.groq.com/openai/v1/chat/completions"
ROOT_MODEL = os.getenv("ROOT_MODEL", "llama-3.3-70b-versatile")
SUB_MODEL = os.getenv("SUB_MODEL", "llama-3.1-8b-instant")
VISION_MODEL = os.getenv("VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
MAX_STEPS = int(os.getenv("MAX_STEPS", "10"))
TOOL_CHARS = 3000
DATA = os.path.join(os.path.dirname(__file__), "data")
AGENTS = ["abstract-analyst", "full-text-analyst", "trial-analyst", "figure-analyst", "document-analyst"]

SYSTEM = """You are Deep Life Sci, a biomedical research agent. Follow this workflow:
1. PLAN: call write_todos first (3-6 short items). Update statuses as you go.
2. SEARCH: find candidates with pubmed_search, ctgov_search, web_search. Search results only list titles/IDs.
3. DELEGATE: never read whole documents yourself. Send each paper/trial/file to ONE analyst via delegate (one document per call, with a precise question) and use the facts it returns.
4. COMPUTE: use python_sandbox for tables, statistics and plots. Save xlsx/csv/png in the current directory; uploaded files are in UPLOADS. Print what you need.
5. ANSWER: concise markdown. Cite PMIDs/NCT IDs. Write 'not reported' for missing values. Never invent numbers or papers.
Be frugal: prefer few, well-aimed tool calls. Process at most ~8 documents unless asked otherwise and say so."""

SUB_SYS = ("You are {agent}, a biomedical extraction sub-agent. Read the SOURCE and answer the QUESTION with facts only. "
           "Quote numbers exactly with units and the arm/group they belong to. If absent write 'not reported'. Max 160 words.")


def S(t, d=""):
    return {"type": "string", "description": d} if d else {"type": "string"}


def tool(name, desc, props, req):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": req}}}


TOOLS = [
    tool("write_todos", "Create/update the plan.", {"todos": {"type": "array", "items": {"type": "object", "properties": {
        "text": S("t"), "status": {"type": "string", "enum": ["todo", "doing", "done"]}}, "required": ["text", "status"]}}}, ["todos"]),
    tool("pubmed_search", "Search PubMed; returns PMIDs+titles.", {"query": S("t"), "max_results": {"type": "integer"}}, ["query"]),
    tool("ctgov_search", "Search ClinicalTrials.gov; returns NCT IDs.", {"condition": S("t"), "intervention": S("t"), "phase": S("e.g. PHASE3"), "max_results": {"type": "integer"}}, ["condition"]),
    tool("web_search", "Search the open web.", {"query": S("t")}, ["query"]),
    tool("delegate", "Send ONE document to an analyst. abstract-analyst(target=PMID) full-text-analyst(target=PMID or PMCID) trial-analyst(target=NCT id) figure-analyst/document-analyst(target=uploaded filename).",
         {"agent": {"type": "string", "enum": AGENTS}, "target": S("t"), "question": S("what to extract")}, ["agent", "target", "question"]),
    tool("python_sandbox", "Run Python (pandas, numpy, scipy, matplotlib, openpyxl). Save outputs in cwd. Print results.", {"code": S("t")}, ["code"]),
    tool("memory_save", "Save a durable user preference or reusable lesson.", {"text": S("t")}, ["text"]),
]


def ev(t, **k):
    return {"type": t, **k}


def lim(s, n=TOOL_CHARS):
    s = str(s)
    return s if len(s) <= n else s[:n] + f"\n…[truncated {len(s) - n} chars]"


class Ctx:
    def __init__(self, sid, limit):
        self.sid, self.limit, self.used, self.n = sid, limit, 0, 0
        self.todos, self.cache = [], {}
        self.workdir = os.path.join(DATA, "outputs", sid)
        self.uploads = os.path.join(DATA, "uploads", sid)

    def nid(self):
        self.n += 1
        return self.n


def llm(model, messages, ctx, tools=None, max_tokens=700, who="root"):
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set in backend/.env")
    body = {"model": model, "messages": messages, "temperature": 0.2, "max_tokens": max_tokens}
    if tools:
        body.update(tools=tools, tool_choice="auto", parallel_tool_calls=False)
    for attempt in range(5):
        r = httpx.post(GROQ, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=120)
        if r.status_code == 429:
            try:
                wait = float(r.headers.get("retry-after", "12")) + 1
            except ValueError:
                wait = 15
            if wait > 60:
                raise RuntimeError("Groq free-tier daily/minute token limit reached. Retry later or lower the token budget.")
            yield ev("status", text=f"Free-tier rate limit - waiting {int(wait)}s")
            time.sleep(wait)
            continue
        if r.status_code == 400 and "tool_use_failed" in r.text:
            yield ev("status", text="Malformed tool call - retrying")
            body["temperature"] = 0.0 if attempt % 2 else 0.4
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"Groq {r.status_code}: {r.text[:300]}")
        d = r.json()
        ctx.used += d.get("usage", {}).get("total_tokens", 0)
        yield ev("usage", used=ctx.used, limit=ctx.limit, who=who)
        return d
    raise RuntimeError("Model kept failing; please retry.")


def fetch_source(agent, target, question, ctx):
    if agent == "abstract-analyst":
        return T.fetch_abstract(target)
    if agent == "full-text-analyst":
        return T.pmc_text(target, question)
    if agent == "trial-analyst":
        return T.ctgov_record(target.upper())
    path = os.path.join(ctx.uploads, os.path.basename(target))
    if not os.path.exists(path):
        raise FileNotFoundError(f"No uploaded file named {target}")
    if agent == "document-analyst":
        return T.select_passages(T.read_document(path), question, 7000)
    return path  


def run_subagent(agent, target, question, ctx):
    sid = ctx.nid()
    yield ev("sub_start", id=sid, agent=agent, target=target)
    key = (agent, target, question)
    try:
        if key in ctx.cache:
            out = ctx.cache[key]
        else:
            src = fetch_source(agent, target, question, ctx)
            if agent == "figure-analyst":
                ext = os.path.splitext(src)[1].lower().strip(".") or "png"
                b64 = base64.b64encode(open(src, "rb").read()).decode()
                user = [{"type": "text", "text": f"QUESTION: {question}"},
                        {"type": "image_url", "image_url": {"url": f"data:image/{ext};base64,{b64}"}}]
                model = VISION_MODEL
            else:
                user, model = f"QUESTION: {question}\n\nSOURCE:\n{src}", SUB_MODEL
            d = yield from llm(model, [{"role": "system", "content": SUB_SYS.format(agent=agent)},
                                       {"role": "user", "content": user}], ctx, max_tokens=450, who=agent)
            out = d["choices"][0]["message"]["content"] or "not reported"
            ctx.cache[key] = out
    except Exception as e:
        out = f"{agent} failed: {type(e).__name__}: {e}"
    yield ev("sub_end", id=sid, summary=out[:240])
    return out


def exec_tool(name, a, ctx):
    try:
        if name == "write_todos":
            ctx.todos = a.get("todos", [])
            yield ev("plan", todos=ctx.todos)
            return "Plan saved."
        if name == "pubmed_search":
            out = T.pubmed_search(a["query"], a.get("max_results", 10))
        elif name == "ctgov_search":
            out = T.ctgov_search(a["condition"], a.get("intervention"), a.get("phase"), a.get("max_results", 15))
        elif name == "web_search":
            out = T.web_search(a["query"])
        elif name == "delegate":
            out = yield from run_subagent(a["agent"], str(a["target"]), a.get("question", ""), ctx)
        elif name == "python_sandbox":
            out, files = T.run_python(a["code"], ctx.workdir, ctx.uploads)
            for f in files:
                yield ev("file", name=f, url=f"/files/{ctx.sid}/{f}", size=os.path.getsize(os.path.join(ctx.workdir, f)))
        elif name == "memory_save":
            memory.remember("note", a["text"])
            out = "Saved."
        else:
            out = f"Unknown tool {name}"
    except Exception as e:
        out = f"ERROR {type(e).__name__}: {e}"
    return lim(out)


def compact(messages):
    """Context compaction: shrink old tool results, keep recent ones intact."""
    if sum(len(str(m.get("content") or "")) for m in messages) < 22000:
        return False
    idx = [i for i, m in enumerate(messages) if m["role"] == "tool"][:-3]
    for i in idx:
        c = messages[i]["content"]
        if len(c) > 260:
            messages[i]["content"] = c[:240] + " …[compacted]"
    return bool(idx)


def run(sid, text, attachments, limit):
    ctx = Ctx(sid, limit)
    os.makedirs(ctx.uploads, exist_ok=True)
    history = [{"role": m["role"], "content": m["content"][:1500]} for m in memory.get_msgs(sid)[-6:]]
    first = not history

    yield ev("status", step="prime", text="Loading memory")
    mem = memory.recall(text, 4)
    system = SYSTEM + ("\nRelevant memory:\n- " + "\n- ".join(mem) if mem else "")
    att = f"\n[Uploaded files: {', '.join(attachments)}]" if attachments else ""
    memory.add_msg(sid, "user", text + att)
    if first:
        memory.set_title(sid, text)
    messages = [{"role": "system", "content": system}] + history + [{"role": "user", "content": text + att}]
    files, final, reflections = [], "", 0

    for step in range(MAX_STEPS):
        if ctx.used >= ctx.limit:
            yield ev("status", text="Token budget reached - writing answer from evidence so far")
            break
        if compact(messages):
            yield ev("status", step="compact", text="Compacted older tool output")
        yield ev("status", step="act", text="Thinking")
        d = yield from llm(ROOT_MODEL, messages, ctx, TOOLS, 900)
        msg = d["choices"][0]["message"]
        calls = msg.get("tool_calls")
        if not calls:
            final = msg.get("content") or ""
            if reflections < 1 and ctx.used < ctx.limit * 0.7 and len(final) > 40:
                reflections += 1
                yield ev("status", step="reflect", text="Reviewing draft against the request")
                r = yield from llm(SUB_MODEL, [{"role": "user", "content":
                    f"REQUEST: {text[:800]}\nDRAFT: {final[:2200]}\nDoes the draft answer every part with evidence and no invented numbers? "
                    "Reply exactly OK, or one line naming what is missing."}], ctx, max_tokens=80, who="reviewer")
                verdict = (r["choices"][0]["message"]["content"] or "OK").strip()
                if not verdict.upper().startswith("OK"):
                    messages += [{"role": "assistant", "content": final},
                                 {"role": "user", "content": f"Reviewer: {verdict} Fix with minimal extra tool calls, then give the final answer."}]
                    final = ""
                    continue
            break
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": msg["tool_calls"]})
        for c in calls:
            fn = c["function"]["name"]
            try:
                args = json.loads(c["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            tid = ctx.nid()
            yield ev("tool_call", id=tid, name=fn, args={k: (v if len(str(v)) < 160 else str(v)[:160] + "…") for k, v in args.items() if k != "todos"})
            res = yield from exec_tool(fn, args, ctx)
            yield ev("tool_result", id=tid, preview=res[:200])
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": res})

    if not final: 
        yield ev("status", step="finalize", text="Writing final answer")
        messages.append({"role": "user", "content": "Write the final answer now from the evidence gathered. Mark gaps as 'not reported'."})
        d = yield from llm(ROOT_MODEL, messages, ctx, None, 1200)
        final = d["choices"][0]["message"]["content"] or "No answer produced."

   
    if os.path.isdir(ctx.workdir):
        files = [{"name": f, "url": f"/files/{sid}/{f}", "size": os.path.getsize(os.path.join(ctx.workdir, f))}
                 for f in sorted(os.listdir(ctx.workdir)) if not f.startswith("_")]
    memory.add_msg(sid, "assistant", final, {"files": files, "usage": {"used": ctx.used, "limit": ctx.limit}})
    yield ev("final", text=final)

   
    if ctx.used < ctx.limit * 1.1:
        try:
            r = yield from llm(SUB_MODEL, [{"role": "user", "content":
                f"Request: {text[:400]}\nAnswer: {final[:900]}\nList up to 2 durable facts about the user's focus or reusable search lessons, "
                "one per line starting with '- '. If none, write NONE."}], ctx, max_tokens=90, who="memory")
            for ln in (r["choices"][0]["message"]["content"] or "").splitlines():
                if ln.strip().startswith("- "):
                    memory.remember("lesson", ln.strip()[2:])
        except Exception:
            pass
    yield ev("done", used=ctx.used)
