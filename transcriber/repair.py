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

import difflib
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
    doubts = name_doubts(m["id"], m)
    if doubts:
        parts.append(doubts)
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


def _norm(word: str) -> str:
    return re.sub(r"[^\w']", "", word).lower()


def split_time(words: list[dict], u: dict, at: str, match_words: int = 4):
    """When the second person in a merged line starts, to the millisecond.

    `at` is the text the second person begins with. Find that run of words
    among the words the transcriber timed inside this line, and the first of
    them carries the real timestamp — no guessing from how long the sentence
    looks. Returns None when there are no word timings to work from, or when
    the phrase can't be located, and the caller estimates instead."""
    window = store.words_between(words, u.get("start"), u.get("end"))
    wanted = [w for w in (_norm(x) for x in at.split()) if w][:match_words]
    if not window or not wanted:
        return None
    have = [_norm(w.get("t") or "") for w in window]
    for i in range(len(have) - len(wanted) + 1):
        if have[i:i + len(wanted)] == wanted:
            start = window[i].get("s")
            return start if isinstance(start, (int, float)) else None
    return None


# Words that open a sentence are capitalised whatever they are, so they crowd
# out the real names. This is the short list that showed up as noise.
_NOT_A_NAME = {
    "the", "then", "this", "that", "these", "those", "and", "but", "for", "you",
    "your", "yeah", "okay", "our", "are", "have", "haven", "has", "just", "was",
    "were", "what", "when", "where", "which", "with", "they", "their", "there",
    "she", "all", "any", "anything", "not", "now", "one", "two", "very", "well",
    "yes", "let", "lets", "make", "kind", "right", "see", "how", "why", "who",
    "because", "definitely", "whatever", "assign", "meet", "quite", "program",
}


def name_doubts(mid: str, m: dict, ratio: float = 0.85, limit: int = 12) -> str:
    """The same name written two ways in one transcript.

    The first try here listed the words the transcriber scored lowest, on the
    theory that a misheard name is one it was unsure of. On real transcripts
    that is false: the median word scores 0.999 and the bottom of the range is
    all short function words, while a name it got wrong ("Jeannie" for Jeanne)
    comes back around 0.7 and confident. Two spellings of one name in the same
    conversation is the signal that actually finds those, and it needs no
    confidence at all — the word timings only supply a score per spelling to
    help judge which one is right.
    """
    counts: dict[str, int] = {}
    for u in m.get("utterances") or []:
        for t in re.findall(r"\b[A-Z][\w']{2,}\b", u.get("text") or ""):
            if t.lower() in _NOT_A_NAME or "'" in t:
                continue
            counts[t] = counts.get(t, 0) + 1
    scores = _word_scores(store.load_words(mid))
    names = sorted(counts)
    rows = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if a.lower().rstrip("s") == b.lower().rstrip("s"):
                continue          # a plural, not a second spelling
            if difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() < ratio:
                continue
            rows.append(f"- “{a}” ({counts[a]}x{scores.get(a.lower(), '')}) and "
                        f"“{b}” ({counts[b]}x{scores.get(b.lower(), '')})")
    if not rows:
        return ""
    return ("These look like one name spelled two ways. Decide which is right "
            "and propose a replace for the other, or leave both if they are "
            "genuinely different words.\n" + "\n".join(rows[:limit]))


def _word_scores(words: list[dict]) -> dict[str, str]:
    """How sure the transcriber was of each spelling, averaged. Empty when the
    meeting has no word timings."""
    tally: dict[str, list[float]] = {}
    for w in words or []:
        t = re.sub(r"[^\w']", "", w.get("t") or "").lower()
        c = w.get("c")
        if t and isinstance(c, (int, float)):
            tally.setdefault(t, []).append(c)
    return {t: f", {int(100 * sum(v) / len(v))}% sure" for t, v in tally.items()}


def _shift(m: dict, after: int, by: int = 1) -> None:
    """A split inserted a line; every later index in the bookkeeping moves.

    Repairs still pointing at the line that was split need care rather than a
    shift. A line holding three turns (a question, its answer, the asker's
    reaction) draws one proposal per cut, and applying the first moves the
    later cuts' text into the newly inserted line. Following the text keeps
    those usable; without this they failed validation as "split point not
    found", which quietly dropped a ninth of everything proposed.
    """
    utts = m.get("utterances") or []
    for it in (m.get("repairs") or {}).get("items", []):
        if it.get("status") != "proposed" or not isinstance(it.get("line"), int):
            continue
        if it["line"] > after:
            it["line"] += by
        elif it["line"] == after:
            needle = it.get("at") if it.get("kind") == "split" else (
                it.get("find") if it.get("kind") == "replace" else None)
            if not needle or after + by >= len(utts):
                continue          # a reassign has no text to follow
            if needle not in utts[after].get("text", "") and \
                    needle in utts[after + by].get("text", ""):
                it["line"] = after + by
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
        cut = split_time(store.load_words(mid), u, r["at"])
        if cut is None and isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
            # No word timings on this meeting (transcribed before we kept them,
            # or words.json is gone): fall back to guessing the moment from how
            # far into the line the second person starts.
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
