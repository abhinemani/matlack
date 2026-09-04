"""Thin AssemblyAI client over its REST API. No SDK, so nothing drifts."""
from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

BASE = "https://api.assemblyai.com/v2"


def _key() -> str:
    key = os.environ.get("ASSEMBLYAI_API_KEY")
    if not key:
        raise RuntimeError("ASSEMBLYAI_API_KEY is not set (put it in .env)")
    return key


def _headers() -> dict:
    return {"authorization": _key()}


def upload(path: Path, chunk_size: int = 5 * 1024 * 1024) -> str:
    """Stream the audio file up; returns a private URL AssemblyAI can read."""
    def chunks():
        with open(path, "rb") as f:
            while True:
                b = f.read(chunk_size)
                if not b:
                    break
                yield b

    with httpx.Client(timeout=httpx.Timeout(600.0)) as c:
        r = c.post(f"{BASE}/upload", headers=_headers(), content=chunks())
        r.raise_for_status()
        return r.json()["upload_url"]


def submit(audio_url: str, speech_model: str | None = None,
           language_code: str | None = None) -> str:
    body = {
        "audio_url": audio_url,
        "speaker_labels": True,
        "punctuate": True,
        "format_text": True,
    }
    if speech_model:
        body["speech_model"] = speech_model
    if language_code:
        body["language_code"] = language_code
    with httpx.Client(timeout=60.0) as c:
        r = c.post(f"{BASE}/transcript", headers=_headers(), json=body)
        r.raise_for_status()
        return r.json()["id"]


def get(transcript_id: str) -> dict:
    with httpx.Client(timeout=60.0) as c:
        r = c.get(f"{BASE}/transcript/{transcript_id}", headers=_headers())
        r.raise_for_status()
        return r.json()


def wait(transcript_id: str, poll: float = 5.0, timeout: float = 3 * 3600,
         on_tick=None) -> dict:
    start = time.time()
    while True:
        t = get(transcript_id)
        status = t.get("status")
        if status == "completed":
            return t
        if status == "error":
            raise RuntimeError(f"AssemblyAI error: {t.get('error')}")
        if on_tick:
            on_tick(status)
        if time.time() - start > timeout:
            raise TimeoutError(f"Transcript {transcript_id} still {status} after {timeout}s")
        time.sleep(poll)


def utterances_from(transcript: dict) -> list[dict]:
    """Normalize to the small shape we store: speaker letter, text, start, end (ms)."""
    out = []
    for u in transcript.get("utterances") or []:
        out.append({
            "speaker": str(u.get("speaker", "A")),
            "text": (u.get("text") or "").strip(),
            "start": u.get("start"),
            "end": u.get("end"),
        })
    if not out and transcript.get("text"):
        out.append({"speaker": "A", "text": transcript["text"], "start": 0,
                    "end": transcript.get("audio_duration", 0) * 1000})
    return out
