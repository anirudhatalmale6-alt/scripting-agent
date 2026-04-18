"""
test_trigger_agent.py
─────────────────────
Test Trigger Agent — decoupled from RCA.

Responsibilities:
  - Receive GitHub webhook (push / PR)
  - Detect code changes (Scenario A: dep upgrade, Scenario B: new feature)
  - Generate new k6 / LoadRunner / Selenium scripts
  - Patch existing scripts
  - Track processed commits in .commit_checkpoint.json
  - Post script-change summary to PR comment + Slack

Run:
  python test_trigger_agent.py          (port 5001)
  docker-compose up test-trigger-agent
"""

import json
import logging
import os
import threading
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, jsonify, request

from agent.commit_tracker import get_checkpoint, get_full_report
from agent.concurrency import WebhookQueue
from agent.test_orchestrator import orchestrate
from agent.perf_policy import load_policy, build_impact_map, should_skip_file
from github_reporting import comment_pr
from slack_service import send_slack

load_dotenv()

from agent.log_filter import setup_log_filter
setup_log_filter()

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
_log_file = os.path.join("logs", f"test_trigger_{datetime.now().strftime('%Y%m%d')}.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("test_trigger")

# ── App + queue ───────────────────────────────────────────────────────────────
app = Flask(__name__)
_queue = WebhookQueue()

GITHUB_REPO  = os.getenv("GITHUB_REPOS", "").split(",")[0].strip()  # primary repo for backward compat
SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK", "")


# ── Webhook ───────────────────────────────────────────────────────────────────

@app.route("/github-webhook", methods=["POST"])
def github_webhook():
    payload = request.json or {}

    branch = payload.get("ref", "")
    if branch and branch != "refs/heads/main":
        return jsonify({"message": f"Skipped branch: {branch}"}), 200

    # Identify which repo this event is from
    event_repo = (payload.get("repository", {}).get("full_name") or
                  payload.get("pull_request", {}).get("head", {}).get("repo", {}).get("full_name") or
                  GITHUB_REPO)

    # Check if this repo is monitored
    from agent.repo_config import is_monitored_repo
    if not is_monitored_repo(event_repo):
        log.info(f"[webhook] Repo {event_repo} not in monitored list — skipping")
        return jsonify({"message": f"Repo {event_repo} not monitored"}), 200

    pr_number  = payload.get("pull_request", {}).get("number")
    commit_sha = payload.get("head_commit", {}).get("id")

    if not pr_number and not commit_sha:
        return jsonify({"message": "No PR or commit in payload"}), 200

    # ── Policy-driven impact map ──────────────────────────────────────────────
    changed_files = []
    for commit in payload.get("commits", []):
        changed_files += commit.get("added", []) + commit.get("modified", [])
    if not changed_files and "pull_request" in payload:
        changed_files = [
            f.get("filename", "") for f in
            payload.get("pull_request", {}).get("changed_files_list", [])
        ]

    impact_map = {}
    if changed_files:
        try:
            policy = load_policy()
            if policy.loaded:
                impact_map = build_impact_map(changed_files, policy)
                log.info(
                    f"[webhook] Impact: domains={impact_map.get('changed_domains')}, "
                    f"risk={impact_map.get('risk_level')}, "
                    f"k6={impact_map.get('test_updates_needed', {}).get('k6')}"
                )
                # Skip if all files are docs/config with no test impact
                if not impact_map.get("changed_domains") and not impact_map.get("test_updates_needed", {}).get("k6"):
                    all_skipped = all(should_skip_file(f, policy) for f in changed_files if f)
                    if all_skipped:
                        log.info("[webhook] All changed files skipped per routing rules")
                        return jsonify({"message": "Skipped — docs/config only change"}), 200
        except Exception as e:
            log.warning(f"[webhook] Impact map failed: {e}")

    log.info(f"[webhook] Received from {event_repo} — PR: {pr_number}, "
             f"commit: {str(commit_sha)[:8] if commit_sha else None}")

    future = _queue.submit(
        orchestrate,
        pr_number=pr_number,
        commit_sha=commit_sha,
        repo=event_repo,
        impact_map=impact_map,   # pass policy-driven routing into orchestrator
    )

    try:
        result = future.result(timeout=600)
    except Exception as e:
        log.error(f"[webhook] Orchestration failed: {e}")
        return jsonify({"error": str(e)}), 500

    md = result.to_markdown()
    log.info(f"[webhook] {result.summary}")

    # Run sandbox tests if scripts were generated/updated
    sandbox_summary = {}
    if result.generated_scripts:
        try:
            from tools.test_runner import run_all_k6, run_all_selenium, summarise
            env = os.getenv("ENV", "dev")
            k6_results  = run_all_k6(event_repo, env)
            sel_results = run_all_selenium(event_repo, env)
            sandbox_summary = summarise(k6_results + sel_results)
            log.info(f"[webhook] Sandbox: {sandbox_summary['passed']} passed, "
                     f"{sandbox_summary['needs_manual']} need manual review")
        except Exception as e:
            log.warning(f"[webhook] Sandbox test failed: {e}")

    # Post PR comment
    if pr_number and md:
        try:
            comment_pr(pr_number, md)
        except Exception as e:
            log.warning(f"[webhook] PR comment failed: {e}")

    # Slack notification
    if SLACK_WEBHOOK and "xxxx" not in SLACK_WEBHOOK:
        try:
            pre   = result.pre_health
            post  = result.post_health
            gate  = result.merge_gate
            gate_icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}.get(gate.status, "ℹ️")
            health_line = ""
            if pre and post:
                health_line = (
                    f"\nPRE:  {len(pre.passing)} passing, {len(pre.failing)} failing"
                    f"\nPOST: {len(post.passing)} passing, {len(post.failing)} failing"
                    + (" ✅ All clear" if len(post.failing) == 0 else " ⚠️ Failures remain")
                )
            send_slack(
                f"🧪 *Test Trigger Agent*\n{result.summary}"
                f"\n{gate_icon} Merge gate: *{gate.status.upper()}* — {gate.reason}"
                f"{health_line}\n\n{md[:400]}"
            )
        except Exception as e:
            log.warning(f"[webhook] Slack failed: {e}")

    return jsonify({
        "message":          "Test Trigger Agent executed",
        "commits_processed": result.commits_processed,
        "summary":          result.summary,
        "impact_map":       impact_map,
        "merge_gate":       {
            "status":      result.merge_gate.status,
            "reason":      result.merge_gate.reason,
            "block_merge": result.merge_gate.block_merge,
            "risk_level":  result.merge_gate.risk_level,
        },
        "report": md,
    }), 200


