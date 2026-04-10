"""
agent/repo_config.py
────────────────────
Resolves which repos the agent monitors.

Priority:
  1. GITHUB_REPOS (comma-separated) — multi-repo
  2. GITHUB_REPO  (single)          — backward compat

Usage:
    from agent.repo_config import get_repos, is_monitored_repo

    for repo in get_repos():
        # repo = "owner/repo-name"
        ...
"""

import os


def get_repos() -> list:
    """Return list of repos to monitor."""
    multi = os.getenv("GITHUB_REPOS", "").strip()
    if multi:
        return [r.strip() for r in multi.split(",") if r.strip()]
    single = os.getenv("GITHUB_REPO", "").strip()
    return [single] if single else []


def is_monitored_repo(repo: str) -> bool:
    """Return True if this repo is in the monitored list."""
    return repo in get_repos()


def get_primary_repo() -> str:
    """Return first repo (backward compat for single-repo code paths)."""
    repos = get_repos()
    return repos[0] if repos else ""
