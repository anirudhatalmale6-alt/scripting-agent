"""
agent/test_orchestrator.py
──────────────────────────
Test Trigger Agent pipeline per commit.

Pipeline:
  1. PRE health check  — run existing scripts, record baseline
  2. Fix pre-failing   — auto-fix scripts broken before this commit
  3. Route             — use .perf/mappings.yaml to decide what to skip/run
  4. Detect changes    — Scenario A (dep upgrade) / Scenario B (new feature)
  5. Generate/update   — only scripts for affected domains
  6. Patch             — update scripts for dep changes
  7. POST health check — re-run all scripts, confirm everything passes
  8. Merge gate        — apply pass/warn/fail from .perf/profiles/
  9. Save checkpoint   — record what was created/updated/fixed

Public API
──────────
  orchestrate(pr_number, commit_sha, repo, impact_map) -> OrchestrationResult
"""

import logging
import os
import requests
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from dotenv import load_dotenv

from agent.code_change_detector import detect_changes, ChangeReport
from agent.script_generator import generate_all, GeneratedScripts
from agent.script_patcher import patch_all, PatchResult
from agent.script_health_checker import run_pre_check, run_post_check, HealthReport
from agent.commit_tracker import get_checkpoint, save_checkpoint, get_unprocessed_commits
from agent.perf_policy import (
    PerfPolicy, ProfileConfig,
    load_policy, build_impact_map, should_skip_file,
    get_profile, load_agent_skill,
)

load_dotenv()

log = logging.getLogger(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO  = os.getenv("GITHUB_REPOS", "").split(",")[0].strip()
ENV          = os.getenv("ENV", "dev")

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}

# ── Cached module-level resources ─────────────────────────────────────────────
_policy: Optional[PerfPolicy] = None
_scripting_skill: str = ""


def _get_policy() -> PerfPolicy:
    global _policy
    if _policy is None:
        _policy = load_policy()
    return _policy


def _get_scripting_skill() -> str:
    global _scripting_skill
    if not _scripting_skill:
        _scripting_skill = load_agent_skill("SCRIPTING_AGENT.md")
    return _scripting_skill


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class MergeGateResult:
    status: str = "pass"          # pass | warn | fail
    reason: str = ""
    risk_level: str = "low"
    block_merge: bool = False


@dataclass
class OrchestrationResult:
    change_report: Optional[ChangeReport] = None
    generated_scripts: List[GeneratedScripts] = field(default_factory=list)
    patch_results: List[PatchResult] = field(default_factory=list)
    commits_processed: List[str] = field(default_factory=list)
    pre_health: Optional[HealthReport] = None
    post_health: Optional[HealthReport] = None
    scripts_fixed: List[str] = field(default_factory=list)
    impact_map: Dict = field(default_factory=dict)
    merge_gate: MergeGateResult = field(default_factory=MergeGateResult)
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

        # Impact map
        if self.impact_map.get("changed_domains"):
            lines.append(f"**Affected domains:** {', '.join(self.impact_map['changed_domains'])}")
            lines.append(f"**Risk level:** {self.impact_map.get('risk_level', 'low')}\n")

        # Merge gate
        gate = self.merge_gate
        icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}.get(gate.status, "ℹ️")
        lines.append(f"**Merge gate:** {icon} {gate.status.upper()} — {gate.reason}\n")

        if self.pre_health:
            lines.append("### Script Health Check")
            lines.append(self.pre_health.to_markdown())
            lines.append("")

        if self.scripts_fixed:
            lines.append("### Pre-existing Failures Fixed")
            for s in self.scripts_fixed:
                lines.append(f"- ✅ `{s}`")
            lines.append("")

        if self.post_health:
            lines.append(self.post_health.to_markdown())
            lines.append("")
            if self.pre_health:
                pre_fail  = len(self.pre_health.failing)
                post_fail = len(self.post_health.failing)
                if post_fail == 0:
                    lines.append("✅ All scripts passing after changes.")
                elif post_fail < pre_fail:
                    lines.append(f"⚠️ {post_fail} script(s) still failing (improved from {pre_fail}).")
                else:
                    lines.append(f"❌ {post_fail} script(s) failing — manual review needed.")
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


# ── Merge gate ────────────────────────────────────────────────────────────────

