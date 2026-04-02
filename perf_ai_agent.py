import requests
import os
from dotenv import load_dotenv
from agent.analysis import analyze_results

load_dotenv()

# --------------------------------
# MCP SERVER CONFIG
# --------------------------------

MCP_URL = os.getenv("MCP_URL")
TOKEN = os.getenv("TOKEN")


# --------------------------------
# MCP TOOL CALL FUNCTION
# --------------------------------

def call_tool(tool):

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

    print(f"\nTool: {tool}")
    print(data)

    return data


# --------------------------------
# PERFORMANCE TEST RUN
# --------------------------------

def run_performance_tests():

    k6 = call_tool("k6_test")

    speedcurve = call_tool("speedcurve")

    grafana = call_tool("grafana_dashboards")

    return {
        "k6": k6,
        "speedcurve": speedcurve,
        "grafana": grafana
    }


# --------------------------------
# ROOT CAUSE ANALYSIS
# --------------------------------

def perform_rca():

    datadog = call_tool("datadog_metrics")

    commits = call_tool("github_commits")

    return {
        "datadog": datadog,
        "commits": commits
    }


# --------------------------------
# AI AGENT MAIN LOGIC
# --------------------------------

def perf_ai_agent(event):

    if event == "code_push":

        print("\nCode push detected")

        perf_data = run_performance_tests()

        infra_data = perform_rca()

        combined = {
            "performance": perf_data,
            "infra": infra_data
        }

        print("\nRunning AI Analysis...\n")

        result = analyze_results(combined)

        print("\nAI Root Cause Analysis:\n")
        print(result)

    elif event == "manual_trigger":

        print("\nManual performance test\n")

        run_performance_tests()

    else:

        print("Unknown event")


# --------------------------------
# MAIN
# --------------------------------

if __name__ == "__main__":

    event = input("Enter event (code_push / manual_trigger): ")

    perf_ai_agent(event)