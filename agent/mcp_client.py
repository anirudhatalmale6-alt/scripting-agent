"""
agent/mcp_client.py
───────────────────
Thin HTTP client for the MCP server.
Any agent imports this instead of calling tools directly.

Usage:
    from agent.mcp_client import call_tool, list_tools

    result = call_tool("k6")
    result = call_tool("jira", {"summary": "Regression", "description": "p95 > 2s"})
    result = call_tool("github_commits", {"per_page": 10})
    tools  = list_tools()
"""

import logging
import os
import requests

log = logging.getLogger(__name__)

MCP_BASE = os.getenv("MCP_URL", "http://mcp-server:5002")


def call_tool(tool_name: str, params: dict = None) -> dict:
    """Call a tool on the MCP server. Returns the result dict."""
    url = f"{MCP_BASE}/tools/{tool_name}"
    try:
        r = requests.post(url, json=params or {}, timeout=300)
        r.raise_for_status()
        return r.json().get("result", {})
    except requests.exceptions.ConnectionError:
        log.warning(f"[mcp_client] MCP server unreachable at {MCP_BASE} — tool: {tool_name}")
        return {"status": "skipped", "reason": "MCP server not reachable"}
    except Exception as e:
        log.error(f"[mcp_client] call_tool({tool_name}) failed: {e}")
        return {"error": str(e)}


def list_tools() -> dict:
    """Discover all tools exposed by the MCP server."""
    try:
        r = requests.get(f"{MCP_BASE}/tools", timeout=10)
        r.raise_for_status()
        return r.json().get("tools", {})
    except Exception as e:
        log.error(f"[mcp_client] list_tools failed: {e}")
        return {}
