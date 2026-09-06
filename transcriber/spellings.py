"""Fixed spellings for names the transcriber mishears.

Two different things nudge the transcriber toward the right words:

- the people list on a meeting becomes `keyterms_prompt`, a hint that helps
  the model *hear* an unusual name in the first place;
- this file becomes `custom_spelling`, a rule applied to the finished text:
  wherever the transcriber wrote one of the wrong spellings, it writes the
  right one instead.

You maintain spellings.txt by hand, one correct word per line:

    # comments start with #
    Zubo: Zuba, Suber, Zooba
    Matlack: Matlock, Mat Lack

The word before the colon is what you want written. Everything after it is
what the transcriber tends to produce instead. AssemblyAI's rule is that the
corrected spelling must be a single word, so a full name goes in as one line
per word ("Vera: Verra" and "Zubo: Zuba"), not "Vera Zubo: ...". Each wrong
spelling may be up to five words, which is how "Mat Lack" -> "Matlack" works.

Matching is case-insensitive; the replacement keeps the case you typed here.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPELLINGS_FILE = Path(os.environ.get("SPELLINGS_FILE", ROOT / "spellings.txt"))

MAX_FROM_WORDS = 5   # AssemblyAI's limit on a phrase to match


def parse(text: str) -> tuple[list[dict], list[str]]:
    """Returns (entries, problems). Entries are in AssemblyAI's shape,
    [{"from": [...], "to": "Word"}]. Problems are plain-English complaints
    about lines that were skipped; nothing raises, so one bad line never
    stops a transcription."""
    entries: list[dict] = []
    problems: list[str] = []
    seen: dict[str, str] = {}          # wrong spelling (lowered) -> line it came from
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            problems.append(f"line {n}: no colon, so there's nothing to correct — "
                            f"write it as `Correct: what it hears, what else`")
            continue
        right, wrongs = line.split(":", 1)
        right = " ".join(right.split())
        if not right:
            problems.append(f"line {n}: nothing before the colon")
            continue
        if len(right.split()) > 1:
            problems.append(f"line {n}: “{right}” is more than one word — the corrected "
                            f"spelling has to be a single word, so give each word its "
                            f"own line ({', '.join(w + ': …' for w in right.split())})")
            continue
        froms: list[str] = []
        offered = 0
        for w in re.split(r"[,;]+", wrongs):
            w = " ".join(w.split())
            if not w:
                continue
            offered += 1
            if len(w.split()) > MAX_FROM_WORDS:
                problems.append(f"line {n}: “{w}” is longer than {MAX_FROM_WORDS} words; skipped")
                continue
            if w.lower() == right.lower():
                continue               # correcting a word to itself does nothing
            if w.lower() in seen:
                problems.append(f"line {n}: “{w}” is already corrected to "
                                f"“{seen[w.lower()]}”; the later one is ignored")
                continue
            seen[w.lower()] = right
            froms.append(w)
        if not froms:
            # Only complain when the line offered nothing at all; when every
            # spelling on it was rejected, that has been said already.
            if not offered:
                problems.append(f"line {n}: “{right}” has nothing listed after the colon "
                                f"to correct from")
            continue
        entries.append({"from": froms, "to": right})
    return entries, problems


def load(path: Path | None = None) -> tuple[list[dict], list[str]]:
    """Read spellings.txt. Missing file is normal and means no corrections."""
    p = Path(path or SPELLINGS_FILE)
    if not p.is_file():
        return [], []
    try:
        return parse(p.read_text())
    except OSError as e:
        return [], [f"could not read {p}: {e}"]


def entries(path: Path | None = None) -> list[dict]:
    """Just the usable entries, for handing to the transcriber."""
    return load(path)[0]


EXAMPLE = """\
# Names the transcriber gets wrong, and how they should be spelled.
#
#   Correct: what it hears instead, and another, and another
#
# The word before the colon must be a single word, so a full name goes in as
# one line per word. Everything after the colon may be up to five words each.
# Lines starting with # are ignored. Run `python transcribe.py spellings` to
# check this file.

# Zubo: Zuba, Suber, Zooba
# Matlack: Matlock, Mat Lack
"""
