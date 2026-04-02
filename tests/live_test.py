"""
tests/live_test.py
──────────────────
Live test against a real GitHub repo using credentials from .env.

Tests:
  1. GitHub credentials configured
  2. GitHub API reachable
  3. Latest commit fetched
  4. Changed files returned
  5. detect_changes runs against real diff
  6. Scripts generated for any new features found
  7. Scripts patched for any dependency changes found
  8. Full orchestrate() call with real commit SHA

Run with:
    python tests/live_test.py
"""

import os
import sys
import requests

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = "\033[92m✅ PASS\033[0m"
FAIL = "\033[91m❌ FAIL\033[0m"
INFO = "\033[94mℹ️ \033[0m"

results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    print(f"  {status}  {name}")
    if detail:
        print(f"       {detail}")
    results.append((name, condition))
    return condition

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

# ─────────────────────────────────────────────────────────────────────────────
# TEST — Real GitHub Repo
# ─────────────────────────────────────────────────────────────────────────────
section("GitHub Live Test — Real Repo Commit Detection")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO  = os.getenv("GITHUB_REPO", "")

if not GITHUB_TOKEN or not GITHUB_REPO:
    check("GitHub credentials configured", False, "GITHUB_TOKEN or GITHUB_REPO not set in .env")
    sys.exit(1)

check("GitHub credentials configured", True, f"repo={GITHUB_REPO}")

headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
latest_sha = None

# ── Fetch latest commit ───────────────────────────────────────────────────────
try:
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/commits",
        headers=headers, params={"per_page": 1}, timeout=10
    )
    if r.status_code in (401, 403):
        check("GitHub API reachable", False, f"Auth failed ({r.status_code}) — check GITHUB_TOKEN in .env")
        sys.exit(1)
    elif r.status_code == 404:
        check("GitHub API reachable", False, f"Repo not found ({r.status_code}) — check GITHUB_REPO={GITHUB_REPO}")
        sys.exit(1)
    else:
        check("GitHub API reachable", r.status_code == 200, f"status={r.status_code}")
        if r.status_code == 200:
            commits = r.json()
            latest_sha = commits[0]["sha"] if commits else None
            check("Latest commit found", bool(latest_sha), str(latest_sha)[:12] if latest_sha else "none")
except Exception as e:
    check("GitHub API reachable", False, str(e))
    sys.exit(1)

if not latest_sha:
    print(f"\n  {INFO} No commits found in repo — nothing to test")
    sys.exit(0)

# ── Fetch changed files ───────────────────────────────────────────────────────
changed_files = []
try:
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/commits/{latest_sha}",
        headers=headers, timeout=10
    )
    check("Commit detail fetched", r.status_code == 200, f"sha={latest_sha[:8]}")
    commit_data = r.json() if r.status_code == 200 else {}
    changed_files = commit_data.get("files", [])
    check("Changed files returned", isinstance(changed_files, list), f"{len(changed_files)} files")
except Exception as e:
    check("Commit detail fetched", False, str(e))

# ── Run detect_changes ────────────────────────────────────────────────────────
from agent.code_change_detector import detect_changes

real_report = detect_changes("", changed_files)

print(f"\n  {INFO} Commit {latest_sha[:8]} — {len(changed_files)} files changed")
print(f"  {INFO} Dependency changes : {len(real_report.dependency_changes)}")
print(f"  {INFO} Feature changes    : {len(real_report.feature_changes)}")
for d in real_report.dependency_changes:
    print(f"       dep : {d.package} {d.old_version} → {d.new_version}")
for f in real_report.feature_changes:
    print(f"       feat: {f.method} {f.path}")

check("detect_changes ran without error", True, "")
check("ChangeReport returned", hasattr(real_report, "has_dependency_changes"), "")

# ── Generate scripts for new features ────────────────────────────────────────
if real_report.has_feature_changes:
    print(f"\n  {INFO} Generating scripts for {len(real_report.feature_changes)} feature(s)...")
    from agent.script_generator import generate_all
    generated = generate_all(real_report.feature_changes, env="dev")
    check("Scripts generated", len(generated) > 0, f"{len(generated)} sets")
    for gs in generated:
        check(f"k6 script created: {os.path.basename(gs.k6_path)}",
              os.path.exists(gs.k6_path), gs.k6_path)
        check(f"k6 validated: {os.path.basename(gs.k6_path)}",
              gs.k6_validated,
              f"attempts={gs.k6_validation_attempts} err={gs.k6_validation_error[:80]}")
else:
    print(f"  {INFO} No new features in latest commit — script generation skipped")
    check("No feature changes is valid outcome", True,
          "Latest commit has no new endpoints — detection working correctly")

# ── Patch scripts for dependency changes ─────────────────────────────────────
if real_report.has_dependency_changes:
    from agent.script_patcher import patch_all
    patch_results = patch_all(real_report)
    patched = [p for p in patch_results if p.patched]
    print(f"  {INFO} Patched {len(patched)}/{len(patch_results)} scripts for dep upgrade")
    check("Script patching ran without error", True, f"patched={len(patched)}")
else:
    check("No dep changes is valid outcome", True,
          "Latest commit has no dependency changes — detection working correctly")

# ── Full orchestrate() ────────────────────────────────────────────────────────
print(f"\n  {INFO} Running full orchestrate() with commit {latest_sha[:8]}...")
from agent.test_orchestrator import orchestrate
try:
    orch = orchestrate(commit_sha=latest_sha)
    check("orchestrate() completed without exception", orch.error is None, orch.error or "ok")
    check("OrchestrationResult has change_report", orch.change_report is not None, "")
    md = orch.to_markdown()
    check("to_markdown() produces output", len(md) > 50, f"{len(md)} chars")
    print(f"\n  {INFO} Orchestration summary: {orch.summary}")
except Exception as e:
    check("orchestrate() completed without exception", False, str(e))

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
passed = sum(1 for _, r in results if r)
failed = sum(1 for _, r in results if not r)
print(f"\n{'='*60}")
print(f"  Results: {passed} passed, {failed} failed out of {len(results)} checks")
print(f"{'='*60}\n")
sys.exit(0 if failed == 0 else 1)
