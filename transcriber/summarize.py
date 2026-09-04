"""Turn a transcript into an organized summary that follows an interview guide.

A guide is a markdown file in guides/: a title, an optional intro paragraph,
then one `## Section` per question with the main question on the first line
and follow-up probes as `- ` bullets. Claude reads the whole transcript and
files what was said under each section, cleaned up, with supporting quotes.

The result is stored in meeting.json under "summary" so the CLI and the web
page see the same thing."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from . import store

GUIDES_DIR = Path(os.environ.get("GUIDES_DIR", Path(__file__).resolve().parent.parent / "guides"))
DEFAULT_GUIDE = os.environ.get("DEFAULT_GUIDE", "efficiency-review")
MODEL = os.environ.get("SUMMARY_MODEL", "claude-opus-5")
MAX_CHARS = int(os.environ.get("SUMMARY_MAX_CHARS", "600000"))
MAX_WORDS = int(os.environ.get("SUMMARY_MAX_WORDS", "700"))  # about two printed pages

SYSTEM = """You turn interview transcripts into clean, organized summaries that
follow a fixed interview guide. The transcript is a diarized recording with
timestamps; it may be rambling, out of order, or interrupted. Your job is to
find what the interviewee actually said in answer to each section of the guide
and present it clearly, in the interviewee's own terms but without filler.

Length: the finished summary is read by busy people and must fit on two
printed pages. The user message states the total word budget for everything
combined (overview, all sections, priorities, follow-ups). Stay under it.
That means: overview 3-4 sentences; each section 1-3 sentences plus at most
three short bullets (under 15 words each) and at most one quote; at most
three priorities; at most four follow-ups. Sections that were not discussed
get one sentence. Prefer the specific over the general, and cut anything the
reader could infer. When the guide has only a few sections, give them more
room but still stay under the budget.

Rules:
- Attribute content only to what is in the transcript. Never invent facts,
  numbers, names, or positions. If a section was not discussed, say so.
- Answers often arrive out of order or under a different question. File each
  point under the section it best answers, not where it was said.
- Write in plain prose, third person ("The director said..."), past tense.
  Keep department-specific terms and numbers exactly as spoken.
- Quotes must be verbatim spans from the transcript (light cleanup of "um"
  and false starts is fine), each with the timestamp of the line it came from.
- Interviewers' own remarks are context, not answers. Do not summarize them
  as the interviewee's views.

