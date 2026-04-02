"""
slack_service.py
────────────────
Posts alert messages to a Slack channel via incoming webhook.
Skips silently when the webhook URL is not configured or is a placeholder.
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK", "")


def send_slack(message: str) -> bool:
    """
    Post a message to Slack.
    Returns True on success, False when config is missing or post fails.
    """
    if not SLACK_WEBHOOK or "xxxx" in SLACK_WEBHOOK:
        print("[slack] Skipped — SLACK_WEBHOOK not configured in .env")
        return False

    try:
        resp = requests.post(
            SLACK_WEBHOOK,
            json={"text": message},
            timeout=10,
        )
        if resp.status_code == 200:
            print("[slack] Message sent")
            return True
        else:
            print(f"[slack] Unexpected status: {resp.status_code} — {resp.text}")
            return False

    except Exception as e:
        print(f"[slack] Failed to send: {e}")
        return False
