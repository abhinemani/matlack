"""Publish approved meetings to a read-only static site.

The site is a separate git repository (PUBLISH_REPO) served by GitHub Pages.
Each publish renders the meetings you have marked public into small JSON
files, encrypts them with PUBLISH_PASSPHRASE, copies in the static viewer
from site/, commits what changed and pushes. Nothing runs on the server: the
viewer decrypts in the browser after you type the passphrase.

Env:
  PUBLISH_REPO        git URL or path of the site repository (required)
  PUBLISH_PASSPHRASE  passphrase the site asks for; required unless
                      PUBLISH_PUBLIC=1 says you really want it in the clear
  PUBLISH_URL         where the site lives, for links (optional)
  PUBLISH_BRANCH      branch Pages serves (default: main)
  PUBLISH_DIR         local checkout (default: <DATA_DIR>/published)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import store

HERE = Path(__file__).resolve().parent.parent
SITE_SRC = HERE / "site"
PBKDF2_ITER = 250_000


class PublishError(RuntimeError):
    pass


# --- config ------------------------------------------------------------------
def config() -> dict:
    return {
        "repo": os.environ.get("PUBLISH_REPO", "").strip(),
        "passphrase": os.environ.get("PUBLISH_PASSPHRASE", ""),
        "public_ok": os.environ.get("PUBLISH_PUBLIC") == "1",
        "url": os.environ.get("PUBLISH_URL", "").strip().rstrip("/"),
        "branch": os.environ.get("PUBLISH_BRANCH", "main").strip() or "main",
        "dir": Path(os.environ.get("PUBLISH_DIR") or (store.DATA_DIR / "published")).resolve(),
    }


def check_config(cfg: dict | None = None) -> dict:
    cfg = cfg or config()
    if not cfg["repo"]:
        raise PublishError("PUBLISH_REPO is not set. Create an empty private repository for the "
                           "site and put its git URL in .env as PUBLISH_REPO.")
    if not cfg["passphrase"] and not cfg["public_ok"]:
        raise PublishError("PUBLISH_PASSPHRASE is not set. Pick a passphrase and put it in .env; "
                           "the site asks for it before showing anything. To publish in the "
                           "clear on purpose, set PUBLISH_PUBLIC=1 instead.")
    return cfg


# --- what gets published -----------------------------------------------------
def payload(m: dict) -> dict:
    """The public shape of a meeting: transcript, display names, summary.
    No audio, no API ids, no processing log, no guess evidence."""
    speakers = {}
    for label, sp in sorted(m.get("speakers", {}).items()):
        speakers[label] = {"name": store.display_name(m, label),
                           "confirmed": bool(sp.get("confirmed"))}
    out = {
        "id": m["id"],
        "title": m["title"],
        "created": m.get("created"),
        "duration_ms": m.get("duration_ms"),
        "speakers": speakers,
        "utterances": [{"speaker": u["speaker"], "start": u.get("start"),
                        "end": u.get("end"), "text": u.get("text", "")}
                       for u in m.get("utterances", [])],
    }
    s = m.get("summary") or {}
    if s.get("status") == "ready":
        out["summary"] = {
            "guide": s.get("guide"), "guide_title": s.get("guide_title"),
            "created": s.get("created"), "words": s.get("words"), "model": s.get("model"),
            "overview": s.get("overview", ""),
            "sections": [{k: sec.get(k) for k in
                          ("id", "title", "question", "covered", "summary", "points", "quotes")}
                         for sec in s.get("sections", [])],
            "priorities": s.get("priorities", []),
            "follow_ups": s.get("follow_ups", []),
        }
    return out


def fingerprint(obj: dict) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def state(m: dict) -> str:
    """off | pending (approved, never pushed) | changed (edited since) | published"""
    if not m.get("public"):
        return "off"
    pub = m.get("published") or {}
    if not pub.get("hash"):
        return "pending"
    if m["status"] == "ready" and fingerprint(payload(m)) != pub["hash"]:
        return "changed"
    return "published"


def entry(m: dict) -> dict:
    """One row of the site's index."""
    return {
        "id": m["id"], "title": m["title"], "created": m.get("created"),
        "duration_ms": m.get("duration_ms"), "lines": len(m.get("utterances", [])),
        "speakers": [store.display_name(m, l) for l in sorted(m.get("speakers", {}))],
        "has_summary": (m.get("summary") or {}).get("status") == "ready",
        "guide_title": (m.get("summary") or {}).get("guide_title"),
    }


