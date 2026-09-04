# Meeting transcriber

Turns hour-long meeting recordings (m4a, mp3) into speaker-labeled transcripts.
AssemblyAI does the transcription and tells voices apart; Claude reads the
result and guesses who each voice is from what was said; you confirm or fix
the names, in a terminal or in a small web page. Batches run in parallel.

Everything lives in one folder on your machine. Nothing is hosted unless you
want it to be.

## Setup (once)

1. Get an AssemblyAI API key and an Anthropic API key.
2. Copy `.env.example` to `.env` and paste both keys in.
3. Install: `pip install -r requirements.txt` (Python 3.10+).

If you use the Claude desktop app, open this folder in the Code tab. It reads
`CLAUDE.md` and knows the commands, so you can just say "transcribe the new
files" or "speaker B is Vera in the budget kickoff."

## Daily use

Drop recordings in `data/inbox/`, then:

    python transcribe.py run

Each meeting becomes `data/meetings/<id>/` with the audio, a `meeting.json`
(the source of truth), and a `<id>.md` transcript with guessed names applied
and marked as guesses. To keep it running and pick up files as they land:

    python transcribe.py run --watch

Fixing speakers from the command line:

    python transcribe.py list
    python transcribe.py show budget-kickoff          # who's who, with the evidence
    python transcribe.py rename budget-kickoff B "Vera Zubo"
    python transcribe.py confirm budget-kickoff       # accept all guesses
    python transcribe.py merge budget-kickoff C B     # diarization split one person in two
    python transcribe.py reassign budget-kickoff 42 A # one line went to the wrong person
    python transcribe.py export budget-kickoff --format docx

## The web page

When a transcript is messy enough that command-line fixes get tedious:

    python serve.py            # http://127.0.0.1:8000
    python serve.py --watch    # also watches the inbox while it runs

Upload files by dragging them onto the page, then open a meeting: rename or
accept speakers on the left, change any single line's speaker with the
dropdown on the right, click a timestamp to hear that moment, click text to
fix a transcription error. Every change saves immediately to the same
`meeting.json` the CLI uses, so you can move between the two freely.

## Hosting it

    cp .env.example .env    # fill in keys; set APP_PASSWORD
    docker compose up -d

That runs the web page with the inbox watcher on, storing everything in
`./data`. Put it behind whatever you normally use for HTTPS.

## How it's built

`transcriber/` is the library: `aai.py` (AssemblyAI REST), `naming.py`
(the Claude pass), `store.py` (meeting.json read/write and speaker edits),
`export.py`, and `pipeline.py` (file → transcript, batch, watch).
`transcribe.py` and `serve.py` are thin wrappers over it. Deleting
`serve.py`, `templates/` and `static/` leaves a working CLI tool.

Costs: AssemblyAI bills per audio hour; the Claude pass sends the transcript
text once per meeting. Check both providers' current pricing pages.
