"""
agent/sanitizer.py
------------------
PII sanitization layer - redacts sensitive data before sending to LLM,
restores originals in the response. Prevents code, credentials, and PII
from being exposed to external LLM providers.

Configurable via config/sanitization_rules.yaml.
"""

import re
import os
import logging
from dataclasses import dataclass, field
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class RedactionMapping:
    original: str
    placeholder: str
    pattern_name: str


@dataclass
class SanitizationResult:
    sanitized_text: str
    redactions: list[RedactionMapping] = field(default_factory=list)
    redaction_count: int = 0


class PIISanitizer:
    def __init__(self, config_path: str = None):
        self.patterns: list[dict] = []
        self.exclusion_files: list[str] = []
        self.exclusion_dirs: list[str] = []
        if config_path is None:
            config_path = os.getenv(
                "SANITIZATION_CONFIG_PATH",
                "config/sanitization_rules.yaml",
            )
        self._load_config(config_path)

    def _load_config(self, config_path: str):
        try:
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)

            for p in config.get("patterns", []):
                if p.get("enabled", True):
                    self.patterns.append({
                        "name": p["name"],
                        "regex": re.compile(p["regex"]),
                        "replacement": p["replacement"],
                    })

            for p in config.get("custom_patterns", []):
                if p.get("enabled", True):
                    self.patterns.append({
                        "name": p["name"],
                        "regex": re.compile(p["regex"]),
                        "replacement": p.get("replacement", "REDACTED_CUSTOM_{n}"),
                    })

            exclusions = config.get("exclusions", {})
            self.exclusion_files = exclusions.get("files", [])
            self.exclusion_dirs = exclusions.get("directories", [])

            logger.info(f"Loaded {len(self.patterns)} sanitization patterns")
        except FileNotFoundError:
            logger.warning(f"Sanitization config not found at {config_path}, using defaults")
            self._load_defaults()

    def _load_defaults(self):
        self.patterns = [
            {"name": "email", "regex": re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'), "replacement": "REDACTED_EMAIL_{n}"},
            {"name": "api_key", "regex": re.compile(r'(?i)(?:api[_-]?key|secret[_-]?key)\s*[=:]\s*["\']?([a-zA-Z0-9_\-]{16,})["\']?'), "replacement": "REDACTED_API_KEY_{n}"},
            {"name": "password", "regex": re.compile(r'(?i)(?:password|passwd|pwd)\s*[=:]\s*["\']?([^\s"\']+)["\']?'), "replacement": "REDACTED_PASSWORD_{n}"},
            {"name": "ip_address", "regex": re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'), "replacement": "REDACTED_IP_{n}"},
            {"name": "connection_string", "regex": re.compile(r'(?i)(?:jdbc:|mongodb://|mysql://|postgres://|redis://)[^\s"\'<>]+'), "replacement": "REDACTED_CONN_STRING_{n}"},
        ]

    def sanitize(self, text: str) -> SanitizationResult:
        redactions: list[RedactionMapping] = []
        sanitized = text
        counter = 0

        for pattern in self.patterns:
            matches = list(pattern["regex"].finditer(sanitized))
            for match in reversed(matches):
                counter += 1
                original = match.group(0)
                placeholder = pattern["replacement"].format(n=counter)
                redactions.append(RedactionMapping(
                    original=original,
                    placeholder=placeholder,
                    pattern_name=pattern["name"],
                ))
                sanitized = sanitized[:match.start()] + placeholder + sanitized[match.end():]

        if counter > 0:
            logger.info(f"Sanitized {counter} PII items")
        return SanitizationResult(
            sanitized_text=sanitized,
            redactions=redactions,
            redaction_count=counter,
        )

    def desanitize(self, text: str, redactions: list[RedactionMapping]) -> str:
        result = text
        for r in redactions:
            result = result.replace(r.placeholder, r.original)
        return result

    def should_exclude_file(self, filepath: str) -> bool:
        import fnmatch
        for pattern in self.exclusion_files:
            if fnmatch.fnmatch(filepath, pattern):
                return True
        for d in self.exclusion_dirs:
            if d in filepath.split("/"):
                return True
        return False


_sanitizer: Optional[PIISanitizer] = None


def get_sanitizer() -> Optional[PIISanitizer]:
    global _sanitizer
    enabled = os.getenv("SANITIZATION_ENABLED", "true").lower() == "true"
    if not enabled:
        return None
    if _sanitizer is None:
        _sanitizer = PIISanitizer()
    return _sanitizer


def sanitize_for_llm(text: str) -> SanitizationResult:
    sanitizer = get_sanitizer()
    if sanitizer is None:
        return SanitizationResult(sanitized_text=text)
    return sanitizer.sanitize(text)


def desanitize_from_llm(text: str, redactions: list[RedactionMapping]) -> str:
    sanitizer = get_sanitizer()
    if sanitizer is None:
        return text
    return sanitizer.desanitize(text, redactions)