Respond with ONLY a JSON object, no prose and no code fences, shaped exactly:
{
  "overview": "3-4 sentences: who was interviewed (name and role if stated), which department, and the overall picture that emerged.",
  "sections": {
    "<section id>": {
      "covered": true | false,
      "summary": "1-3 sentences answering the section's question.",
      "points": ["at most three short, specific bullets"],
      "quotes": [{"text": "at most one verbatim quote", "speaker": "name", "time": "mm:ss"}]
    }
  },
  "priorities": ["up to three of the interviewee's own top priorities, in their order, or empty"],
  "follow_ups": ["up to four: things promised, questions left open, or claims worth verifying"]
}
Include every section id from the guide in "sections". Quotes are optional;
use one only when it says something the summary cannot. Empty arrays are
fine."""


# --- guides ------------------------------------------------------------------
def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def parse_guide(text: str, gid: str) -> dict:
    title, intro, sections = gid, [], []
    cur = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("# ") and cur is None and not sections:
            title = line[2:].strip()
        elif line.startswith("## "):
            cur = {"id": _slug(line[3:]), "title": line[3:].strip(), "question": "", "probes": []}
            sections.append(cur)
        elif cur is None:
            if line.strip():
                intro.append(line.strip())
        elif line.lstrip().startswith(("- ", "* ")):
            cur["probes"].append(line.lstrip()[2:].strip())
        elif line.strip():
            cur["question"] = (cur["question"] + " " + line.strip()).strip()
    return {"id": gid, "title": title, "intro": " ".join(intro), "sections": sections}


def list_guides() -> list[dict]:
    out = []
    if GUIDES_DIR.exists():
        for p in sorted(GUIDES_DIR.glob("*.md")):
            g = parse_guide(p.read_text(), p.stem)
            out.append({"id": g["id"], "title": g["title"], "sections": len(g["sections"])})
    return out


def load_guide(gid: str | None = None) -> dict:
    gid = gid or DEFAULT_GUIDE
    p = GUIDES_DIR / f"{gid}.md"
    if not p.exists():
        raise FileNotFoundError(f"no guide named {gid!r} in {GUIDES_DIR}")
    return parse_guide(p.read_text(), gid)


def _guide_text(guide: dict) -> str:
    lines = [f"Interview guide: {guide['title']}"]
    if guide["intro"]:
        lines += ["", guide["intro"]]
    for s in guide["sections"]:
        lines += ["", f"[{s['id']}] {s['title']}", f"  Q: {s['question']}"]
        lines += [f"  - {p}" for p in s["probes"]]
    return "\n".join(lines)


# --- transcript --------------------------------------------------------------
def render_transcript(meeting: dict) -> str:
    """Consecutive lines from one speaker become one paragraph, with the
    timestamp of its first line."""
    blocks = []
    for u in meeting.get("utterances", []):
        if not u.get("text"):
            continue
        name = store.display_name(meeting, u["speaker"])
        if blocks and blocks[-1][0] == name:
            blocks[-1][2] += " " + u["text"]
        else:
            blocks.append([name, u["start"], u["text"]])
    text = "\n".join(f"[{store.fmt_ts(b[1])}] {b[0]}: {b[2]}" for b in blocks)
    if len(text) > MAX_CHARS:
        raise ValueError(f"transcript is {len(text)} characters, over the {MAX_CHARS} limit")
    return text


def _parse(text: str) -> dict:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    return json.loads(m.group(0) if m else text)


# --- the pass ----------------------------------------------------------------
def summarize(meeting: dict, guide: dict) -> dict:
    """Run Claude over the transcript. Returns the summary payload (not saved)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    if not meeting.get("utterances"):
        raise ValueError("no transcript yet")
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    speakers = ", ".join(f"{store.display_name(meeting, l)}" for l in sorted(meeting.get("speakers", {})))
    n = len(guide["sections"])
    prompt = (f"{_guide_text(guide)}\n\n"
              f"Total word budget for the whole summary: {MAX_WORDS} words "
              f"(the guide has {n} section{'s' if n != 1 else ''}; roughly "
              f"{max(40, (MAX_WORDS - 200) // max(n, 1))} words each after the overview).\n\n"
              f"Meeting: {meeting['title']}\nSpeakers: {speakers}\n\n"
              f"Transcript:\n\n{render_transcript(meeting)}")
    with client.messages.stream(
        model=MODEL, max_tokens=16000, system=SYSTEM,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        resp = stream.get_final_message()
    if resp.stop_reason == "refusal":
        raise RuntimeError("the model declined to summarize this transcript")
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    data = _parse(raw)

    sections = []
    for s in guide["sections"]:
        d = (data.get("sections") or {}).get(s["id"]) or {}
        sections.append({
            "id": s["id"], "title": s["title"], "question": s["question"],
            "covered": bool(d.get("covered", bool(d.get("summary")))),
            "summary": (d.get("summary") or "").strip(),
            "points": [str(p).strip() for p in d.get("points") or [] if str(p).strip()],
            "quotes": [{"text": str(q.get("text", "")).strip(), "speaker": q.get("speaker") or "",
                        "time": q.get("time") or ""} for q in d.get("quotes") or [] if q.get("text")],
        })
    words = len(" ".join([data.get("overview") or ""] + [
        s["summary"] + " " + " ".join(s["points"]) + " " + " ".join(q["text"] for q in s["quotes"])
        for s in sections] + [str(p) for p in (data.get("priorities") or []) + (data.get("follow_ups") or [])]).split())
    return {
        "status": "ready", "error": None, "words": words,
        "guide": guide["id"], "guide_title": guide["title"],
        "model": MODEL, "created": time.time(),
        "overview": (data.get("overview") or "").strip(),
        "sections": sections,
        "priorities": [str(p).strip() for p in data.get("priorities") or []],
        "follow_ups": [str(p).strip() for p in data.get("follow_ups") or []],
    }


def run(mid: str, guide_id: str | None = None) -> dict:
    """Summarize a meeting and save the result into its meeting.json.
    Marks the summary as running first so the web page can show progress."""
    guide = load_guide(guide_id)
    m = store.load(mid)
    m["summary"] = {"status": "running", "error": None, "guide": guide["id"],
                    "guide_title": guide["title"], "created": time.time()}
    store.save(m)
    try:
        result = summarize(m, guide)
    except Exception as e:
        m = store.load(mid)
        m["summary"] = {"status": "error", "error": str(e), "guide": guide["id"],
                        "guide_title": guide["title"], "created": time.time()}
        store.save(m)
        raise
    m = store.load(mid)
    m["summary"] = result
    store.save(m)
    from . import export
    export.write_summary(m, "md")
    return m


def edit_section(mid: str, sid: str, summary: str | None = None,
                 points: list[str] | None = None) -> dict:
    m = store.load(mid)
    s = m.get("summary") or {}
    for sec in s.get("sections", []):
        if sec["id"] == sid:
            if summary is not None:
                sec["summary"] = summary.strip()
            if points is not None:
                sec["points"] = [p.strip() for p in points if p.strip()]
            break
    else:
        raise KeyError(sid)
    store.save(m)
    from . import export
    export.write_summary(m, "md")
    return m


def edit_field(mid: str, field: str, value) -> dict:
    """Edit overview / priorities / follow_ups."""
    if field not in ("overview", "priorities", "follow_ups"):
        raise KeyError(field)
    m = store.load(mid)
    s = m.get("summary") or {}
    if field == "overview":
        s["overview"] = str(value).strip()
    else:
        s[field] = [str(p).strip() for p in value if str(p).strip()]
    m["summary"] = s
    store.save(m)
    from . import export
    export.write_summary(m, "md")
    return m
