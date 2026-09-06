"""Proposed fixes to a transcript, from a Claude pass that runs after naming.

The diarizer and the speech model make three kinds of mistake this tool can
see from the text alone: a line given to the wrong voice, two people's turns
run together as one line, and a name misheard ("Lucia" for Alicia). Claude
proposes concrete edits for those; nothing is changed until a person applies
each one, in the web page or with `transcribe.py repairs`.

Each proposal is one of:
  reassign  {line, to: label | null, new_name, reason}
  split     {line, at: text where the second part begins, second: label | null,
             second_name, reason}
  replace   {line: index | null (everywhere), find, replace, reason}
They live in meeting.json under "repairs" with a status of proposed,
applied or rejected."""
from __future__ import annotations

import json
import os
import re
import time

from . import naming, store

SYSTEM = """You review a meeting transcript for mistakes made by the automatic
transcriber and propose concrete edits. The voices are labeled A, B, C... by a
diarization system that does not know names; the speaker key shows which
person each label is believed to be. Lines are numbered:
  #12 [B] 03:45: text

Look for three kinds of mistake and nothing else:

1. Wrong speaker: a line attributed to a label that plainly did not say it,
   for instance someone answering their own question, or a greeting to "Mark"
   attributed to Mark. Propose "reassign" with the correct label; if the
   person has no label yet, give "new_name" instead of "to".

2. Merged line: one line containing two people's turns, such as two
   self-introductions, or a question and its answer. Propose "split" with
   "at" set to the exact text where the second person begins (copy it from
   the line, character for character, at least four words), and "second" set
   to the label of the second speaker, or "second_name" if they have no label.

3. Misheard name: a person's name rendered as a similar-sounding word, judged
   against the speaker key and the list of people known to be there. Propose
   "replace" with the wrong spelling in "find" and the right one in "replace".
   Use "line": null when every occurrence should change, or a line number for
   a single spot. Only names and titles; never rephrase ordinary words.

Rules:
- Propose only what the text itself supports; give a one-sentence reason
  citing line numbers. When unsure, leave it out; a missed fix costs a person
  a moment, a wrong fix corrupts the record.
- Do not propose reassigning a line just to balance the speakers, and do not
  rename speakers here (the key handles that).
- "at" and "find" must be exact substrings of the line as written, including
  punctuation and capitalisation.
- At most 40 proposals; the most consequential first.

Respond with a JSON object of this shape (fields that don't apply are null):
{"repairs": [
  {"kind": "reassign", "line": 9, "to": "C", "reason": "..."},
  {"kind": "reassign", "line": 33, "new_name": "Lucy", "reason": "..."},
  {"kind": "split", "line": 1, "at": "Good morning, I'm Michelle Day.", "second": "B", "reason": "..."},
  {"kind": "replace", "line": null, "find": "Lucia", "replace": "Alicia", "reason": "..."}
]}"""

_S = {"type": ["string", "null"]}
SCHEMA = {
    "type": "object",
    "properties": {"repairs": {"type": "array", "items": {
        "type": "object",
        "properties": {"kind": {"type": "string", "enum": ["reassign", "split", "replace"]},
                       "line": {"type": ["integer", "null"]}, "to": _S, "new_name": _S,
                       "at": _S, "second": _S, "second_name": _S, "find": _S, "replace": _S,
                       "reason": {"type": "string"}},
        "required": ["kind", "line", "to", "new_name", "at", "second", "second_name",
                     "find", "replace", "reason"],
        "additionalProperties": False}}},
    "required": ["repairs"], "additionalProperties": False,
}


def _key(m: dict) -> str:
    rows = []
    for label, sp in sorted(m.get("speakers", {}).items()):
        name = store.display_name(m, label)
        how = "confirmed" if sp.get("confirmed") else (
            f"guess, {sp.get('confidence') or 'low'}" if sp.get("guess") else "unidentified")
        rows.append(f"- {label}: {name} ({how})")
    return "\n".join(rows) or "- (no speakers)"


