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
           language_code: str | None = None, speakers_expected: int | None = None,
           keyterms: list[str] | None = None, advanced_diarization: bool = False) -> str:
    body = {
        "audio_url": audio_url,
        "speaker_labels": True,
        "punctuate": True,
        "format_text": True,
    }
    if keyterms:
        # Names the user gave: a nudge toward those spellings when the audio
        # is close, not a rule. Up to 200 phrases of at most six words.
        terms = [" ".join(t.split()) for t in keyterms if t and len(t.split()) <= 6]
        if terms:
            body["keyterms_prompt"] = list(dict.fromkeys(terms))[:200]
    if advanced_diarization:
        # AssemblyAI's experimental diarization (priced separately), meant for
        # many speakers or difficult audio. It lives in speaker_options, which
        # can't be combined with the plain speakers_expected field, so an
        # exact count becomes equal hard limits there.
        body["speaker_options"] = {"advanced_speaker_segmentation": True}
        if speakers_expected:
            body["speaker_options"].update(min_speakers_expected=int(speakers_expected),
                                           max_speakers_expected=int(speakers_expected))
    elif speakers_expected:
        # Exact count from the user; diarization then neither merges two
        # voices into one label nor splits one voice into two.
        body["speakers_expected"] = int(speakers_expected)
    if speech_model:
        body["speech_model"] = speech_model
    if language_code:
        body["language_code"] = language_code
    with httpx.Client(timeout=60.0) as c:
        r = c.post(f"{BASE}/transcript", headers=_headers(), json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"AssemblyAI rejected the request ({r.status_code}): {r.text[:300]}")
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
