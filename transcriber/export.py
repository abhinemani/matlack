"""Turn a meeting into something you can read or share."""
from __future__ import annotations

from pathlib import Path

from . import store


def _blocks(meeting: dict) -> list[dict]:
    """Merge consecutive utterances from the same speaker into paragraphs."""
    blocks = []
    for u in meeting.get("utterances", []):
        name = store.display_name(meeting, u["speaker"])
        if blocks and blocks[-1]["name"] == name:
            blocks[-1]["text"] += " " + u["text"]
        else:
            blocks.append({"name": name, "start": u["start"], "text": u["text"]})
    return blocks


def _speaker_key(meeting: dict) -> list[str]:
    lines = []
    for label, sp in sorted(meeting.get("speakers", {}).items()):
        name = store.display_name(meeting, label)
        if sp.get("confirmed"):
            lines.append(f"{name}")
        elif sp.get("guess"):
            lines.append(f"{name} (guessed, {sp.get('confidence', 'low')} confidence)")
        else:
            lines.append(f"{name} (unidentified)")
    return lines


def to_markdown(meeting: dict) -> str:
    out = [f"# {meeting['title']}", ""]
    key = _speaker_key(meeting)
    if key:
        out.append("Speakers: " + "; ".join(key))
        out.append("")
    for b in _blocks(meeting):
        out.append(f"**{b['name']}** ({store.fmt_ts(b['start'])}): {b['text']}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def to_text(meeting: dict) -> str:
    out = [meeting["title"], ""]
    key = _speaker_key(meeting)
    if key:
        out.append("Speakers: " + "; ".join(key))
        out.append("")
    for b in _blocks(meeting):
        out.append(f"{b['name']} ({store.fmt_ts(b['start'])})")
        out.append(b["text"])
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def to_docx(meeting: dict, path: Path) -> Path:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.add_heading(meeting["title"], level=1)
    key = _speaker_key(meeting)
    if key:
        p = doc.add_paragraph()
        p.add_run("Speakers: ").bold = True
        p.add_run("; ".join(key))
    for b in _blocks(meeting):
        p = doc.add_paragraph()
        r = p.add_run(f"{b['name']} ")
        r.bold = True
        t = p.add_run(f"({store.fmt_ts(b['start'])})  ")
        t.font.size = Pt(9)
        p.add_run(b["text"])
    doc.save(str(path))
    return path


def write(meeting: dict, fmt: str = "md") -> Path:
    d = store.meeting_dir(meeting["id"])
    if fmt == "md":
        p = d / f"{meeting['id']}.md"
        p.write_text(to_markdown(meeting))
    elif fmt == "txt":
        p = d / f"{meeting['id']}.txt"
        p.write_text(to_text(meeting))
    elif fmt == "docx":
        p = to_docx(meeting, d / f"{meeting['id']}.docx")
    else:
        raise ValueError(f"unknown format {fmt}")
    return p
