"""
run_local.py
────────────
Run the full AI Performance Agent stack locally without Docker.

Starts:
  - MCP Server        → http://localhost:5002
  - Test Trigger Agent → http://localhost:5001
  - RCA Agent          → http://localhost:5000

Prerequisites (install once):
  pip install -r requirements.txt
  # k6:     https://k6.io/docs/get-started/installation/
  # Java 11: https://adoptium.net
  # Maven:   https://maven.apache.org/download.cgi

Usage:
  python run_local.py

Stop: Ctrl+C
"""

import os
import sys
import subprocess
import time
import signal
import threading

# ── Patch .env for local run ──────────────────────────────────────────────────
os.environ.setdefault("USE_SANDBOX",          "false")
os.environ.setdefault("MCP_URL",              "http://localhost:5002")
os.environ.setdefault("AUTO_TEST_ON_STARTUP", "false")

# Load .env
from dotenv import load_dotenv
load_dotenv()

# Override Docker-internal URLs with localhost
sfcc = os.getenv("SFCC_SITE_URL", "http://localhost:8000")
if "mock-app" in sfcc or "api:" in sfcc:
    os.environ["SFCC_SITE_URL"] = "http://localhost:8000"
    print(f"[run_local] SFCC_SITE_URL → http://localhost:8000")

k6_rw = os.getenv("K6_PROMETHEUS_RW_SERVER_URL", "")
if "prometheus:" in k6_rw:
    os.environ["K6_PROMETHEUS_RW_SERVER_URL"] = ""
    print("[run_local] Prometheus remote-write disabled (not running locally)")

processes = []


def start(name, cmd, cwd=None):
    print(f"[run_local] Starting {name}...")
    p = subprocess.Popen(
        [sys.executable] + cmd,
        cwd=cwd or os.getcwd(),
        env=os.environ.copy(),
    )
    processes.append((name, p))
    return p


def check_prereqs():
    missing = []
    for tool, url in [
        ("k6",   "https://k6.io/docs/get-started/installation/"),
        ("java", "https://adoptium.net"),
        ("mvn",  "https://maven.apache.org/download.cgi"),
    ]:
        try:
            subprocess.run([tool, "--version" if tool != "java" else "-version"],
                           capture_output=True)
        except FileNotFoundError:
            missing.append(f"{tool}  →  {url}")

    if missing:
        print("\n[run_local] ⚠️  Optional tools not found (those tests will be skipped):")
        for m in missing:
            print(f"  - {m}")
        print()


def shutdown(sig=None, frame=None):
    print("\n[run_local] Shutting down...")
    for name, p in processes:
        print(f"[run_local] Stopping {name}...")
        p.terminate()
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("=" * 60)
    print("  AI Performance Agent — Local Run (no Docker)")
    print("=" * 60)

    check_prereqs()

    # Start services with a small delay between each
    start("MCP Server",          ["mcp_server.py"])
    time.sleep(2)
    start("Test Trigger Agent",  ["test_trigger_agent.py"])
    time.sleep(1)
    start("RCA Agent",           ["rca_agent.py"])

    print()
    print("=" * 60)
    print("  All services started:")
    print("  MCP Server         → http://localhost:5002")
    print("  Test Trigger Agent → http://localhost:5001")
    print("  RCA Agent          → http://localhost:5000")
    print()
    print("  Webhook URL for GitHub:")
    print("  http://localhost:5001/github-webhook")
    print()
    print("  To expose publicly (for GitHub webhooks):")
    print("  ngrok http 5001")
    print()
    print("  Press Ctrl+C to stop all services")
    print("=" * 60)

    # Keep alive — monitor processes
    while True:
        time.sleep(5)
        for name, p in processes:
            if p.poll() is not None:
                print(f"[run_local] ⚠️  {name} exited (code {p.returncode}) — restarting...")
                processes.remove((name, p))
                if name == "MCP Server":
                    start("MCP Server", ["mcp_server.py"])
                elif name == "Test Trigger Agent":
                    start("Test Trigger Agent", ["test_trigger_agent.py"])
                elif name == "RCA Agent":
                    start("RCA Agent", ["rca_agent.py"])
                break