def _evaluate_merge_gate(
    post_health: HealthReport,
    impact_map: dict,
    profile: Optional[ProfileConfig],
) -> MergeGateResult:
    """
    Apply merge gate rules from the execution profile.
    Returns MergeGateResult with status: pass | warn | fail.
    """
    risk = impact_map.get("risk_level", "low")
    post_fail = len(post_health.failing) if post_health else 0

    # No profile — default permissive gate
    if not profile:
        if post_fail == 0:
            return MergeGateResult(status="pass", reason="All scripts passing", risk_level=risk)
        return MergeGateResult(
            status="warn", reason=f"{post_fail} script(s) failing", risk_level=risk
        )

    block_on_critical = getattr(profile, "block_on_critical_flow_failure", True)
    warn_pct  = getattr(profile, "warn_on_regression_pct",  20)
    block_pct = getattr(profile, "block_on_regression_pct", 50)

    # Critical flow failure on high-risk domain
    if post_fail > 0 and risk == "high" and block_on_critical:
        return MergeGateResult(
            status="fail",
            reason=f"{post_fail} script(s) failing on high-risk domain — merge blocked",
            risk_level=risk,
            block_merge=True,
        )

    # Percentage-based gate (when we have pre+post counts)
    if post_health and post_fail > 0:
        total = len(post_health.passing) + post_fail
        fail_pct = (post_fail / total * 100) if total > 0 else 0
        if fail_pct >= block_pct:
            return MergeGateResult(
                status="fail",
                reason=f"{fail_pct:.0f}% scripts failing (threshold: {block_pct}%) — merge blocked",
                risk_level=risk,
                block_merge=True,
            )
        if fail_pct >= warn_pct:
            return MergeGateResult(
                status="warn",
                reason=f"{fail_pct:.0f}% scripts failing (warn threshold: {warn_pct}%)",
                risk_level=risk,
            )

    if post_fail == 0:
        return MergeGateResult(status="pass", reason="All scripts passing", risk_level=risk)

    return MergeGateResult(
        status="warn", reason=f"{post_fail} script(s) failing", risk_level=risk
    )


# ── GitHub API helpers ────────────────────────────────────────────────────────

def _get_pr_files(pr_number: int, repo: str = None) -> list:
    _repo = repo or GITHUB_REPO
    url = f"https://api.github.com/repos/{_repo}/pulls/{pr_number}/files"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning(f"[orchestrator] Failed to fetch PR files: {e}")
        return []


def _get_pr_diff(pr_number: int, repo: str = None) -> str:
    _repo = repo or GITHUB_REPO
    url = f"https://api.github.com/repos/{_repo}/pulls/{pr_number}"
    try:
        resp = requests.get(
            url, headers={**HEADERS, "Accept": "application/vnd.github.diff"}, timeout=15,
        )
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        log.warning(f"[orchestrator] Failed to fetch PR diff: {e}")
        return ""


def _get_commit_files(sha: str, repo: str = None) -> list:
    _repo = repo or GITHUB_REPO
    url = f"https://api.github.com/repos/{_repo}/commits/{sha}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json().get("files", [])
    except Exception as e:
        log.warning(f"[orchestrator] Failed to fetch commit {sha[:8]} for {_repo}: {e}")
        return []


# ── Routing — filter features to only affected domains ───────────────────────

def _filter_features_by_impact(features: list, impact_map: dict) -> list:
    """
    Use the impact map to keep only features whose domain is in the affected list.
    Falls back to all features if impact map is empty (no .perf/mappings.yaml).
    """
    affected_domains = impact_map.get("changed_domains", [])
    if not affected_domains:
        return features  # no mapping — process everything

    filtered = []
    for f in features:
        path_lower = f.path.lower()
        # Check if any affected domain keyword appears in the endpoint path
        for domain in affected_domains:
            domain_keyword = domain.replace("-domain", "").lower()
            if domain_keyword in path_lower or domain_keyword in f.file.lower():
                filtered.append(f)
                break
        else:
            # Also include if the file itself is in the affected k6 list
            k6_affected = impact_map.get("test_updates_needed", {}).get("k6", [])
            if any(kw in path_lower for kw in k6_affected):
                filtered.append(f)

    log.info(
        f"[orchestrator] Routing: {len(features)} features → "
        f"{len(filtered)} after domain filter (domains: {affected_domains})"
    )
    return filtered if filtered else features  # never return empty — fallback to all


def _should_skip_change(changed_files: list, policy: PerfPolicy) -> bool:
    """Return True if ALL changed files should be skipped per routing rules."""
    if not changed_files:
        return False
    filenames = [f.get("filename", "") if isinstance(f, dict) else f for f in changed_files]
    return all(should_skip_file(fn, policy) for fn in filenames if fn)


# ── Auto-fix pre-existing failures ───────────────────────────────────────────

