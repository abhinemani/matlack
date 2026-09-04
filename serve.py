"""Web UI. Reads and writes the same meeting.json files the CLI does.

  python serve.py            # http://127.0.0.1:8000
  python serve.py --watch    # also watch data/inbox while serving

Env: APP_PASSWORD (optional, enables basic auth), WATCH_INBOX=1, WORKERS=3, HOST, PORT.
"""
from __future__ import annotations

import json
import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from transcriber import load_env
load_env()

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse  # noqa: E402
from fastapi.security import HTTPBasic, HTTPBasicCredentials  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from fastapi.templating import Jinja2Templates  # noqa: E402

from transcriber import export, pipeline, store  # noqa: E402

HERE = Path(__file__).parent
WORKERS = int(os.environ.get("WORKERS", "3"))

app = FastAPI(title="Meeting transcriber")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters["ts"] = store.fmt_ts

_executor = ThreadPoolExecutor(max_workers=WORKERS)
_watch_thread: threading.Thread | None = None

# --- optional password -------------------------------------------------------
security = HTTPBasic(auto_error=False)


def auth(creds: HTTPBasicCredentials | None = Depends(security)):
    pw = os.environ.get("APP_PASSWORD")
    if not pw:
        return
    if not creds or not secrets.compare_digest(creds.password, pw):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})


# --- helpers -----------------------------------------------------------------
def _meeting_or_404(mid: str) -> dict:
    try:
        return store.load(mid)
    except FileNotFoundError:
        raise HTTPException(404, "No such meeting")


def _public(m: dict) -> dict:
    return {k: m[k] for k in ("id", "title", "status", "error", "created", "updated",
                              "duration_ms", "speakers") if k in m} | {
        "n_utterances": len(m.get("utterances", []))}


def start_watcher() -> None:
    global _watch_thread
    if _watch_thread:
        return
    stop = threading.Event()
    _watch_thread = threading.Thread(
        target=pipeline.watch, kwargs={"workers": WORKERS, "stop": stop}, daemon=True)
    _watch_thread.start()


@app.on_event("startup")
def _startup():
    store.ensure_dirs()
    if os.environ.get("WATCH_INBOX") == "1":
        start_watcher()
    # Resume anything interrupted by a restart.
    for mid in pipeline.pending_ids():
        _executor.submit(pipeline.process_meeting, mid)


# --- pages -------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse, dependencies=[Depends(auth)])
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {
        "meetings": [_public(m) for m in store.list_meetings()],
        "watching": _watch_thread is not None,
        "inbox": str(store.INBOX_DIR),
        "store": store,
    })


@app.get("/t/{mid}", response_class=HTMLResponse, dependencies=[Depends(auth)])
def transcript_page(request: Request, mid: str):
    m = _meeting_or_404(mid)
    payload = json.dumps(m, ensure_ascii=False).replace("</", "<\\/")
    return templates.TemplateResponse(request, "transcript.html",
                                      {"m": m, "meeting_json": payload, "store": store})


# --- api ---------------------------------------------------------------------
@app.post("/upload", dependencies=[Depends(auth)])
async def upload(files: list[UploadFile] = File(...)):
    store.ensure_dirs()
    ids = []
    for f in files:
        if Path(f.filename).suffix.lower() not in store.AUDIO_EXT:
            continue
        tmp = store.INBOX_DIR / f".upload-{f.filename}"
        with open(tmp, "wb") as out:
            while chunk := await f.read(4 * 1024 * 1024):
                out.write(chunk)
        final = store.INBOX_DIR / f.filename
        tmp.replace(final)
        m = pipeline.ingest_file(final)
        ids.append(m["id"])
        _executor.submit(pipeline.process_meeting, m["id"])
    return RedirectResponse("/", status_code=303)


@app.get("/api/meetings", dependencies=[Depends(auth)])
def api_meetings():
    return [_public(m) for m in store.list_meetings()]


