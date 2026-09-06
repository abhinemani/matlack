"""Tidy the transcript text without changing what anyone said.

A diarized transcript reads badly for reasons that have nothing to do with
what was meant: filler words, a false start repeated three times, a sentence
that never got its capital letter. This pass fixes those and nothing else.

The line is that a reader should not be able to tell the difference between
the cleaned text and what the speaker would say they said. Removing "um" is
fine. Turning "we ain't got the money" into "we do not have the funding" is
not -- these are interview records, and tidying someone's register
misrepresents them.

The original of every changed line is kept in the utterance's "raw" field, so
the verbatim record is never lost and `clean --undo` puts it back.

Two guards sit between the model and the transcript, because "do not change
the content" is not something a prompt can guarantee:
  - a cleaned line may not introduce a content word the original did not have
    (catches invention),
  - it may not fall below half the original length (catches summarising),
  - it may not flatten a word the speaker repeated three or more times, which
    is emphasis rather than a stumble.
A line failing either is left exactly as it was and counted as refused.

Runs on the same model as every other pass (Opus unless CLAUDE_MODEL says
otherwise). CLEANUP_EFFORT tunes how hard it thinks about a batch;
CLEANUP=0 skips the pass entirely.
"""
from __future__ import annotations

import os
import re
import time

from . import naming, store

EFFORT = os.environ.get("CLEANUP_EFFORT") or "medium"
BATCH = int(os.environ.get("CLEANUP_BATCH", "80"))

SYSTEM = """You tidy the text of meeting transcripts. The words are already
correct; you are fixing how they read on the page, not what was said.

Fix only these:
- filler words that carry no meaning: um, uh, er, ah, mm, hmm, and "you know"
  or "I mean" ONLY when used as pure filler mid-sentence
- stutters and false starts where the speaker immediately restarts the same
  thought: "I— I think we should" -> "I think we should"; "the the budget"
  -> "the budget"
- a word repeated by accident, and only a doubling: "we we need" -> "we need".
  Three or more in a row is emphasis and stays exactly as it is: "a good,
  good, good change" keeps all three goods.
- missing or wrong sentence punctuation and capitalisation
- a stray dash or ellipsis left by a cut-off that the speaker then completed
- spacing and obvious typography

Never do any of these:
- rephrase, paraphrase, condense, summarise or "improve" anything
- correct someone's grammar, dialect, or register. If a speaker says "we was
  gonna", it stays "we was gonna". Making people sound more polished than
  they were misrepresents them.
- change or standardise any name, number, date, title, acronym or piece of
  jargon, even one that looks wrong
- add a word that is not already in the line, or introduce a fact
- drop a hesitation that carries meaning: a "well..." before a difficult
  answer, or a genuine trailing off mid-thought, is content
- drop an aside the speaker slipped in, even when it sits inside a false
  start you are otherwise right to remove. In "We had the one-time, which I
  found out, we had the one-time cost for the cubicles", the abandoned "We
  had the one-time," goes and "which I found out," stays, because it tells
  the reader something. An aside is content however small, however awkwardly
  placed, and however much tidier the line reads without it.
- flatten emphasis. Repetition, "really really", a doubled "no, no" -- if a
  speaker leaned on something, the reader should be able to tell
- merge lines, split lines, or move text between lines
- translate or change the language

When a line is already clean, return it unchanged. When you are unsure
whether something is filler or meaning, leave it alone: an untidy line costs
a reader nothing, a changed meaning corrupts the record.

Return every line you were given, once each, by its number."""

SCHEMA = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {
        "type": "object",
        "properties": {"line": {"type": "integer"}, "text": {"type": "string"}},
        "required": ["line", "text"], "additionalProperties": False}}},
    "required": ["lines"], "additionalProperties": False,
}

# Words that may appear in a cleaned line without being in the original: the
# fillers we asked to have removed can leave a contraction behind.
_FILLER = {"um", "uh", "er", "ah", "mm", "hmm", "umm", "uhh", "erm"}


def _content_words(text: str) -> set[str]:
    """The words that carry meaning, for comparing before and after. Short
    words are ignored: punctuation and capitalisation changes move them
    around legitimately."""
    return {w for w in re.findall(r"[a-z0-9']{4,}", text.lower()) if w not in _FILLER}


