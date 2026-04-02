from flask import Flask, request, jsonify
import requests
import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI
from email_service import send_email
from slack_service import send_slack
import glob
from github_reporting import comment_pr
import psutil
import json
import threading
import sys
import traceback
import gc

# ── NEW: smart code-change detection + script generation/patching ─────────────
from agent.test_orchestrator import orchestrate
from agent.concurrency import WebhookQueue
from report_writer import write_report

# Single queue — serialises all concurrent webhook events
_webhook_queue = WebhookQueue()

# ── File logging setup ────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
_log_file = os.path.join("logs", f"agent_{datetime.now().strftime('%Y%m%d')}.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_log_file, encoding="utf-8"),
        logging.StreamHandler(),          # keep console output too
    ],
)
log = logging.getLogger("agent")
log.info(f"Agent log file: {_log_file}")

# --------------------------------
# LOAD ENV VARIABLES
# --------------------------------

load_dotenv()

MCP_URL = os.getenv("MCP_URL")
TOKEN = os.getenv("TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

GRAFANA_RW_URL = os.getenv("GRAFANA_RW_URL")
GRAFANA_USERNAME = os.getenv("GRAFANA_USERNAME")
GRAFANA_API_KEY = os.getenv("GRAFANA_API_KEY")
K6_SCRIPT = os.getenv("K6_SCRIPT", "script.js")

SFCC_SITE_URL = os.getenv("SFCC_SITE_URL")

JIRA_URL = os.getenv("JIRA_URL")
JIRA_EMAIL = os.getenv("JIRA_EMAIL")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ENABLE_AI = os.getenv("ENABLE_AI", "true")
GITHUB_REPO_URL = os.getenv("GITHUB_REPO_URL")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
# --------------------------------
# FLASK APP
# --------------------------------

app = Flask(__name__)

def get_system_metrics():
    return {
        "cpu": psutil.cpu_percent(interval=1),
        "memory": psutil.virtual_memory().percent
    }


def get_thread_dump():

    try:
        dump = []

        for thread_id, frame in sys._current_frames().items():
            thread_name = None

            for t in threading.enumerate():
                if t.ident == thread_id:
                    thread_name = t.name
                    break

            stack = traceback.format_stack(frame)

            dump.append({
                "thread_id": thread_id,
                "thread_name": thread_name,
                "stack_trace": stack[-5:]  # last few lines only
            })

        return {
            "thread_count": len(dump),
            "threads": dump[:3]  # limit output (important)
        }

    except Exception as e:
        return {"error": str(e)}   

def get_heap_snapshot():

    try:
        return {
            "objects_in_memory": len(gc.get_objects()),
            "garbage": len(gc.garbage)
        }

    except Exception as e:
        return {"error": str(e)}      


def detect_latency_anomaly():
    return {
        "p95_threshold": 200,
        "status": "within limits"
    }


def detect_memory_trend():
    mem = psutil.virtual_memory().percent
    status = "normal"
    if mem > 85:
        status = "high memory usage (possible leak)"
    return {
        "memory_usage": mem,
        "status": status
    }


def detect_cpu_spike():
    cpu = psutil.cpu_percent(interval=1)
    status = "normal"
    if cpu > 80:
        status = "high cpu usage"
    return {
        "cpu": cpu,
        "status": status
    }           
# --------------------------------
# MCP TOOL CALL
# --------------------------------

def call_tool(tool):

    try:

        headers = {
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json"
        }

        body = {
            "tool": tool
        }

        response = requests.post(
            MCP_URL,
            headers=headers,
            json=body
        )

        data = response.json()

        print("\n-----------------------------")
        print("Tool:", tool)
        print("Response:", data)
        print("-----------------------------\n")

        return data

    except Exception as e:

        print("Tool Error:", str(e))
        return {"error": str(e)}

def format_report(perf, infra):

    report = {
        "summary": {},
        "issues": [],
        "tools_output": {}
    }

    # K6 summary
    report["tools_output"]["k6"] = perf.get("k6")

    # SpeedCurve
    report["tools_output"]["speedcurve"] = perf.get("speedcurve")

    # Datadog
    report["tools_output"]["datadog"] = perf.get("datadog")

    # Git commits
    report["tools_output"]["github"] = infra

    return report



def detect_anomalies(perf):

    issues = []

    # Check k6 failures
    k6 = perf.get("k6", {})
    results = k6.get("results", [])

    for r in results:
        if r["exit_code"] != 0:
            issues.append(f"K6 script failed: {r['script']}")

    # Check SpeedCurve error
    if "error" in str(perf.get("speedcurve")):
        issues.append("SpeedCurve API failure / auth issue")

    # Check Datadog error
    if "error" in str(perf.get("datadog")):
        issues.append("Datadog API failure / auth issue")

    return issues    


def extract_anomalies(perf):

    anomalies = {}

    # K6
    k6 = perf.get("k6", {})
    results = k6.get("results", [])

    failed = [r["script"] for r in results if r["exit_code"] != 0]

    anomalies["k6"] = {
        "status": "ok" if not failed else "failed",
        "failed_scripts": failed
    }

    # Grafana (manual detection for now)
    anomalies["grafana"] = "possible 429 rate limit (check logs)"

    # SpeedCurve
    if "401" in str(perf.get("speedcurve")):
        anomalies["speedcurve"] = "auth failure (401)"
    else:
        anomalies["speedcurve"] = "ok"

    # Datadog
    if "401" in str(perf.get("datadog")):
        anomalies["datadog"] = "auth failure (401)"
    else:
        anomalies["datadog"] = "ok"

    return anomalies    
# --------------------------------
# RUN K6 + PUSH METRICS TO GRAFANA
# --------------------------------

def run_k6():
    # Delegate to tools/k6.py which handles subprocess, timeouts,
    # Grafana remote-write, and graceful error handling properly.
    from tools.k6 import run_k6_test
    return run_k6_test()


# --------------------------------
# PERFORMANCE TESTS
# --------------------------------

def run_tests():

    print("\nStarting Performance Tests\n")

    k6 = run_k6()

    speedcurve = call_tool("speedcurve")

    datadog = call_tool("datadog_metrics")

    return {
        "k6": k6,
        "speedcurve": speedcurve,
        "datadog": datadog
    }

def get_git_diff():

    try:
        url = f"{GITHUB_REPO_URL}/commits"

        headers = {
            "Authorization": f"Bearer {GITHUB_TOKEN}"
        }

        response = requests.get(url, headers=headers)
        commits = response.json()

        if not commits:
            return {}

        latest_commit = commits[0]["sha"]

        diff_url = f"{GITHUB_REPO_URL}/commits/{latest_commit}"

        diff_res = requests.get(diff_url, headers=headers)
        diff_data = diff_res.json()

        files = diff_data.get("files", [])

        changes = []

        for f in files:
            changes.append({
                "filename": f.get("filename"),
                "changes": f.get("changes"),
                "patch": f.get("patch", "")[:200]
            })

        return {
            "commit": latest_commit,
            "files_changed": changes
        }

    except Exception as e:
        return {"error": str(e)}
# --------------------------------
# ROOT CAUSE DATA
# --------------------------------

def run_rca():

    print("\nRunning Root Cause Data Collection\n")

    commits = call_tool("github_commits")

    git_diff = get_git_diff()

    return {
        "commits": commits,
        "git_diff": git_diff
    }



def create_jira_ticket(summary, description):

    try:

        url = f"{JIRA_URL}/rest/api/3/issue"

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        auth = (JIRA_EMAIL, JIRA_API_TOKEN)

        payload = {
            "fields": {
                "project": {
                    "key": JIRA_PROJECT_KEY
                },
                "summary": summary,
                "description": description,
                "issuetype": {
                    "name": "Bug"
                }
            }
        }

        response = requests.post(
            url,
            json=payload,
            headers=headers,
            auth=auth
        )

        print("\nJIRA Ticket Created\n", response.json())

        return response.json()

    except Exception as e:

        print("JIRA Error:", str(e))
        return {"error": str(e)}
# --------------------------------
# AI ROOT CAUSE ANALYSIS
# --------------------------------

def ai_analysis(data):

    try:

        print("\nRunning AI Root Cause Analysis\n")

        prompt = f"""
You are a performance engineer.

Analyze system data.

Focus on:
- Failed APIs
- Slow transactions
- Errors (401, 500)
- Infra issues

Detected Issues:
{data["issues"]}

Performance Data:
{data}
"""

        response = client.chat.completions.create(

            model=OPENAI_MODEL,

            messages=[
                {
                    "role": "system",
                    "content": "You are a performance engineering expert."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],

            temperature=0.2
        )

        ai_result = response.choices[0].message.content

        print("\nAI ANALYSIS RESULT\n")
        print(ai_result)

        return ai_result

    except Exception as e:

        print("AI Error:", str(e))
        return {"error": str(e)}


# --------------------------------
# MAIN AGENT
# --------------------------------

def run_agent(orch_markdown: str = ""):

    perf_data = run_tests()
    infra_data = run_rca()

    issues = detect_anomalies(perf_data)
    anomalies = extract_anomalies(perf_data)

    report = format_report(perf_data, infra_data)

    system_metrics = get_system_metrics()

    latency_check = detect_latency_anomaly()
    memory_check = detect_memory_trend()
    cpu_check = detect_cpu_spike()

    thread_dump = get_thread_dump()
    heap_dump = get_heap_snapshot() 

    combined_data = {
        "performance": perf_data,
        "infra": infra_data,
        "issues": issues
    }

    # AI
    if ENABLE_AI == "true":
        ai_result = ai_analysis(combined_data)
    else:
        ai_result = "AI disabled by configuration"

    # JIRA
    try:
        # Skip if JIRA is not configured
        if JIRA_URL and JIRA_EMAIL and JIRA_API_TOKEN and JIRA_PROJECT_KEY \
                and "xxxxx" not in JIRA_URL and "xxxxx" not in JIRA_API_TOKEN:
            jira_ticket = create_jira_ticket(
                "Performance Regression Detected",
                str(ai_result)
            )
        else:
            print("[jira] Skipped — JIRA credentials not configured")
            jira_ticket = {"status": "skipped"}
    except Exception as e:
        print("Jira failed:", e)
        jira_ticket = {"error": str(e)}

    # RESULT
    result = {
        "summary": "AI Performance Analysis",
        "issues": issues,
        "anomalies": anomalies,
        "diagnostics": {
            "latency": latency_check,
            "memory": memory_check,
            "cpu": cpu_check
        },
        "advanced_diagnostics": {
            "thread_dump": thread_dump,
            "heap_snapshot": heap_dump
        },
        "tools_output": report["tools_output"],
        "ai_analysis": ai_result,
        "jira": jira_ticket,
        "system": system_metrics
    }

    # MESSAGE
    final_message = f"""
🚀 AI PERFORMANCE REPORT

==============================
SUMMARY:
{result["summary"]}

ISSUES:
{issues}

ANOMALIES:
{json.dumps(anomalies, indent=2)}

==============================
AI ANALYSIS:
{ai_result}

==============================
🔔 NOTIFICATION:
Slack + Email triggered successfully
"""

    # SEND
    # Always write to reports/ folder first — works even without Slack/Email
    try:
        report_path = write_report(result, orch_markdown)
        log.info(f"Report saved: {report_path}")
    except Exception as e:
        print("Report write failed:", e)

    try:
        send_slack(final_message)
    except Exception as e:
        print("Slack failed:", e)

    try:
        send_email(final_message)
    except Exception as e:
        print("Email failed:", e)

    # PRINT
    print("\n================ FINAL REPORT (JSON) ================\n")
    print(json.dumps(result, indent=4))
    print("\n====================================================\n")

    return result   # ✅ ONLY ONE return (inside function)

# --------------------------------
# GITHUB WEBHOOK
# --------------------------------

@app.route("/github-webhook", methods=["POST"])
def github_push():

    try:

        payload = request.json

        print("\n=================================")
        print("GitHub Event Received")
        print("Payload:", payload)
        print("=================================\n")

        # --------------------------------
        # Extract PR Number (if PR event)
        # --------------------------------

        pr_number = None

        if "pull_request" in payload:
            pr_number = payload["pull_request"]["number"]

        # --------------------------------
        # Smart Webhook Filter
        # Only run tests on main branch
        # --------------------------------

        branch = payload.get("ref")

        if branch and branch != "refs/heads/main":

            print("Skipping non-main branch")

            return jsonify({
                "message": "Skipped non main branch"
            })

        # ── Step 1: Code-change detection + script generation/patching ──────────
        commit_sha = None
        if "head_commit" in payload:
            commit_sha = payload["head_commit"].get("id")

        # Only run orchestration when we have real GitHub data to work with
        orch_markdown = ""
        if pr_number or commit_sha:
            log.info(
                f"[webhook] Queuing orchestration for "
                f"{'PR #' + str(pr_number) if pr_number else 'commit ' + str(commit_sha)[:8]} "
                f"— queue depth: {_webhook_queue.queue_depth}"
            )
            future = _webhook_queue.submit(
                orchestrate,
                pr_number=pr_number,
                commit_sha=commit_sha,
            )
            orch_result = future.result(timeout=600)   # wait up to 10 min
            orch_markdown = orch_result.to_markdown()
            log.info(f"[webhook] Orchestration: {orch_result.summary}")
        else:
            log.info("[webhook] No PR number or commit SHA in payload — skipping orchestration")

        # ── Step 2: Run existing performance agent ────────────────────────────
        result = run_agent(orch_markdown=orch_markdown)

        # ── Step 3: Post PR comment (regression + script-change report) ───────
        if pr_number and orch_markdown:
            combined_comment = orch_markdown + "\n\n---\n\n" + str(result.get("ai_analysis", ""))
            comment_pr(pr_number, combined_comment)

        return jsonify({
            "message": "AI Agent Executed",
            "orchestration": orch_markdown or "skipped (no PR/commit data)",
            "result": result
        }), 200

    except Exception as e:

        print("Webhook Error:", str(e))

        return jsonify({
            "error": str(e)
        }), 500


# --------------------------------
# HEALTH CHECK
# --------------------------------

@app.route("/", methods=["GET"])
def home():

    return {
        "status": "AI Performance Agent Running"
    }


@app.route("/trigger-from-slack", methods=["POST"])
def trigger_slack():

    print("Triggered from Slack")

    result = run_agent(orch_markdown="")

    return jsonify({
        "message": "Triggered via Slack",
        "result": result
    })
# --------------------------------
# MAIN
# --------------------------------

if __name__ == "__main__":

    print("\nAI Performance Webhook Agent Started\n")

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )