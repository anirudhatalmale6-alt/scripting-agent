"""
tests/live_test.py
──────────────────
Live end-to-end test using the running Docker stack + real GitHub repo.

Flow:
  1. Verify Docker services are up (RCA agent, Prometheus, Grafana, target app)
  2. Fetch latest commit from real GitHub repo
  3. Detect changes from the commit diff
  4. Fire webhook to RCA agent → triggers real k6 run against target app
  5. Poll for report written to disk
  6. Verify k6 actually ran scripts (not fake)
  7. Verify Prometheus received real k6 metrics

Run with:
    python tests/live_test.py
"""

import os
import sys
import time
import json
import threading
import requests

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

AGENT_URL  = "http://localhost:5000"
PROM_URL   = "http://localhost:9090"
GRAFANA_URL = "http://localhost:3000"
SFCC_URL   = os.getenv("SFCC_SITE_URL", "http://localhost:8000")

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
# 1 — Docker stack health
# ─────────────────────────────────────────────────────────────────────────────
section("1 — Docker Stack Health")

try:
    r = requests.get(f"{AGENT_URL}/", timeout=5)
    check("RCA agent running", r.status_code == 200, r.text.strip())
except Exception as e:
    check("RCA agent running", False, str(e))
    print(f"\n  ❌ RCA agent not reachable — is docker-compose up?")
    sys.exit(1)

try:
    r = requests.get(f"{PROM_URL}/-/ready", timeout=5)
    check("Prometheus running", r.status_code == 200)
except Exception as e:
    check("Prometheus running", False, str(e))

try:
    r = requests.get(f"{GRAFANA_URL}/api/health", timeout=5)
    check("Grafana running", r.status_code == 200)
except Exception as e:
    check("Grafana running", False, str(e))

try:
    # api:8000 is Docker-internal — check via localhost from host machine
    target_local = SFCC_URL.replace("host.docker.internal", "localhost").replace("api:", "localhost:")
    r = requests.get(target_local, timeout=5)
    check("Target app reachable", r.status_code < 500, f"{target_local} → {r.status_code}")
except Exception as e:
    check("Target app reachable", False, f"tried {SFCC_URL} as localhost — {e}")

# ─────────────────────────────────────────────────────────────────────────────
# 2 — GitHub: fetch latest commit + detect changes
# ─────────────────────────────────────────────────────────────────────────────
section("2 — GitHub Commit Detection")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO  = os.getenv("GITHUB_REPO", "")

if not GITHUB_TOKEN or not GITHUB_REPO:
    check("GitHub credentials configured", False, "GITHUB_TOKEN or GITHUB_REPO not set in .env")
    sys.exit(1)

check("GitHub credentials configured", True, f"repo={GITHUB_REPO}")

gh_headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
latest_sha = None

try:
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/commits",
        headers=gh_headers, params={"per_page": 1}, timeout=10
    )
    if r.status_code in (401, 403):
        check("GitHub API reachable", False, f"Auth failed ({r.status_code}) — check GITHUB_TOKEN")
        sys.exit(1)
    elif r.status_code == 404:
        check("GitHub API reachable", False, f"Repo not found — check GITHUB_REPO={GITHUB_REPO}")
        sys.exit(1)
    check("GitHub API reachable", r.status_code == 200, f"status={r.status_code}")
    commits = r.json()
    latest_sha = commits[0]["sha"] if commits else None
    check("Latest commit found", bool(latest_sha), (latest_sha or "none")[:12])
except Exception as e:
    check("GitHub API reachable", False, str(e))
    sys.exit(1)

changed_files = []
try:
    r = requests.get(
        f"https://api.github.com/repos/{GITHUB_REPO}/commits/{latest_sha}",
        headers=gh_headers, timeout=10
    )
    check("Commit detail fetched", r.status_code == 200, f"sha={latest_sha[:8]}")
    changed_files = r.json().get("files", []) if r.status_code == 200 else []
    check("Changed files returned", len(changed_files) > 0, f"{len(changed_files)} files")
except Exception as e:
    check("Commit detail fetched", False, str(e))

from agent.code_change_detector import detect_changes
report = detect_changes("", changed_files)

print(f"\n  {INFO} Commit {latest_sha[:8]} — {len(changed_files)} files")
print(f"  {INFO} Features detected  : {len(report.feature_changes)}")
print(f"  {INFO} Dep changes        : {len(report.dependency_changes)}")
for f in report.feature_changes:
    print(f"       {f.method} {f.path}")

check("detect_changes completed", True, "")

# ─────────────────────────────────────────────────────────────────────────────
# 3 — Generate scripts locally (written to scripts/ volume shared with Docker)
# ─────────────────────────────────────────────────────────────────────────────
section("3 — Script Generation (local → shared volume)")

# Clear checkpoint so orchestrate() always processes this commit fresh
from agent.commit_tracker import _get_checkpoint_file
import json as _json

cp_file = _get_checkpoint_file()
try:
    if os.path.exists(cp_file):
        with open(cp_file, "r", encoding="utf-8") as _f:
            cp_data = _json.load(_f)
        if GITHUB_REPO in cp_data:
            cp_data[GITHUB_REPO]["last_processed_sha"] = None
            with open(cp_file, "w", encoding="utf-8") as _f:
                _json.dump(cp_data, _f, indent=2)
    print(f"  {INFO} Checkpoint cleared for {GITHUB_REPO}")
