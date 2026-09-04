# Meeting transcriber — notes for Claude Code

This folder is a small tool that turns meeting recordings into speaker-labeled
transcripts. Read this before doing anything.

## What it does
- `python transcribe.py run` processes every audio file in `data/inbox/`
  (AssemblyAI diarization, then a Claude pass that guesses names from context).
- Results live in `data/meetings/<id>/meeting.json` plus a `<id>.md` export.
- `python serve.py` starts a web page for correcting speakers by hand.
- Both the CLI and the web UI read/write the same meeting.json; never edit the
  .md exports by hand — regenerate them with `python transcribe.py export <id>`.

## Common requests and the right command
- "Transcribe the new files" → `python transcribe.py run`
- "What's in the queue / what's done" → `python transcribe.py list`
- "Who is speaker B in <meeting>" → `python transcribe.py show <id>`
- "Speaker B is Vera" → `python transcribe.py rename <id> B "Vera Zubo"`
- "The guesses look right" → `python transcribe.py confirm <id>`
- "C and B are the same person" → `python transcribe.py merge <id> C B`
- "Line 42 is actually Mark" → `python transcribe.py reassign <id> 42 A`
  (find the line number with `show <id> --full`)
- "Give me a Word doc" → `python transcribe.py export <id> --format docx`
- "Open the editor" → `python serve.py` then http://127.0.0.1:8000

## Rules
- Keys live in `.env`. Never print them or commit them.
- Don't delete anything under `data/meetings/` without asking.
- Speaker labels are single capital letters (A, B, C…). Names are free text.
