import os, re, sys, json, time, subprocess
import httpx
import xml.etree.ElementTree as ET

EU = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
HDR = {"User-Agent": "deep-life-sci-react/1.0"}


def _eu(path, **p):
    key = os.getenv("NCBI_API_KEY")
    if key:
        p["api_key"] = key
    time.sleep(0.12 if key else 0.36)
    r = httpx.get(EU + path, params=p, timeout=40, headers=HDR)
    r.raise_for_status()
    return r


def _txt(el):
    return " ".join("".join(el.itertext()).split())


def select_passages(text, question, budget=6000, size=1100):
    paras = [p.strip() for p in re.split(r"\n{1,}", text) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if len(cur) + len(p) > size and cur:
            chunks.append(cur); cur = ""
        cur += p + "\n"
    if cur:
        chunks.append(cur)
    q = set(re.findall(r"[a-z0-9]{3,}", question.lower()))
    scored = []
    for i, c in enumerate(chunks):
        low = c.lower()
        s = sum(low.count(t) for t in q) + (3 if c.startswith("[Table") else 0) + (2 if i == 0 else 0)
        scored.append((s, i, c))
    picked, used = [], 0
    for s, i, c in sorted(scored, reverse=True):
        if used + len(c) > budget:
            continue
        picked.append((i, c)); used += len(c)
    return "\n".join(c for _, c in sorted(picked))


def pubmed_search(query, max_results=10):
    max_results = max(1, min(int(max_results or 10), 25))
    js = _eu("esearch.fcgi", db="pubmed", term=query, retmax=max_results, retmode="json", sort="relevance").json()["esearchresult"]
    ids = js.get("idlist", [])
    if not ids:
        return f"0 results for: {query}"
    sm = _eu("esummary.fcgi", db="pubmed", id=",".join(ids), retmode="json").json()["result"]
    lines = [f"{js.get('count')} total hits; showing {len(ids)}"]
    for i in ids:
        d = sm.get(i, {})
        lines.append(f"PMID {i} | {d.get('pubdate','')[:4]} | {d.get('source','')} | {d.get('title','')[:140]}")
    return "\n".join(lines)


def fetch_abstract(pmid):
    root = ET.fromstring(_eu("efetch.fcgi", db="pubmed", id=pmid, retmode="xml").text)
    title = _txt(root.find(".//ArticleTitle")) if root.find(".//ArticleTitle") is not None else ""
    parts = []
    for a in root.iter("AbstractText"):
        lab = a.get("Label")
        parts.append((f"{lab}: " if lab else "") + _txt(a))
    return f"PMID {pmid}\nTITLE: {title}\n" + ("\n".join(parts) or "No abstract available.")


def pmid_to_pmc(pmid):
    js = _eu("elink.fcgi", dbfrom="pubmed", db="pmc", id=pmid, retmode="json").json()
    try:
        return js["linksets"][0]["linksetdbs"][0]["links"][0]
    except (KeyError, IndexError):
        return None


def pmc_text(ident, question):
    ident = str(ident).strip()
    pmc = re.sub(r"\D", "", ident) if ident.upper().startswith("PMC") else pmid_to_pmc(ident)
    if not pmc:
        return "No open-access full text in PMC for this PMID; use abstract-analyst instead."
    root = ET.fromstring(_eu("efetch.fcgi", db="pmc", id=pmc, retmode="xml").text)
    body = root.find(".//body")
    if body is None:
        return "Full text not available (embargoed or not open access)."
    blocks = []
    ab = root.find(".//abstract")
    if ab is not None:
        blocks.append("ABSTRACT: " + _txt(ab))
    for p in body.iter("p"):
        t = _txt(p)
        if len(t) > 60:
            blocks.append(t)
    for tw in root.iter("table-wrap"):
        cap = _txt(tw.find("caption")) if tw.find("caption") is not None else ""
        rows = []
        for tr in tw.iter("tr"):
            rows.append(" | ".join(_txt(td) for td in tr if td.tag in ("td", "th")))
        blocks.append(f"[Table] {cap}\n" + "\n".join(rows[:30]))
    return select_passages("\n".join(blocks), question)


CT = "https://clinicaltrials.gov/api/v2/studies"


def ctgov_search(condition, intervention=None, phase=None, max_results=15):
    p = {"query.cond": condition, "pageSize": max(1, min(int(max_results or 15), 25)), "countTotal": "true", "format": "json"}
    if intervention:
        p["query.intr"] = intervention
    if phase:
        p["filter.advanced"] = f"AREA[Phase]{phase}"
    d = httpx.get(CT, params=p, timeout=40, headers=HDR)
    d.raise_for_status()
    d = d.json()
    lines = [f"{d.get('totalCount', '?')} total trials; showing {len(d.get('studies', []))}"]
    for s in d.get("studies", []):
        ps = s["protocolSection"]
        idm, dm = ps["identificationModule"], ps.get("designModule", {})
        lines.append("{} | {} | n={} | {} | results={} | {}".format(
            idm["nctId"], "/".join(dm.get("phases", [])), dm.get("enrollmentInfo", {}).get("count", "?"),
            ps.get("statusModule", {}).get("overallStatus", ""), s.get("hasResults", False),
            idm.get("briefTitle", "")[:110]))
    return "\n".join(lines)


def ctgov_record(nct):
    d = httpx.get(f"{CT}/{nct}", params={"format": "json"}, timeout=40, headers=HDR)
    d.raise_for_status()
    d = d.json()
    ps, rs = d["protocolSection"], d.get("resultsSection", {})
    out = {
        "id": nct, "title": ps["identificationModule"].get("briefTitle"),
        "design": {k: ps.get("designModule", {}).get(k) for k in ("phases", "enrollmentInfo")},
        "arms": [{"label": a.get("label"), "type": a.get("type"), "desc": (a.get("description") or "")[:200]}
                 for a in ps.get("armsInterventionsModule", {}).get("armGroups", [])],
        "primary": [{"t": o.get("measure"), "tf": o.get("timeFrame")} for o in ps.get("outcomesModule", {}).get("primaryOutcomes", [])][:4],
    }
    flow = rs.get("participantFlowModule", {}).get("groups", [])
    out["flow_groups"] = [{"id": g["id"], "title": g.get("title")} for g in flow]
    base = rs.get("baselineCharacteristicsModule", {})
    out["baseline"] = []
    for m in base.get("measures", [])[:8]:
        row = {"measure": m.get("title"), "unit": m.get("unitOfMeasure"), "vals": []}
        for cl in m.get("classes", [])[:1]:
            for cat in cl.get("categories", [])[:2]:
                row["vals"] += [(x.get("groupId"), x.get("value"), x.get("spread")) for x in cat.get("measurements", [])]
        out["baseline"].append(row)
    out["results"] = []
    for m in rs.get("outcomeMeasuresModule", {}).get("outcomeMeasures", [])[:5]:
        row = {"title": m.get("title"), "type": m.get("type"), "unit": m.get("unitOfMeasure"),
               "time": m.get("timeFrame"), "groups": [(g["id"], g.get("title")) for g in m.get("groups", [])], "vals": []}
        for cl in m.get("classes", [])[:2]:
            for cat in cl.get("categories", [])[:2]:
                row["vals"] += [(x.get("groupId"), x.get("value")) for x in cat.get("measurements", [])]
        out["results"].append(row)
    return json.dumps(out, separators=(",", ":"))[:9000]


def web_search(query, max_results=4):
    key = os.getenv("TAVILY_API_KEY")
    if not key:
        return "web_search unavailable: TAVILY_API_KEY is not set."
    r = httpx.post("https://api.tavily.com/search", headers={"Authorization": f"Bearer {key}"}, timeout=40,
                   json={"query": query, "max_results": max(1, min(int(max_results or 4), 6)), "search_depth": "basic"})
    r.raise_for_status()
    return "\n".join(f"{x['title'][:90]} | {x['url']} | {x['content'][:320]}" for x in r.json().get("results", [])) or "No results."


def read_document(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(path).pages)
    if ext in (".csv", ".tsv", ".xlsx", ".xls"):
        import pandas as pd
        df = pd.read_csv(path, sep=None, engine="python") if ext in (".csv", ".tsv") else pd.read_excel(path)
        return f"shape={df.shape}\ncolumns={list(df.columns)[:40]}\n{df.head(8).to_string()}\n{df.describe(include='all').T.head(25).to_string()}"
    if ext == ".docx":
        import zipfile
        xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf8", "ignore")
        return re.sub(r"<[^>]+>", " ", xml.replace("</w:p>", "\n"))
    return open(path, encoding="utf8", errors="ignore").read()


BLOCK = re.compile(r"\b(os\.system|subprocess|shutil\.rmtree|socket|requests|urllib|httpx|__import__|eval\(|exec\()")


def run_python(code, workdir, uploads):
    if BLOCK.search(code):
        return "Blocked: network/process/system calls are not allowed in the sandbox.", []
    os.makedirs(workdir, exist_ok=True)
    snap = lambda: {f: os.path.getmtime(os.path.join(workdir, f)) for f in os.listdir(workdir)}
    before = snap()
    prelude = f"import warnings; warnings.filterwarnings('ignore')\nUPLOADS = r'''{uploads}'''\nimport matplotlib; matplotlib.use('Agg')\n"
    script = os.path.join(workdir, "_run.py")
    open(script, "w", encoding="utf8").write(prelude + code)
    env = {k: v for k, v in os.environ.items() if "KEY" not in k.upper() and "TOKEN" not in k.upper()}
    try:
        p = subprocess.run([sys.executable, "_run.py"], cwd=workdir, capture_output=True, text=True, timeout=60, env=env)
        out = (p.stdout + ("\nSTDERR:\n" + p.stderr[-800:] if p.returncode else ""))[:2500]
    except subprocess.TimeoutExpired:
        out = "Timed out after 60s."
    finally:
        if os.path.exists(script):
            os.remove(script)
    after = snap()
    new = [f for f in after if f not in before or after[f] != before[f]]
    return out or "(no output)", new