@app.get("/api/meetings/{mid}", dependencies=[Depends(auth)])
def api_meeting(mid: str):
    return _meeting_or_404(mid)


@app.post("/api/meetings/{mid}/rename", dependencies=[Depends(auth)])
async def api_rename_meeting(mid: str, request: Request):
    _meeting_or_404(mid)
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        raise HTTPException(400, "title required")
    m = store.rename_meeting(mid, title)
    export.write(m, "md")
    return {"title": m["title"]}


@app.post("/api/meetings/{mid}/retry", dependencies=[Depends(auth)])
def api_retry(mid: str):
    _meeting_or_404(mid)
    store.set_status(mid, "queued")
    _executor.submit(pipeline.process_meeting, mid)
    return {"ok": True}


@app.delete("/api/meetings/{mid}", dependencies=[Depends(auth)])
def api_delete(mid: str):
    _meeting_or_404(mid)
    store.delete_meeting(mid)
    return {"ok": True}


@app.post("/api/meetings/{mid}/scan", dependencies=[Depends(auth)])
def api_scan(mid: str = "inbox"):
    created = pipeline.scan_inbox()
    for m in created:
        _executor.submit(pipeline.process_meeting, m["id"])
    return {"queued": [m["id"] for m in created]}


@app.post("/api/meetings/{mid}/speakers/{label}", dependencies=[Depends(auth)])
async def api_speaker(mid: str, label: str, request: Request):
    _meeting_or_404(mid)
    body = await request.json()
    if "name" in body:
        m = store.rename_speaker(mid, label, body["name"])
    else:
        m = store.confirm_speaker(mid, label)
    export.write(m, "md")
    return m["speakers"]


@app.post("/api/meetings/{mid}/speakers", dependencies=[Depends(auth)])
async def api_add_speaker(mid: str, request: Request):
    _meeting_or_404(mid)
    body = await request.json()
    m, label = store.add_speaker(mid, body.get("name", ""))
    return {"label": label, "speakers": m["speakers"]}


@app.post("/api/meetings/{mid}/merge", dependencies=[Depends(auth)])
async def api_merge(mid: str, request: Request):
    _meeting_or_404(mid)
    body = await request.json()
    m = store.merge_speakers(mid, body["source"], body["into"])
    export.write(m, "md")
    return m


@app.post("/api/meetings/{mid}/utterances/{index}", dependencies=[Depends(auth)])
async def api_utterance(mid: str, index: int, request: Request):
    m = _meeting_or_404(mid)
    if not 0 <= index < len(m["utterances"]):
        raise HTTPException(400, "bad index")
    body = await request.json()
    if "speaker" in body:
        m = store.reassign_utterance(mid, index, body["speaker"])
    if "text" in body:
        m = store.edit_utterance_text(mid, index, body["text"])
    export.write(m, "md")
    return {"ok": True, "speakers": m["speakers"]}


@app.get("/t/{mid}/export.{fmt}", dependencies=[Depends(auth)])
def api_export(mid: str, fmt: str):
    m = _meeting_or_404(mid)
    if fmt == "md":
        return PlainTextResponse(export.to_markdown(m), media_type="text/markdown",
                                 headers={"Content-Disposition": f'attachment; filename="{mid}.md"'})
    if fmt == "txt":
        return PlainTextResponse(export.to_text(m),
                                 headers={"Content-Disposition": f'attachment; filename="{mid}.txt"'})
    if fmt == "docx":
        p = export.write(m, "docx")
        return FileResponse(p, filename=f"{mid}.docx")
    raise HTTPException(404)


@app.get("/t/{mid}/audio", dependencies=[Depends(auth)])
def api_audio(mid: str):
    m = _meeting_or_404(mid)
    return FileResponse(store.meeting_dir(mid) / m["audio"])


if __name__ == "__main__":
    import argparse
    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    ap.add_argument("--watch", action="store_true")
    a = ap.parse_args()
    if a.watch:
        os.environ["WATCH_INBOX"] = "1"
    uvicorn.run(app, host=a.host, port=a.port)
