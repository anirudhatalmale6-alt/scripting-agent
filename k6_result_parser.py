import json


def parse_k6_results(file_path):

    with open(file_path, "r") as f:
        data = json.load(f)

    latency = data["metrics"]["http_req_duration"]["avg"]
    error_rate = data["metrics"]["http_req_failed"]["rate"] * 100

    return {
        "latency": latency,
        "error_rate": error_rate
    }


def detect_regression(previous, current):

    latency_change = ((current["latency"] - previous["latency"]) / previous["latency"]) * 100
    error_change = current["error_rate"] - previous["error_rate"]

    regression = False

    if latency_change > 20:
        regression = True

    if error_change > 2:
        regression = True

    return {
        "previous_latency": previous["latency"],
        "current_latency": current["latency"],
        "latency_change_percent": round(latency_change, 2),
        "previous_error_rate": previous["error_rate"],
        "current_error_rate": current["error_rate"],
        "regression_detected": regression
    }


if __name__ == "__main__":

    previous = parse_k6_results("k6_previous.json")
    current = parse_k6_results("k6_current.json")

    result = detect_regression(previous, current)

    print("\nRegression Detection Result\n")
    print(json.dumps(result, indent=4))