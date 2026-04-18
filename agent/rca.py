"""
agent/rca.py
────────────
Thin wrapper — delegates to regression_engine for policy-driven detection.
Kept for backward compatibility with any callers using agent.rca.detect_regression.
"""
from agent.regression_engine import detect_regression  # noqa: F401