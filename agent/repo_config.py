"""
agent/repo_config.py
────────────────────
Resolves which repos the agent monitors.

Set GITHUB_REPOS as comma-separated list in .env:
  GITHUB_REPOS=org/repo1,org/repo2,org/repo3

Each repo gets its own isolated scripts/ subfolder.
"""

import os


def get_repos() -> list:
    """
    Return deduplicated list of repos to monitor.
    Set GITHUB_REPOS as comma-separated list in .env:
      GITHUB_REPOS=org/repo1,org/repo2,org/repo3
    """
    raw = os.getenv("GITHUB_REPOS", "").split("#")[0].strip()
    if not raw:
        return []
    repos = [r.strip() for r in raw.split(",") if r.strip()]
    return [r for r in repos if "/" in r]


def is_monitored_repo(repo: str) -> bool:
    """Return True if this repo is in the monitored list."""
    return repo in get_repos()


def get_primary_repo() -> str:
    """Return first repo (backward compat for single-repo code paths)."""
    repos = get_repos()
    return repos[0] if repos else ""
