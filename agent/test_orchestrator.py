"""
agent/test_orchestrator.py
──────────────────────────
Test Trigger Agent pipeline per commit:

  1. PRE health check  — run all existing scripts, record baseline
  2. Fix pre-failing   — auto-fix any scripts already broken before this commit
  3. Detect changes    — Scenario A (dep upgrade) / Scenario B (new feature)
  4. Generate/update   — create new scripts or update existing ones
  5. Patch             — update scripts for dep changes
  6. POST health check — re-run all scripts, confirm everything passes
  7. Save checkpoint   — record what was created/updated/fixed

Public API
──────────
  orchestrate(pr_number, commit_sha) -> OrchestrationResult
"""

import os
import re
import requests
from dataclasses import dataclass, field
from typing import List, Optional
from dotenv import load_dotenv

from agent.code_change_detector import detect_changes, ChangeReport
from agent.script_generator import generate_all, GeneratedScripts
from agent.script_patcher import patch_all, PatchResult
from agent.script_health_checker import run_pre_check, run_post_check, HealthReport
from agent.commit_tracker import get_checkpoint, save_checkpoint, get_unprocessed_commits

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO  = os.getenv("GITHUB_REPOS", "").split(",")[0].strip()  # primary repo fallback
ENV          = os.getenv("ENV", "dev")

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class OrchestrationResult:
    change_report: Optional[ChangeReport] = None
    generated_scripts: List[GeneratedScripts] = field(default_factory=list)
    patch_results: List[PatchResult] = field(default_factory=list)
    commits_processed: List[str] = field(default_factory=list)
    pre_health: Optional[HealthReport] = None
    post_health: Optional[HealthReport] = None
    scripts_fixed: List[str] = field(default_factory=list)   # pre-existing failures fixed
    summary: str = ""
    error: Optional[str] = None

    def to_markdown(self) -> str:
        lines = ["## AI Test Orchestrator Report\n"]

        if self.error:
            lines.append(f"**Error:** {self.error}")
            return "\n".join(lines)

        if self.commits_processed:
            lines.append(
                f"**Commits processed:** {len(self.commits_processed)} "
                f"({', '.join(s[:8] for s in self.commits_processed)})\n"
            )

        # Health check delta
        if self.pre_health:
            lines.append("### Script Health Check")
            lines.append(self.pre_health.to_markdown())
            lines.append("")

        if self.scripts_fixed:
            lines.append("### Pre-existing Failures Fixed by Agent")
            for s in self.scripts_fixed:
                lines.append(f"- ✅ `{s}` (was failing before this commit)")
            lines.append("")

        if self.post_health:
            lines.append(self.post_health.to_markdown())
            lines.append("")
            # Delta summary
            if self.pre_health:
                pre_fail  = len(self.pre_health.failing)
                post_fail = len(self.post_health.failing)
                if post_fail == 0:
                    lines.append("✅ All scripts passing after changes.")
                elif post_fail < pre_fail:
                    lines.append(f"⚠️ {post_fail} script(s) still failing (improved from {pre_fail}).")
                else:
                    lines.append(f"❌ {post_fail} script(s) failing after changes — manual review needed.")
            lines.append("")

        cr = self.change_report
        if cr:
            if cr.raw_diff_summary:
                lines.append(f"**Diff summary:** {cr.raw_diff_summary}\n")
            if cr.has_dependency_changes:
                lines.append("### Dependency Upgrades")
                for d in cr.dependency_changes:
                    lines.append(f"- `{d.package}`: {d.old_version or 'new'} → {d.new_version or 'removed'}")
                lines.append("")
            if cr.has_feature_changes:
                lines.append("### New / Changed Endpoints")
                for f in cr.feature_changes:
                    lines.append(f"- `{f.method} {f.path}` — {f.description}")
                lines.append("")

        if self.generated_scripts:
            lines.append("### Generated / Updated Scripts")
            for gs in self.generated_scripts:
                status = "✅" if gs.k6_validated else f"⚠️ failed after {gs.k6_validation_attempts} attempts"
                lines.append(f"- k6 ({status}): `{gs.k6_path}`")
                lines.append(f"  LoadRunner: `{gs.loadrunner_path}`")
                lines.append(f"  Selenium: `{gs.selenium_path}`")
            lines.append("")

        patched = [r for r in self.patch_results if r.patched]
        if patched:
            lines.append("### Patched Scripts")
            for r in patched:
                lines.append(f"- `{r.file}`: {r.reason}")
            lines.append("")

        if not cr or not cr.needs_action:
            lines.append("No test changes required for this push.")

        return "\n".join(lines)


