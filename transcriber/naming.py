"""Guess who each diarized speaker is from what was said. Names, introductions,
who gets addressed by whom. Guesses are stored separately from confirmed names
so the UI can show them as guesses until you accept them.

The pass works evidence first: Claude lists every clue with the line it came
from (self-introductions, being addressed by name, third-person references),
notes lines where diarization seems to have merged two people, and only then
names the speakers, with confidence graded by the kind of evidence."""
from __future__ import annotations

import json
import os
import re

SYSTEM = """You identify speakers in meeting transcripts. A diarization system
has labeled the voices A, B, C... without knowing names; it also makes mistakes:
it sometimes runs two people's turns together as one line, and it sometimes
mishears names (a listed "Alicia" may appear as "Lucia" or "Lucy").

Each transcript line is numbered and shows the label and a timestamp:
  #12 [B] 03:45: text

Work evidence first. Before naming anyone, find every clue and note its line:
- self-introduction: the speaker of that line says who they are
  ("I'm Vera", "this is Mark", "Jenny with a Y", "my name is...")
- addressed: another speaker says a name to this speaker, and the next turn
  or the reply shows who was meant ("go ahead, Lindsay", "thanks, Mark")
- third-person: someone is mentioned by name or role in a way that pins a
  label ("I'm going to let Laurien, our budget director, speak" followed by
  a new voice)
- role: the speaker states their own job, useful when no name is ever said

Watch for merged lines: one line that contains two different people
introducing themselves, or a greeting and its reply. A self-introduction that
sits in the second half of a merged line does NOT tell you who the label is;
say so in merged_lines and do not use it as evidence for that label.

Confidence rules, applied strictly:
- high: a self-introduction by this label, or this label is addressed by name
  at least twice, or once with an unambiguous reply
- medium: one direct address, or consistent third-person references, or a
  stated role matched to a listed person
- low: elimination ("the only name left"), the roster having a spare name,
  or any guess without a line you can cite
One exception: if only one label speaks at all and the user listed exactly
one person, that person is the speaker (medium).
Never invent a name that is not in the transcript or the user's list. A label
with no usable evidence gets "name": null and "confidence": "low"; give a role
description only if the speaker states it. Naming a label from a
self-introduction that belongs to someone else is the worst mistake here;
prefer null over that.

The user may list people they know were there. Treat the list as context, not
an answer key: it can be partial, it can include people who never spoke, and
speakers may include people not on it. When the transcript supports a match,
use the listed spelling and say so. Never assign a listed name to a label just
because the list has a name left over. The user may also have confirmed some
labels already; keep those and use them as anchors.

Respond with ONLY a JSON object, no prose and no code fences:
{
  "clues": [{"line": 12, "label": "B", "kind": "self-introduction" | "addressed" |
             "third-person" | "role", "quote": "short quote", "means": "what it implies"}],
  "merged_lines": [{"line": 1, "note": "why this line seems to hold two people"}],
  "speakers": {"A": {"name": "Vera Zubo" | null, "confidence": "high" | "medium" | "low",
                     "evidence": "one sentence citing line numbers, e.g. #3 self-introduction; addressed at #40, #57"}}
}
Every label must appear under "speakers"."""

MAX_CHARS = int(os.environ.get("NAMING_MAX_CHARS", "600000"))


def _ts(ms) -> str:
    if ms is None:
        return "--:--"
    s = int(ms // 1000)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02}:{sec:02}" if h else f"{m:02}:{sec:02}"


def _render(utterances: list[dict]) -> str:
    lines = [f"#{i} [{u['speaker']}] {_ts(u.get('start'))}: {u['text']}"
             for i, u in enumerate(utterances) if u.get("text")]
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


def _context(people: list[str] | None, known: dict[str, str] | None,
             expected: int | None) -> str:
    parts = []
    if people:
        parts.append("People the user says were there (partial, not all of them "
                     "necessarily spoke):\n" + "\n".join(f"- {p}" for p in people))
    if expected:
        parts.append(f"The user says {expected} people spoke.")
    if known:
        parts.append("Already confirmed by the user:\n"
                     + "\n".join(f"- Speaker {l} is {n}" for l, n in sorted(known.items())))
    return ("\n\n".join(parts) + "\n\n") if parts else ""


def guess_names(utterances: list[dict], people: list[str] | None = None,
                known: dict[str, str] | None = None,
                expected: int | None = None) -> dict[str, dict]:
    """Returns {label: {guess, confidence, evidence}} plus, under the key
    "_notes", the clue list and suspected merged lines. Empty dict if no key.
    `people` are names the user supplied ahead of time; `known` maps labels the
    user has already confirmed to their names (used when guessing again)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not utterances:
        return {}
    import anthropic

    model = os.environ.get("CLAUDE_MODEL", "claude-opus-5")
    client = anthropic.Anthropic(api_key=api_key)
    labels = sorted({u["speaker"] for u in utterances})
    prompt = (_context(people, known, expected)
              + f"Speaker labels present: {', '.join(labels)}\n\nTranscript:\n\n"
              + _render(utterances))
    request = dict(model=model, max_tokens=16000, system=SYSTEM,
                   messages=[{"role": "user", "content": prompt}])
    # Server-side refusal fallback, so an unlikely safety decline on a
    # transcript still yields names from another model. Older API surfaces
    # reject the parameter; fall back to a plain request then.
    try:
        with client.messages.stream(
            **request,
            extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
            extra_body={"fallbacks": "default"},
        ) as stream:
            resp = stream.get_final_message()
    except anthropic.BadRequestError:
        with client.messages.stream(**request) as stream:
            resp = stream.get_final_message()
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    data = _parse(raw)
    speakers = data.get("speakers") or {k: v for k, v in data.items() if k in labels}
    out = {}
    for label in labels:
        g = speakers.get(label) or {}
        out[label] = {
            "guess": (g.get("name") or None),
            "confidence": (g.get("confidence") or "low").lower(),
            "evidence": g.get("evidence"),
        }
    out["_notes"] = {
        "model": resp.model,
        "clues": [c for c in data.get("clues") or [] if isinstance(c, dict)],
        "merged_lines": [c for c in data.get("merged_lines") or [] if isinstance(c, dict)],
    }
    return out
