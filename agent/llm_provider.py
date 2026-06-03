"""
agent/llm_provider.py
---------------------
Swappable LLM abstraction layer.
Set LLM_PROVIDER=ollama or LLM_PROVIDER=openai in .env to switch.

All AI calls in the codebase go through get_llm_client() and llm_chat().
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_client = None
_provider = None


def _detect_provider() -> str:
    provider = os.getenv("LLM_PROVIDER", "").lower().strip()
    if provider in ("ollama", "openai"):
        return provider
    key = os.getenv("OPENAI_API_KEY", "")
    if key and key.startswith("sk-") and "xxxxxx" not in key:
        return "openai"
    return "ollama"


def get_provider() -> str:
    global _provider
    if _provider is None:
        _provider = _detect_provider()
    return _provider


def get_model() -> str:
    provider = get_provider()
    if provider == "ollama":
        return os.getenv("OLLAMA_MODEL", "llama3.1")
    return os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def get_llm_client():
    global _client
    if _client is not None:
        return _client

    provider = get_provider()

    if provider == "ollama":
        from openai import OpenAI
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/v1"
        _client = OpenAI(base_url=base_url, api_key="ollama")
        logger.info(f"LLM Provider: Ollama at {base_url}")
    else:
        from openai import OpenAI
        key = os.getenv("OPENAI_API_KEY", "")
        if key and key.startswith("sk-") and "xxxxxx" not in key:
            _client = OpenAI(api_key=key)
        else:
            _client = OpenAI(api_key="placeholder")
        logger.info(f"LLM Provider: OpenAI ({get_model()})")

    return _client


def llm_available() -> bool:
    provider = get_provider()
    if provider == "ollama":
        try:
            import urllib.request
            base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
            urllib.request.urlopen(base, timeout=2)
            return True
        except Exception:
            return False
    else:
        key = os.getenv("OPENAI_API_KEY", "")
        return bool(key) and key.startswith("sk-") and "xxxxxx" not in key


def llm_chat(prompt: str, temperature: float = 0.2, system: str = None) -> str:
    from agent.sanitizer import sanitize_for_llm, desanitize_from_llm

    san_result = sanitize_for_llm(prompt)
    sanitized_prompt = san_result.sanitized_text
    if san_result.redaction_count > 0:
        logger.info(f"Sanitized {san_result.redaction_count} PII items before LLM call")

    client = get_llm_client()
    model = get_model()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": sanitized_prompt})

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
        )
        response_text = resp.choices[0].message.content.strip()
        return desanitize_from_llm(response_text, san_result.redactions)
    except Exception as e:
        logger.error(f"LLM call failed ({get_provider()}/{model}): {e}")
        raise


def sanitize_prompt(prompt: str):
    from agent.sanitizer import sanitize_for_llm
    return sanitize_for_llm(prompt)


def restore_response(text: str, redactions) -> str:
    from agent.sanitizer import desanitize_from_llm
    return desanitize_from_llm(text, redactions)


def reset_client():
    global _client, _provider
    _client = None
    _provider = None
