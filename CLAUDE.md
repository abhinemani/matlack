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
- "Transcribe the new files" → ask who was there and how many spoke if the
  user hasn't said, then `python transcribe.py run -y`, or
  `add <file> --people ... --speakers N`. In a terminal `run` asks these
  itself; `-y` skips. Files the web page found in the inbox sit as `waiting`
  until someone answers on the Meetings page or `run` asks. All Claude
  passes use Opus (`CLAUDE_MODEL` in `.env` overrides); there is no
  per-meeting model choice, that was tried and dropped.
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
- "What fixes did Claude suggest / apply them" → `python transcribe.py repairs <id>`
  (review-pass proposals: wrong speaker, merged line to split, misheard
  name; `--apply all` or `--apply 3 7`, `--reject 2`, `--again` for a fresh
  list; the web page shows the same list under "Suggested fixes")
- "Give me a Word doc" → `python transcribe.py export <id> --format docx`
- "Summarize the interview" → `python transcribe.py summarize <id>`
  (follows `guides/efficiency-review.md`; result in meeting.json under
  `summary` plus `<id>-summary.md`; `--guide <name>` picks another guide)
- "Word version of the summary" → `python transcribe.py export <id> --summary --format docx`
- "It keeps spelling <name> wrong" → add a line to `spellings.txt`
  (`Zubo: Zuba, Suber` — corrected word before the colon, must be one word;
  `python transcribe.py spellings` checks the file, `--create` starts one).
  Applies from the next transcription; `retry <id>` re-does an old one.
- "Add a question to the interview guide" → edit `guides/<name>.md`
  (`## Section` = one question, first line is the question, `- ` = probes),
  then re-run `summarize`
- "Open the editor" → `python serve.py` then http://127.0.0.1:8000
- "Publish <meeting> / put it on the site" → `python transcribe.py publish <id>`
  (approves it and pushes the read-only site; needs PUBLISH_* in `.env`)
- "Push the site / publish my edits" → `python transcribe.py publish`
  (only meetings marked public go up; a newly transcribed meeting is
  private until `publish <id>` or Make public in the web page)
- "Take <meeting> off the site" → `python transcribe.py unpublish <id>`
- "What's public" → `python transcribe.py list` (marks public meetings and
  whether they have unpushed changes)

## Unfinished setup
`HANDOFF.md` lists what a previous session left for the laptop to finish
(publishing setup). If the user mentions publishing, the site, or picking up
where the last session left off, read it first.

## Rules
- Local first. Transcribing, naming speakers, summarizing, the web page and
  the exports must keep working with no `PUBLISH_*` settings, no network to
  GitHub and no `cryptography` package. Publishing is an optional last step
  that may fail on its own; never make anything else depend on it, and never
  let a publish failure touch `data/meetings/`.
- README.md must always say how to run everything locally, CLI and web page
  (the "Daily use", "Summaries" and "The web page" sections). Keep those
  current when commands change.
- Keys and the publish passphrase live in `.env`. Never print them or commit them.
- `data/published/` is a git checkout the publish step manages; don't edit it.
- Don't delete anything under `data/meetings/` without asking.
- Speaker labels are single capital letters (A, B, C…). Names are free text.
- `data/meetings/<id>/words.json` holds per-word timings and confidence from
  AssemblyAI. Only splitting a line and the review pass read it; everything
  must keep working when it is missing (old meetings have none).
- Don't use word confidence to find misheard names: measured on real
  transcripts the median word is 0.999 and names it got wrong sit near 0.7
  while ordinary function words fill the bottom. `repair.name_doubts` finds
  them by spotting one name spelled two ways instead.
- `spellings.txt` is local and gitignored, like `.env`. A malformed line is
  reported and skipped, never fatal.
- Summaries are editable in the web page; regenerating replaces those edits.
