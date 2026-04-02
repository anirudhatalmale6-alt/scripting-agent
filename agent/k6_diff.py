def compare_k6_results(old, new):

    old_latency = old["latency"]
    new_latency = new["latency"]

    diff = new_latency - old_latency

    if diff > 50:

        return {
            "regression": True,
            "message": "Latency increased"
        }

    return {
        "regression": False
    }