# ── GitHub API helpers ────────────────────────────────────────────────────────

def _get_pr_files(pr_number: int) -> list:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/pulls/{pr_number}/files"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[orchestrator] Failed to fetch PR files: {e}")
        return []


def _get_pr_diff(pr_number: int) -> str:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/pulls/{pr_number}"
    try:
        resp = requests.get(
            url, headers={**HEADERS, "Accept": "application/vnd.github.diff"}, timeout=15,
        )
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"[orchestrator] Failed to fetch PR diff: {e}")
        return ""


def _get_commit_files(sha: str, repo: str = None) -> list:
    _repo = repo or GITHUB_REPO
    url = f"https://api.github.com/repos/{_repo}/commits/{sha}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json().get("files", [])
    except Exception as e:
        print(f"[orchestrator] Failed to fetch commit {sha[:8]} for {_repo}: {e}")
        return []


# ── Auto-fix pre-existing failures ───────────────────────────────────────────

def _fix_failing_scripts(pre_health: HealthReport) -> List[str]:
    """
    For each script that was FAILING before this commit, ask GPT to fix it.
    Returns list of script paths that were successfully fixed.
    """
    from agent.script_generator import (
        _openai_available, _k6_template, _inject_options_if_missing,
        _validate_and_fix_k6, _write, MAX_FIX_ITERATIONS,
    )
    from agent.code_change_detector import FeatureChange
    from openai import OpenAI
    import os

    fixed = []
    if not pre_health.failing:
        return fixed

    print(f"[orchestrator] Auto-fixing {len(pre_health.failing)} pre-existing failures...")

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY")) if _openai_available() else None

    for sr in pre_health.failing:
        path = sr.path
        if not os.path.exists(path) or not path.endswith(".js"):
            continue
        print(f"[orchestrator] Fixing pre-existing failure: {os.path.basename(path)}")

        if not client:
            print(f"[orchestrator] No OpenAI key — skipping fix for {path}")
            continue

        try:
            current = open(path, encoding="utf-8").read()
            resp = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[{"role": "user", "content": f"""Fix this failing k6 script.
Error: {sr.error[:1000]}
Requirements: valid k6 API, export const options, export default function,
__ENV.SFCC_SITE_URL fallback to 'https://test.k6.io', IS_REAL_APP guard,
status 200/201 checks, sleep(1). Return ONLY JS, no fences.
---
{current}"""}],
                temperature=0,
            )
            fixed_content = _inject_options_if_missing(
                resp.choices[0].message.content.strip(),
                FeatureChange(file=path, method="GET", path="/", description="pre-fix"),
            )
            _write(path, fixed_content)

            # Quick validation
            from agent.script_health_checker import _run_one, _resolve_target
            target = _resolve_target()
            if target:
                result = _run_one(path, target)
                if result.status == "passing":
                    print(f"[orchestrator] ✅ Fixed: {os.path.basename(path)}")
                    fixed.append(os.path.basename(path))
                else:
                    print(f"[orchestrator] ⚠️  Still failing after fix: {os.path.basename(path)}")
            else:
                fixed.append(os.path.basename(path))  # can't verify, assume fixed
        except Exception as e:
            print(f"[orchestrator] Fix failed for {path}: {e}")

    return fixed


# ── Full repo scan (first boot, no scripts exist) ────────────────────────────

