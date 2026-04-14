"""
agent/commit_tracker.py
───────────────────────
One checkpoint file per repo:
  .commit_checkpoint_owner__repo.json

This keeps repos fully isolated — no shared state, no conflicts
when multiple repos are processed in parallel.

Public API
──────────
  get_checkpoint(repo)                          -> str | None
  save_checkpoint(repo, sha, entry)
  get_unprocessed_commits(repo, sha, headers)   -> list[str]
  get_history(repo)                             -> list
  get_full_report(repo)                         -> dict
"""

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import Optional


def _repo_slug(repo: str) -> str:
    """'owner/my-repo' → 'owner__my-repo'"""
    return re.sub(r'[^a-z0-9_\-]+', '_', repo.lower()).replace('/', '__')


def _checkpoint_file(repo: str) -> str:
    """Return path to per-repo checkpoint file."""
    slug = _repo_slug(repo)
    filename = f".commit_checkpoint_{slug}.json"
    # Support override via env (useful in tests)
    base_dir = os.environ.get("CHECKPOINT_DIR", ".")
    return os.path.join(base_dir, filename)


def _load(repo: str) -> dict:
    path = _checkpoint_file(repo)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save(repo: str, data: dict) -> None:
    path = _checkpoint_file(repo)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ── Public API ────────────────────────────────────────────────────────────────

def get_checkpoint(repo: str) -> Optional[str]:
    """Return the last processed commit SHA for this repo, or None."""
    return _load(repo).get("last_processed_sha")


def save_checkpoint(repo: str, sha: str, entry: dict) -> None:
    data = _load(repo)
    if not data:
        data = {"repo": repo, "last_processed_sha": None, "history": []}

    entry["sha"]          = sha
    entry["processed_at"] = datetime.now(timezone.utc).isoformat()

    data["last_processed_sha"] = sha
    data["last_processed_at"]  = entry["processed_at"]
    data["repo"]               = repo

    data.setdefault("history", []).append(entry)
    data["history"] = data["history"][-100:]  # keep last 100

    _save(repo, data)
    print(f"[tracker] Checkpoint saved: {_checkpoint_file(repo)} @ {sha[:8]}")


def get_unprocessed_commits(repo: str, current_sha: str, headers: dict) -> list:
    import requests as _req

    last_sha = get_checkpoint(repo)

    if not last_sha:
        print(f"[tracker] No checkpoint for {repo} — processing {current_sha[:8]}")
        return [current_sha]

    if last_sha == current_sha:
        print(f"[tracker] {current_sha[:8]} already processed — skipping")
        return []

    url = f"https://api.github.com/repos/{repo}/compare/{last_sha}...{current_sha}"
    try:
        resp = _req.get(url, headers=headers, timeout=15)
        if resp.status_code == 404:
            print(f"[tracker] Base {last_sha[:8]} not found — resetting")
            return [current_sha]
        resp.raise_for_status()
        shas = [c["sha"] for c in resp.json().get("commits", [])]
        if not shas:
            shas = [current_sha]
        print(f"[tracker] {len(shas)} unprocessed commit(s) since {last_sha[:8]}")
        return shas
    except Exception as e:
        print(f"[tracker] Compare failed: {e} — using current commit only")
        return [current_sha]


def get_history(repo: str) -> list:
    return _load(repo).get("history", [])


def get_full_report(repo: str) -> dict:
    return _load(repo)
