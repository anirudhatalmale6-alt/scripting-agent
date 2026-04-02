import os
from openai import OpenAI

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def ai_root_cause(data):

    prompt = f"""
You are a senior performance engineer.

Analyze the performance regression and find the root cause.

Data:
{data}

Explain:
- what failed
- possible root cause
- recommended fix
"""

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "user", "content": prompt}
        ]
    )

    return response.choices[0].message.content