"""
test_trigger_agent.py
─────────────────────
AI Scripting Agent — generates k6, LoadRunner, and Selenium test scripts
by scanning source code for HTTP endpoints.

Modes:
  LOCAL  — set LOCAL_REPO_PATH to a directory containing source code.
           The agent scans all source files, detects endpoints, and
           generates test scripts. No GitHub needed.

  GITHUB — set GITHUB_REPOS to monitor repos via webhooks.
           POST http://localhost:5001/github-webhook

Endpoints:
  GET  /              → health check
  POST /scan          → trigger a local rescan (LOCAL mode)
  POST /github-webhook→ receive GitHub push/PR events (GITHUB mode)
  POST /run-tests     → run all generated k6 + Selenium tests
  POST /self-heal     → find and fix failing scripts via AI
  GET  /checkpoint    → show commit processing history
  POST /checkpoint/reset → reset checkpoint

Run:
  python test_trigger_agent.py          (port 5001)
  docker-compose up --build
"""

import json
import logging
import os
import threading
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, jsonify, request

from agent.commit_tracker import get_checkpoint, get_full_report, save_checkpoint
from agent.concurrency import WebhookQueue
from agent.code_change_detector import (
    FeatureChange, _parse_feature_diff, _ai_extract_features,
    _is_source_file, _is_ui_file, _ui_feature_from_file,
    SOURCE_EXTENSIONS,
)
from agent.script_generator import (
    repo_slug, generate_scripts, generate_all,
    _is_meaningful_path, _deduplicate_features, _slug,
)

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

LOCAL_REPO_PATH = os.getenv("LOCAL_REPO_PATH", "").strip()
GITHUB_REPO     = os.getenv("GITHUB_REPOS", "").split(",")[0].strip()
SLACK_WEBHOOK   = os.getenv("SLACK_WEBHOOK", "")
REPO_NAME       = os.getenv("REPO_NAME", "local-repo")


# ══════════════════════════════════════════════════════════════════════════════
# LOCAL SCAN MODE — no GitHub needed
# ══════════════════════════════════════════════════════════════════════════════