def propose(m: dict) -> dict:
    """Ask Claude for repairs. Returns the block stored under meeting["repairs"]."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not m.get("utterances"):
        return {"created": time.time(), "model": None, "items": []}
    import anthropic

    model = store.model_id(m)
    client = anthropic.Anthropic(api_key=api_key)
    parts = ["Speaker key:\n" + _key(m)]
    if m.get("people"):
        parts.append("People known to be there (may be partial):\n"
                     + "\n".join(f"- {p}" for p in m["people"]))
    merged = (m.get("naming") or {}).get("merged_lines") or []
    if merged:
        parts.append("Lines the naming pass thought hold two people:\n"
                     + "\n".join(f"- #{x.get('line')}: {x.get('note')}" for x in merged))
    parts.append("Transcript:\n\n" + naming._render(m["utterances"]))
    request = dict(model=model, max_tokens=16000, system=SYSTEM,
                   messages=[{"role": "user", "content": "\n\n".join(parts)}],
                   **naming.request_options(model, SCHEMA))
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
    data = naming._parse(raw)
    items = []
    for i, r in enumerate(data.get("repairs") or []):
        if not isinstance(r, dict):
            continue
        ok, why = validate(m, r)
        if ok:
            items.append(r | {"id": i + 1, "status": "proposed"})
    return {"created": time.time(), "model": resp.model, "items": items[:40]}


def validate(m: dict, r: dict) -> tuple[bool, str]:
    """Only keep proposals that can actually be applied to this transcript."""
    utts = m.get("utterances", [])
    kind = r.get("kind")
    line = r.get("line")
    if kind in ("reassign", "split"):
        if not isinstance(line, int) or not 0 <= line < len(utts):
            return False, "bad line"
    if kind == "reassign":
        to = r.get("to")
        if to and to not in m.get("speakers", {}):
            return False, "unknown label"
        if not to and not (r.get("new_name") or "").strip():
            return False, "no target"
        if to and to == utts[line]["speaker"]:
            return False, "same speaker"
        return True, ""
    if kind == "split":
        at = (r.get("at") or "").strip()
        pos = utts[line]["text"].find(at) if at else -1
        if pos <= 0:
            return False, "split point not found"
        second = r.get("second")
        if second and second not in m.get("speakers", {}):
            return False, "unknown label"
        return True, ""
    if kind == "replace":
        find, rep = (r.get("find") or "").strip(), (r.get("replace") or "").strip()
        if not find or not rep or find == rep:
            return False, "nothing to replace"
        if line is None:
            return (any(find in u["text"] for u in utts), "not found")
        if not isinstance(line, int) or not 0 <= line < len(utts):
            return False, "bad line"
        return (find in utts[line]["text"], "not found")
    return False, "unknown kind"


def describe(m: dict, r: dict) -> str:
    """Short human wording used by the CLI and the page."""
    name = lambda l: store.display_name(m, l)
    if r["kind"] == "reassign":
        who = name(r["to"]) if r.get("to") else f"{r.get('new_name')} (new)"
        return f"move line to {who}"
    if r["kind"] == "split":
        who = name(r["second"]) if r.get("second") else (
            f"{r['second_name']} (new)" if r.get("second_name") else "the same speaker")
        at = r["at"] if len(r["at"]) <= 40 else r["at"][:37] + "…"
        return f"split at “{at}”, second part to {who}"
    if r["kind"] == "replace":
        where = "everywhere" if r.get("line") is None else "on this line"
        return f"“{r['find']}” → “{r['replace']}” {where}"
    return r["kind"]


def _shift(m: dict, after: int, by: int = 1) -> None:
    """A split inserted a line; every later index in the bookkeeping moves."""
    for it in (m.get("repairs") or {}).get("items", []):
        if it.get("status") == "proposed" and isinstance(it.get("line"), int) and it["line"] > after:
            it["line"] += by
    for x in (m.get("naming") or {}).get("merged_lines") or []:
        if isinstance(x.get("line"), int) and x["line"] > after:
            x["line"] += by


def apply(mid: str, rid: int) -> dict:
    """Apply one proposal to the transcript. Re-validates first, since earlier
    edits may have changed the line it pointed at."""
    m = store.load(mid)
    items = (m.get("repairs") or {}).get("items", [])
    r = next((x for x in items if x.get("id") == rid), None)
    if not r:
        raise KeyError(f"no repair {rid}")
    if r.get("status") != "proposed":
        return m
    ok, why = validate(m, r)
    if not ok:
        r["status"] = "stale"
        r["note"] = why
        return store.save(m)
    utts = m["utterances"]
    if r["kind"] == "reassign":
        label = r.get("to") or _new_label(m, r.get("new_name", ""))
        utts[r["line"]]["speaker"] = label
    elif r["kind"] == "split":
        u = utts[r["line"]]
        pos = u["text"].find(r["at"])
        first, second = u["text"][:pos].rstrip(), u["text"][pos:].strip()
        label = r.get("second") or (_new_label(m, r["second_name"]) if r.get("second_name") else u["speaker"])
        start, end = u.get("start"), u.get("end")
        cut = None
        if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
            cut = start + (end - start) * (len(first) / max(1, len(u["text"])))
        u["text"] = first
        if cut is not None:
            u["end"] = cut
        utts.insert(r["line"] + 1, {"speaker": label, "text": second,
                                     "start": cut if cut is not None else start, "end": end})
        _shift(m, r["line"])
    elif r["kind"] == "replace":
        pat = re.compile(r"(?<!\w)" + re.escape(r["find"]) + r"(?!\w)")
        targets = utts if r.get("line") is None else [utts[r["line"]]]
        for u in targets:
            u["text"] = pat.sub(r["replace"], u["text"]) if pat.search(u["text"]) else u["text"].replace(r["find"], r["replace"])
    r["status"] = "applied"
    r["applied_at"] = time.time()
    return store.save(m)


def _new_label(m: dict, name: str) -> str:
    """A speaker for someone the diarizer never separated out. Reuses a
    speaker who already has exactly this name, otherwise adds one."""
    name = " ".join((name or "").split())
    for l, sp in m["speakers"].items():
        if name and (sp.get("name") or "").strip().lower() == name.lower():
            return l
    label = next(chr(c) for c in range(ord("A"), ord("Z") + 1) if chr(c) not in m["speakers"])
    m["speakers"][label] = {"name": name, "guess": None, "confidence": None,
                            "evidence": None, "confirmed": bool(name)}
    return label


def reject(mid: str, rid: int) -> dict:
    m = store.load(mid)
    for it in (m.get("repairs") or {}).get("items", []):
        if it.get("id") == rid and it.get("status") == "proposed":
            it["status"] = "rejected"
    return store.save(m)


def apply_all(mid: str) -> dict:
    m = store.load(mid)
    for it in list((m.get("repairs") or {}).get("items", [])):
        if it.get("status") == "proposed":
            m = apply(mid, it["id"])
    return m


def pending(m: dict) -> list[dict]:
    return [it for it in (m.get("repairs") or {}).get("items", []) if it.get("status") == "proposed"]
