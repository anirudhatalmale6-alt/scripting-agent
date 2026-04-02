"""
agent/controller.py
───────────────────
Calls MCP tools OR falls back to direct local tool implementations when
the MCP server is unavailable (placeholder URL, network error, etc.).

Previously this crashed silently when MCP_URL was a placeholder — now
every tool has a local fallback so the agent keeps running.
"""

import os
import requests
from dotenv import load_dotenv
from agent.analysis import analyze_results

load_dotenv()

MCP_URL = os.getenv("MCP_URL", "")
TOKEN = os.getenv("TOKEN", "")

_headers = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

# ── Local fallbacks (used when MCP is unreachable) ────────────────────────────

def _local_k6():
    from tools.k6 import run_k6_test
    return run_k6_test()


def _local_speedcurve():
    try:
        from tools.speedcurve import get_speedcurve_data
        return get_speedcurve_data()
    except Exception as e:
        return {"error": str(e), "source": "speedcurve"}


def _local_datadog():
    try:
        from tools.datadog import get_datadog_metrics
        return get_datadog_metrics()
    except Exception as e:
        return {"error": str(e), "source": "datadog"}


def _local_grafana():
    try:
        from tools.grafana import get_dashboards
        return get_dashboards()
    except Exception as e:
        return {"error": str(e), "source": "grafana"}


def _local_github_commits():
    """Return a placeholder when GitHub tool is not wired via MCP."""
    return {"status": "no_mcp", "commits": []}


# Map tool names to their local fallback functions
_FALLBACKS = {
    "k6_test":          _local_k6,
    "speedcurve":       _local_speedcurve,
    "datadog_metrics":  _local_datadog,
    "grafana_dashboards": _local_grafana,
    "github_commits":   _local_github_commits,
}


# ── Public API ────────────────────────────────────────────────────────────────

def call_tool(tool: str) -> dict:
    """
    Try the MCP server first; fall back to the local implementation if MCP
    is unreachable or returns an error.
    """
    # Skip MCP entirely when URL is a placeholder or empty
    mcp_available = MCP_URL and "xxxxx" not in MCP_URL and MCP_URL.startswith("http")

    if mcp_available:
        try:
            r = requests.post(
                MCP_URL,
                json={"tool": tool},
                headers=_headers,
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            print(f"[controller] MCP tool '{tool}' → OK")
            return data
        except Exception as e:
            print(f"[controller] MCP tool '{tool}' failed ({e}), using local fallback")

    # Local fallback
    fallback = _FALLBACKS.get(tool)
    if fallback:
        print(f"[controller] Running local fallback for '{tool}'")
        try:
            return fallback()
        except Exception as e:
            return {"error": str(e), "tool": tool}

    return {"error": f"Unknown tool: {tool}"}


def run_performance_analysis() -> dict:
    """Orchestrate a full performance analysis run."""
    print("\n[controller] Starting performance analysis\n")

    k6 = call_tool("k6_test")
    speedcurve = call_tool("speedcurve")

    data = {"k6": k6, "speedcurve": speedcurve}

    print("\n[controller] Running AI analysis...\n")
    try:
        ai_report = analyze_results(data)
        print("\n[controller] AI RCA:\n", ai_report)
    except Exception as e:
        ai_report = f"AI analysis failed: {e}"
        print("[controller]", ai_report)

    return {"data": data, "ai_report": ai_report}


if __name__ == "__main__":
    run_performance_analysis()
