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

In a terminal it first asks two things about each new meeting: who was
there, and how many people spoke. Answer with any names you already know
(partial is fine, roles in parentheses) and a head count if you are sure of
it, or press Enter to skip either; `run -y` skips both. The names give the
speaker guesses something to go on; the count is passed to the diarizer so
it keeps exactly that many voices apart, which matters most when people
introduce themselves in quick succession. The same details can be given up
front with `add <file> --people "Vera Zubo" "Mark" --speakers 4`, typed
into the upload box on the web page, or added later:

    python transcribe.py people budget-kickoff "Vera Zubo (budget director)" "Mark"

On a finished transcript that guesses the names again, keeping anything you
have already confirmed.

Each meeting becomes `data/meetings/<id>/` with the audio, a `meeting.json`
(the source of truth), and a `<id>.md` transcript with guessed names applied
and marked as guesses. The audio file is only needed for playback and for
re-transcribing; once a meeting is done you can delete it to save space and
the transcript, names and summary stay exactly as they are, locally and on
the published site (which then shows that meeting as text only). To keep it
running and pick up files as they land:

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
dropdown on the right (pick "Someone else…" there to add a person the first
pass never separated out), click a timestamp to hear that moment, click
text to fix a transcription error. Every change saves immediately to the
same `meeting.json` the CLI uses, so you can move between the two freely.

Recordings that land in the inbox folder while the page is open (or when
you click "check the inbox") don't start on their own: a card appears at
the top of the Meetings page asking who was there and how many people
spoke, and the meeting waits until you press Start. That is the moment the
answers are most useful, since the head count has to reach the diarizer
before transcription begins. `python transcribe.py run` picks up waiting
meetings too, asking the same questions in the terminal.

The speaker panel also lists lines where the naming pass thinks two
people's turns were run together by the diarizer; click one to jump there
and reassign or split it by hand.

## What happens to a recording

1. You say who was there and how many spoke (optional, either place).
2. AssemblyAI transcribes and separates the voices. It gets the head count
   as an exact speaker count and the names as spelling hints, a nudge
   toward "Alicia" over "Lucia" when the audio is close, not a rule.
   `AAI_ADVANCED_DIARIZATION=1` in `.env` switches on AssemblyAI's
   experimental diarization, which is meant for many speakers or rough
   audio and costs a few cents more per hour; it helps most when people
   introduce themselves in quick succession.
3. Claude reads the transcript and works out who each voice is, citing the
   lines that support each guess and flagging lines that look like two
   people run together.
4. Claude reads it once more as a reviewer and proposes concrete fixes:
   a line given to the wrong voice, a merged line to split at a given
   phrase, a misheard name to replace. Nothing is changed by itself. The
   proposals sit under **Suggested fixes** in the speaker panel with Apply
   and Dismiss on each, or in the terminal:

       python transcribe.py repairs budget-kickoff              # list them
       python transcribe.py repairs budget-kickoff --apply all  # or --apply 3 7
       python transcribe.py repairs budget-kickoff --reject 2
       python transcribe.py repairs budget-kickoff --again      # fresh review

   Applying a split moves every later line number along, so the remaining
   proposals still point at the right lines. Both Claude passes together
   cost on the order of twenty cents for an hour-long meeting.

## Publishing a read-only site

This section is optional. Everything above runs entirely on your machine
with no publishing set up, and stays that way if publishing is set up and
later breaks: a failed push, a GitHub outage, a missing `PUBLISH_*` line or
a missing `cryptography` package only ever stops the publish step itself.
Transcribing, naming speakers, summarizing, the web page and the exports
never touch the site, and `data/meetings/` is the only copy that matters.

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

Or in the web page: **Make public** on a meeting's row or its transcript
page, then **Publish** in the top bar. A new transcript is private until
you do that, so a fresh meeting never goes up by accident; if you press
Publish while finished meetings are still private, it names them and
offers to make them public and push in one go. Rows show whether a public
meeting has changes that haven't been pushed yet.

What goes up is the transcript with confirmed names, the summary if there
is one, and the recording, so timestamps on the site play the audio just as
they do locally. Name-guess evidence and processing details stay home.
Recordings are the bulk of the site (roughly 20 MB per half hour); the
viewer downloads and decrypts one only when you ask for it, and an unchanged
recording is never uploaded twice.

GitHub Pages serves sites up to about 1 GB, so recordings have a budget,
900 MB by default (`PUBLISH_AUDIO_CAP_MB`). Meetings already on the site
keep their audio; once the budget is spent, newer ones go up as text only
and the publish output says which. The site keeps working either way, only
the player is missing on those pages. To go text only for everything, set
`PUBLISH_AUDIO=0` in `.env` and publish again; that removes the recordings
from the site (though not from the branch's git history).
Everything is encrypted with the passphrase before it leaves your machine,
so GitHub only ever holds ciphertext; the site asks for the passphrase and
decrypts in the browser. A Pages URL is public even for a private repo,
which is why the passphrase isn't optional. If you truly want an open site,
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
text once per meeting. Both the name-guessing pass and the summaries use
Claude Opus by default (set `CLAUDE_MODEL` or `SUMMARY_MODEL` in `.env` to
change that); naming an hour-long meeting costs on the order of ten cents.
Check both providers' current pricing pages.
