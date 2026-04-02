import json


def detect_regression(previous, current):

    report = {}

    prev_latency = previous["latency"]
    curr_latency = current["latency"]

    prev_error = previous["error_rate"]
    curr_error = current["error_rate"]

    latency_change = ((curr_latency - prev_latency) / prev_latency) * 100
    error_change = curr_error - prev_error

    regression = False

    if latency_change > 20:
        regression = True

    if error_change > 2:
        regression = True

    report["previous_latency"] = prev_latency
    report["current_latency"] = curr_latency
    report["latency_change_percent"] = round(latency_change, 2)

    report["previous_error_rate"] = prev_error
    report["current_error_rate"] = curr_error

    report["regression_detected"] = regression

    return report


if __name__ == "__main__":

    previous_test = {
        "latency": 300,
        "error_rate": 1
    }

    current_test = {
        "latency": 480,
        "error_rate": 3
    }

    result = detect_regression(previous_test, current_test)

    print("\nRegression Analysis\n")
    print(json.dumps(result, indent=4))