"""
agent/regression_engine.py
──────────────────────────
Detects regressions from k6 results.
Thresholds are loaded from .perf/thresholds.yaml — no hardcoded values.
"""

from agent.perf_policy import load_policy, get_thresholds, PerfPolicy
from typing import Optional

# ── Cached policy — loaded once, not on every regression check ────────────────
_policy: Optional[PerfPolicy] = None


def _get_policy() -> PerfPolicy:
    global _policy
    if _policy is None:
        _policy = load_policy()
    return _policy


def detect_regression(k6_data: dict, speedcurve_data: dict = None, domain: str = "default") -> dict:
    """
    Evaluate k6 results against policy thresholds.

    Parameters
    ----------
    k6_data         : result dict from k6 tool (latency, error_rate)
    speedcurve_data : unused — kept for backward compat with existing callers
    domain          : domain name for threshold lookup (e.g. 'checkout')

    Returns
    -------
    dict with regression bool, issues list, classification, thresholds_used
    """
    policy = _get_policy()
    t = get_thresholds(domain, policy)

    result = {
        "regression":     False,
        "classification": "no_regression",
        "issues":         [],
        "thresholds_used": {
            "p95_ms":      t.p95_ms,
            "error_rate":  t.error_rate,
            "domain":      domain,
            "from_policy": policy.loaded,
        },
    }

    try:
        k6_latency = k6_data.get("latency", 0)
        if k6_latency > t.p95_ms:
            result["regression"] = True
            result["issues"].append(
                f"k6 latency {k6_latency}ms exceeds p95 threshold {t.p95_ms}ms "
                f"(domain: {domain})"
            )

        k6_error_rate = k6_data.get("error_rate", 0)
        if k6_error_rate > t.error_rate:
            result["regression"] = True
            result["issues"].append(
                f"k6 error rate {k6_error_rate:.2%} exceeds threshold {t.error_rate:.2%} "
                f"(domain: {domain})"
            )

    except Exception as e:
        result["issues"].append(f"Regression check error: {e}")

    if result["regression"]:
        result["classification"] = (
            "severe_regression" if len(result["issues"]) >= 2 else "minor_regression"
        )

    return result