def _emphatic(text: str) -> dict[str, int]:
    """Words the speaker said three or more times in a row. A doubling is a
    stumble and may be tidied away; going back a third time is someone
    leaning on a word, and the reader should be able to hear it."""
    runs: dict[str, int] = {}
    words = re.findall(r"[a-z']+", text.lower())
    i = 0
    while i < len(words):
        j = i
        while j + 1 < len(words) and words[j + 1] == words[i]:
            j += 1
        n = j - i + 1
        if n >= 3:
            runs[words[i]] = max(runs.get(words[i], 0), n)
        i = j + 1
    return runs


def check(original: str, cleaned: str) -> str | None:
    """Why this cleaned line must be refused, or None if it is safe."""
    cleaned = cleaned.strip()
    if not cleaned:
        return "empty"
    invented = _content_words(cleaned) - _content_words(original)
    if invented:
        return "introduced " + ", ".join(sorted(invented)[:3])
    if len(cleaned) < len(original.strip()) * 0.55:
        return "dropped too much text"
    after = _emphatic(cleaned)
    for word, n in _emphatic(original).items():
        if after.get(word, 0) < n:
            return f"flattened the repeated “{word}”"
    return None


def _request_options() -> dict:
    return {"output_config": {"effort": EFFORT,
                              "format": {"type": "json_schema", "schema": SCHEMA}}}


def clean_batch(client, model: str, lines: list[tuple[int, str]]) -> dict[int, str]:
    """One request over a slice of the transcript. Returns {index: new text}."""
    body = "\n".join(f"#{i}: {t}" for i, t in lines)
    request = dict(model=model, max_tokens=32000, system=SYSTEM,
                   messages=[{"role": "user", "content": "Tidy these lines.\n\n" + body}],
                   **_request_options())
    import anthropic
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
    if getattr(resp, "stop_reason", None) == "refusal":
        raise RuntimeError("the model declined to clean this passage")
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    data = naming._parse(raw)
    out = {}
    for row in data.get("lines") or []:
        if isinstance(row, dict) and isinstance(row.get("line"), int):
            out[row["line"]] = str(row.get("text") or "")
    return out


def run(mid: str) -> dict:
    """Clean every line of a transcript. Safe to run again: a line already
    cleaned keeps its original in "raw", so a second pass compares against
    the same starting point."""
    m = store.load(mid)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set (put it in .env)")
    if not m.get("utterances"):
        raise RuntimeError(f"{mid} has no transcript yet")
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = store.model_id(m)          # Opus, like every other pass

    # Always clean from the verbatim text, so re-running can't compound.
    source = [(i, u.get("raw") or u.get("text") or "")
              for i, u in enumerate(m["utterances"])]
    source = [(i, t) for i, t in source if t.strip()]

    changed = refused = 0
    notes: list[str] = []
    for start in range(0, len(source), BATCH):
        chunk = source[start:start + BATCH]
        result = clean_batch(client, model, chunk)
        edits: dict[int, str] = {}
        for i, original in chunk:
            new = result.get(i)
            if new is None or new.strip() == original.strip():
                continue
            why = check(original, new)
            if why:
                refused += 1
                if len(notes) < 20:
                    notes.append(f"#{i}: kept as recorded ({why})")
                continue
            edits[i] = new.strip()
        if edits:
            def apply(mm, edits=edits):
                for i, new in edits.items():
                    u = mm["utterances"][i]
                    u.setdefault("raw", u["text"])
                    u["text"] = new
            store.modify(mid, apply)
            changed += len(edits)

    block = {"status": "done", "model": model, "effort": EFFORT,
             "created": time.time(), "lines": len(source),
             "changed": changed, "refused": refused, "notes": notes}
    store.modify(mid, lambda mm: mm.__setitem__("cleanup", block))
    return block


def undo(mid: str) -> int:
    """Put every line back to what the transcriber actually produced."""
    n = 0
    def restore(mm):
        nonlocal n
        for u in mm["utterances"]:
            if u.get("raw"):
                u["text"] = u.pop("raw")
                n += 1
        mm.pop("cleanup", None)
    store.modify(mid, restore)
    return n


def pending(m: dict) -> int:
    """How many lines still hold their original alongside the cleaned text."""
    return sum(1 for u in m.get("utterances") or [] if u.get("raw"))
