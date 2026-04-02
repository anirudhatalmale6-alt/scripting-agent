"""
agent/commit_tracker.py
───────────────────────
Persists a checkpoint of the last processed commit so the orchestrator
processes ALL commits since the checkpoint — not just the latest one.

This handles the multi-developer scenario:
  Dev A pushes commit abc123  → processed, checkpoint = abc123
  Dev B pushes commit def456  → processed, checkpoint = def456
  Dev C pushes commit ghi789  }
  Dev D pushes commit jkl012  } both arrive before agent wakes up
                               → agent fetches commits between def456..jkl012
                               → processes ghi789 AND jkl012 in order
                               → checkpoint = jkl012

Checkpoint file: .commit_checkpoint.json
  {
    "last_processed_sha": "abc123...",
    "last_processed_at":  "2026-04-02T10:00:00Z",
    "repo":               "owner/repo",
    "history": [
      {
        "sha":        "abc123...",
        "processed_at": "2026-04-02T10:00:00Z",
        "scripts_created": ["scripts/dev/api_orders_perf_test.js", ...],
        "scripts_updated": ["scripts/dev/checkout_test.js"],
        "dependency_changes": [{"package": "requests", "old": "2.28", "new": "2.31"}],
        "feature_changes":    [{"method": "POST", "path": "/api/orders"}],
        "summary": "Generated 3 scripts; patched 1."
      }
    ]
  }

Public API
──────────
  get_checkpoint(repo: str) -> str | None
  save_checkpoint(repo: str, sha: str, entry: dict)
  get_unprocessed_commits(repo: str, current_sha: str, headers: dict) -> list[str]
"""

import json
import os
from datetime import datetime, timezone
from typing import Optional

CHECKPOINT_FILE = ".commit_checkpoint.json"


def _load() -> dict:
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save(data: dict) -> None:
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def get_checkpoint(repo: str) -> Optional[str]:
    """Return the last processed commit SHA for this repo, or None."""
    data = _load()
    return data.get(repo, {}).get("last_processed_sha")


def save_checkpoint(repo: str, sha: str, entry: dict) -> None:
    """
    Save a processed commit to the checkpoint file.

    entry should contain:
      scripts_created, scripts_updated, dependency_changes,
      feature_changes, summary
    """
    data = _load()
    if repo not in data:
        data[repo] = {"last_processed_sha": None, "history": []}

    entry["sha"] = sha
    entry["processed_at"] = datetime.now(timezone.utc).isoformat()

    data[repo]["last_processed_sha"] = sha
    data[repo]["last_processed_at"] = entry["processed_at"]
    data[repo]["repo"] = repo

    # Keep last 100 entries
    data[repo].setdefault("history", []).append(entry)
    data[repo]["history"] = data[repo]["history"][-100:]

    _save(data)
    print(f"[tracker] Checkpoint saved: {repo} @ {sha[:8]}")


def get_unprocessed_commits(
    repo: str,
    current_sha: str,
    headers: dict,
) -> list:
    """
    Return ordered list of commit SHAs between last checkpoint and current_sha.
    If no checkpoint exists, returns just [current_sha].
    Oldest first so we process in chronological order.
    """
    import requests as _requests

    last_sha = get_checkpoint(repo)

    if not last_sha:
        print(f"[tracker] No checkpoint for {repo} — processing only current commit {current_sha[:8]}")
        return [current_sha]

    if last_sha == current_sha:
        print(f"[tracker] Commit {current_sha[:8]} already processed — skipping")
        return []

    # Fetch commits between last_sha and current_sha
    # GitHub compare API: GET /repos/{owner}/{repo}/compare/{base}...{head}
    url = f"https://api.github.com/repos/{repo}/compare/{last_sha}...{current_sha}"
    try:
        resp = _requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 404:
            # base commit no longer in history (force push / rebase) — reset
            print(f"[tracker] Base commit {last_sha[:8]} not found — resetting checkpoint")
            return [current_sha]
        resp.raise_for_status()
        data = resp.json()
        commits = data.get("commits", [])
        shas = [c["sha"] for c in commits]
        if not shas:
            shas = [current_sha]
        print(f"[tracker] {len(shas)} unprocessed commit(s) since checkpoint {last_sha[:8]}")
        return shas  # oldest first
    except Exception as e:
        print(f"[tracker] Compare failed: {e} — falling back to current commit only")
        return [current_sha]


def get_history(repo: str) -> list:
    """Return full processing history for a repo."""
    data = _load()
    return data.get(repo, {}).get("history", [])


def get_full_report(repo: str) -> dict:
    """Return the full checkpoint data for a repo."""
    data = _load()
    return data.get(repo, {})