@app.route("/", methods=["GET"])
def health():
    from agent.repo_config import get_repos
    repos = get_repos()
    repo_status = {}
    for r in repos:
        cp = get_checkpoint(r)
        repo_status[r] = cp[:8] if cp else None
    return jsonify({
        "status": "Test Trigger Agent running",
        "repos": repos,
        "checkpoints": repo_status,
        "queue_depth": _queue.queue_depth,
    })


@app.route("/checkpoint", methods=["GET"])
def checkpoint():
    """Return full commit processing history."""
    return jsonify(get_full_report(GITHUB_REPO))


@app.route("/checkpoint/reset", methods=["POST"])
def reset_checkpoint():
    """Reset checkpoint — next push will process from scratch."""
    from agent.commit_tracker import _get_checkpoint_file, _load
    import json as _json
    checkpoint_file = _get_checkpoint_file()
    if os.path.exists(checkpoint_file):
        data = _load()
        if GITHUB_REPO in data:
            data[GITHUB_REPO]["last_processed_sha"] = None
            with open(checkpoint_file, "w", encoding="utf-8") as _f:
                _json.dump(data, _f, indent=2)
    return jsonify({"message": "Checkpoint reset"})


@app.route("/self-heal", methods=["POST"])
def self_heal():
    """Manually trigger a self-heal run — finds and fixes all failing scripts."""
    def _run():
        from agent.script_health_checker import run_pre_check
        from agent.test_orchestrator import _fix_failing_scripts
        env = os.getenv("ENV", "dev")
        report = run_pre_check(GITHUB_REPO, env)
        if report.failing:
            fixed = _fix_failing_scripts(report)
            log.info(f"[self-heal] Manual run: fixed {len(fixed)}/{len(report.failing)}")
        else:
            log.info("[self-heal] Manual run: all scripts passing")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"message": "Self-heal started — check logs for progress"}), 202


