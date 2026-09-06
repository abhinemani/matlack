"""The actual work: file -> AssemblyAI -> name guesses -> meeting.json + markdown.
Used identically by the CLI and the web server."""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import aai, cleanup, export, naming, repair, spellings, store

_in_flight: set[str] = set()
_lock = threading.Lock()


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def audio_context(m: dict) -> str:
    """What the transcriber is told about the meeting before it starts.

    The interview guide is the useful part: knowing the subject up front is
    what makes an unfamiliar term or a programme name come out right the
    first time. The title and the people ride along because they cost
    nothing. Set AAI_CONTEXT=0 to send none of it, AAI_CONTEXT_GUIDE to use
    a guide other than the default one."""
    if os.environ.get("AAI_CONTEXT") == "0":
        return ""
    parts = []
    title = (m.get("title") or "").strip()
    if title:
        parts.append(f"This is a recording of: {title}")
    if m.get("people"):
        parts.append("People in the room (may be partial): "
                     + "; ".join(m["people"]))
    try:
        from . import summarize
        guide = summarize.load_guide(os.environ.get("AAI_CONTEXT_GUIDE") or None)
    except Exception:
        guide = None                      # no guides folder is fine
    if guide and guide.get("sections"):
        lines = [f"It follows an interview guide, {guide['title']}, covering:"]
        if guide.get("intro"):
            lines.append(guide["intro"])
        for sec in guide["sections"]:
            lines.append(f"- {sec['title']}: {sec['question']}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


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

        fixed, problems = spellings.load()
        for why in problems:
            log(f"[{mid}] spellings.txt: {why}")

        store.set_status(mid, "transcribing")
        tid = aai.submit(url, speech_model=os.environ.get("AAI_SPEECH_MODEL") or None,
                         language_code=os.environ.get("AAI_LANGUAGE") or None,
                         speakers_expected=m.get("speakers_expected"),
                         keyterms=store.people_names(m.get("people")),
                         advanced_diarization=os.environ.get("AAI_ADVANCED_DIARIZATION") == "1",
                         context=audio_context(m), custom_spelling=fixed,
                         entity_detection=os.environ.get("AAI_ENTITY_DETECTION") != "0")
        store.set_status(mid, "transcribing", aai_id=tid)
        log(f"[{mid}] transcribing ({tid})")
        t = aai.wait(tid)

        utterances = aai.utterances_from(t)
        labels = sorted({u["speaker"] for u in utterances})
        store.save_words(mid, aai.words_from(t))
        heard = aai.people_from(t)
        m = store.load(mid)
        m["utterances"] = utterances
        m["duration_ms"] = (t.get("audio_duration") or 0) * 1000
        m["heard_names"] = heard
        m["speakers"] = {l: {"name": "", "guess": None, "confidence": None,
                             "evidence": None, "confirmed": False} for l in labels}
        m["status"] = "naming"
        store.save(m)

        log(f"[{mid}] {len(utterances)} utterances, {len(labels)} speakers; guessing names")
        try:
            guesses = naming.guess_names(utterances, people=m.get("people"),
                                         expected=m.get("speakers_expected"),
                                         model=store.model_id(m), heard=heard)
        except Exception as e:  # naming is best-effort
            log(f"[{mid}] name guessing failed: {e}")
            guesses = {}
        m = store.load(mid)
        m["naming"] = guesses.pop("_notes", None)
        for label, g in guesses.items():
            m["speakers"].setdefault(label, {}).update(g)
        m["status"] = "ready"
        m["error"] = None
        store.save(m)
        tidy(mid)
        m = store.load(mid)
        export.write(m, "md")
        log(f"[{mid}] ready")
        suggest_repairs(mid)
        return store.load(mid)
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
    guesses = naming.guess_names(m["utterances"], people=m.get("people"), known=known,
                                 expected=m.get("speakers_expected"), model=store.model_id(m),
                                 heard=m.get("heard_names"))
    m = store.load(mid)
    m["naming"] = guesses.pop("_notes", None)
    for label, g in guesses.items():
        sp = m["speakers"].setdefault(label, {"name": "", "confirmed": False})
        if not sp.get("confirmed"):
            sp.update(g)
    store.save(m)
    export.write(m, "md")
    log(f"[{mid}] guessed names again ({len(m.get('people') or [])} people given)")
    suggest_repairs(mid)
    return store.load(mid)


def tidy(mid: str) -> dict:
    """The cleanup pass: fillers, false starts and punctuation, with the
    verbatim text kept beside every line it touches. Best effort, like the
    naming pass -- a failure leaves the transcript exactly as recorded.
    Set CLEANUP=0 to skip it."""
    if os.environ.get("CLEANUP") == "0":
        return {}
    store.modify(mid, lambda m: m.__setitem__(
        "cleanup", {"status": "running", "created": time.time()}))
    try:
        block = cleanup.run(mid)
    except Exception as e:
        log(f"[{mid}] cleanup failed: {e}")
        return store.modify(mid, lambda m: m.__setitem__(
            "cleanup", {"status": "error", "error": str(e), "created": time.time()}))
    log(f"[{mid}] tidied {block['changed']} of {block['lines']} lines"
        + (f", {block['refused']} left as recorded" if block["refused"] else ""))
    return block


def suggest_repairs(mid: str) -> dict:
    """The review pass: Claude proposes line and name fixes for a person to
    apply. Best effort; a failure leaves the transcript as it is."""
    # Marked running first, so a restart knows to pick this up again.
    m = store.modify(mid, lambda m: m.__setitem__(
        "repairs", {"status": "running", "created": time.time(), "items": []}))
    try:
        block = repair.propose(m)
    except Exception as e:
        log(f"[{mid}] repair suggestions failed: {e}")
        return store.modify(mid, lambda m: m.__setitem__(
            "repairs", {"status": "error", "error": str(e), "created": time.time(), "items": []}))
    m = store.modify(mid, lambda m: m.__setitem__("repairs", block))
    n = len(block["items"])
    log(f"[{mid}] {n} fix{'es' if n != 1 else ''} suggested" if n else f"[{mid}] no fixes suggested")
    return m


def ingest_file(path: Path, move: bool = True, people: list[str] | None = None,
                hold: bool = False) -> dict:
    """Register a file. With `hold`, it waits (status "waiting") for the user to
    say who was there before anything is sent off; otherwise it is queued."""
    return store.create_from_file(path, move=move, people=people, hold=hold)


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
               settle: float = 0.0, hold: bool = False) -> list[dict]:
    """Register every stable audio file in the inbox as a queued meeting
    (or, with `hold`, a waiting one that the web page asks about first)."""
    store.ensure_dirs()
    inbox = Path(inbox or store.INBOX_DIR)
    seen = seen if seen is not None else {}
    created = []
    for p in sorted(inbox.iterdir()):
        if not p.is_file() or p.suffix.lower() not in store.AUDIO_EXT or p.name.startswith("."):
            continue
        if settle and not _stable(p, seen, settle):
            continue
        m = ingest_file(p, hold=hold)
        seen.pop(p, None)
        log(f"[{m['id']}] {'waiting for details' if hold else 'queued'} from {p.name}")
        created.append(m)
    return created


def pending_ids(include_waiting: bool = False) -> list[str]:
    """Meetings that still need processing. Ones waiting for the user to say
    who was there are left out unless asked for (the CLI asks, then runs them)."""
    skip = ("ready", "error") + (() if include_waiting else ("waiting",))
    return [m["id"] for m in store.list_meetings()
            if m["status"] not in skip and m["id"] not in _in_flight]


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
          settle: float = 15.0, stop: threading.Event | None = None,
          hold: bool = False) -> None:
    """Loop forever: pick up new inbox files, process them. With `hold`, new
    files only get registered; the web page asks who was there, then starts."""
    seen: dict = {}
    ex = ThreadPoolExecutor(max_workers=workers)
    log(f"watching {inbox or store.INBOX_DIR}")
    while not (stop and stop.is_set()):
        try:
            scan_inbox(inbox, seen, settle=settle, hold=hold)
            for mid in pending_ids():
                ex.submit(process_meeting, mid)
        except Exception as e:
            log(f"watch loop error: {e}")
        time.sleep(interval)
    ex.shutdown(wait=True)
