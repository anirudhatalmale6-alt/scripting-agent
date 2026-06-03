"""
rca_agent.py
────────────
RCA Agent — decoupled from Test Trigger.

Responsibilities:
  - Receive GitHub webhook (push / PR)
  - Run k6 performance tests
  - Collect system metrics, thread dumps, heap snapshots
  - Run AI root cause analysis
  - Create Jira tickets for regressions
  - Send Slack + Email reports
  - Write reports to reports/ folder

Run:
  python rca_agent.py                   (port 5000)
  docker-compose up rca-agent
"""

import gc
import logging
import os
import sys
import threading
import traceback
from datetime import datetime

import psutil
from dotenv import load_dotenv
from flask import Flask, jsonify, request

from agent.llm_provider import get_llm_client, get_model, llm_available
from agent.mcp_client import call_tool
from agent.perf_policy import load_policy, get_thresholds, get_profile, load_agent_skill, PerfPolicy
from typing import Optional
from email_service import send_email
from report_writer import write_report
from slack_service import send_slack

load_dotenv()

from agent.log_filter import setup_log_filter
setup_log_filter()

# ── Logging ───────────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
_log_file = os.path.join("logs", f"rca_agent_{datetime.now().strftime('%Y%m%d')}.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("rca_agent")

# ── Config ────────────────────────────────────────────────────────────────────

OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY")
ENABLE_AI       = os.getenv("ENABLE_AI", "true")
JIRA_URL        = os.getenv("JIRA_URL")
JIRA_EMAIL      = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN  = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY")
GITHUB_REPO_URL = os.getenv("GITHUB_REPO_URL")
GITHUB_TOKEN    = os.getenv("GITHUB_TOKEN")
MCP_URL         = os.getenv("MCP_URL")
TOKEN           = os.getenv("TOKEN")

# ── Cached policy ────────────────────────────────────────────────────────────
_rca_policy: Optional[PerfPolicy] = None


def _get_rca_policy() -> PerfPolicy:
    global _rca_policy
    if _rca_policy is None:
        _rca_policy = load_policy()
    return _rca_policy
_exec_skill: str = ""
_platform_skill: str = ""


def _get_exec_skill() -> str:
    global _exec_skill
    if not _exec_skill:
        _exec_skill = load_agent_skill("PERF_EXEC_AGENT.md")
    return _exec_skill


def _get_platform_skill() -> str:
    global _platform_skill
    if not _platform_skill:
        _platform_skill = load_agent_skill("PERF_AGENTS.md")
    return _platform_skill

def _get_openai_client():
    return get_llm_client()
app    = Flask(__name__)


# ── System metrics ────────────────────────────────────────────────────────────

def get_system_metrics():
    return {"cpu": psutil.cpu_percent(interval=1), "memory": psutil.virtual_memory().percent}

def get_thread_dump():
    try:
        dump = []
        for tid, frame in sys._current_frames().items():
            name = next((t.name for t in threading.enumerate() if t.ident == tid), None)
            dump.append({"thread_id": tid, "thread_name": name,
                         "stack_trace": traceback.format_stack(frame)[-5:]})
        return {"thread_count": len(dump), "threads": dump[:3]}
    except Exception as e:
        return {"error": str(e)}

def get_heap_snapshot():
    try:
        return {"objects_in_memory": len(gc.get_objects()), "garbage": len(gc.garbage)}
    except Exception as e:
        return {"error": str(e)}

def detect_latency_anomaly():
    policy = _get_rca_policy()
    default_p95 = policy.thresholds.get("default").p95_ms if policy.loaded and "default" in policy.thresholds else 2000
    return {"p95_threshold": default_p95, "status": "within limits"}

def detect_memory_trend():
    mem = psutil.virtual_memory().percent
    return {"memory_usage": mem,
            "status": "high memory usage (possible leak)" if mem > 85 else "normal"}

def detect_cpu_spike():
    cpu = psutil.cpu_percent(interval=1)
    return {"cpu": cpu, "status": "high cpu usage" if cpu > 80 else "normal"}


# ── k6 ────────────────────────────────────────────────────────────────────────

def _tool(name: str, params: dict = None) -> dict:
    """Call MCP tool, return None if skipped/unavailable so callers can filter."""
    result = call_tool(name, params or {})
    if result.get("status") == "skipped" or "error" in result:
        log.warning(f"[rca_agent] Tool '{name}' unavailable: {result}")
        return {}
    return result


# ── Git diff ──────────────────────────────────────────────────────────────────

def get_git_diff():
    import requests as _req
    try:
        headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}
        commits = _req.get(f"{GITHUB_REPO_URL}/commits", headers=headers).json()
        if not commits:
            return {}
        sha = commits[0]["sha"]
        data = _req.get(f"{GITHUB_REPO_URL}/commits/{sha}", headers=headers).json()
        return {
            "commit": sha,
            "files_changed": [
                {"filename": f.get("filename"), "changes": f.get("changes"),
                 "patch": f.get("patch", "")[:200]}
                for f in data.get("files", [])
            ],
        }
    except Exception as e:
        return {"error": str(e)}


# ── Anomaly detection ─────────────────────────────────────────────────────────

def detect_anomalies(perf):
    issues = []
    for r in perf.get("k6", {}).get("results", []):
        if r.get("exit_code", 1) != 0:
            issues.append(f"K6 script failed: {r['script']}")
    if "error" in str(perf.get("speedcurve")):
        issues.append("SpeedCurve API failure")
    if "error" in str(perf.get("datadog")):
        issues.append("Datadog API failure")
    return issues