def _fix_failing_scripts(pre_health: HealthReport) -> List[str]:
    """
    For each script failing before this commit, ask GPT to fix it.
    Returns list of script paths successfully fixed.
    """
    from agent.script_generator import (
        _openai_available, _inject_options_if_missing, _write,
    )
    from agent.code_change_detector import FeatureChange
    from openai import OpenAI

    fixed = []
    if not pre_health.failing:
        return fixed

    if not _openai_available():
        log.info("[orchestrator] No OpenAI key — skipping auto-fix")
        return fixed

    log.info(f"[orchestrator] Auto-fixing {len(pre_health.failing)} pre-existing failures...")
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    skill_context = ""
    skill = _get_scripting_skill()
    if skill and "## What You Must Never Do" in skill:
        skill_context = (
            "\nStandards: export const options, IS_REAL_APP guard, "
            "check(), sleep(1), domain tags required.\n"
        )

    for sr in pre_health.failing:
        path = sr.path
        if not os.path.exists(path) or not path.endswith(".js"):
            continue
        log.info(f"[orchestrator] Fixing: {os.path.basename(path)}")
        try:
            current = open(path, encoding="utf-8").read()
            resp = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[{"role": "user", "content": (
                    f"Fix this failing k6 script.\n"
                    f"Error: {sr.error[:1000]}\n"
                    f"Requirements: valid k6 API, export const options, export default function, "
                    f"__ENV.SFCC_SITE_URL fallback to 'https://test.k6.io', IS_REAL_APP guard, "
                    f"status 200/201 checks, sleep(1). Return ONLY JS, no fences.\n"
                    f"{skill_context}---\n{current}"
                )}],
                temperature=0,
            )
            fixed_content = _inject_options_if_missing(
                resp.choices[0].message.content.strip(),
                FeatureChange(file=path, method="GET", path="/", description="pre-fix"),
            )
            _write(path, fixed_content)

            from agent.script_health_checker import _run_one, _resolve_target
            target = _resolve_target()
            if target:
                result = _run_one(path, target)
                if result.status == "passing":
                    log.info(f"[orchestrator] ✅ Fixed: {os.path.basename(path)}")
                    fixed.append(os.path.basename(path))
                else:
                    log.warning(f"[orchestrator] ⚠️ Still failing: {os.path.basename(path)}")
            else:
                fixed.append(os.path.basename(path))
        except Exception as e:
            log.error(f"[orchestrator] Fix failed for {path}: {e}")

    return fixed


# ── Single-commit processing ──────────────────────────────────────────────────

def _process_commit(
    sha: str,
    pre_health: HealthReport,
    impact_map: dict,
    repo: str = None,
    env: str = None,
) -> dict:
    _repo = repo or GITHUB_REPO
    _env  = env  or ENV
    policy = _get_policy()

    log.info(f"[orchestrator] Processing commit {sha[:8]} for {_repo}")
    changed_files = _get_commit_files(sha, _repo)

    if not changed_files:
        entry = {
            "scripts_created": [], "scripts_updated": [],
            "dependency_changes": [], "feature_changes": [],
            "summary": "No changed files.",
        }
        save_checkpoint(_repo, sha, entry)
        return entry

    # ── Routing: skip docs-only commits ──────────────────────────────────────
    if _should_skip_change(changed_files, policy):
        log.info(f"[orchestrator] {sha[:8]}: docs/config only — skipping per routing rules")
        entry = {
            "scripts_created": [], "scripts_updated": [],
            "dependency_changes": [], "feature_changes": [],
            "summary": "Skipped — docs/config only change per .perf/rules/commit-routing.md",
        }
        save_checkpoint(_repo, sha, entry)
        return entry

    change_report = detect_changes("", changed_files)
    scripts_created, scripts_updated, generated, patches = [], [], [], []

    if change_report.has_feature_changes:
        # ── Policy routing: only generate for affected domains ────────────────
        features_to_generate = _filter_features_by_impact(
            change_report.feature_changes, impact_map
        )

        # ── Selenium index: find existing scripts affected by this change ─────
        from agent.selenium_index import get_or_build_index, find_scripts_for_changed_files
        from agent.script_generator import _script_root, repo_slug
        sel_root = os.path.join(_script_root(_repo, _env), "selenium")
        sel_index = get_or_build_index(sel_root)
        filenames = [f.get("filename", "") if isinstance(f, dict) else f for f in changed_files]
        affected_sel = find_scripts_for_changed_files(filenames, sel_index, policy)
        if affected_sel:
            log.info(
                f"[orchestrator] {sha[:8]}: index found {len(affected_sel)} affected "
                f"Selenium scripts — targeted update only"
            )

        log.info(
            f"[orchestrator] {sha[:8]}: {len(features_to_generate)} feature(s) "
            f"to generate for {_repo}"
        )
        generated = generate_all(features_to_generate, env=_env, repo=_repo)
        pre_paths = {r.path for r in pre_health.results}
        for gs in generated:
            if gs.k6_path in pre_paths:
                scripts_updated += [gs.k6_path, gs.loadrunner_path, gs.selenium_path]
            else:
                scripts_created += [gs.k6_path, gs.loadrunner_path, gs.selenium_path]
    else:
        log.info(f"[orchestrator] {sha[:8]}: no feature changes detected")

    if change_report.needs_action:
        patches = patch_all(change_report, repo=_repo)
        scripts_updated += [r.file for r in patches if r.patched]

    summary = f"Created {len(scripts_created)} scripts; updated {len(scripts_updated)} scripts."
    log.info(f"[orchestrator] {sha[:8]}: {summary}")

    entry = {
        "scripts_created": scripts_created,
        "scripts_updated": scripts_updated,
        "dependency_changes": [
            {"package": d.package, "old": d.old_version, "new": d.new_version}
            for d in change_report.dependency_changes
        ],
        "feature_changes": [
            {"method": f.method, "path": f.path} for f in change_report.feature_changes
        ],
        "summary": summary,
    }
    save_checkpoint(_repo, sha, entry)
    return {
        "change_report": change_report,
        "generated": generated,
        "patches": patches,
        "entry": entry,
    }


