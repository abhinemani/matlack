"""The actual work: file -> AssemblyAI -> name guesses -> meeting.json + markdown.
Used identically by the CLI and the web server."""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import aai, export, naming, store

_in_flight: set[str] = set()
_lock = threading.Lock()


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def process_meeting(mid: str) -> dict:
    """Run the full pipeline for a registered meeting. Safe to re-run on error."""
    with _lock:
        if mid in _in_flight:
            return store.load(mid)
        _in_flight.add(mid)
    try:
        m = store.load(mid)
        audio = store.meeting_dir(mid) / m["audio"]
        if not audio.is_file():
            raise FileNotFoundError(f"the recording {m['audio']} was deleted, so this meeting "
                                    "can't be transcribed again; its transcript and summary are kept")

        store.set_status(mid, "uploading")
        log(f"[{mid}] uploading {audio.name}")
        url = aai.upload(audio)

        store.set_status(mid, "transcribing")
        tid = aai.submit(url, speech_model=os.environ.get("AAI_SPEECH_MODEL") or None,
                         language_code=os.environ.get("AAI_LANGUAGE") or None)
        store.set_status(mid, "transcribing", aai_id=tid)
        log(f"[{mid}] transcribing ({tid})")
        t = aai.wait(tid)

        utterances = aai.utterances_from(t)
        labels = sorted({u["speaker"] for u in utterances})
        m = store.load(mid)
        m["utterances"] = utterances
        m["duration_ms"] = (t.get("audio_duration") or 0) * 1000
        m["speakers"] = {l: {"name": "", "guess": None, "confidence": None,
                             "evidence": None, "confirmed": False} for l in labels}
        m["status"] = "naming"
        store.save(m)

        log(f"[{mid}] {len(utterances)} utterances, {len(labels)} speakers; guessing names")
        try:
            guesses = naming.guess_names(utterances, people=m.get("people"))
        except Exception as e:  # naming is best-effort
            log(f"[{mid}] name guessing failed: {e}")
            guesses = {}
        m = store.load(mid)
        for label, g in guesses.items():
            m["speakers"].setdefault(label, {}).update(g)
        m["status"] = "ready"
        m["error"] = None
        store.save(m)
        export.write(m, "md")
        log(f"[{mid}] ready")
        return m
    except Exception as e:
        log(f"[{mid}] error: {e}")
        store.set_status(mid, "error", error=str(e))
        raise
    finally:
        with _lock:
            _in_flight.discard(mid)


def reguess(mid: str) -> dict:
    """Run the naming pass again on a finished transcript, using the people
    list and any names already confirmed. Confirmed speakers are left alone;
    the others get fresh guesses."""
    m = store.load(mid)
    if m["status"] != "ready":
        raise RuntimeError(f"{mid} is not transcribed yet (status: {m['status']})")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set (put it in .env)")
    known = {l: sp["name"] for l, sp in m["speakers"].items()
             if sp.get("confirmed") and sp.get("name")}
    guesses = naming.guess_names(m["utterances"], people=m.get("people"), known=known)
    m = store.load(mid)
    for label, g in guesses.items():
        sp = m["speakers"].setdefault(label, {"name": "", "confirmed": False})
        if not sp.get("confirmed"):
            sp.update(g)
    store.save(m)
    export.write(m, "md")
    log(f"[{mid}] guessed names again ({len(m.get('people') or [])} people given)")
    return m


def ingest_file(path: Path, move: bool = True, people: list[str] | None = None) -> dict:
    return store.create_from_file(path, move=move, people=people)


def _stable(path: Path, seen: dict, settle: float) -> bool:
    """A file counts as stable when its size hasn't changed for `settle` seconds,
    so we never grab something still being copied or synced."""
    size = path.stat().st_size
    prev = seen.get(path)
    now = time.time()
    if prev is None or prev[0] != size:
        seen[path] = (size, now)
        return False
    return now - prev[1] >= settle


def scan_inbox(inbox: Path | None = None, seen: dict | None = None,
               settle: float = 0.0) -> list[dict]:
    """Register every stable audio file in the inbox as a queued meeting."""
    store.ensure_dirs()
    inbox = Path(inbox or store.INBOX_DIR)
    seen = seen if seen is not None else {}
    created = []
    for p in sorted(inbox.iterdir()):
        if not p.is_file() or p.suffix.lower() not in store.AUDIO_EXT or p.name.startswith("."):
            continue
        if settle and not _stable(p, seen, settle):
            continue
        m = ingest_file(p)
        seen.pop(p, None)
        log(f"[{m['id']}] queued from {p.name}")
        created.append(m)
    return created


def pending_ids() -> list[str]:
    return [m["id"] for m in store.list_meetings()
            if m["status"] not in ("ready", "error") and m["id"] not in _in_flight]


def run_batch(workers: int = 3, ids: list[str] | None = None) -> list[dict]:
    """Process every queued/interrupted meeting, several at a time."""
    ids = ids if ids is not None else pending_ids()
    if not ids:
        return []
    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in [ex.submit(process_meeting, i) for i in ids]:
            try:
                results.append(fut.result())
            except Exception:
                pass
    return results


def watch(inbox: Path | None = None, workers: int = 3, interval: float = 10.0,
          settle: float = 15.0, stop: threading.Event | None = None) -> None:
    """Loop forever: pick up new inbox files, process them."""
    seen: dict = {}
    ex = ThreadPoolExecutor(max_workers=workers)
    log(f"watching {inbox or store.INBOX_DIR}")
    while not (stop and stop.is_set()):
        try:
            scan_inbox(inbox, seen, settle=settle)
            for mid in pending_ids():
                ex.submit(process_meeting, mid)
        except Exception as e:
            log(f"watch loop error: {e}")
        time.sleep(interval)
    ex.shutdown(wait=True)
