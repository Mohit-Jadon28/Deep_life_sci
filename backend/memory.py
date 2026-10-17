import os, re, json, time, uuid, sqlite3, threading

DB = os.path.join(os.path.dirname(__file__), "data", "app.db")
os.makedirs(os.path.dirname(DB), exist_ok=True)
_lock = threading.Lock()
_c = sqlite3.connect(DB, check_same_thread=False)
_c.row_factory = sqlite3.Row
_c.executescript("""
create table if not exists sessions(id text primary key, title text, ts real);
create table if not exists msgs(id integer primary key autoincrement, sid text, role text, content text, meta text, ts real);
create virtual table if not exists memfts using fts5(text, kind unindexed, ts unindexed);
""")


def _q(sql, args=()):
    with _lock:
        cur = _c.execute(sql, args)
        _c.commit()
        return cur.fetchall()


def new_session():
    sid = uuid.uuid4().hex[:10]
    _q("insert into sessions values(?,?,?)", (sid, "New research", time.time()))
    return sid


def list_sessions():
    return [dict(r) for r in _q("select * from sessions order by ts desc limit 50")]


def set_title(sid, title):
    _q("update sessions set title=? where id=?", (title[:60], sid))


def delete_session(sid):
    _q("delete from msgs where sid=?", (sid,))
    _q("delete from sessions where id=?", (sid,))


def add_msg(sid, role, content, meta=None):
    _q("insert into msgs(sid,role,content,meta,ts) values(?,?,?,?,?)",
       (sid, role, content, json.dumps(meta or {}), time.time()))
    _q("update sessions set ts=? where id=?", (time.time(), sid))


def get_msgs(sid):
    return [{"role": r["role"], "content": r["content"], "meta": json.loads(r["meta"] or "{}")}
            for r in _q("select * from msgs where sid=? order by id", (sid,))]


def remember(kind, text):
    text = text.strip()[:400]
    if len(text) < 8 or _q("select 1 from memfts where text=?", (text,)):
        return
    _q("insert into memfts(text,kind,ts) values(?,?,?)", (text, kind, time.time()))


def recall(query, k=4):
    toks = list(dict.fromkeys(re.findall(r"[A-Za-z0-9\-]{3,}", query.lower())))[:12]
    if not toks:
        return []
    match = " OR ".join(f'"{t}"' for t in toks)
    try:
        rows = _q("select text from memfts where memfts match ? order by rank limit ?", (match, k))
    except sqlite3.OperationalError:
        return []
    return [r["text"] for r in rows]


def all_memories():
    return [{"id": r["id"], "text": r["text"], "kind": r["kind"]}
            for r in _q("select rowid as id, text, kind from memfts order by rowid desc limit 100")]


def forget(mid):
    _q("delete from memfts where rowid=?", (mid,))
