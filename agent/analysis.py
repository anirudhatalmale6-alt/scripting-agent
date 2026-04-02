import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

def analyze_results(data):

    prompt = f"""
You are a senior performance engineer AI.

Analyze the following performance testing results.

Data:
{data}

Tasks:
1. Detect if there is a performance regression.
2. Identify possible root causes.
3. Suggest what engineers should fix.

Provide a clear explanation in natural language.
"""

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    )

    return response.choices[0].message.content