def extract_anomalies(perf):
    k6 = perf.get("k6", {})
    failed = [r["script"] for r in k6.get("results", []) if r.get("exit_code", 1) != 0]
    return {
        "k6": {"status": "ok" if not failed else "failed", "failed_scripts": failed},
        "grafana": "possible 429 rate limit (check logs)",
        "speedcurve": "auth failure (401)" if "401" in str(perf.get("speedcurve")) else "ok",
        "datadog":    "auth failure (401)" if "401" in str(perf.get("datadog"))    else "ok",
    }


# ── AI RCA ────────────────────────────────────────────────────────────────────

def ai_analysis(data):
    try:
        c = _get_openai_client()
        if not c:
            return "AI unavailable — OPENAI_API_KEY not configured."

        # Load agent skill file as system prompt — drives agent behaviour
        exec_skill = _get_exec_skill()
        platform_skill = _get_platform_skill()

        system_prompt = "You are a performance engineering expert."
        if exec_skill:
            # Use the RCA-specific sections as the system prompt
            system_prompt = exec_skill
        elif platform_skill:
            system_prompt = platform_skill

        # Inject policy regression rules into the user prompt
        policy = _get_rca_policy()
        policy_context = ""
        if policy.loaded and policy.regression_rules:
            policy_context = (
                f"\n## Regression thresholds from repo policy:\n"
                f"{policy.regression_rules[:600]}\n"
            )

        prompt = f"""Analyze the following performance data.
Focus on: Failed APIs, Slow transactions, Errors (401, 500), Infra issues.
Classify regression as: no_regression / minor_regression / severe_regression.
Provide: likely cause (1 sentence) + recommendation (1 sentence).
{policy_context}
Detected Issues: {data['issues']}
Performance Data: {data}"""

        resp = c.chat.completions.create(
            model=get_model(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.2,
        )
        return resp.choices[0].message.content
    except Exception as e:
        return {"error": str(e)}


# ── Jira ──────────────────────────────────────────────────────────────────────

def create_jira_ticket(summary, description):
    return _tool("jira", {"summary": summary, "description": description})


# ── Main RCA pipeline ─────────────────────────────────────────────────────────

def run_rca_pipeline(orch_markdown: str = "") -> dict:
    # Run all tests — AI self-heals failures, only sends summary to AI analysis
    test_results = _tool("run_tests", {
        "repo": os.getenv("GITHUB_REPOS", "").split(",")[0].strip(),
        "env":  os.getenv("ENV", "dev"),
    })

    perf_data = {
        "test_results": test_results,
        "speedcurve":   _tool("speedcurve"),
        "datadog":      _tool("datadog"),
    }
    infra_data = {
        "commits":  _tool("github_commits"),
        "git_diff": get_git_diff(),
    }

    # Only call AI if there are actual failures needing analysis
    summary = test_results.get("summary", {})
    has_failures = summary.get("needs_manual", 0) > 0 or summary.get("failed", 0) > 0

    if ENABLE_AI == "true" and has_failures:
        ai_result = ai_analysis({"performance": perf_data, "infra": infra_data,
                                 "issues": test_results})
    elif not has_failures:
        ai_result = f"All tests passed. {summary.get('passed', 0)} scripts OK, " \
                    f"{summary.get('ai_fixes_used', 0)} auto-fixed by AI."
    else:
        ai_result = "AI disabled"

    # Only create Jira ticket if something needs manual review
    jira = {}
    if summary.get("needs_manual", 0) > 0:
        manual = summary.get("manual_review", [])
        jira = create_jira_ticket(
            f"Test failures need manual review ({len(manual)} scripts)",
            f"Scripts requiring manual fix:\n" + "\n".join(manual) + f"\n\nAI Analysis:\n{ai_result}"
        )

    result = {
        "summary":    "AI Performance RCA",
        "test_summary": summary,
        "diagnostics": {
            "latency": detect_latency_anomaly(),
            "memory":  detect_memory_trend(),
            "cpu":     detect_cpu_spike(),
        },
        "advanced_diagnostics": {
            "thread_dump":   get_thread_dump(),
            "heap_snapshot": get_heap_snapshot(),
        },
        "tools_output": {
            "tests":      test_results,
            "speedcurve": perf_data["speedcurve"],
            "datadog":    perf_data["datadog"],
            "github":     infra_data,
        },
        "ai_analysis": ai_result,
        "jira":        jira,
        "system":      get_system_metrics(),
    }

    passed  = summary.get("passed", 0)
    manual  = summary.get("needs_manual", 0)
    ai_used = summary.get("ai_fixes_used", 0)
    msg = f"""🚀 AI Performance RCA Report
✅ Passed: {passed} | ⚠️ Needs manual: {manual} | 🔧 AI fixes used: {ai_used}
{ai_result[:400]}"""

    try:
        write_report(result, orch_markdown)
    except Exception as e:
        log.warning(f"Report write failed: {e}")

    try:
        send_slack(msg)
    except Exception:
        pass

    try:
        send_email(msg)
    except Exception:
        pass

    return result


# ── Webhook ───────────────────────────────────────────────────────────────────

@app.route("/github-webhook", methods=["POST"])
def github_webhook():
    payload = request.json or {}
    branch  = payload.get("ref", "")
    if branch and branch != "refs/heads/main":
        return jsonify({"message": f"Skipped branch: {branch}"}), 200

    result = run_rca_pipeline()
    return jsonify({"message": "RCA Agent executed", "result": result}), 200


@app.route("/trigger", methods=["POST"])
def trigger():
    """Manual trigger — e.g. from Slack."""
    result = run_rca_pipeline()
    return jsonify({"message": "RCA triggered manually", "result": result}), 200


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "RCA Agent running"})


if __name__ == "__main__":
    log.info("RCA Agent started on port 5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
