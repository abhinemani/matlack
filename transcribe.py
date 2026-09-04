#!/usr/bin/env python3
"""Command line for the transcriber. Examples:

  python transcribe.py run                  # process everything in data/inbox
  python transcribe.py run ~/Downloads/mtg  # process a specific folder (files are moved in)
  python transcribe.py run --watch          # keep watching the inbox
  python transcribe.py add file1.m4a file2.mp3
  python transcribe.py list
  python transcribe.py show <id>
  python transcribe.py rename <id> B "Vera Zubo"
  python transcribe.py merge <id> C B       # C was really B
  python transcribe.py reassign <id> 42 A   # utterance #42 belongs to A
  python transcribe.py export <id> --format docx
  python transcribe.py summarize <id>           # organized summary following guides/efficiency-review.md
  python transcribe.py summarize <id> --guide other-guide
  python transcribe.py export <id> --summary --format docx
  python transcribe.py guides                   # list interview guides
  python transcribe.py retry <id>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from transcriber import load_env
load_env()

from transcriber import export, pipeline, store, summarize  # noqa: E402


def cmd_run(a):
    store.ensure_dirs()
    inbox = Path(a.folder) if a.folder else store.INBOX_DIR
    if a.watch:
        pipeline.watch(inbox, workers=a.workers, interval=a.interval)
        return
    created = pipeline.scan_inbox(inbox)
    pending = pipeline.pending_ids()
    if not pending:
        print("Nothing to do. Drop .m4a/.mp3 files in", inbox)
        return
    print(f"Processing {len(pending)} meeting(s) with {a.workers} workers…")
    done = pipeline.run_batch(workers=a.workers, ids=pending)
    for m in store.list_meetings():
        if m["id"] in pending:
            print(f"  {m['status']:12} {m['id']}" + (f"  ({m['error']})" if m.get("error") else ""))


def cmd_add(a):
    for f in a.files:
        m = pipeline.ingest_file(Path(f), move=not a.copy)
        print("queued", m["id"])
    if not a.no_run:
        pipeline.run_batch(workers=a.workers)


def cmd_list(a):
    ms = store.list_meetings()
    if not ms:
        print("No meetings yet.")
    for m in ms:
        n = len(m.get("utterances", []))
        sp = ", ".join(store.display_name(m, l) for l in sorted(m.get("speakers", {})))
        print(f"{m['status']:12} {m['id']:40} {store.fmt_ts(m.get('duration_ms'))}  {n:4} lines  {sp}")


def cmd_show(a):
    m = store.load(a.id)
    print(f"{m['title']}  [{m['status']}]  {store.fmt_ts(m.get('duration_ms'))}")
    for label, sp in sorted(m["speakers"].items()):
        tag = "confirmed" if sp.get("confirmed") else (
            f"guess, {sp.get('confidence')}" if sp.get("guess") else "unidentified")
        print(f"  {label}: {store.display_name(m, label)}  ({tag})"
              + (f"  – {sp['evidence']}" if sp.get("evidence") and not sp.get("confirmed") else ""))
    if a.full:
        for i, u in enumerate(m["utterances"]):
            print(f"{i:4} {store.fmt_ts(u['start'])} {store.display_name(m, u['speaker'])}: {u['text']}")


def _after_edit(mid):
    m = store.load(mid)
    export.write(m, "md")
    return m


def cmd_rename(a):
    store.rename_speaker(a.id, a.label.upper(), a.name)
    m = _after_edit(a.id)
    print("ok:", a.label.upper(), "→", store.display_name(m, a.label.upper()))


def cmd_confirm(a):
    for label in a.labels or list(store.load(a.id)["speakers"]):
        store.confirm_speaker(a.id, label.upper())
    _after_edit(a.id)
    print("confirmed")


def cmd_merge(a):
    store.merge_speakers(a.id, a.source.upper(), a.into.upper())
    _after_edit(a.id)
    print(f"ok: {a.source.upper()} merged into {a.into.upper()}")


def cmd_reassign(a):
    store.reassign_utterance(a.id, a.index, a.label.upper())
    _after_edit(a.id)
    print("ok")


def cmd_export(a):
    m = store.load(a.id)
    if a.summary:
        if a.format == "txt":
            sys.exit("summaries export as md or docx")
        p = export.write_summary(m, a.format)
    else:
        p = export.write(m, a.format)
    print(p)


def cmd_summarize(a):
    m = store.load(a.id)
    if m["status"] != "ready":
        sys.exit(f"{a.id} is not transcribed yet (status: {m['status']})")
    guide = summarize.load_guide(a.guide)
    print(f"Summarizing {m['title']} with “{guide['title']}” ({summarize.MODEL})…")
    m = summarize.run(a.id, a.guide)
    s = m["summary"]
    print(f"done: {sum(1 for x in s['sections'] if x['covered'])} of {len(s['sections'])} sections covered, "
          f"about {s.get('words', 0)} words")
    print(export.write_summary(m, "md"))
    if a.docx:
        print(export.write_summary(m, "docx"))


def cmd_guides(a):
    for g in summarize.list_guides():
        print(f"{g['id']:30} {g['title']}  ({g['sections']} sections)")


def cmd_retry(a):
    store.set_status(a.id, "queued")
    pipeline.run_batch(workers=1, ids=[a.id])


def cmd_serve(a):
    import uvicorn
    import serve as web
    if a.watch:
        web.start_watcher()
    uvicorn.run(web.app, host=a.host, port=a.port)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("run", help="process inbox (or a folder); --watch to keep going")
    s.add_argument("folder", nargs="?")
    s.add_argument("--watch", action="store_true")
    s.add_argument("--workers", type=int, default=3)
    s.add_argument("--interval", type=float, default=10.0)
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("add", help="register specific files and process them")
    s.add_argument("files", nargs="+")
    s.add_argument("--copy", action="store_true", help="copy instead of move")
    s.add_argument("--no-run", action="store_true")
    s.add_argument("--workers", type=int, default=3)
    s.set_defaults(fn=cmd_add)

    sub.add_parser("list").set_defaults(fn=cmd_list)

    s = sub.add_parser("show"); s.add_argument("id"); s.add_argument("--full", action="store_true")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("rename"); s.add_argument("id"); s.add_argument("label"); s.add_argument("name")
    s.set_defaults(fn=cmd_rename)

    s = sub.add_parser("confirm", help="accept guessed names"); s.add_argument("id")
    s.add_argument("labels", nargs="*"); s.set_defaults(fn=cmd_confirm)

    s = sub.add_parser("merge"); s.add_argument("id"); s.add_argument("source"); s.add_argument("into")
    s.set_defaults(fn=cmd_merge)

    s = sub.add_parser("reassign"); s.add_argument("id"); s.add_argument("index", type=int)
    s.add_argument("label"); s.set_defaults(fn=cmd_reassign)

    s = sub.add_parser("export"); s.add_argument("id")
    s.add_argument("--format", choices=["md", "txt", "docx"], default="md")
    s.add_argument("--summary", action="store_true", help="export the summary instead of the transcript")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("summarize", help="organized summary following an interview guide")
    s.add_argument("id"); s.add_argument("--guide", default=None, help="guide id from guides/ (default: efficiency-review)")
    s.add_argument("--docx", action="store_true", help="also write a Word version")
    s.set_defaults(fn=cmd_summarize)

    sub.add_parser("guides", help="list interview guides").set_defaults(fn=cmd_guides)

    s = sub.add_parser("retry"); s.add_argument("id"); s.set_defaults(fn=cmd_retry)

    s = sub.add_parser("serve", help="start the web UI")
    s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=8000)
    s.add_argument("--watch", action="store_true", help="also watch the inbox while serving")
    s.set_defaults(fn=cmd_serve)

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
