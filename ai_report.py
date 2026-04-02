# ai_report.py

import os
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables
load_dotenv()

# Get OpenAI API key
api_key = os.getenv("OPENAI_API_KEY")

if not api_key:
    raise ValueError("OPENAI_API_KEY not found in environment variables")

# Create OpenAI client
client = OpenAI(api_key=api_key)
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


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
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are a senior performance engineer."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.2
        )

        return response.choices[0].message.content

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