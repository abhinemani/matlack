"""Storage. One folder per meeting under data/meetings/<id>/ holding the audio,
a meeting.json with everything the tool knows, and any exports.

Every layer (CLI, web UI, Claude Code) reads and writes the same meeting.json,
so state never lives in one interface.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "data")).resolve()
INBOX_DIR = Path(os.environ.get("INBOX_DIR", DATA_DIR / "inbox")).resolve()
MEETINGS_DIR = DATA_DIR / "meetings"

AUDIO_EXT = {".m4a", ".mp3", ".wav", ".mp4", ".aac", ".ogg", ".flac", ".webm", ".mov"}

STATUS_ORDER = ["queued", "uploading", "transcribing", "naming", "ready", "error"]


def ensure_dirs() -> None:
    for d in (DATA_DIR, INBOX_DIR, MEETINGS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def slugify(name: str) -> str:
    stem = Path(name).stem.lower()
    stem = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return stem[:60] or "meeting"


def new_id(filename: str) -> str:
    base = slugify(filename)
    candidate = base
    n = 2
    while (MEETINGS_DIR / candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def meeting_dir(mid: str) -> Path:
    return MEETINGS_DIR / mid


def meeting_path(mid: str) -> Path:
    return meeting_dir(mid) / "meeting.json"


def load(mid: str) -> dict:
    p = meeting_path(mid)
    if not p.exists():
        raise FileNotFoundError(mid)
    return json.loads(p.read_text())


def save(meeting: dict) -> dict:
    meeting["updated"] = time.time()
    p = meeting_path(meeting["id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meeting, indent=2, ensure_ascii=False))
    tmp.replace(p)
    return meeting


def list_meetings() -> list[dict]:
    ensure_dirs()
    out = []
    for d in MEETINGS_DIR.iterdir():
        p = d / "meeting.json"
        if p.exists():
            try:
                out.append(json.loads(p.read_text()))
            except json.JSONDecodeError:
                continue
    out.sort(key=lambda m: m.get("created", 0), reverse=True)
    return out


def parse_people(text: str | list | None) -> list[str]:
    """Names the user typed, deduplicated. Free text splits on commas,
    semicolons and newlines; a list is taken item by item, so a role can ride
    along after a comma ("Vera Zubo, budget director")."""
    if not text:
        return []
    items = text if isinstance(text, list) else re.split(r"[,;\n]+", str(text))
    out: list[str] = []
    for raw in items:
        s = " ".join(str(raw).split())
        if s and s.lower() not in {o.lower() for o in out}:
            out.append(s)
    return out


def create_from_file(src: Path, move: bool = True, people: list[str] | None = None) -> dict:
    """Register an audio file as a new meeting. Moves (or copies) the audio
    into the meeting folder so the inbox stays clean. `people` is an optional,
    partial list of names the user already knows were there."""
    ensure_dirs()
    src = Path(src)
    mid = new_id(src.name)
    d = meeting_dir(mid)
    d.mkdir(parents=True)
    dest = d / src.name
    if move:
        shutil.move(str(src), str(dest))
    else:
        shutil.copy2(str(src), str(dest))
    meeting = {
        "id": mid,
        "title": src.stem,
        "audio": dest.name,
        "status": "queued",
        "error": None,
        "aai_id": None,
        "created": time.time(),
        "updated": time.time(),
        "duration_ms": None,
        "utterances": [],
        "speakers": {},
        "people": parse_people(people),
        "log": [],
    }
    return save(meeting)


def set_people(mid: str, people: str | list | None) -> dict:
    m = load(mid)
    m["people"] = parse_people(people)
    return save(m)


def set_public(mid: str, public: bool) -> dict:
    """Approve (or withdraw) a meeting for the published site."""
    m = load(mid)
    m["public"] = bool(public)
    return save(m)


def rename_meeting(mid: str, title: str) -> dict:
    m = load(mid)
    m["title"] = title.strip() or m["title"]
    return save(m)


def set_status(mid: str, status: str, error: str | None = None, **extra) -> dict:
    m = load(mid)
    m["status"] = status
    m["error"] = error
    m.update(extra)
    m.setdefault("log", []).append({"t": time.time(), "status": status, "error": error})
    return save(m)


# --- speaker operations shared by CLI and UI ---------------------------------

def display_name(meeting: dict, label: str) -> str:
    sp = meeting.get("speakers", {}).get(label, {})
    return sp.get("name") or sp.get("guess") or f"Speaker {label}"


def rename_speaker(mid: str, label: str, name: str) -> dict:
    m = load(mid)
    sp = m["speakers"].setdefault(label, {})
    sp["name"] = name.strip()
    sp["confirmed"] = True
    return save(m)


def confirm_speaker(mid: str, label: str) -> dict:
    m = load(mid)
    sp = m["speakers"].setdefault(label, {})
    if not sp.get("name"):
        sp["name"] = sp.get("guess") or f"Speaker {label}"
    sp["confirmed"] = True
    return save(m)


def merge_speakers(mid: str, source: str, into: str) -> dict:
    m = load(mid)
    if source == into:
        return m
    for u in m["utterances"]:
        if u["speaker"] == source:
            u["speaker"] = into
    m["speakers"].pop(source, None)
    m["speakers"].setdefault(into, {})
    return save(m)


def add_speaker(mid: str, name: str = "") -> tuple[dict, str]:
    m = load(mid)
    existing = set(m["speakers"].keys())
    label = next(chr(c) for c in range(ord("A"), ord("Z") + 1) if chr(c) not in existing)
    m["speakers"][label] = {"name": name, "guess": None, "confidence": None,
                            "evidence": None, "confirmed": bool(name)}
    return save(m), label


def reassign_utterance(mid: str, index: int, label: str) -> dict:
    m = load(mid)
    m["utterances"][index]["speaker"] = label
    m["speakers"].setdefault(label, {})
    return save(m)


def edit_utterance_text(mid: str, index: int, text: str) -> dict:
    m = load(mid)
    m["utterances"][index]["text"] = text
    return save(m)


def delete_meeting(mid: str) -> None:
    d = meeting_dir(mid)
    if d.exists():
        shutil.rmtree(d)


def fmt_ts(ms: int | None) -> str:
    if ms is None:
        return "--:--"
    s = int(ms // 1000)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