except Exception as e:
    print(f"  {INFO} Could not clear checkpoint: {e} — continuing anyway")

from agent.test_orchestrator import orchestrate
try:
    orch = orchestrate(commit_sha=latest_sha)
    check("orchestrate() completed", orch.error is None, orch.error or "ok")
    check("Scripts generated", len(orch.generated_scripts) > 0,
          f"{len(orch.generated_scripts)} sets")
    for gs in orch.generated_scripts[:3]:
        check(f"k6 script on disk: {os.path.basename(gs.k6_path)}",
              os.path.exists(gs.k6_path), gs.k6_path)
    print(f"  {INFO} Summary: {orch.summary}")
except Exception as e:
    check("orchestrate() completed", False, str(e))

# ─────────────────────────────────────────────────────────────────────────────
# 4 — Fire webhook → real k6 run inside Docker against target app
# ─────────────────────────────────────────────────────────────────────────────
section("4 — Webhook → k6 Run Against Real App")

reports_before = set(os.listdir("reports")) if os.path.exists("reports") else set()

result_box = {}
def _fire():
    try:
        r = requests.post(
            f"{AGENT_URL}/github-webhook",
            json={"ref": "refs/heads/main", "head_commit": {"id": latest_sha}},
            timeout=600
        )
        result_box["status"] = r.status_code
        result_box["body"]   = r.json()
    except Exception as e:
        result_box["status"] = 0
        result_box["error"]  = str(e)

t = threading.Thread(target=_fire, daemon=True)
t.start()
print(f"\n  {INFO} Webhook fired for commit {latest_sha[:8]} — polling for report...")

# Poll up to 5 minutes for a new report
new_reports = set()
for i in range(30):
    time.sleep(10)
    current = set(os.listdir("reports")) if os.path.exists("reports") else set()
    new_reports = current - reports_before
    if new_reports:
        print(f"  {INFO} Report appeared after {(i+1)*10}s")
        break
    print(f"  {INFO} Waiting... {(i+1)*10}s")

check("Report written to disk", bool(new_reports), str(new_reports))

# ─────────────────────────────────────────────────────────────────────────────
# 4 — Verify k6 actually ran (not fake)
# ─────────────────────────────────────────────────────────────────────────────
section("5 — k6 Results Verification")

json_files = [f for f in new_reports if f.endswith(".json")]
if json_files:
    with open(f"reports/{sorted(json_files)[-1]}", encoding="utf-8") as f:
        rdata = json.load(f)

    k6 = rdata.get("tools_output", {}).get("k6", {})
    scripts_run = k6.get("scripts_run", 0)
    status      = k6.get("status", "unknown")

    check("k6 ran at least 1 script",   scripts_run > 0,          f"scripts_run={scripts_run}")
    # status=failed means threshold violations (e.g. 404 on parametrised endpoints) — not a k6 crash
    check("k6 executed (completed or threshold fail)", status in ("completed", "failed"), f"status={status}")
    check("AI analysis present",        bool(rdata.get("ai_analysis")), "")
    check("System metrics present",     "cpu" in rdata.get("system", {}), "")

    # Show which scripts ran
    for r in k6.get("results", []):
        label = "✅" if r.get("exit_code") == 0 else "❌"
        print(f"  {INFO} {label} {r['script']} (exit {r.get('exit_code')})")
else:
    check("Report JSON found", False, "no .json report in reports/")

# ─────────────────────────────────────────────────────────────────────────────
# 5 — Prometheus has real k6 metrics
# ─────────────────────────────────────────────────────────────────────────────
section("6 — Prometheus Metrics")

try:
    r = requests.get(
        f"{PROM_URL}/api/v1/query",
        params={"query": "k6_http_reqs_total"},
        timeout=5
    )
    data = r.json()
    metric_count = len(data.get("data", {}).get("result", []))
    check("k6 metrics in Prometheus", metric_count > 0, f"series={metric_count}")
except Exception as e:
    check("k6 metrics in Prometheus", False, str(e))

# ─────────────────────────────────────────────────────────────────────────────
# 6 — Script generation (from orchestrate via webhook, verify files on disk)
# ─────────────────────────────────────────────────────────────────────────────
section("7 — Generated Scripts on Disk")

from agent.script_generator import repo_slug
import glob as _glob

repo_dir   = os.path.join("scripts", repo_slug(GITHUB_REPO))
k6_scripts = _glob.glob(f"{repo_dir}/**/*.js", recursive=True)
print(f"  {INFO} Looking in: {repo_dir}")
check("Scripts folder created for repo", os.path.isdir(repo_dir), repo_dir)
check("k6 scripts exist on disk", len(k6_scripts) > 0, f"{len(k6_scripts)} scripts")
for s in k6_scripts:
    size = os.path.getsize(s)
    check(f"Script not empty: {os.path.basename(s)}", size > 100, f"{size} bytes")

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
t.join(timeout=5)
passed = sum(1 for _, r in results if r)
failed = sum(1 for _, r in results if not r)
print(f"\n{'='*60}")
print(f"  Results: {passed} passed, {failed} failed out of {len(results)} checks")
print(f"{'='*60}\n")
sys.exit(0 if failed == 0 else 1)
