# Meeting transcriber — notes for Claude Code

This folder is a small tool that turns meeting recordings into speaker-labeled
transcripts. Read this before doing anything.

## What it does
- `python transcribe.py run` processes every audio file in `data/inbox/`
  (AssemblyAI diarization, then a Claude pass that guesses names from context).
- Results live in `data/meetings/<id>/meeting.json` plus a `<id>.md` export.
- `python transcribe.py summarize <id>` runs a second Claude pass that files
  what was said under each question of an interview guide in `guides/`.
- `python serve.py` starts a web page for correcting speakers by hand.
- Both the CLI and the web UI read/write the same meeting.json; never edit the
  .md exports by hand — regenerate them with `python transcribe.py export <id>`.

## Common requests and the right command
- "Transcribe the new files" → `python transcribe.py run`
  (in a terminal it asks who was in each new meeting; `-y` skips the question)
- "Vera and Mark were in that meeting" → `python transcribe.py people <id> "Vera Zubo" "Mark (interviewer)"`
  (optional, partial list; stored under `people` and used by the name guesses;
  on a finished transcript it guesses again, keeping confirmed names)
- "What's in the queue / what's done" → `python transcribe.py list`
- "Who is speaker B in <meeting>" → `python transcribe.py show <id>`
- "Speaker B is Vera" → `python transcribe.py rename <id> B "Vera Zubo"`
- "The guesses look right" → `python transcribe.py confirm <id>`
- "C and B are the same person" → `python transcribe.py merge <id> C B`
- "Line 42 is actually Mark" → `python transcribe.py reassign <id> 42 A`
  (find the line number with `show <id> --full`)
- "Give me a Word doc" → `python transcribe.py export <id> --format docx`
- "Summarize the interview" → `python transcribe.py summarize <id>`
  (follows `guides/efficiency-review.md`; result in meeting.json under
  `summary` plus `<id>-summary.md`; `--guide <name>` picks another guide)
- "Word version of the summary" → `python transcribe.py export <id> --summary --format docx`
- "Add a question to the interview guide" → edit `guides/<name>.md`
  (`## Section` = one question, first line is the question, `- ` = probes),
  then re-run `summarize`
- "Open the editor" → `python serve.py` then http://127.0.0.1:8000

## Rules
- Keys live in `.env`. Never print them or commit them.
- Don't delete anything under `data/meetings/` without asking.
- Speaker labels are single capital letters (A, B, C…). Names are free text.
- Summaries are editable in the web page; regenerating replaces those edits.
