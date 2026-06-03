# ai_report.py

from agent.llm_provider import llm_chat


def generate_ai_report(data):

    prompt = f"""
You are an AI performance engineer.

Analyze the following performance regression data.

Provide:

1. Issue summary
2. Possible root cause
3. Recommendations for engineers

Performance Data:
{data}
"""

    try:
        return llm_chat(prompt, temperature=0.2,
                        system="You are a senior performance engineer.")
    except Exception as e:
        return f"AI Report Error: {str(e)}"


# Sample regression data
sample_data = {
    "previous_latency": 300,
    "current_latency": 480,
    "latency_change_percent": 60.0,
    "previous_error_rate": 1.0,
    "current_error_rate": 3.0,
    "regression_detected": True
}


if __name__ == "__main__":

    print("\n==============================")
    print("AI PERFORMANCE REPORT")
    print("==============================\n")

    report = generate_ai_report(sample_data)

    print(report)
