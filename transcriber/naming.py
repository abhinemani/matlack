"""Guess who each diarized speaker is from what was said. Names, introductions,
who gets addressed by whom. Guesses are stored separately from confirmed names
so the UI can show them as guesses until you accept them."""
from __future__ import annotations

import json
import os
import re

SYSTEM = """You identify speakers in meeting transcripts. The transcript has
speakers labeled A, B, C... by a diarization system that does not know names.
Use the content: self-introductions ("this is Vera"), direct address
("thanks, Mark"), references to roles, who answers questions aimed at whom.

Respond with ONLY a JSON object, no prose, no code fences, keyed by speaker
label. For each label give:
  "name": your best guess at the person's name, or a descriptive role
          ("the county budget director") if no name is ever said, or null
  "confidence": "high" | "medium" | "low"
  "evidence": one short sentence citing what in the transcript supports it

Be honest about low confidence. Never invent a name that is not in the text."""

MAX_CHARS = int(os.environ.get("NAMING_MAX_CHARS", "150000"))


def _render(utterances: list[dict]) -> str:
    lines = [f"{u['speaker']}: {u['text']}" for u in utterances if u.get("text")]
    text = "\n".join(lines)
    if len(text) <= MAX_CHARS:
        return text
    # Keep the opening (introductions live there) plus a slice of the rest.
    head = text[: int(MAX_CHARS * 0.6)]
    tail = text[-int(MAX_CHARS * 0.4):]
    return head + "\n[...]\n" + tail


def _parse(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    return json.loads(m.group(0) if m else text)


def guess_names(utterances: list[dict]) -> dict[str, dict]:
    """Returns {label: {guess, confidence, evidence}}. Empty dict if no key set."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not utterances:
        return {}
    import anthropic

    model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
    client = anthropic.Anthropic(api_key=api_key)
    labels = sorted({u["speaker"] for u in utterances})
    prompt = (f"Speaker labels present: {', '.join(labels)}\n\nTranscript:\n\n"
              + _render(utterances))
    resp = client.messages.create(
        model=model, max_tokens=1500, system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    data = _parse(raw)
    out = {}
    for label in labels:
        g = data.get(label) or {}
        out[label] = {
            "guess": g.get("name") or None,
            "confidence": (g.get("confidence") or "low").lower(),
            "evidence": g.get("evidence"),
        }
    return out