# ── Full repo scan (first boot) ───────────────────────────────────────────────

def scan_full_repo(repo: str = None, env: str = None) -> OrchestrationResult:
    """
    Walk every commit in repo history, accumulate unique endpoints,
    generate scripts once. Called once on first boot.
    """
    result = OrchestrationResult()
    _repo = repo or GITHUB_REPO
    _env  = env  or ENV

    if not _repo:
        result.error = "GITHUB_REPO not set"
        return result

    log.info(f"[orchestrator] Full commit-history scan — {_repo}")

    all_commits, page = [], 1
    while True:
        try:
            r = requests.get(
                f"https://api.github.com/repos/{_repo}/commits",
                headers=HEADERS, params={"per_page": 100, "page": page}, timeout=30,
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

    all_commits.reverse()
    head_sha = all_commits[-1]["sha"]
    log.info(f"[orchestrator] {len(all_commits)} commits, HEAD={head_sha[:8]}")

    from agent.code_change_detector import FeatureChange, DependencyChange
    seen_endpoints: set = set()
    all_features: List[FeatureChange] = []
    all_dep_changes: List[DependencyChange] = []

    for idx, commit in enumerate(all_commits, 1):
        sha = commit["sha"]
        changed_files = _get_commit_files(sha)
        if not changed_files:
            continue
        report = detect_changes("", changed_files)
        all_dep_changes.extend(report.dependency_changes)
        for f in report.feature_changes:
            key = (f.method, f.path)
            if key not in seen_endpoints:
                seen_endpoints.add(key)
                all_features.append(f)

    log.info(f"[orchestrator] Scan: {len(all_features)} unique endpoints")

    if not all_features:
        result.summary = "No endpoints detected — no scripts generated."
        save_checkpoint(_repo, head_sha, {
            "scripts_created": [], "scripts_updated": [],
            "dependency_changes": [], "feature_changes": [],
            "summary": result.summary,
        })
        return result

    result.generated_scripts = generate_all(all_features, env=_env, repo=_repo)

    # Build selenium index after full scan
    from agent.selenium_index import get_or_build_index
    from agent.script_generator import _script_root
    sel_root = os.path.join(_script_root(_repo, _env), "selenium")
    get_or_build_index(sel_root)
    log.info(f"[orchestrator] Selenium index built for {_repo}")
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
            f"Full scan: {len(all_commits)} commits, "
            f"{len(all_features)} endpoints, {len(created_paths)} scripts."
        ),
    })
    result.summary = (
        f"Full scan: {len(all_commits)} commits, "
        f"{len(all_features)} endpoints, {len(result.generated_scripts) * 3} scripts generated."
    )
    return result


# ── Public API ────────────────────────────────────────────────────────────────

