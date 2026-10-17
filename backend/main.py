import os, json
import pandas as pd
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
import memory, harness

app = FastAPI(title="Deep Life Sci")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])
DATA = harness.DATA


class ChatReq(BaseModel):
    session_id: str
    message: str
    attachments: list[str] = []
    token_limit: int = int(os.getenv("TOKEN_LIMIT", "30000"))


@app.get("/api/config")
def config():
    return {"root_model": harness.ROOT_MODEL, "sub_model": harness.SUB_MODEL, "default_limit": int(os.getenv("TOKEN_LIMIT", "30000")),
            "groq": bool(os.getenv("GROQ_API_KEY")), "tavily": bool(os.getenv("TAVILY_API_KEY"))}


@app.get("/api/sessions")
def sessions():
    return memory.list_sessions()


@app.post("/api/sessions")
def new_session():
    return {"id": memory.new_session()}


@app.delete("/api/sessions/{sid}")
def del_session(sid: str):
    memory.delete_session(sid)
    return {"ok": True}


@app.get("/api/sessions/{sid}/messages")
def messages(sid: str):
    return memory.get_msgs(sid)


@app.post("/api/sessions/{sid}/upload")
async def upload(sid: str, file: UploadFile = File(...)):
    d = os.path.join(DATA, "uploads", sid)
    os.makedirs(d, exist_ok=True)
    name = os.path.basename(file.filename)
    with open(os.path.join(d, name), "wb") as f:
        f.write(await file.read())
    return {"name": name}


@app.post("/api/chat")
def chat(req: ChatReq):
    def gen():
        try:
            for e in harness.run(req.session_id, req.message, req.attachments, max(4000, min(req.token_limit, 120000))):
                yield f"data: {json.dumps(e)}\n\n"
        except Exception as ex:
            yield f"data: {json.dumps({'type': 'error', 'text': str(ex)})}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/memory")
def mem():
    return memory.all_memories()


@app.delete("/api/memory/{mid}")
def forget(mid: int):
    memory.forget(mid)
    return {"ok": True}


def _path(sid, name):
    p = os.path.join(DATA, "outputs", os.path.basename(sid), os.path.basename(name))
    if not os.path.exists(p):
        raise HTTPException(404, "File not found")
    return p


@app.get("/files/{sid}/{name}")
def files(sid: str, name: str):
    return FileResponse(_path(sid, name), filename=name)


@app.get("/api/preview/{sid}/{name}")
def preview(sid: str, name: str):
    p = _path(sid, name)
    df = pd.read_csv(p) if name.endswith(".csv") else pd.read_excel(p)
    df = df.head(12).fillna("").astype(str)
    return {"columns": list(df.columns), "rows": df.values.tolist(), "total": int(len(pd.read_csv(p) if name.endswith(".csv") else pd.read_excel(p)))}
