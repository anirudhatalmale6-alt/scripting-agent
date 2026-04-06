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
from openai import OpenAI

from agent.mcp_client import call_tool
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
load_dotenv()

OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL    = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ENABLE_AI       = os.getenv("ENABLE_AI", "true")
JIRA_URL        = os.getenv("JIRA_URL")
JIRA_EMAIL      = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN  = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY")
GITHUB_REPO_URL = os.getenv("GITHUB_REPO_URL")
GITHUB_TOKEN    = os.getenv("GITHUB_TOKEN")
MCP_URL         = os.getenv("MCP_URL")
TOKEN           = os.getenv("TOKEN")

client = OpenAI(api_key=OPENAI_API_KEY)
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
    return {"p95_threshold": 200, "status": "within limits"}

def detect_memory_trend():
    mem = psutil.virtual_memory().percent
    return {"memory_usage": mem,
            "status": "high memory usage (possible leak)" if mem > 85 else "normal"}

def detect_cpu_spike():
    cpu = psutil.cpu_percent(interval=1)
    return {"cpu": cpu, "status": "high cpu usage" if cpu > 80 else "normal"}


# ── k6 ────────────────────────────────────────────────────────────────────────

def run_k6():
    return call_tool("k6")


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
        prompt = f"""You are a performance engineer.
Analyze system data. Focus on: Failed APIs, Slow transactions, Errors (401, 500), Infra issues.
Detected Issues: {data['issues']}
Performance Data: {data}"""
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are a performance engineering expert."},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.2,
        )
        return resp.choices[0].message.content
    except Exception as e:
        return {"error": str(e)}


# ── Jira ──────────────────────────────────────────────────────────────────────

def create_jira_ticket(summary, description):
    return call_tool("jira", {"summary": summary, "description": description})


# ── Main RCA pipeline ─────────────────────────────────────────────────────────

def run_rca_pipeline(orch_markdown: str = "") -> dict:
    # Run all tests — AI self-heals failures, only sends summary to AI analysis
    test_results = call_tool("run_tests", {
        "repo": os.getenv("GITHUB_REPO", ""),
        "env":  os.getenv("ENV", "dev"),
    })

    perf_data = {
        "test_results": test_results,
        "speedcurve":   call_tool("speedcurve"),
        "datadog":      call_tool("datadog"),
    }
    infra_data = {
        "commits":  call_tool("github_commits"),
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