def _scan_local_directory(repo_path: str) -> list:
    """
    Walk a local directory and find all source files that may contain
    HTTP endpoints. Returns list of (filepath, content) tuples.
    """
    source_files = []
    for root, dirs, files in os.walk(repo_path):
        # Skip hidden dirs, venv, node_modules, __pycache__
        dirs[:] = [d for d in dirs if not d.startswith('.')
                   and d not in ('venv', 'env', 'node_modules', '__pycache__',
                                 '.git', 'dist', 'build', '.tox', '.mypy_cache')]
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext in SOURCE_EXTENSIONS:
                full_path = os.path.join(root, fname)
                try:
                    with open(full_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                    rel_path = os.path.relpath(full_path, repo_path)
                    source_files.append((rel_path, content))
                except Exception:
                    continue
    return source_files


def _detect_endpoints_from_source(source_files: list) -> list:
    """
    Analyse source files and extract all HTTP endpoints.
    Uses regex pattern matching + optional AI fallback.
    Returns list of FeatureChange objects.
    """
    seen = set()
    all_features = []

    for rel_path, content in source_files:
        if _is_ui_file(rel_path):
            feature = _ui_feature_from_file(rel_path, content)
            if feature:
                key = (feature.method, feature.path)
                if key not in seen:
                    seen.add(key)
                    all_features.append(feature)
            continue

        if not _is_source_file(rel_path):
            continue

        features = _parse_feature_diff(rel_path, content)
        if not features and len(content) > 50:
            features = _ai_extract_features(rel_path, content[:4000])

        for feat in features:
            key = (feat.method, feat.path)
            if key not in seen:
                seen.add(key)
                all_features.append(feat)

    return all_features


def local_scan(repo_path: str = None, repo_name: str = None) -> dict:
    """
    Full local scan pipeline:
    1. Read all source files from LOCAL_REPO_PATH
    2. Detect endpoints via regex + AI
    3. Generate k6, LoadRunner, Selenium scripts
    Returns summary dict.
    """
    _path = repo_path or LOCAL_REPO_PATH
    _name = repo_name or REPO_NAME

    if not _path or not os.path.isdir(_path):
        return {"error": f"LOCAL_REPO_PATH not set or directory not found: {_path}"}

    log.info(f"[local-scan] Scanning directory: {_path}")

    # Step 1: find source files
    source_files = _scan_local_directory(_path)
    log.info(f"[local-scan] Found {len(source_files)} source files")

    if not source_files:
        return {"status": "no_source_files", "message": "No source files found in directory"}

    # Step 2: detect endpoints
    all_features = _detect_endpoints_from_source(source_files)
    log.info(f"[local-scan] Detected {len(all_features)} endpoints")

    if not all_features:
        return {"status": "no_endpoints", "message": "No HTTP endpoints detected in source files"}

    # Step 3: filter and deduplicate
    all_features = [f for f in all_features if _is_meaningful_path(f.path)]
    all_features = _deduplicate_features(all_features)
    log.info(f"[local-scan] After dedup: {len(all_features)} unique endpoints")

    # Step 4: generate scripts
    env = os.getenv("ENV", "dev")
    generated = generate_all(all_features, env=env, repo=_name)

    created_paths = []
    for gs in generated:
        created_paths += [gs.k6_path, gs.selenium_path, gs.loadrunner_path]

    # Save checkpoint
    save_checkpoint(_name, "local-scan", {
        "scripts_created": created_paths,
        "scripts_updated": [],
        "dependency_changes": [],
        "feature_changes": [{"method": f.method, "path": f.path} for f in all_features],
        "summary": f"Local scan: {len(source_files)} files, {len(all_features)} endpoints, {len(created_paths)} scripts.",
    })

    summary = {
        "status": "ok",
        "source_files_scanned": len(source_files),
        "endpoints_detected": len(all_features),
        "scripts_generated": len(generated),
        "endpoints": [{"method": f.method, "path": f.path, "description": f.description}
                      for f in all_features],
        "scripts": created_paths,
    }

    log.info(f"[local-scan] Done: {len(all_features)} endpoints, {len(created_paths)} scripts generated")
    return summary


# ══════════════════════════════════════════════════════════════════════════════
# HTTP ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/scan", methods=["POST", "GET"])
def trigger_scan():
    """Trigger a local directory scan — generates scripts for all detected endpoints."""
    data = request.get_json(silent=True) or {}
    repo_path = data.get("repo_path", LOCAL_REPO_PATH)
    repo_name = data.get("repo_name", REPO_NAME)

    if not repo_path:
        return jsonify({"error": "No repo path. Set LOCAL_REPO_PATH env var or pass repo_path in body"}), 400

    def _run():
        result = local_scan(repo_path, repo_name)
        log.info(f"[scan] Result: {result.get('status', 'unknown')}")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"message": "Scan started — check logs for progress"}), 202


