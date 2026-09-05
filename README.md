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

In a terminal it first asks who was in each new meeting. Answer with any
names you already know (partial is fine, roles in parentheses) or press
Enter to skip; `run -y` skips the question. Claude still works out who is
who from the conversation, it just starts with better context. The same
names can be given up front with `add <file> --people "Vera Zubo" "Mark"`,
typed into the upload box on the web page, or added later:

    python transcribe.py people budget-kickoff "Vera Zubo (budget director)" "Mark"

On a finished transcript that guesses the names again, keeping anything you
have already confirmed.

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

## Summaries that follow an interview guide

Interviews that follow a script can be turned into an organized summary:
Claude reads the transcript and files what was said under each question,
cleaned up, with the interviewee's own quotes and timestamps.

    python transcribe.py summarize frederick-budget-director
    python transcribe.py export frederick-budget-director --summary --format docx

The questions live in `guides/efficiency-review.md`. Each `## Section` is one
question: the first line is the question itself and `- ` bullets are the
follow-up probes. Add another guide as `guides/<name>.md` and pick it with
`--guide <name>`. The result is saved into `meeting.json` under `summary`,
written out as `<id>-summary.md`, and shown on the meeting's Summary page in
the web UI, where every paragraph and bullet can be edited in place.

## The web page

When a transcript is messy enough that command-line fixes get tedious:

    python serve.py            # http://127.0.0.1:8000
    python serve.py --watch    # also watches the inbox while it runs

Upload files by dragging them onto the page, then open a meeting: rename or
accept speakers on the left, change any single line's speaker with the
dropdown on the right, click a timestamp to hear that moment, click text to
fix a transcription error. Every change saves immediately to the same
`meeting.json` the CLI uses, so you can move between the two freely.

## Publishing a read-only site

Transcription, fixing names and summarizing all happen on your machine.
When a meeting is ready to share, approve it and push; a small static site
on GitHub Pages shows the approved transcripts and summaries, and nothing
else. The site can't transcribe or edit anything.

One-time setup is three lines in `.env`. The simplest arrangement uses a
`gh-pages` branch of this very repository, so there is nothing to create:

    PUBLISH_REPO=https://github.com/you/matlack
    PUBLISH_BRANCH=gh-pages
    PUBLISH_PASSPHRASE=a long phrase you will type to open the site
    PUBLISH_URL=https://you.github.io/matlack

The first publish pushes the branch along with a small GitHub Actions
workflow that deploys it to Pages and switches Pages on for the repository.
If GitHub declines to switch it on by itself, do it once by hand: repository
*Settings → Pages → Source: GitHub Actions*. Later pushes deploy within a
minute or two.

If you would rather keep the site out of the code repository, create an
empty repository (private needs a paid plan for Pages; public is fine since
only ciphertext is stored) and point `PUBLISH_REPO` at it with
`PUBLISH_BRANCH=main`.

Then, per meeting:

    python transcribe.py publish budget-kickoff     # approve it and push
    python transcribe.py publish                    # push again after edits
    python transcribe.py unpublish budget-kickoff   # take it down

Or in the web page: **Make public** on a transcript, then **Publish** on the
Meetings page. Rows show whether an approved meeting has changes that
haven't been pushed yet.

What goes up is the transcript with confirmed names, and the summary if
there is one. Audio, name-guess evidence and processing details stay home.
Everything is encrypted with the passphrase before it leaves your machine,
so GitHub only ever holds ciphertext; the site asks for the passphrase and
decrypts in the browser. Each transcript and summary page has Copy and Word
buttons; the Word file is built in the browser too, so nothing is decrypted
anywhere but on the reader's machine. A Pages URL is public even for a
private repo, which is why the passphrase isn't optional. If you truly want an open site,
set `PUBLISH_PUBLIC=1` instead of a passphrase.

The site checkout lives in `data/published/` and is managed for you. The
viewer's source is in `site/`; it is copied in on every publish.

## How it's built

`transcriber/` is the library: `aai.py` (AssemblyAI REST), `naming.py`
(the Claude name-guessing pass), `summarize.py` (the Claude summary pass,
driven by `guides/`), `store.py` (meeting.json read/write and speaker edits),
`export.py`, `publish.py` (the encrypted static site and its git push), and
`pipeline.py` (file → transcript, batch, watch). `site/` is the viewer that
gets published.
`transcribe.py` and `serve.py` are thin wrappers over it. Deleting
`serve.py`, `templates/` and `static/` leaves a working CLI tool.

Costs: AssemblyAI bills per audio hour; each Claude pass sends the transcript
text once per meeting (summaries use Claude Opus by default; set
`SUMMARY_MODEL` in `.env` to change that). Check both providers' current pricing pages.