@app.route("/run-tests", methods=["POST"])
def run_tests():
    """Run all k6 + Selenium scripts, AI fixes failures, returns summary."""
    def _run():
        from agent.mcp_client import call_tool
        result = call_tool("run_tests", {
            "repo": GITHUB_REPO,
            "env":  os.getenv("ENV", "dev"),
        })
        summary = result.get("summary", {})
        log.info(f"[run-tests] Done — {summary}")
        if SLACK_WEBHOOK and "xxxx" not in SLACK_WEBHOOK:
            try:
                send_slack(
                    f"🧪 *Test Run Complete*\n"
                    f"✅ Passed: {summary.get('passed', 0)} | "
                    f"⚠️ Manual: {summary.get('needs_manual', 0)} | "
                    f"🔧 AI fixes: {summary.get('ai_fixes_used', 0)}\n"
                    + ("\n".join(f"• `{s}`" for s in summary.get("manual_review", [])))
                )
            except Exception:
                pass
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"message": "Test run started — check logs for progress"}), 202


if __name__ == "__main__":
    log.info("Test Trigger Agent started on port 5001")

    # ── Startup: generate scripts if none exist AND no checkpoint for this repo ─
    def _startup_generate():
        import time
        from agent.repo_config import get_repos

        repos = get_repos()
        if not repos:
            log.warning("[startup] No repos configured — set GITHUB_REPOS=owner/repo1,owner/repo2 in .env")
            return

        time.sleep(5)  # wait for network

        if len(repos) == 1:
            # Single repo — run directly
            log.info(f"[startup] Processing repo: {repos[0]}")
            _startup_scan_repo(repos[0])
        else:
            # Multiple repos — run in parallel threads
            log.info(f"[startup] Processing {len(repos)} repos in parallel: {repos}")
            threads = []
            for repo in repos:
                t = threading.Thread(
                    target=_startup_scan_repo,
                    args=(repo,),
                    name=f"startup-{repo}",
                    daemon=True,
                )
                threads.append(t)
                t.start()
            # Wait for all to complete
            for t in threads:
                t.join()
            log.info(f"[startup] All {len(repos)} repos processed")

    def _startup_scan_repo(repo: str):
        import requests
        from agent.script_generator import repo_slug, generate_scripts, _slug
        from agent.commit_tracker import get_checkpoint, save_checkpoint
        from agent.code_change_detector import _parse_feature_diff, _ai_extract_features
        import glob

        env       = os.getenv("ENV", "dev")
        rslug     = repo_slug(repo)
        k6_dir    = os.path.join("scripts", rslug, env, "k6")
        lr_dir    = os.path.join("scripts", rslug, env, "loadrunner")
        sel_dir   = os.path.join("scripts", rslug, env, "selenium")
        token     = os.getenv("GITHUB_TOKEN", "")
        headers   = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}

        # ── Step 1: discover all endpoints in repo via commit history ─────────
        log.info(f"[startup] Scanning commit history for endpoints in {repo}...")
        try:
            all_commits, page = [], 1
            while True:
                r = requests.get(f"https://api.github.com/repos/{repo}/commits",
                                 headers=headers, params={"per_page": 100, "page": page}, timeout=30)
                if r.status_code != 200 or not r.json():
                    break
                batch = r.json()
                all_commits.extend(batch)
                if len(batch) < 100:
                    break
                page += 1
        except Exception as e:
            log.error(f"[startup] Could not fetch commits for {repo}: {e}")
            return

        if not all_commits:
            log.warning(f"[startup] No commits found for {repo}")
            return

        all_commits.reverse()  # oldest first
        head_sha = all_commits[-1]["sha"]

        # Collect all unique endpoints from commit history
        seen, all_features = set(), []
        for commit in all_commits:
            sha = commit["sha"]
            try:
                r = requests.get(f"https://api.github.com/repos/{repo}/commits/{sha}",
                                 headers=headers, timeout=15)
                if r.status_code != 200:
                    continue
                for f in r.json().get("files", []):
                    patch = f.get("patch", "")
                    fname = f.get("filename", "")
                    features = _parse_feature_diff(fname, patch)
                    if not features and patch:
                        features = _ai_extract_features(fname, patch[:3000])
                    for feat in features:
                        key = (feat.method, feat.path)
                        if key not in seen:
                            seen.add(key)
                            all_features.append(feat)
            except Exception:
                continue

        log.info(f"[startup] Found {len(all_features)} unique endpoints across "
                 f"{len(all_commits)} commits")

        if not all_features:
            log.warning(f"[startup] No endpoints detected for {repo}")
            save_checkpoint(repo, head_sha, {
                "scripts_created": [], "scripts_updated": [],
                "dependency_changes": [], "feature_changes": [],
                "summary": "Startup: no endpoints detected.",
            })
            return

        # Deduplicate — same resource+method keeps most specific path only
        from agent.script_generator import _is_meaningful_path, _deduplicate_features
        all_features = [f for f in all_features if _is_meaningful_path(f.path)]
        all_features = _deduplicate_features(all_features)
        log.info(f"[startup] After dedup: {len(all_features)} unique endpoints to generate")

        # ── Step 2: check which scripts are missing on disk ─────────────────
        # LR is one journey file, k6 and selenium are per-endpoint
        lr_journey = os.path.join(lr_dir, "full_journey_lr_test.c")
        missing = []
        for feat in all_features:
            slug     = _slug(feat.path)
            k6_path  = os.path.join(k6_dir, f"{slug}_perf_test.js")
            sel_test = os.path.join(sel_dir, "src", "test", "java", "com",
                                    "ecommerce", "tests",
                                    f"{feat.path.strip('/').split('/')[0].capitalize()}Test.java")
            if not os.path.exists(k6_path) or not os.path.exists(sel_test):
                missing.append(feat)

        # Also check journey LR script (only if LR enabled)
        lr_missing = (os.getenv("ENABLE_LOADRUNNER", "true").lower() == "true"
                      and not os.path.exists(lr_journey))

        existing_count = len(all_features) - len(missing)
        log.info(f"[startup] {existing_count}/{len(all_features)} endpoint scripts exist, "
                 f"{len(missing)} need generating, LR journey: {'exists' if not lr_missing else 'missing'}")

        if not missing and not lr_missing:
            log.info("[startup] All scripts present — bootstrapping checkpoint")
            # Also register any manually added scripts not in commit history
            all_existing = (
                glob.glob(os.path.join(k6_dir,  "**/*.js"),  recursive=True) +
                glob.glob(os.path.join(lr_dir,  "**/*.c"),   recursive=True) +
                glob.glob(os.path.join(sel_dir, "**/*.py"),  recursive=True)
            )
            save_checkpoint(repo, head_sha, {
                "scripts_created": all_existing,
                "scripts_updated": [],
                "dependency_changes": [],
                "feature_changes": [{"method": f.method, "path": f.path}
                                     for f in all_features],
                "summary": (f"Startup: {len(all_features)} endpoints from commits, "
                            f"{len(all_existing)} total scripts on disk registered."),
            })
            log.info(f"[startup] Registered {len(all_existing)} scripts "
                     f"({len(all_features)} from commits, rest manually added)")
            return

        # ── Step 3: generate only missing scripts ─────────────────────────────
        log.info(f"[startup] Generating {len(missing)} missing endpoint scripts"
                 + (" + LR journey" if lr_missing else "") + "...")
        created = []
        for feat in missing:
            try:
                gs = generate_scripts(feat, env=env, repo=repo)
                created += [gs.k6_path, gs.selenium_path]
                log.info(f"[startup] Generated: {feat.method} {feat.path}")
            except Exception as e:
                log.error(f"[startup] Failed to generate {feat.path}: {e}")

        # Generate LR journey if missing and flag enabled
        if lr_missing and all_features and os.getenv("ENABLE_LOADRUNNER", "true").lower() == "true":
            try:
                from agent.script_generator import _generate_lr_journey
                _generate_lr_journey(all_features, env=env, repo=repo)
                created.append(lr_journey)
                log.info(f"[startup] Generated LR journey: {lr_journey}")
            except Exception as e:
                log.error(f"[startup] Failed to generate LR journey: {e}")
        # Also pick up any manually added scripts beyond what commits detected
        all_existing = (
            glob.glob(os.path.join(k6_dir,  "**/*.js"),  recursive=True) +
            glob.glob(os.path.join(lr_dir,  "**/*.c"),   recursive=True) +
            glob.glob(os.path.join(sel_dir, "**/*.py"),  recursive=True)
        )

        save_checkpoint(repo, head_sha, {
            "scripts_created": list(set(created + all_existing)),
            "scripts_updated": [],
            "dependency_changes": [],
            "feature_changes": [{"method": f.method, "path": f.path}
                                 for f in all_features],
            "summary": (f"Startup gap-fill: {existing_count} existed, "
                        f"{len(missing)} generated, "
                        f"{len(all_existing)} total on disk registered."),
        })
        log.info(f"[startup] Done — {len(created)} generated, "
                 f"{len(all_existing)} total scripts registered")

        # Auto-run sandbox tests after generation if any scripts were created
        if created and os.getenv("AUTO_TEST_ON_STARTUP", "true").lower() == "true":
            log.info("[startup] Running sandbox tests on generated scripts...")
            try:
                from tools.test_runner import run_all_k6, run_all_selenium, summarise
                k6_results  = run_all_k6(repo, env)
                sel_results = run_all_selenium(repo, env)
                summary = summarise(k6_results + sel_results)
                log.info(f"[startup] Sandbox test results: "
                         f"{summary['passed']} passed, "
                         f"{summary['needs_manual']} need manual review, "
                         f"{summary['ai_fixes_used']} AI fixes used")
            except Exception as e:
                log.error(f"[startup] Sandbox test run failed: {e}")

    threading.Thread(target=_startup_generate, daemon=True).start()

    # ── Scheduled self-heal loop ──────────────────────────────────────────────
    # Runs every SELF_HEAL_INTERVAL_MINUTES (default 60).
    # Finds all failing k6 scripts and asks GPT to fix them — no commit needed.
    def _self_heal_loop():
        import time
        interval = int(os.getenv("SELF_HEAL_INTERVAL_MINUTES", "60")) * 60
        time.sleep(30)
        while True:
            try:
                from agent.script_health_checker import run_pre_check
                from agent.test_orchestrator import _fix_failing_scripts
                from agent.repo_config import get_repos
                env = os.getenv("ENV", "dev")
                for repo in get_repos():
                    log.info(f"[self-heal] Health check for {repo}...")
                    report = run_pre_check(repo, env)
                    if report.failing:
                        log.info(f"[self-heal] {len(report.failing)} failing in {repo} — fixing...")
                        fixed = _fix_failing_scripts(report)
                        log.info(f"[self-heal] Fixed {len(fixed)}/{len(report.failing)} in {repo}")
                    else:
                        log.info(f"[self-heal] {repo}: all {len(report.passing)} scripts passing")
            except Exception as e:
                log.error(f"[self-heal] Error: {e}")
            time.sleep(interval)

    threading.Thread(target=_self_heal_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5001, debug=False)