@app.route("/github-webhook", methods=["POST"])
def github_webhook():
    """Receive GitHub webhook — only active when GITHUB_REPOS is configured."""
    if not GITHUB_REPO:
        return jsonify({"error": "GitHub mode not configured. Set GITHUB_REPOS in .env or use /scan for local mode."}), 400

    payload = request.get_json(silent=True) or {}
    branch = payload.get("ref", "")
    if branch and branch != "refs/heads/main":
        return jsonify({"message": f"Skipped branch: {branch}"}), 200

    event_repo = (payload.get("repository", {}).get("full_name") or
                  payload.get("pull_request", {}).get("head", {}).get("repo", {}).get("full_name") or
                  GITHUB_REPO)

    from agent.repo_config import is_monitored_repo
    if not is_monitored_repo(event_repo):
        log.info(f"[webhook] Repo {event_repo} not in monitored list — skipping")
        return jsonify({"message": f"Repo {event_repo} not monitored"}), 200

    pr_number  = payload.get("pull_request", {}).get("number")
    commit_sha = payload.get("head_commit", {}).get("id")

    if not pr_number and not commit_sha:
        return jsonify({"message": "No PR or commit in payload"}), 200

    from agent.perf_policy import load_policy, build_impact_map
    from agent.test_orchestrator import orchestrate

    changed_files = []
    for commit in payload.get("commits", []):
        changed_files += commit.get("added", []) + commit.get("modified", [])

    impact_map = {}
    if changed_files:
        try:
            policy = load_policy()
            if policy.loaded:
                impact_map = build_impact_map(changed_files, policy)
        except Exception as e:
            log.warning(f"[webhook] Impact map failed: {e}")

    log.info(f"[webhook] Received from {event_repo} — PR: {pr_number}, "
             f"commit: {str(commit_sha)[:8] if commit_sha else None}")

    future = _queue.submit(
        orchestrate,
        pr_number=pr_number,
        commit_sha=commit_sha,
        repo=event_repo,
        impact_map=impact_map,
    )

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
            from github_reporting import comment_pr
            comment_pr(pr_number, md)
        except Exception as e:
            log.warning(f"[webhook] PR comment failed: {e}")

    # Slack notification
    if SLACK_WEBHOOK and "xxxx" not in SLACK_WEBHOOK:
        try:
            from slack_service import send_slack
            send_slack(f"Test Trigger Agent\n{result.summary}\n{md[:400]}")
        except Exception as e:
            log.warning(f"[webhook] Slack failed: {e}")

    return jsonify({
        "message":           "Test Trigger Agent executed",
        "commits_processed": result.commits_processed,
        "summary":           result.summary,
        "merge_gate": {
            "status":      result.merge_gate.status,
            "reason":      result.merge_gate.reason,
            "block_merge": result.merge_gate.block_merge,
        },
    }), 200


@app.route("/", methods=["GET"])
def health():
    mode = "local" if LOCAL_REPO_PATH else ("github" if GITHUB_REPO else "unconfigured")
    info = {"status": "Scripting Agent running", "mode": mode}

    if LOCAL_REPO_PATH:
        info["local_repo_path"] = LOCAL_REPO_PATH
        info["repo_name"] = REPO_NAME
    if GITHUB_REPO:
        from agent.repo_config import get_repos
        repos = get_repos()
        info["repos"] = repos
        repo_status = {}
        for r in repos:
            cp = get_checkpoint(r)
            repo_status[r] = cp[:8] if cp else None
        info["checkpoints"] = repo_status

    cp = get_checkpoint(REPO_NAME)
    if cp:
        info["last_scan"] = cp

    return jsonify(info)


@app.route("/checkpoint", methods=["GET"])
def checkpoint():
    repo = REPO_NAME if LOCAL_REPO_PATH else GITHUB_REPO
    return jsonify(get_full_report(repo))


@app.route("/checkpoint/reset", methods=["POST"])
def reset_checkpoint():
    repo = REPO_NAME if LOCAL_REPO_PATH else GITHUB_REPO
    from agent.commit_tracker import _checkpoint_file
    import json as _json
    path = _checkpoint_file(repo)
    if os.path.exists(path):
        data = {"repo": repo, "last_processed_sha": None, "history": []}
        with open(path, "w", encoding="utf-8") as _f:
            _json.dump(data, _f, indent=2)
    return jsonify({"message": "Checkpoint reset"})


@app.route("/self-heal", methods=["POST"])
def self_heal():
    """Find and fix all failing scripts via AI."""
    def _run():
        from agent.script_health_checker import run_pre_check
        from agent.test_orchestrator import _fix_failing_scripts
        repo = REPO_NAME if LOCAL_REPO_PATH else GITHUB_REPO
        env = os.getenv("ENV", "dev")
        report = run_pre_check(repo, env)
        if report.failing:
            fixed = _fix_failing_scripts(report)
            log.info(f"[self-heal] Fixed {len(fixed)}/{len(report.failing)}")
        else:
            log.info("[self-heal] All scripts passing")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"message": "Self-heal started — check logs for progress"}), 202


