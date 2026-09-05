# Handoff: finishing the publishing setup on the laptop

Written at the end of a remote Claude Code session on 2026-09-05. Everything
below is already built, committed and pushed on the branch
`claude/status-py5840` (pull request #1). What remains has to happen on the
laptop that holds the recordings and the `.env` file.

## What was built in that session

- **Known names before guessing.** `transcribe.py run` and `add` ask who
  was in each new meeting (`-y` skips). `transcribe.py people <id> NAME...`
  sets names later and re-guesses. The web page has the same field on the
  upload box and in the speaker panel.
- **Docker removed.** The web page runs locally only; `python serve.py`.
- **Publishing.** Approved meetings are encrypted and pushed to the
  `gh-pages` branch of this repository, which GitHub Pages serves as a
  read-only site. Code in `transcriber/publish.py`, viewer in `site/`.

## Already done on GitHub

- The `gh-pages` branch exists and Pages is switched on.
- The site is live at **https://abhinemani.com/matlack/** (the github.io
  address redirects there because of the custom domain).
- It currently shows one fake sample meeting, "Parks efficiency review",
  used for testing. The first real publish from the laptop replaces it.
- The passphrase was chosen in that session and is **not** written down
  anywhere in the repository. Ask the user for it if it is needed.

## Steps on the laptop, in order

1. Get the branch and the one new dependency:

       git fetch origin
       git checkout claude/status-py5840
       pip install -r requirements.txt

2. Add these lines to `.env` (below the two API keys). Use the passphrase
   the user chose; never print or commit it:

       PUBLISH_REPO=https://github.com/abhinemani/matlack
       PUBLISH_BRANCH=gh-pages
       PUBLISH_PASSPHRASE=<the passphrase>
       PUBLISH_URL=https://abhinemani.com/matlack

3. Confirm the viewer works in a real browser: open
   https://abhinemani.com/matlack/ and enter the passphrase. The sample
   meeting should open, with a transcript and a summary. (This was checked
   from the remote session by decrypting the live files, but not by driving
   the live site in a browser.)

4. Publish a real meeting. Either start `python serve.py`, open a finished
   transcript, click **Make public**, then **Push**; or run

       python transcribe.py publish <meeting id>

   The first run clones the `gh-pages` branch into `data/published/` and
   pushes with the laptop's normal GitHub credentials. About a minute later
   the site shows the meeting and the sample is gone.

5. Check `python transcribe.py list`: public meetings are marked, and
   "changed" means edits that haven't been pushed yet. `python transcribe.py
   publish` with no id pushes them; `unpublish <id>` takes one down.

6. When it all works, merge pull request #1 into `main`.

## If something goes wrong

- `publish failed: PUBLISH_REPO is not set` → step 2 wasn't picked up;
  `.env` must be in the folder you run from.
- `PUBLISH_PASSPHRASE is not set` → same file; the site refuses to publish
  in the clear unless `PUBLISH_PUBLIC=1` is set on purpose.
- A git error on push → the laptop's GitHub credentials; try
  `git push origin claude/status-py5840` from the repo to see the same
  prompt outside the tool.
- The site says the passphrase didn't work → the `.env` passphrase differs
  from the one the site was last published with. Publishing again from the
  laptop re-encrypts everything with the `.env` value, so the fix is simply
  to publish.
- Wanting the site out of this repository later → create a separate repo,
  point `PUBLISH_REPO` at it with `PUBLISH_BRANCH=main`, and publish. The
  old `gh-pages` branch can then be deleted.
