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

GITHUB_REPO  = os.getenv("GITHUB_REPO", "")
SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK", "")


# ── Webhook ───────────────────────────────────────────────────────────────────

@app.route("/github-webhook", methods=["POST"])
def github_webhook():
    payload = request.json or {}

    branch = payload.get("ref", "")
    if branch and branch != "refs/heads/main":
        return jsonify({"message": f"Skipped branch: {branch}"}), 200

    pr_number  = payload.get("pull_request", {}).get("number")
    commit_sha = payload.get("head_commit", {}).get("id")

    if not pr_number and not commit_sha:
        return jsonify({"message": "No PR or commit in payload"}), 200

    log.info(
        f"[webhook] Received — PR: {pr_number}, commit: {str(commit_sha)[:8] if commit_sha else None}, "
        f"queue depth: {_queue.queue_depth}"
    )

    future = _queue.submit(orchestrate, pr_number=pr_number, commit_sha=commit_sha)

    try:
        result = future.result(timeout=600)
    except Exception as e:
        log.error(f"[webhook] Orchestration failed: {e}")
        return jsonify({"error": str(e)}), 500

    md = result.to_markdown()
    log.info(f"[webhook] {result.summary}")

    # Post PR comment
    if pr_number and md:
        try:
            comment_pr(pr_number, md)
        except Exception as e:
            log.warning(f"[webhook] PR comment failed: {e}")

    # Slack notification
    if SLACK_WEBHOOK and "xxxx" not in SLACK_WEBHOOK:
        try:
            pre  = result.pre_health
            post = result.post_health
            health_line = ""
            if pre and post:
                health_line = (
                    f"\nPRE:  {len(pre.passing)} passing, {len(pre.failing)} failing"
                    f"\nPOST: {len(post.passing)} passing, {len(post.failing)} failing"
                    + (" ✅ All clear" if len(post.failing) == 0 else " ⚠️ Failures remain")
                )
            send_slack(f"🧪 *Test Trigger Agent*\n{result.summary}{health_line}\n\n{md[:400]}")
        except Exception as e:
            log.warning(f"[webhook] Slack failed: {e}")

    return jsonify({
        "message": "Test Trigger Agent executed",
        "commits_processed": result.commits_processed,
        "summary": result.summary,
        "report": md,
    }), 200


@app.route("/", methods=["GET"])
def health():
    checkpoint = get_checkpoint(GITHUB_REPO)
    return jsonify({
        "status": "Test Trigger Agent running",
        "repo": GITHUB_REPO,
        "last_checkpoint": checkpoint[:8] if checkpoint else None,
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
        import requests
        from agent.script_generator import repo_slug, generate_scripts, _slug
        from agent.commit_tracker import get_checkpoint, save_checkpoint
        from agent.code_change_detector import _parse_feature_diff, _ai_extract_features
        import glob, base64

        if not GITHUB_REPO:
            log.warning("[startup] GITHUB_REPO not set — skipping")
            return

        time.sleep(5)  # wait for network

        env       = os.getenv("ENV", "dev")
        rslug     = repo_slug(GITHUB_REPO)
        k6_dir    = os.path.join("scripts", rslug, env, "k6")
        lr_dir    = os.path.join("scripts", rslug, env, "loadrunner")
        sel_dir   = os.path.join("scripts", rslug, env, "selenium")
        token     = os.getenv("GITHUB_TOKEN", "")
        headers   = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}

        # ── Step 1: discover all endpoints in repo via commit history ─────────
        log.info(f"[startup] Scanning commit history for endpoints in {GITHUB_REPO}...")
        try:
            all_commits, page = [], 1
            while True:
                r = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/commits",
                                 headers=headers, params={"per_page": 100, "page": page}, timeout=30)
                if r.status_code != 200 or not r.json():
                    break
                batch = r.json()
                all_commits.extend(batch)
                if len(batch) < 100:
                    break
                page += 1
        except Exception as e:
            log.error(f"[startup] Could not fetch commits: {e}")
            return

        if not all_commits:
            log.warning("[startup] No commits found")
            return

        all_commits.reverse()  # oldest first
        head_sha = all_commits[-1]["sha"]

        # Collect all unique endpoints from commit history
        seen, all_features = set(), []
        for commit in all_commits:
            sha = commit["sha"]
            try:
                r = requests.get(f"https://api.github.com/repos/{GITHUB_REPO}/commits/{sha}",
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
            log.warning("[startup] No endpoints detected — saving checkpoint and exiting")
            save_checkpoint(GITHUB_REPO, head_sha, {
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

        # Also check journey LR script
        lr_missing = not os.path.exists(lr_journey)

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
            save_checkpoint(GITHUB_REPO, head_sha, {
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
                gs = generate_scripts(feat, env=env, repo=GITHUB_REPO)
                created += [gs.k6_path, gs.selenium_path]
                log.info(f"[startup] Generated: {feat.method} {feat.path}")
            except Exception as e:
                log.error(f"[startup] Failed to generate {feat.path}: {e}")

        # Generate LR journey if missing (covers all endpoints in one script)
        if lr_missing and all_features:
            try:
                from agent.script_generator import _generate_lr_journey
                _generate_lr_journey(all_features, env=env, repo=GITHUB_REPO)
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

        save_checkpoint(GITHUB_REPO, head_sha, {
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

    threading.Thread(target=_startup_generate, daemon=True).start()

    # ── Scheduled self-heal loop ──────────────────────────────────────────────
    # Runs every SELF_HEAL_INTERVAL_MINUTES (default 60).
    # Finds all failing k6 scripts and asks GPT to fix them — no commit needed.
    def _self_heal_loop():
        import time
        interval = int(os.getenv("SELF_HEAL_INTERVAL_MINUTES", "60")) * 60
        # Wait for startup scan to finish first
        time.sleep(30)
        while True:
            try:
                log.info("[self-heal] Starting scheduled health check...")
                from agent.script_health_checker import run_pre_check
                from agent.test_orchestrator import _fix_failing_scripts
                env = os.getenv("ENV", "dev")
                report = run_pre_check(GITHUB_REPO, env)
                if report.failing:
                    log.info(f"[self-heal] {len(report.failing)} failing scripts — attempting AI fix...")
                    fixed = _fix_failing_scripts(report)
                    log.info(f"[self-heal] Fixed {len(fixed)}/{len(report.failing)} scripts")
                    if SLACK_WEBHOOK and "xxxx" not in SLACK_WEBHOOK:
                        try:
                            send_slack(
                                f"🔧 *Self-Heal Report*\n"
                                f"Found {len(report.failing)} failing scripts.\n"
                                f"Auto-fixed: {len(fixed)}\n"
                                f"Still failing: {len(report.failing) - len(fixed)}\n"
                                + ("\n".join(f"• `{s}`" for s in fixed) if fixed else "")
                            )
                        except Exception:
                            pass
                else:
                    log.info(f"[self-heal] All {len(report.passing)} scripts passing — nothing to fix")
            except Exception as e:
                log.error(f"[self-heal] Error: {e}")
            time.sleep(interval)

    threading.Thread(target=_self_heal_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5001, debug=False)