def orchestrate(
    pr_number: Optional[int] = None,
    commit_sha: Optional[str] = None,
    repo: Optional[str] = None,
    impact_map: Optional[dict] = None,
) -> OrchestrationResult:
    """
    Full policy-driven pipeline.
    impact_map is built by the webhook handler from .perf/mappings.yaml
    and passed in — orchestrator never re-builds it (no tight coupling).
    """
    result = OrchestrationResult()
    _repo  = repo or GITHUB_REPO
    _env   = ENV
    policy = _get_policy()
    _impact_map = impact_map or {}
    result.impact_map = _impact_map

    # Determine execution profile from trigger type
    trigger = "pull_request" if pr_number else "push"
    profile = get_profile(trigger, policy)

    if not _repo:
        result.error = "GITHUB_REPO env var not set"
        return result

    # ── Step 1: PRE health check ──────────────────────────────────────────────
    log.info(f"[orchestrator] PRE health check — {_repo}")
    pre_health = run_pre_check(_repo, _env)
    result.pre_health = pre_health

    # ── Step 2: Fix pre-existing failures ────────────────────────────────────
    if pre_health.failing:
        result.scripts_fixed = _fix_failing_scripts(pre_health)

    # ── PR event ──────────────────────────────────────────────────────────────
    if pr_number:
        log.info(f"[orchestrator] PR #{pr_number} — {_repo}")
        changed_files = _get_pr_files(pr_number, _repo)
        diff_text     = _get_pr_diff(pr_number, _repo)

        if not changed_files:
            result.summary = "No changed files in PR."
        elif _should_skip_change(changed_files, policy):
            result.summary = "Skipped — docs/config only change."
            log.info(f"[orchestrator] PR #{pr_number}: skipped per routing rules")
        else:
            # Build impact map from PR files if not provided by caller
            if not _impact_map:
                filenames = [f.get("filename", "") for f in changed_files]
                _impact_map = build_impact_map(filenames, policy)
                result.impact_map = _impact_map

            change_report = detect_changes(diff_text, changed_files)
            result.change_report = change_report

            if change_report.has_feature_changes:
                features = _filter_features_by_impact(
                    change_report.feature_changes, _impact_map
                )
                result.generated_scripts = generate_all(features, env=_env, repo=_repo)

            if change_report.needs_action:
                result.patch_results = patch_all(change_report, repo=_repo)

            n_gen   = len(result.generated_scripts)
            n_patch = sum(1 for r in result.patch_results if r.patched)
            result.summary = (
                f"PR #{pr_number}: {n_gen * 3} scripts generated/updated; "
                f"{n_patch} patched. Risk: {_impact_map.get('risk_level', 'unknown')}."
            )
            result.commits_processed = [f"PR#{pr_number}"]

        # POST check + merge gate
        log.info(f"[orchestrator] POST health check — {_repo}")
        result.post_health = run_post_check(_repo, _env)
        result.merge_gate  = _evaluate_merge_gate(result.post_health, _impact_map, profile)
        log.info(f"[orchestrator] Merge gate: {result.merge_gate.status} — {result.merge_gate.reason}")
        return result

    # ── Push event ────────────────────────────────────────────────────────────
    if not commit_sha:
        result.error = "Must provide pr_number or commit_sha"
        return result

    checkpoint = get_checkpoint(_repo)
    log.info(
        f"[orchestrator] Push — {_repo} commit={commit_sha[:8]}, "
        f"checkpoint={checkpoint[:8] if checkpoint else 'none'}"
    )

    shas = get_unprocessed_commits(_repo, commit_sha, HEADERS)
    if not shas:
        result.summary = "All commits already processed."
        result.post_health = run_post_check(_repo, _env)
        result.merge_gate  = _evaluate_merge_gate(result.post_health, _impact_map, profile)
        return result

    all_generated: List[GeneratedScripts] = []
    all_patches:   List[PatchResult]      = []
    last_report:   Optional[ChangeReport] = None

    for sha in shas:
        out = _process_commit(sha, pre_health, _impact_map, repo=_repo, env=_env)
        result.commits_processed.append(sha)
        if isinstance(out, dict) and "change_report" in out:
            last_report = out["change_report"]
            all_generated.extend(out.get("generated", []))
            all_patches.extend(out.get("patches", []))

    result.change_report     = last_report
    result.generated_scripts = all_generated
    result.patch_results     = all_patches

    # POST check + merge gate
    log.info(f"[orchestrator] POST health check — {_repo}")
    result.post_health = run_post_check(_repo, _env)
    result.merge_gate  = _evaluate_merge_gate(result.post_health, _impact_map, profile)

    total_gen     = len(all_generated)
    total_patched = sum(1 for r in all_patches if r.patched)
    post_fail     = len(result.post_health.failing)

    result.summary = (
        f"Processed {len(shas)} commit(s) on {_repo}; "
        f"{total_gen * 3} scripts; {total_patched} patched; "
        f"post-check: {len(result.post_health.passing)} passing, {post_fail} failing; "
        f"gate: {result.merge_gate.status}."
    )
    log.info(f"[orchestrator] Done. {result.summary}")
    return result