@app.route("/run-tests", methods=["POST"])
def run_tests():
    """Run all k6 + Selenium scripts. AI fixes failures automatically."""
    def _run():
        try:
            from tools.test_runner import run_all_k6, run_all_selenium, summarise
            repo = REPO_NAME if LOCAL_REPO_PATH else GITHUB_REPO
            env = os.getenv("ENV", "dev")
            k6_results  = run_all_k6(repo, env)
            sel_results = run_all_selenium(repo, env)
            summary = summarise(k6_results + sel_results)
            log.info(f"[run-tests] Done: {summary}")
        except Exception as e:
            log.error(f"[run-tests] Failed: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"message": "Test run started — check logs for progress"}), 202


if __name__ == "__main__":
    log.info("AI Scripting Agent started on port 5001")

    # ── Startup: auto-scan based on mode ─────────────────────────────────────
    def _startup():
        import time
        time.sleep(3)

        if LOCAL_REPO_PATH:
            # LOCAL MODE — scan directory for endpoints
            log.info(f"[startup] LOCAL MODE — scanning: {LOCAL_REPO_PATH}")
            if not os.path.isdir(LOCAL_REPO_PATH):
                log.error(f"[startup] Directory not found: {LOCAL_REPO_PATH}")
                return
            result = local_scan()
            log.info(f"[startup] Scan complete: {result.get('endpoints_detected', 0)} endpoints, "
                     f"{result.get('scripts_generated', 0)} scripts generated")
            return

        if GITHUB_REPO:
            # GITHUB MODE — scan commit history
            log.info(f"[startup] GITHUB MODE — scanning: {GITHUB_REPO}")
            _startup_github_scan()
            return

        log.warning("[startup] No mode configured. Set LOCAL_REPO_PATH for local scanning "
                    "or GITHUB_REPOS for GitHub monitoring.")

    def _startup_github_scan():
        """Original GitHub-based startup scan."""
        import time
        import requests
        from agent.repo_config import get_repos

        repos = get_repos()
        if not repos:
            return

        time.sleep(2)
        token   = os.getenv("GITHUB_TOKEN", "")
        headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json"}

        for repo in repos:
            log.info(f"[startup] Scanning repo: {repo}")
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
                continue

            if not all_commits:
                continue

            all_commits.reverse()
            head_sha = all_commits[-1]["sha"]

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

            log.info(f"[startup] {repo}: {len(all_features)} endpoints from {len(all_commits)} commits")

            if not all_features:
                save_checkpoint(repo, head_sha, {
                    "scripts_created": [], "scripts_updated": [],
                    "dependency_changes": [], "feature_changes": [],
                    "summary": "Startup: no endpoints detected.",
                })
                continue

            all_features = [f for f in all_features if _is_meaningful_path(f.path)]
            all_features = _deduplicate_features(all_features)

            env = os.getenv("ENV", "dev")
            generated = generate_all(all_features, env=env, repo=repo)
            created = []
            for gs in generated:
                created += [gs.k6_path, gs.selenium_path, gs.loadrunner_path]

            save_checkpoint(repo, head_sha, {
                "scripts_created": created,
                "scripts_updated": [],
                "dependency_changes": [],
                "feature_changes": [{"method": f.method, "path": f.path} for f in all_features],
                "summary": f"Startup: {len(all_features)} endpoints, {len(created)} scripts.",
            })
            log.info(f"[startup] {repo}: generated {len(created)} scripts")

    threading.Thread(target=_startup, daemon=True).start()

    # ── Scheduled self-heal loop ──────────────────────────────────────────────
    def _self_heal_loop():
        import time
        interval = int(os.getenv("SELF_HEAL_INTERVAL_MINUTES", "60")) * 60
        time.sleep(30)
        while True:
            try:
                from agent.script_health_checker import run_pre_check
                from agent.test_orchestrator import _fix_failing_scripts
                repo = REPO_NAME if LOCAL_REPO_PATH else GITHUB_REPO
                if not repo:
                    time.sleep(interval)
                    continue
                env = os.getenv("ENV", "dev")
                log.info(f"[self-heal] Health check for {repo}...")
                report = run_pre_check(repo, env)
                if report.failing:
                    log.info(f"[self-heal] {len(report.failing)} failing — fixing...")
                    fixed = _fix_failing_scripts(report)
                    log.info(f"[self-heal] Fixed {len(fixed)}/{len(report.failing)}")
                else:
                    log.info(f"[self-heal] All {len(report.passing)} scripts passing")
            except Exception as e:
                log.error(f"[self-heal] Error: {e}")
            time.sleep(interval)

    threading.Thread(target=_self_heal_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5001, debug=False)