# --- crypto ------------------------------------------------------------------
def derive_key(passphrase: str, salt: bytes, iterations: int = PBKDF2_ITER) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, dklen=32)


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _aesgcm():
    """The one third-party dependency publishing has, imported late so that a
    missing package only ever breaks publishing, never transcribing or editing."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise PublishError("the cryptography package is not installed; run "
                           "pip install -r requirements.txt. Nothing else needs it: transcripts, "
                           "names and summaries still work locally.")
    return AESGCM


def encrypt(obj: dict, key: bytes) -> dict:
    AESGCM = _aesgcm()
    iv = os.urandom(12)
    data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {"enc": "aes-gcm", "iv": _b64(iv), "data": _b64(AESGCM(key).encrypt(iv, data, None))}


def decrypt(blob: dict, key: bytes) -> dict:
    AESGCM = _aesgcm()
    raw = AESGCM(key).decrypt(base64.b64decode(blob["iv"]), base64.b64decode(blob["data"]), None)
    return json.loads(raw.decode("utf-8"))


# --- git ---------------------------------------------------------------------
def _git(*args: str, cwd: Path) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        raise PublishError("git is not installed or not on PATH")
    except subprocess.CalledProcessError as e:
        raise PublishError(f"git {' '.join(args)} failed: {(e.stderr or e.stdout).strip()}")
    return r.stdout


def checkout(cfg: dict) -> Path:
    """Fresh local copy of the site repo, matching the remote branch."""
    d, branch = cfg["dir"], cfg["branch"]
    if not (d / ".git").exists():
        d.parent.mkdir(parents=True, exist_ok=True)
        if d.exists():
            shutil.rmtree(d)
        _git("clone", "--quiet", cfg["repo"], str(d), cwd=d.parent)
    else:
        _git("remote", "set-url", "origin", cfg["repo"], cwd=d)
    _git("fetch", "--quiet", "origin", cwd=d)
    remote_branches = _git("branch", "-r", cwd=d).split()
    if f"origin/{branch}" in remote_branches:
        _git("checkout", "--quiet", "-B", branch, f"origin/{branch}", cwd=d)
        _git("reset", "--quiet", "--hard", f"origin/{branch}", cwd=d)
    else:  # empty repository: the first commit will start the branch
        _git("symbolic-ref", "HEAD", f"refs/heads/{branch}", cwd=d)
    return d


def _clean(d: Path) -> None:
    for p in d.iterdir():
        if p.name == ".git":
            continue
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()


# --- the publish itself ------------------------------------------------------
def approved() -> list[dict]:
    return [m for m in store.list_meetings() if m.get("public") and m["status"] == "ready"]


def publish(push: bool = True, log=print) -> dict:
    """Render every approved meeting into the site checkout, commit, push.
    Returns a small report the CLI and web UI both show."""
    cfg = check_config()
    d = checkout(cfg)
    log(f"site checkout at {d}")

    meetings = approved()
    enc = bool(cfg["passphrase"])
    site_json_path = d / "site.json"
    salt = None
    if enc and site_json_path.exists():
        try:
            old = json.loads(site_json_path.read_text())
            if old.get("enc") and old.get("salt"):
                salt = base64.b64decode(old["salt"])
        except (json.JSONDecodeError, ValueError):
            salt = None
    salt = salt or os.urandom(16)
    key = derive_key(cfg["passphrase"], salt) if enc else None

    def wrap(obj: dict) -> str:
        return json.dumps(encrypt(obj, key) if enc else obj, ensure_ascii=False)

    # Encryption makes every write look new (fresh IV), so keep the previous
    # bytes for anything whose content hasn't changed. That way a publish
    # with no edits produces no commit.
    old_site = {}
    if site_json_path.exists():
        try:
            old_site = json.loads(site_json_path.read_text())
        except json.JSONDecodeError:
            old_site = {}
    old_files = {p.name: p.read_text() for p in (d / "m").glob("*.json")} if (d / "m").is_dir() else {}
    old_index = (d / "index.json").read_text() if (d / "index.json").exists() else None
    # A changed passphrase (or a switch to/from encryption) means nothing on
    # the site can be reused: re-encrypt everything.
    same_key = bool(old_site.get("enc")) == enc
    if enc and same_key:
        try:
            same_key = decrypt(old_site["check"], key).get("ok") is True
        except Exception:
            same_key = False
    if not same_key:
        old_files, old_index, old_site = {}, None, {}

    # Viewer files: always refreshed so the site tracks the code.
    _clean(d)
    for src in SITE_SRC.iterdir():
        if src.is_file() and src.name != "pages.yml":
            shutil.copy2(src, d / src.name)
    # The Pages deploy workflow rides along so the site repo needs no setup.
    (d / ".github" / "workflows").mkdir(parents=True)
    shutil.copy2(SITE_SRC / "pages.yml", d / ".github" / "workflows" / "pages.yml")
    shutil.copy2(HERE / "static" / "style.css", d / "style.css")
    shutil.copy2(HERE / "static" / "favicon.svg", d / "favicon.svg")
    (d / ".nojekyll").write_text("")

    # Meetings.
    (d / "m").mkdir()
    report = {"added": [], "updated": [], "removed": [], "unchanged": [],
              "url": cfg["url"], "encrypted": enc, "pushed": False, "commit": None}
    now = time.time()
    published: dict[str, dict] = {}
    for m in meetings:
        p = payload(m)
        h = fingerprint(p)
        name = f"{m['id']}.json"
        prev = (m.get("published") or {}).get("hash")
        if prev == h and name in old_files:
            (d / "m" / name).write_text(old_files[name])
            report["unchanged"].append(m["id"])
        else:
            (d / "m" / name).write_text(wrap(p))
            report["updated" if prev else "added"].append(m["id"])
        published[m["id"]] = {"at": now, "hash": h}
    index = {"meetings": [entry(m) for m in
                          sorted(meetings, key=lambda x: x.get("created") or 0, reverse=True)]}
    ih = fingerprint(index)
    if old_index is not None and old_site.get("index") == ih:
        (d / "index.json").write_text(old_index)
    else:
        index["generated"] = now
        (d / "index.json").write_text(wrap(index))
    site = {"enc": enc, "index": ih}
    if enc:
        site.update(salt=_b64(salt), iter=PBKDF2_ITER,
                    check=old_site.get("check") if old_site.get("salt") == _b64(salt)
                    else encrypt({"ok": True}, key))
    site_json_path.write_text(json.dumps(site, sort_keys=True))

    # Anything published before but no longer approved is gone from the
    # checkout now; note it so the report can say so.
    for m in store.list_meetings():
        if (m.get("published") or {}).get("hash") and m["id"] not in published:
            report["removed"].append(m["id"])
            published[m["id"]] = {}

    _git("add", "-A", cwd=d)
    if not _git("status", "--porcelain", cwd=d).strip():
        log("nothing changed since the last publish")
        return report
    parts = []
    if report["added"]:
        parts.append(f"add {len(report['added'])}")
    if report["updated"]:
        parts.append(f"update {len(report['updated'])}")
    if report["removed"]:
        parts.append(f"remove {len(report['removed'])}")
    msg = "Publish: " + (", ".join(parts) if parts else "refresh viewer") + f" ({len(meetings)} public)"
    _git("-c", "user.name=Matlack", "-c", "user.email=matlack@localhost",
         "commit", "--quiet", "-m", msg, cwd=d)
    report["commit"] = _git("rev-parse", "--short", "HEAD", cwd=d).strip()
    if not push:
        log(f"committed {report['commit']} locally; not pushed")
        return report
    _git("push", "--quiet", "-u", "origin", cfg["branch"], cwd=d)
    report["pushed"] = True
    log(f"pushed {report['commit']} to {cfg['repo']} ({cfg['branch']})")
    for mid, pub in published.items():  # record what went out, once it really did
        cur = store.load(mid)
        cur["published"] = pub
        store.save(cur)
    return report
