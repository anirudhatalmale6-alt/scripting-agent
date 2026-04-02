def detect_regression(k6_data, speedcurve_data):

    result = {
        "regression": False,
        "issues": []
    }

    try:

        k6_latency = k6_data.get("latency", 0)
        speedcurve_load = speedcurve_data.get("load_time", 0)

        if k6_latency > 200:
            result["regression"] = True
            result["issues"].append(
                f"k6 latency high: {k6_latency}ms"
            )

        if speedcurve_load > 3000:
            result["regression"] = True
            result["issues"].append(
                f"SpeedCurve load time high: {speedcurve_load}ms"
            )

    except Exception as e:

        result["issues"].append(str(e))

    return result