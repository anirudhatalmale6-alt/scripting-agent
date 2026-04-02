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
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, jsonify, request

from agent.commit_tracker import get_checkpoint, get_full_report
from agent.concurrency import WebhookQueue
from agent.test_orchestrator import orchestrate
from github_reporting import comment_pr
from slack_service import send_slack

load_dotenv()

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


if __name__ == "__main__":
    log.info("Test Trigger Agent started on port 5001")
    app.run(host="0.0.0.0", port=5001, debug=False)
