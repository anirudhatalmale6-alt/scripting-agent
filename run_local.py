"""
run_local.py
────────────
Run the AI Scripting Agent locally without Docker.

Starts:
  - Scripting Agent → http://localhost:5001

Prerequisites (install once):
  pip install -r requirements.txt
  # k6 (optional): https://k6.io/docs/get-started/installation/

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
os.environ.setdefault("AUTO_TEST_ON_STARTUP", "false")

# Load .env
from dotenv import load_dotenv
load_dotenv()

# Override Docker-internal URLs with localhost
sfcc = os.getenv("SFCC_SITE_URL", "http://localhost:8000")
if "host.docker.internal" in sfcc:
    os.environ["SFCC_SITE_URL"] = "http://localhost:8000"
    print(f"[run_local] SFCC_SITE_URL → http://localhost:8000")

# For local run, LOCAL_REPO_PATH should point to actual path on disk
local_repo = os.getenv("LOCAL_REPO_PATH", "")
if local_repo and not os.path.isabs(local_repo):
    abs_path = os.path.abspath(local_repo)
    os.environ["LOCAL_REPO_PATH"] = abs_path
    print(f"[run_local] LOCAL_REPO_PATH → {abs_path}")

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
    ]:
        try:
            subprocess.run([tool, "--version"], capture_output=True)
        except FileNotFoundError:
            missing.append(f"{tool}  →  {url}")

    if missing:
        print("\n[run_local] Optional tools not found (those tests will be skipped):")
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
    print("  AI Scripting Agent — Local Run (no Docker)")
    print("=" * 60)

    check_prereqs()

    mode = "LOCAL" if os.getenv("LOCAL_REPO_PATH") else "GITHUB"
    print(f"\n  Mode: {mode}")
    if mode == "LOCAL":
        print(f"  Source: {os.getenv('LOCAL_REPO_PATH')}")
    print()

    start("Scripting Agent", ["test_trigger_agent.py"])

    print()
    print("=" * 60)
    print("  Scripting Agent → http://localhost:5001")
    print()
    print("  Endpoints:")
    print("  POST /scan       → trigger source code scan")
    print("  POST /run-tests  → run generated test scripts")
    print("  POST /self-heal  → fix failing scripts via AI")
    print("  GET  /            → health check")
    print()
    print("  Press Ctrl+C to stop")
    print("=" * 60)

    # Keep alive — monitor process
    while True:
        time.sleep(5)
        for name, p in processes:
            if p.poll() is not None:
                print(f"[run_local] {name} exited (code {p.returncode}) — restarting...")
                processes.remove((name, p))
                start("Scripting Agent", ["test_trigger_agent.py"])
                break