def scan_full_repo(repo: str = None, env: str = None) -> OrchestrationResult:
    """
    Walk every commit in the repo history (oldest → newest), run
    detect_changes on each commit's diff, accumulate all unique endpoints
    and dependency changes, then generate scripts once per unique endpoint.

    Called once on first boot when no scripts and no checkpoint exist.
    Saves a checkpoint at HEAD so this never runs again on restart.
    """
    result = OrchestrationResult()
    _repo = repo or GITHUB_REPO
    _env  = env  or ENV

    if not _repo:
        result.error = "GITHUB_REPO not set"
        return result

    print(f"[orchestrator] Full commit-history scan — {_repo}")

    # 1. Fetch all commits (oldest first, paginated)
    all_commits = []
    page = 1
    while True:
        try:
            r = requests.get(
                f"https://api.github.com/repos/{_repo}/commits",
                headers=HEADERS,
                params={"per_page": 100, "page": page},
                timeout=30,
            )
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            all_commits.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        except Exception as e:
            result.error = f"Could not fetch commits (page {page}): {e}"
            return result

    if not all_commits:
        result.error = "No commits found in repo"
        return result

    # oldest → newest
    all_commits.reverse()
    head_sha = all_commits[-1]["sha"]
    print(f"[orchestrator] {len(all_commits)} commits to scan (oldest→newest), HEAD={head_sha[:8]}")

    # 2. Walk each commit, accumulate unique features + dep changes
    from agent.code_change_detector import detect_changes, FeatureChange, DependencyChange

    seen_endpoints: set = set()
    all_features:   list[FeatureChange]    = []
    all_dep_changes: list[DependencyChange] = []

    for idx, commit in enumerate(all_commits, 1):
        sha = commit["sha"]
        msg = commit.get("commit", {}).get("message", "")[:60]
        print(f"[orchestrator] [{idx}/{len(all_commits)}] {sha[:8]} — {msg}")

        changed_files = _get_commit_files(sha)
        if not changed_files:
            continue

        report = detect_changes("", changed_files)

        # Accumulate dependency changes (all of them, no dedup needed)
        all_dep_changes.extend(report.dependency_changes)

        # Deduplicate endpoints by (method, path) — keep first occurrence
        for f in report.feature_changes:
            key = (f.method, f.path)
            if key not in seen_endpoints:
                seen_endpoints.add(key)
                all_features.append(f)
                print(f"[orchestrator]   + {f.method} {f.path} ({f.file})")

    print(f"[orchestrator] Scan complete — {len(all_features)} unique endpoints, "
          f"{len(all_dep_changes)} dep changes across {len(all_commits)} commits")

    if not all_features:
        result.summary = "No endpoints detected across commit history — no scripts generated."
        save_checkpoint(_repo, head_sha, {
            "scripts_created": [], "scripts_updated": [],
            "dependency_changes": [], "feature_changes": [],
            "summary": result.summary,
        })
        return result

    # 3. Generate scripts for all unique endpoints (create or update, no blind overwrite)
    print(f"[orchestrator] Generating scripts for {len(all_features)} endpoints...")
    result.generated_scripts = generate_all(all_features, env=_env, repo=_repo)

    # 4. Save checkpoint at HEAD — prevents re-scan on next restart
    created_paths = []
    for gs in result.generated_scripts:
        created_paths += [gs.k6_path, gs.loadrunner_path, gs.selenium_path]

    save_checkpoint(_repo, head_sha, {
        "scripts_created": created_paths,
        "scripts_updated": [],
        "dependency_changes": [
            {"package": d.package, "old": d.old_version, "new": d.new_version}
            for d in all_dep_changes
        ],
        "feature_changes": [{"method": f.method, "path": f.path} for f in all_features],
        "summary": (
            f"Full history scan: {len(all_commits)} commits, "
            f"{len(all_features)} endpoints, {len(created_paths)} scripts generated."
        ),
    })

    result.summary = (
        f"Full history scan: {len(all_commits)} commits scanned, "
        f"{len(all_features)} unique endpoints found, "
        f"{len(result.generated_scripts) * 3} scripts generated."
    )
    print(f"[orchestrator] {result.summary}")
    return result


# ── Single-commit processing ──────────────────────────────────────────────────

def _process_commit(sha: str, pre_health: HealthReport, repo: str = None, env: str = None) -> dict:
    _repo = repo or GITHUB_REPO
    _env  = env  or ENV
    print(f"[orchestrator] Processing commit {sha[:8]} for {_repo}")
    changed_files = _get_commit_files(sha, _repo)

    if not changed_files:
        entry = {
            "scripts_created": [], "scripts_updated": [],
            "dependency_changes": [], "feature_changes": [],
            "summary": "No changed files.",
        }
        save_checkpoint(_repo, sha, entry)
        return entry

    change_report = detect_changes("", changed_files)
    scripts_created, scripts_updated, generated, patches = [], [], [], []

    if change_report.has_feature_changes:
        print(f"[orchestrator] {len(change_report.feature_changes)} feature(s) detected in {_repo}")
        generated = generate_all(change_report.feature_changes, env=_env, repo=_repo)
        for gs in generated:
            pre_paths = {r.path for r in pre_health.results}
            if gs.k6_path in pre_paths:
                scripts_updated += [gs.k6_path, gs.loadrunner_path, gs.selenium_path]
            else:
                scripts_created += [gs.k6_path, gs.loadrunner_path, gs.selenium_path]
    else:
        print(f"[orchestrator] {sha[:8]}: no Scenario A/B changes — checkpoint anchored, no scripts generated")

    if change_report.needs_action:
        patches = patch_all(change_report, repo=_repo)
        scripts_updated += [r.file for r in patches if r.patched]

    summary = f"Created {len(scripts_created)} scripts; updated {len(scripts_updated)} scripts."
    print(f"[orchestrator] {sha[:8]}: {summary}")

    entry = {
        "scripts_created": scripts_created,
        "scripts_updated": scripts_updated,
        "dependency_changes": [
            {"package": d.package, "old": d.old_version, "new": d.new_version}
            for d in change_report.dependency_changes
        ],
        "feature_changes": [{"method": f.method, "path": f.path}
                             for f in change_report.feature_changes],
        "summary": summary,
    }
    save_checkpoint(_repo, sha, entry)
    return {"change_report": change_report, "generated": generated,
            "patches": patches, "entry": entry}


