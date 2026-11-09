"""Update the app from a GitHub repository (latest release or a branch), with backup and rollback."""
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import requests

import db

API = "https://api.github.com"
UA = "XtreamTV-updater"
APP_DIR = Path(__file__).resolve().parent
BACKUP_DIR = db.data_dir() / "backup"
KEEP = {".venv", "venv", "__pycache__", ".git"}  # never touched by an update


class UpdateError(Exception):
    pass


def config_from_db():
    return {"repo": db.get("update_repo", ""), "mode": db.get("update_mode", "release"),
            "branch": db.get("update_branch", "main"), "token": db.get("update_token", "")}


def parse_repo(text):
    t = (text or "").strip().rstrip("/")
    t = re.sub(r"^(https?://)?(www\.)?github\.com/", "", t)
    t = re.sub(r"\.git$", "", t)
    m = re.fullmatch(r"([\w.-]+)/([\w.-]+)", t)
    if not m:
        raise UpdateError("Enter the repository as owner/name or its GitHub URL.")
    return m.group(1), m.group(2)


def _headers(token):
    h = {"Accept": "application/vnd.github+json", "User-Agent": UA, "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _check_response(r, token=""):
    if r.status_code == 401:
        raise UpdateError("GitHub rejected the access token.")
    if r.status_code == 403:
        if r.headers.get("X-RateLimit-Remaining") == "0":
            raise UpdateError("GitHub rate limit reached - try again in a while (a token raises the limit).")
        raise UpdateError("GitHub refused access (403). Check the token's permissions.")
    if r.status_code == 404:
        raise UpdateError("Repository not found. If it is private, add an access token.")
    r.raise_for_status()


def check(cfg):
    """Ask GitHub what the newest version is. Returns {ref, label, notes, tarball}."""
    owner, repo = parse_repo(cfg.get("repo"))
    token, mode = cfg.get("token") or "", cfg.get("mode") or "release"
    with requests.Session() as s:
        s.headers.update(_headers(token))
        if mode == "release":
            with s.get(f"{API}/repos/{owner}/{repo}/releases/latest", timeout=15) as r:
                if r.status_code == 404:
                    with s.get(f"{API}/repos/{owner}/{repo}", timeout=15) as r2:
                        _check_response(r2, token)
                    raise UpdateError("No published releases yet. Publish a release on GitHub, or switch the "
                                      "source to a branch.")
                _check_response(r, token)
                d = r.json()
                return {"ref": d["tag_name"], "label": d.get("name") or d["tag_name"],
                        "notes": d.get("body") or "", "tarball": d["tarball_url"]}
        branch = cfg.get("branch") or "main"
        with s.get(f"{API}/repos/{owner}/{repo}/commits/{branch}", timeout=15) as r:
            if r.status_code == 422:
                raise UpdateError(f"Branch '{branch}' not found.")
            _check_response(r, token)
            d = r.json()
            sha = d["sha"]
            return {"ref": sha, "label": f"{branch} @ {sha[:7]}", "notes": d["commit"]["message"],
                    "tarball": f"{API}/repos/{owner}/{repo}/tarball/{sha}"}


# ------------------------------------------------------------------ helpers
def _safe_extract(tgz, dest):
    dest = Path(dest).resolve()
    with tarfile.open(tgz) as tf:
        members = []
        for m in tf.getmembers():
            if m.issym() or m.islnk() or m.isdev():
                continue
            if not str((dest / m.name).resolve()).startswith(str(dest)):
                raise UpdateError("The download contains an unsafe path - update aborted.")
            members.append(m)
        try:
            tf.extractall(dest, members=members, filter="data")
        except TypeError:  # Python < 3.12
            tf.extractall(dest, members=members)


def _find_root(extracted):
    cands = sorted((p.parent for p in Path(extracted).rglob("main.py")
                    if (p.parent / "xtream.py").exists() and (p.parent / "db.py").exists()),
                   key=lambda p: len(p.parts))
    if not cands:
        raise UpdateError("This repository doesn't contain the Xtream TV app (main.py not found).")
    return cands[0]


def _py_check(folder):
    for f in sorted(Path(folder).glob("*.py")):
        try:
            compile(f.read_text(encoding="utf-8"), str(f), "exec")
        except SyntaxError as e:
            raise UpdateError(f"{f.name} has a syntax error (line {e.lineno}) - update aborted.")


def _ignore(_dir, names):
    return [n for n in names if n in KEEP or n in {".github", ".DS_Store"} or n.endswith(".pyc")]


def _install_tree(src, app_dir):
    app_dir = Path(app_dir)
    for p in app_dir.iterdir():
        if p.name in KEEP:
            continue
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    shutil.copytree(src, app_dir, dirs_exist_ok=True, ignore=_ignore)


def _backup(app_dir, backup_dir):
    backup_dir = Path(backup_dir)
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    shutil.copytree(app_dir, backup_dir, ignore=_ignore)
    (backup_dir / ".ref").write_text(json.dumps(
        {"ref": db.get("installed_ref", ""), "label": db.get("installed_label", "")}))


def _pip(req):
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--prefer-binary",
                        "--disable-pip-version-check", "-r", str(req)],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        raise UpdateError("Could not install the required libraries: " + " ".join(tail))


