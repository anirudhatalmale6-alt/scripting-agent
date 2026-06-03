from agent.llm_provider import llm_chat


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

    return llm_chat(prompt, temperature=0.2)