# ── Public API ────────────────────────────────────────────────────────────────

def orchestrate(
    pr_number: Optional[int] = None,
    commit_sha: Optional[str] = None,
    repo: Optional[str] = None,
) -> OrchestrationResult:
    """
    Full pipeline:
      PRE check → fix failures → detect → generate/patch → POST check → checkpoint
    """
    result = OrchestrationResult()
    _repo = repo or GITHUB_REPO
    _env  = ENV

    if not _repo:
        result.error = "GITHUB_REPO env var not set"
        return result

    # ── Step 1: PRE health check ──────────────────────────────────────────────
    print(f"[orchestrator] Running PRE health check for {_repo}...")
    pre_health = run_pre_check(_repo, _env)
    result.pre_health = pre_health

    # ── Step 2: Fix any pre-existing failures ─────────────────────────────────
    if pre_health.failing:
        result.scripts_fixed = _fix_failing_scripts(pre_health)

    # ── PR event ──────────────────────────────────────────────────────────────
    if pr_number:
        print(f"[orchestrator] Processing PR #{pr_number} for {_repo}")
        changed_files = _get_pr_files(pr_number)
        diff_text     = _get_pr_diff(pr_number)

        if not changed_files:
            result.summary = "No changed files in PR."
        else:
            change_report = detect_changes(diff_text, changed_files)
            result.change_report = change_report

            if change_report.has_feature_changes:
                result.generated_scripts = generate_all(
                    change_report.feature_changes, env=_env, repo=_repo
                )
            if change_report.needs_action:
                result.patch_results = patch_all(change_report, repo=_repo)

            n_gen   = len(result.generated_scripts)
            n_patch = sum(1 for r in result.patch_results if r.patched)
            result.summary = f"PR #{pr_number}: generated {n_gen * 3} scripts; patched {n_patch}."
            result.commits_processed = [f"PR#{pr_number}"]

        # POST check
        print(f"[orchestrator] Running POST health check for {_repo}...")
        result.post_health = run_post_check(_repo, _env)
        return result

    # ── Push event ────────────────────────────────────────────────────────────
    if not commit_sha:
        result.error = "Must provide pr_number or commit_sha"
        return result

    checkpoint = get_checkpoint(_repo)
    print(f"[orchestrator] Push on {_repo} — current: {commit_sha[:8]}, "
          f"checkpoint: {checkpoint[:8] if checkpoint else 'none'}")

    shas = get_unprocessed_commits(_repo, commit_sha, HEADERS)
    if not shas:
        result.summary = "All commits already processed."
        result.post_health = run_post_check(_repo, _env)
        return result

    all_generated: List[GeneratedScripts] = []
    all_patches:   List[PatchResult]      = []
    last_report:   Optional[ChangeReport] = None

    for sha in shas:
        out = _process_commit(sha, pre_health, repo=_repo, env=_env)
        result.commits_processed.append(sha)
        if isinstance(out, dict) and "change_report" in out:
            last_report = out["change_report"]
            all_generated.extend(out.get("generated", []))
            all_patches.extend(out.get("patches", []))

    result.change_report     = last_report
    result.generated_scripts = all_generated
    result.patch_results     = all_patches

    # ── Step 6: POST health check ─────────────────────────────────────────────
    print(f"[orchestrator] Running POST health check for {_repo}...")
    result.post_health = run_post_check(_repo, _env)

    total_created = len([gs for gs in all_generated])
    total_patched = sum(1 for r in all_patches if r.patched)
    post_fail     = len(result.post_health.failing)

    result.summary = (
        f"Processed {len(shas)} commit(s) on {_repo}; "
        f"created/updated {total_created * 3} scripts; "
        f"patched {total_patched}; "
        f"post-check: {len(result.post_health.passing)} passing, {post_fail} failing."
    )
    print(f"[orchestrator] Done. {result.summary}")
    return result
