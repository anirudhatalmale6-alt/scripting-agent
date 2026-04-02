"""
email_service.py
────────────────
Sends alert emails via SMTP.
Skips silently when SMTP credentials are not configured so the agent
doesn't crash on environments where email is not set up.
"""

import os
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

SMTP_SERVER = os.getenv("SMTP_SERVER", "")
SMTP_PORT   = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER   = os.getenv("SMTP_USER", "")
SMTP_PASS   = os.getenv("SMTP_PASS", "")
EMAIL_TO    = os.getenv("EMAIL_TO", "")


def send_email(message: str) -> bool:
    """
    Send an alert email.
    Returns True on success, False when config is missing or send fails.
    """
    if not all([SMTP_SERVER, SMTP_USER, SMTP_PASS, EMAIL_TO]):
        print("[email] Skipped — SMTP credentials not configured in .env")
        return False

    try:
        msg = MIMEText(message)
        msg["Subject"] = "AI Performance Agent — Regression Alert"
        msg["From"]    = SMTP_USER
        msg["To"]      = EMAIL_TO

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, EMAIL_TO, msg.as_string())

        print("[email] Alert sent to", EMAIL_TO)
        return True

    except Exception as e:
        print(f"[email] Failed to send: {e}")
        return False
