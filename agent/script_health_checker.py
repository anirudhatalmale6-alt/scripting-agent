"""
agent/script_health_checker.py
───────────────────────────────
Runs ALL existing k6 scripts for a repo/env BEFORE any changes are made.

This gives the orchestrator a baseline:
  - PASSING  → script works today; update it for new changes, then re-verify
  - FAILING  → script is already broken; agent must fix it regardless of new changes
  - SKIPPED  → k6 not installed or no target reachable

After changes are applied the orchestrator calls run_post_check() to confirm
everything passes. The delta (before vs after) is included in the PR comment.

Public API
──────────
  run_pre_check(repo, env)  -> HealthReport
  run_post_check(repo, env) -> HealthReport
"""

import glob
import os
import subprocess
import socket
from dataclasses import dataclass, field
from typing import List, Optional
from datetime import datetime, timezone


@dataclass
class ScriptResult:
    path: str
    status: str          # "passing" | "failing" | "skipped"
    error: str = ""
    duration_s: float = 0.0


@dataclass
class HealthReport:
    phase: str           # "pre" | "post"
    repo: str
    env: str
    results: List[ScriptResult] = field(default_factory=list)
    checked_at: str = ""

    @property
    def passing(self) -> List[ScriptResult]:
        return [r for r in self.results if r.status == "passing"]

    @property
    def failing(self) -> List[ScriptResult]:
        return [r for r in self.results if r.status == "failing"]

    @property
    def skipped(self) -> List[ScriptResult]:
        return [r for r in self.results if r.status == "skipped"]

    def to_markdown(self) -> str:
        lines = [f"#### {self.phase.upper()} health check — `{self.repo}` / `{self.env}`\n"]
        lines.append(f"✅ Passing: {len(self.passing)}  "
                     f"❌ Failing: {len(self.failing)}  "
                     f"⏭ Skipped: {len(self.skipped)}\n")
        for r in self.failing:
            lines.append(f"- ❌ `{os.path.basename(r.path)}` — {r.error[:120]}")
        for r in self.passing:
            lines.append(f"- ✅ `{os.path.basename(r.path)}` ({r.duration_s:.1f}s)")
        return "\n".join(lines)

    def summary_line(self) -> str:
        return (f"{self.phase.upper()}: {len(self.passing)} passing, "
                f"{len(self.failing)} failing, {len(self.skipped)} skipped")


# ── Target URL resolution ─────────────────────────────────────────────────────

def _resolve_target() -> Optional[str]:
    """Return a reachable target URL or None if nothing is up."""
    sfcc = os.getenv("SFCC_SITE_URL", "")
    placeholders = ("your-sfcc-site", "your-app", "example.com", "")
    if sfcc and not any(p in sfcc for p in placeholders):
        return sfcc
    for host in ("localhost", "mock-app"):
        try:
            socket.create_connection((host, 8080), timeout=1).close()
            return f"http://{host}:8080"
        except OSError:
            continue
    return None


# ── Single script runner ──────────────────────────────────────────────────────

def _run_one(script_path: str, target: str) -> ScriptResult:
    """Run a single k6 script with 1 VU / 5s. Returns ScriptResult."""
    import time
    env = os.environ.copy()
    env["SFCC_SITE_URL"] = target

    t0 = time.time()
    try:
        proc = subprocess.run(
            ["k6", "run", "--vus", "1", "--duration", "5s",
             "--no-usage-report", script_path],
            env=env, capture_output=True, text=True, timeout=60,
        )
        elapsed = time.time() - t0
        if proc.returncode == 0:
            return ScriptResult(path=script_path, status="passing", duration_s=elapsed)
        error = (proc.stderr + "\n" + proc.stdout[-500:]).strip()
        return ScriptResult(path=script_path, status="failing",
                            error=error[:300], duration_s=elapsed)
    except FileNotFoundError:
        return ScriptResult(path=script_path, status="skipped",
                            error="k6 not installed")
    except subprocess.TimeoutExpired:
        return ScriptResult(path=script_path, status="failing",
                            error="timed out after 60s")


# ── Script discovery ──────────────────────────────────────────────────────────

def _find_k6_scripts(repo: str, env: str) -> List[str]:
    """Find all k6 .js scripts for this repo/env."""
    from agent.script_generator import repo_slug
    rslug = repo_slug(repo) if repo else "default"
    # New structure: scripts/<repo>/<env>/k6/*.js
    new_path = os.path.join("scripts", rslug, env, "k6", "*.js")
    # Legacy flat structure: scripts/<env>/*.js  (hand-written baseline scripts)
    legacy_path = os.path.join("scripts", env, "*.js")
    scripts = glob.glob(new_path) + glob.glob(legacy_path)
    return sorted(set(scripts))


# ── Public API ────────────────────────────────────────────────────────────────

def run_health_check(repo: str, env: str, phase: str) -> HealthReport:
    """
    Run all k6 scripts for repo/env and return a HealthReport.

    phase: "pre"  → called before any changes
           "post" → called after changes applied
    """
    report = HealthReport(
        phase=phase,
        repo=repo,
        env=env,
        checked_at=datetime.now(timezone.utc).isoformat(),
    )

    scripts = _find_k6_scripts(repo, env)
    if not scripts:
        print(f"[health] No k6 scripts found for {repo}/{env} — skipping {phase} check")
        return report

    target = _resolve_target()
    if not target:
        print(f"[health] No target reachable — marking all {len(scripts)} scripts as skipped")
        report.results = [ScriptResult(path=s, status="skipped",
                                       error="no target reachable") for s in scripts]
        return report

    print(f"[health] {phase.upper()} check — {len(scripts)} scripts against {target}")
    for script in scripts:
        result = _run_one(script, target)
        icon = "✅" if result.status == "passing" else ("❌" if result.status == "failing" else "⏭")
        print(f"[health]   {icon} {os.path.basename(script)} "
              f"({result.status}) {result.duration_s:.1f}s")
        if result.status == "failing":
            print(f"[health]      {result.error[:120]}")
        report.results.append(result)

    print(f"[health] {report.summary_line()}")
    return report


def run_pre_check(repo: str, env: str) -> HealthReport:
    return run_health_check(repo, env, "pre")


def run_post_check(repo: str, env: str) -> HealthReport:
    return run_health_check(repo, env, "post")
