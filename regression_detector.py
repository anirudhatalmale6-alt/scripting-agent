"""
regression_detector.py
───────────────────────
Compares previous vs current run metrics and classifies the result.
Thresholds are loaded from .perf/thresholds.yaml when available,
falling back to sensible defaults.
"""

import json
from typing import Optional
from agent.perf_policy import load_policy, get_thresholds, PerfPolicy

# ── Cached policy — loaded once, not on every regression check ────────────────
_policy: Optional[PerfPolicy] = None


def _get_policy() -> PerfPolicy:
    global _policy
    if _policy is None:
        _policy = load_policy()
    return _policy


def detect_regression(previous: dict, current: dict, domain: str = "default") -> dict:
    """
    Compare two metric snapshots and classify the regression.

    Parameters
    ----------
    previous : dict with keys: latency, error_rate
    current  : dict with keys: latency, error_rate
    domain   : domain name for policy threshold lookup (e.g. 'checkout', 'auth')

    Returns
    -------
    dict with regression_detected, classification, deltas, thresholds_used
    """
    policy = _get_policy()
    t = get_thresholds(domain, policy)

    prev_latency = previous.get("latency", 0)
    curr_latency = current.get("latency", 0)
    prev_error   = previous.get("error_rate", 0)
    curr_error   = current.get("error_rate", 0)

    latency_change_pct = (
        ((curr_latency - prev_latency) / prev_latency) * 100
        if prev_latency > 0 else 0
    )
    error_delta = curr_error - prev_error

    # Classification using policy thresholds
    regression = False
    classification = "no_regression"

    latency_exceeded  = curr_latency > t.p95_ms
    latency_warn      = curr_latency > t.warn_p95_ms if hasattr(t, "warn_p95_ms") else curr_latency > t.p95_ms * 1.5
    error_exceeded    = curr_error > t.error_rate
    error_warn        = curr_error > t.warn_error_rate if hasattr(t, "warn_error_rate") else curr_error > t.error_rate * 2

    if latency_exceeded or error_exceeded:
        regression = True
        classification = "severe_regression"
    elif latency_warn or error_warn or latency_change_pct > 20 or error_delta > 1:
        regression = True
        classification = "minor_regression"

    return {
        "domain":                domain,
        "regression_detected":   regression,
        "classification":        classification,
        "previous_latency":      prev_latency,
        "current_latency":       curr_latency,
        "latency_change_percent": round(latency_change_pct, 2),
        "previous_error_rate":   prev_error,
        "current_error_rate":    curr_error,
        "error_delta":           round(error_delta, 4),
        "thresholds_used": {
            "p95_ms":       t.p95_ms,
            "error_rate":   t.error_rate,
            "domain":       domain,
            "from_policy":  policy.loaded,
        },
    }


if __name__ == "__main__":
    previous_test = {"latency": 300, "error_rate": 1}
    current_test  = {"latency": 480, "error_rate": 3}

    result = detect_regression(previous_test, current_test, domain="checkout")
    print("\nRegression Analysis\n")
    print(json.dumps(result, indent=4))
