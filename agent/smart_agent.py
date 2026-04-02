from agent.controller import call_tool
from agent.regression_engine import detect_regression
from agent.ai_root_cause import ai_root_cause


def run_ai_agent():

    print("\nRunning AI Performance Agent\n")

    k6 = call_tool("k6_test")

    speedcurve = call_tool("speedcurve")

    grafana = call_tool("grafana_dashboards")

    data = {
        "k6": k6,
        "speedcurve": speedcurve,
        "grafana": grafana
    }

    regression = detect_regression(k6, speedcurve)

    print("\nRegression Result:")
    print(regression)

    if regression["regression"]:

        print("\nRunning AI Root Cause Analysis\n")

        analysis = ai_root_cause(data)

        print("\nAI Root Cause:\n")
        print(analysis)

    else:

        print("\nNo performance regression detected")


if __name__ == "__main__":
    run_ai_agent()