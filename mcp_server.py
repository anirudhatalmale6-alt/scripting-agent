"""
mcp_server.py
─────────────
Standalone MCP (Model Context Protocol) Tool Server — port 5002.

Exposes all tools as HTTP endpoints so ANY agent can plug in:
  POST /tools/k6               → run k6 performance tests
  POST /tools/grafana          → fetch Grafana dashboards
  POST /tools/jira             → create Jira ticket
  POST /tools/datadog          → query Datadog metrics
  POST /tools/speedcurve       → fetch SpeedCurve data
  POST /tools/github_commits   → fetch latest GitHub commits
  POST /tools/slack            → send Slack message
  GET  /tools                  → list all available tools + schemas

Any agent (RCA, test-trigger, future agents) calls this server instead of
importing tools directly. Tools are loaded lazily — missing credentials
cause a graceful skip, not a crash.

Run:
  python mcp_server.py          (port 5002)
  docker-compose up mcp-server
"""

import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()

from agent.log_filter import setup_log_filter
setup_log_filter()

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
_log_file = os.path.join("logs", f"mcp_server_{datetime.now().strftime('%Y%m%d')}.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("mcp_server")

app = Flask(__name__)

# ── Tool registry — describes every tool for discovery ───────────────────────

TOOL_REGISTRY = {
    "k6": {
        "description": "Run k6 performance test scripts with real load and AI self-heal on failure",
        "input": {"script_dir": "string (optional, default: scripts)"},
    },
    "run_tests": {
        "description": "Run all k6 + Selenium scripts for a repo/env. AI fixes failures automatically, only flags what truly needs manual review.",
        "input": {"repo": "string (optional)", "env": "string (optional, default: dev)"},
    },
    "generate_scripts": {
        "description": "Generate or update k6/Selenium/LoadRunner scripts for changed endpoints using policy rules from .perf/",
        "input": {
            "changed_files": "list of changed file paths (required)",
            "repo":          "string (optional)",
            "env":           "string (optional, default: dev)",
        },
    },
    "get_impact_map": {
        "description": "Return which test domains and scripts are affected by a set of changed files, using .perf/mappings.yaml",
        "input": {"changed_files": "list of changed file paths (required)"},
    },
    "compare_with_baseline": {
        "description": "Compare current run metrics against stored baseline and classify regression",
        "input": {
            "current":  "dict with latency and error_rate keys (required)",
            "previous": "dict with latency and error_rate keys (required)",
            "domain":   "string — domain name for threshold lookup (optional, default: default)",
        },
    },
    "grafana": {
        "description": "Fetch Grafana dashboards list or a specific dashboard by UID",
        "input": {"uid": "string (optional — omit to list all dashboards)"},
    },
    "jira": {
        "description": "Create a Jira bug ticket",
        "input": {"summary": "string", "description": "string"},
    },
    "datadog": {
        "description": "Query Datadog system metrics (CPU, memory) for the last hour",
        "input": {},
    },
    "speedcurve": {
        "description": "Fetch SpeedCurve site performance data",
        "input": {},
    },
    "github_commits": {
        "description": "Fetch latest commits from the configured GitHub repo",
        "input": {"per_page": "int (optional, default: 5)"},
    },
    "slack": {
        "description": "Send a message to the configured Slack webhook",
        "input": {"message": "string"},
    },
}


# ── Tool handlers ─────────────────────────────────────────────────────────────

def _handle_k6(params: dict) -> dict:
    from tools.k6 import run_k6_test
    script_dir = params.get("script_dir", "scripts")
    return run_k6_test(script_dir=script_dir)


def _handle_run_tests(params: dict) -> dict:
    from tools.test_runner import run_all_k6, run_all_selenium, summarise
    repo = params.get("repo", os.getenv("GITHUB_REPOS", "").split(",")[0].strip())
    env  = params.get("env", os.getenv("ENV", "dev"))
    k6_results  = run_all_k6(repo, env)
    sel_results = run_all_selenium(repo, env)
    all_results = k6_results + sel_results
    summary = summarise(all_results)
    log.info(f"[mcp] run_tests: {summary}")
    return {
        "summary": summary,
        "k6":      [{"script": r.script, "passed": r.passed, "attempts": r.attempts,
                     "ai_fixes": r.ai_fixes, "needs_manual": r.needs_manual,
                     "error": r.error[:200] if r.error else ""} for r in k6_results],
        "selenium": [{"script": r.script, "passed": r.passed, "attempts": r.attempts,
                      "ai_fixes": r.ai_fixes, "needs_manual": r.needs_manual,
                      "error": r.error[:200] if r.error else ""} for r in sel_results],
    }


def _handle_grafana(params: dict) -> dict:
    grafana_url = os.getenv("GRAFANA_URL", "")
    api_key     = os.getenv("GRAFANA_API_KEY", "")
    if not grafana_url or not api_key:
        return {"status": "skipped", "reason": "GRAFANA_URL or GRAFANA_API_KEY not configured"}
    try:
        import urllib.request, json as _json
        uid = params.get("uid")
        url = f"{grafana_url}/api/dashboards/uid/{uid}" if uid else f"{grafana_url}/api/search"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return _json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def _handle_jira(params: dict) -> dict:
    jira_url    = os.getenv("JIRA_URL", "")
    email       = os.getenv("JIRA_EMAIL", "")
    api_token   = os.getenv("JIRA_API_TOKEN", "")
    project_key = os.getenv("JIRA_PROJECT_KEY", "")
    if not all([jira_url, email, api_token, project_key]) or "xxxxx" in jira_url:
        return {"status": "skipped", "reason": "Jira credentials not configured"}
    try:
        import requests as _req
        from requests.auth import HTTPBasicAuth
        r = _req.post(
            f"{jira_url}/rest/api/3/issue",
            json={"fields": {
                "project":     {"key": project_key},
                "summary":     params.get("summary", "Performance Regression"),
                "description": params.get("description", ""),
                "issuetype":   {"name": "Bug"},
            }},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            auth=HTTPBasicAuth(email, api_token),
            timeout=15,
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def _handle_datadog(params: dict) -> dict:
    api_key = os.getenv("DATADOG_API_KEY", "")
    if not api_key:
        return {"status": "skipped", "reason": "DATADOG_API_KEY not configured"}
    try:
        import urllib.request, json as _json, time as _time
        now  = int(_time.time())
        past = now - 3600
        url  = f"https://api.datadoghq.com/api/v1/query?from={past}&to={now}&query=avg:system.cpu.user"
        req  = urllib.request.Request(url)
        req.add_header("DD-API-KEY", api_key)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return _json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def _handle_speedcurve(params: dict) -> dict:
    api_key = os.getenv("SPEEDCURVE_API_KEY", "")
    site_id = os.getenv("SPEEDCURVE_SITE_ID", "")
    if not api_key or not site_id:
        return {"status": "skipped", "reason": "SPEEDCURVE_API_KEY or SPEEDCURVE_SITE_ID not configured"}
    try:
        import urllib.request, json as _json
        url = f"https://api.speedcurve.com/v1/sites/{site_id}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return _json.loads(resp.read().decode())
    except Exception as e:
        return {"error": str(e)}


def _handle_github_commits(params: dict) -> dict:
    token    = os.getenv("GITHUB_TOKEN", "")
    repo_url = os.getenv("GITHUB_REPO_URL", "")
    if not token or not repo_url:
        return {"status": "skipped", "reason": "GITHUB_TOKEN or GITHUB_REPO_URL not configured"}
    try:
        import requests as _req
        per_page = params.get("per_page", 5)
        r = _req.get(
            f"{repo_url}/commits",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": per_page},
            timeout=15,
        )
        r.raise_for_status()
        commits = r.json()
        return {
            "commits": [
                {"sha": c["sha"][:8], "message": c["commit"]["message"][:80],
                 "author": c["commit"]["author"]["name"]}
                for c in commits
            ]
        }
    except Exception as e:
        return {"error": str(e)}


def _handle_slack(params: dict) -> dict:
    webhook = os.getenv("SLACK_WEBHOOK", "")
    if not webhook or "xxxx" in webhook:
        return {"status": "skipped", "reason": "SLACK_WEBHOOK not configured"}
    try:
        from slack_service import send_slack
        send_slack(params.get("message", ""))
        return {"status": "sent"}
    except Exception as e:
        return {"error": str(e)}


def _handle_generate_scripts(params: dict) -> dict:
    """Generate/update k6+Selenium+LR scripts for a list of changed files."""
    try:
        from agent.code_change_detector import detect_changes
        from agent.script_generator import generate_all
        from agent.perf_policy import load_policy, build_impact_map

        changed_files = params.get("changed_files", [])
        repo = params.get("repo", os.getenv("GITHUB_REPOS", "").split(",")[0].strip())
        env  = params.get("env", os.getenv("ENV", "dev"))

        if not changed_files:
            return {"status": "skipped", "reason": "No changed_files provided"}

        # Build impact map first
        policy = load_policy()
        file_dicts = [{"filename": f, "status": "modified", "patch": ""} for f in changed_files]
        impact = build_impact_map(changed_files, policy)

        # Detect feature changes from diffs
        change_report = detect_changes("", file_dicts)
        features = change_report.feature_changes

        if not features:
            return {
                "status": "no_features_detected",
                "impact_map": impact,
                "message": "No new endpoints detected in changed files",
            }

        results = generate_all(features, env=env, repo=repo)
        return {
            "status": "ok",
            "scripts_generated": len(results),
            "impact_map": impact,
            "scripts": [
                {"k6": r.k6_path, "selenium": r.selenium_path,
                 "loadrunner": r.loadrunner_path, "validated": r.k6_validated}
                for r in results
            ],
        }
    except Exception as e:
        log.error(f"[mcp] generate_scripts error: {e}")
        return {"error": str(e)}


def _handle_get_impact_map(params: dict) -> dict:
    """Return impact map for a list of changed files using .perf/mappings.yaml."""
    try:
        from agent.perf_policy import load_policy, build_impact_map
        changed_files = params.get("changed_files", [])
        if not changed_files:
            return {"status": "skipped", "reason": "No changed_files provided"}
        policy = load_policy()
        if not policy.loaded:
            return {"status": "skipped", "reason": ".perf/mappings.yaml not found"}
        impact = build_impact_map(changed_files, policy)
        return {"status": "ok", "impact_map": impact}
    except Exception as e:
        return {"error": str(e)}


def _handle_compare_with_baseline(params: dict) -> dict:
    """Compare current vs previous run metrics using policy thresholds."""
    try:
        from regression_detector import detect_regression
        current  = params.get("current",  {})
        previous = params.get("previous", {})
        domain   = params.get("domain", "default")
        if not current or not previous:
            return {"status": "skipped", "reason": "current and previous metrics required"}
        return detect_regression(previous, current, domain=domain)
    except Exception as e:
        return {"error": str(e)}


_HANDLERS = {
    "k6":                    _handle_k6,
    "run_tests":             _handle_run_tests,
    "generate_scripts":      _handle_generate_scripts,
    "get_impact_map":        _handle_get_impact_map,
    "compare_with_baseline": _handle_compare_with_baseline,
    "grafana":               _handle_grafana,
    "jira":                  _handle_jira,
    "datadog":               _handle_datadog,
    "speedcurve":            _handle_speedcurve,
    "github_commits":        _handle_github_commits,
    "slack":                 _handle_slack,
}


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/tools", methods=["GET"])
def list_tools():
    """Discovery endpoint — returns all tools with descriptions and input schemas."""
    return jsonify({"tools": TOOL_REGISTRY})


@app.route("/tools/<tool_name>", methods=["POST"])
def call_tool(tool_name: str):
    handler = _HANDLERS.get(tool_name)
    if not handler:
        return jsonify({"error": f"Unknown tool: {tool_name}",
                        "available": list(_HANDLERS.keys())}), 404

    params = request.json or {}
    log.info(f"[mcp] Tool called: {tool_name} params={list(params.keys())}")

    try:
        result = handler(params)
        log.info(f"[mcp] Tool done: {tool_name}")
        return jsonify({"tool": tool_name, "result": result})
    except Exception as e:
        log.error(f"[mcp] Tool error: {tool_name} — {e}")
        return jsonify({"tool": tool_name, "error": str(e)}), 500


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "MCP Server running", "tools": list(_HANDLERS.keys())})


if __name__ == "__main__":
    log.info("MCP Server started on port 5002")
    app.run(host="0.0.0.0", port=5002, debug=False)
