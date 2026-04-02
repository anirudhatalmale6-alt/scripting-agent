def detect_regression(old_latency, new_latency):

    if new_latency > old_latency * 1.2:

        return {
            "regression": True,
            "reason": "Latency increased"
        }

    return {
        "regression": False
    }