def has_backup(backup_dir=BACKUP_DIR):
    return (Path(backup_dir) / "main.py").exists()


def can_write(app_dir=APP_DIR):
    import os
    return os.access(app_dir, os.W_OK)


# ------------------------------------------------------------------ apply / rollback
def apply(info, cfg, progress=lambda _t: None, app_dir=APP_DIR, backup_dir=BACKUP_DIR):
    app_dir = Path(app_dir)
    if not can_write(app_dir):
        raise UpdateError(f"The app folder isn't writable ({app_dir}).")
    tmp = Path(tempfile.mkdtemp(prefix="xtv-update-"))
    try:
        progress("Downloading...")
        tgz = tmp / "src.tar.gz"
        with requests.get(info["tarball"], headers=_headers(cfg.get("token") or ""), stream=True,
                          timeout=(15, 60)) as r:
            _check_response(r)
            total, done = int(r.headers.get("content-length") or 0), 0
            with open(tgz, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        progress(f"Downloading... {done * 100 // total}%")
        progress("Checking the new version...")
        ex = tmp / "x"
        ex.mkdir()
        _safe_extract(tgz, ex)
        root = _find_root(ex)
        _py_check(root)  # nothing is touched until the new code at least compiles

        old_req = (app_dir / "requirements.txt").read_text() if (app_dir / "requirements.txt").exists() else ""
        progress("Backing up the current version...")
        _backup(app_dir, backup_dir)
        try:
            progress("Installing...")
            _install_tree(root, app_dir)
            new_req = (app_dir / "requirements.txt").read_text() if (app_dir / "requirements.txt").exists() else ""
            if new_req.strip() != old_req.strip():
                progress("Updating libraries (can take a few minutes)...")
                _pip(app_dir / "requirements.txt")
            _py_check(app_dir)
        except Exception:
            rollback(app_dir, backup_dir, quiet=True)
            raise
        db.put("installed_ref", info["ref"])
        db.put("installed_label", info["label"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def rollback(app_dir=APP_DIR, backup_dir=BACKUP_DIR, quiet=False):
    backup_dir = Path(backup_dir)
    if not has_backup(backup_dir):
        raise UpdateError("There is no previous version to roll back to.")
    _install_tree(backup_dir, app_dir)
    try:
        meta = json.loads((backup_dir / ".ref").read_text())
        db.put("installed_ref", meta.get("ref", ""))
        db.put("installed_label", meta.get("label", ""))
    except (OSError, ValueError):
        pass
    try:
        _pip(Path(app_dir) / "requirements.txt")  # restore matching libraries (no-op if unchanged)
    except UpdateError:
        if not quiet:
            raise
