from agent.llm_provider import llm_chat


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

    return llm_chat(prompt, temperature=0.2)
