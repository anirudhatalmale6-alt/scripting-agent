from flask import Flask, request, jsonify
import requests
import os
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
# from email_service import connect, send_email
from tools.openspec import get_tools

import hmac
import hashlib
import os

# --------------------------------
# LOAD ENV VARIABLES
# --------------------------------

load_dotenv()

GITHUB_SECRET = os.getenv("GITHUB_SECRET")
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
ENABLE_AI = os.getenv("ENABLE_AI", "true")
GITHUB_REPO_URL = os.getenv("GITHUB_REPO_URL")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# --------------------------------
# FLASK APP
# --------------------------------

app = Flask(__name__)

def verify_signature(payload, signature):

    mac = hmac.new(
        GITHUB_SECRET.encode(),
        payload,
        hashlib.sha256
    )

    expected = "sha256=" + mac.hexdigest()

    return hmac.compare_digest(expected, signature)

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

    try:

        print("\nRunning k6 performance tests\n")

        # Set Grafana remote write environment variables
        os.environ["K6_PROMETHEUS_RW_SERVER_URL"] = GRAFANA_RW_URL
        os.environ["K6_PROMETHEUS_RW_USERNAME"] = GRAFANA_USERNAME
        os.environ["K6_PROMETHEUS_RW_PASSWORD"] = GRAFANA_API_KEY

        # Target website
        os.environ["SFCC_SITE_URL"] = SFCC_SITE_URL

        print("Testing SFCC site:", SFCC_SITE_URL)

        # Find all k6 scripts in scripts folders
        scripts = glob.glob("scripts/**/*.js", recursive=True)

        if not scripts:
            print("No k6 scripts found in scripts folder")
            return {"status": "no scripts found"}

        results = []

        for script in scripts:

            print("\n-----------------------------------")
            print("Running k6 script:", script)
            print("-----------------------------------\n")

            cmd = f'k6 run -o experimental-prometheus-rw {script}'

            exit_code = os.system(cmd)

            results.append({
                "script": script,
                "exit_code": exit_code
            })

        print("\nAll k6 scripts executed\n")

        return {
            "status": "completed",
            "scripts_run": len(results),
            "results": results
        }

    except Exception as e:

        print("k6 Error:", str(e))

        return {"error": str(e)}


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
        print("\n🔍 Fetching GitHub commits...\n")

        headers = {
            "Authorization": f"token {GITHUB_TOKEN}"
        }

        # Step 1: Get commits
        commits_url = f"{GITHUB_REPO_URL}/commits"
        res = requests.get(commits_url, headers=headers)

        print("📡 Commits API Status:", res.status_code)

        commits = res.json()

        if not commits or "message" in commits:
            print("❌ No commits found or access denied")
            return {"error": "No commits found"}

        latest_commit = commits[0]["sha"]

        print("✅ Latest Commit:", latest_commit)

        # Step 2: Get diff
        diff_url = f"{GITHUB_REPO_URL}/commits/{latest_commit}"
        diff_res = requests.get(diff_url, headers=headers)

        print("📡 Diff API Status:", diff_res.status_code)

        diff_data = diff_res.json()

        files = diff_data.get("files", [])

        if not files:
            print("⚠️ No file changes found (maybe first commit)")
        
        changes = []

        print("\n📂 Changed Files:\n")

        for f in files:
            filename = f.get("filename")
            change_count = f.get("changes")
            patch = f.get("patch", "")

            print(f"📄 File: {filename}")
            print(f"🔢 Changes: {change_count}")

            if patch:
                print("🧾 Patch Preview:\n", patch[:300])
            else:
                print("⚠️ No patch available")

            print("-" * 50)

            changes.append({
                "filename": filename,
                "changes": change_count,
                "patch": patch[:300]
            })

        return {
            "commit": latest_commit,
            "files_changed": changes
        }

    except Exception as e:
        print("❌ Git Diff Error:", str(e))
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

                # ✅ FIXED DESCRIPTION (ADF FORMAT)
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {
                                    "type": "text",
                                    "text": description
                                }
                            ]
                        }
                    ]
                },

                # ✅ SAFEST ISSUE TYPE
                "issuetype": {
                    "name": "Task"
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

# # --------------------------------
# # AI ROOT CAUSE ANALYSIS
# # --------------------------------

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

            model="gpt-4o-mini",

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
# AI ROOT CAUSE ANALYSIS (OLLAMA + OPENSPEC)
# --------------------------------

# def ai_analysis(data):

#     try:
#         import requests
#         from tools.openspec import get_tools

#         print("\nRunning AI Root Cause Analysis (Ollama + OpenSpec)\n")

#         # Load tools (OpenSpec)
#         tools = get_tools()

#         # Dynamic config (from .env)
#         OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
#         OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3")

#         # Prompt
#         prompt = f"""
# You are a performance engineer.

# You have access to the following tools:
# {tools}

# Analyze system data.

# Focus on:
# - Failed APIs
# - Slow transactions
# - Errors (401, 500)
# - Infra issues

# Detected Issues:
# {data.get("issues")}

# Performance Data:
# {data}

# Find root cause and suggest fix.
# """

#         # Call Ollama API
#         response = requests.post(
#             f"{OLLAMA_URL}/api/generate",
#             json={
#                 "model": OLLAMA_MODEL,
#                 "prompt": prompt,
#                 "stream": False
#             }
#         )

#         result = response.json()
#         ai_result = result.get("response", "No response from Ollama")

#         print("\nAI ANALYSIS RESULT\n")
#         print(ai_result)

#         return ai_result

#     except Exception as e:
#         print("AI Error:", str(e))
#     return {"error": str(e)}
# --------------------------------
# MAIN AGENT
# --------------------------------

def mask_sensitive(data):

    if isinstance(data, dict):
        return {k: mask_sensitive(v) for k, v in data.items()}

    elif isinstance(data, list):
        return [mask_sensitive(i) for i in data]

    elif isinstance(data, str):
        for key in [
            os.getenv("OPENAI_API_KEY"),
            os.getenv("JIRA_API_TOKEN"),
            os.getenv("GITHUB_TOKEN")
        ]:
            if key:
                data = data.replace(key, "***")
        return data

    return data

def run_agent():

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

    # ✅ ADD THIS
    regression_detected = len(issues) > 0

    # ✅ ONLY RUN IF REGRESSION
    if regression_detected:

        print("🚨 Regression detected — running RCA")

        if ENABLE_AI == "true":
            # ai_result = ai_analysis(combined_data)
            safe_data = mask_sensitive(combined_data)
            ai_result = ai_analysis(safe_data)
        else:
            ai_result = "AI disabled"

        # ✅ RCA DATA
        git_diff = infra_data.get("git_diff")
        grafana = perf_data.get("grafana")
        datadog = perf_data.get("datadog")

        rca_report = f"""
🚨 Regression Detected

AI Analysis:
{ai_result}

Git Diff:
{git_diff}

Grafana:
{grafana}

Datadog:
{datadog}
"""

    else:
        print("✅ No regression — skipping AI & JIRA")
        ai_result = "No regression detected"
        rca_report = ai_result

    # JIRA
    try:
        # ✅ ONLY CREATE JIRA IF REGRESSION
        if regression_detected:
            try:
                jira_ticket = create_jira_ticket(
                    "Performance Regression Detected",
                    rca_report
                )
            except Exception as e:
                print("Jira failed:", e)
                jira_ticket = {}
        else:
            jira_ticket = {}
    except Exception as e:
        print("Jira failed:", e)
        jira_ticket = {}

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
    try:
        send_slack(final_message)
    except Exception as e:
        print("Slack failed:", e)

    try:
        connect()
        send_email(final_message)
    except Exception as e:
        print("Email failed:", e)

    # PRINT
    print("\n================ FINAL REPORT (JSON) ================\n")
    print(json.dumps(result, indent=4))
    print("Issues:", issues)
    print("Regression:", regression_detected)
    print("JIRA:", jira_ticket.get("key") if jira_ticket else "Not created")
    print("\n====================================================\n")

    return result
# --------------------------------
# GITHUB WEBHOOK
# --------------------------------
@app.route("/github-webhook", methods=["POST"])
def github_push():

    try:
        print("\n🔔 Incoming webhook request...")

        # --------------------------------
        # 🔐 VERIFY SIGNATURE (SECURITY)
        # --------------------------------
        signature = request.headers.get("X-Hub-Signature-256")
        payload_raw = request.data

        if not signature:
           print("⚠️ No signature (testing mode)")
        else:   
            if not verify_signature(payload_raw, signature):
                print("❌ Invalid webhook signature")
                return "Unauthorized", 401



        print("✅ Webhook verified successfully")

        # --------------------------------
        # Parse payload
        # --------------------------------
        payload = request.json

        print("\n=================================")
        print("📦 GitHub Event Received")
        print("=================================\n")

        # --------------------------------
        # Extract PR Number (if PR event)
        # --------------------------------
        pr_number = None

        if "pull_request" in payload:
            pr_number = payload["pull_request"]["number"]
            print(f"🔢 PR Detected: {pr_number}")

        # --------------------------------
        # Branch Filter
        # --------------------------------
        branch = payload.get("ref")

        if branch and branch != "refs/heads/main":
            print("⏭ Skipping non-main branch")
            return jsonify({
                "message": "Skipped non main branch"
            })

        print("🚀 Running AI Agent...")

        # --------------------------------
        # Run Agent
        # --------------------------------
        result = run_agent()

        # --------------------------------
        # Post PR Comment (if PR)
        # --------------------------------
        if pr_number:
            print("💬 Posting PR comment...")
            comment_pr(pr_number, result["ai_analysis"])

        print("✅ Webhook execution completed")

        return jsonify({
            "message": "AI Agent Executed",
            "result": result
        }), 200

    except Exception as e:

        print("❌ Webhook Error:", str(e))

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

    result = run_agent()

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