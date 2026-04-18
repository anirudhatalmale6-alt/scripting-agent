"""
agent/perf_policy.py
────────────────────
Loads and exposes the repo-level performance policy layer from .perf/

Policy files drive agent behaviour at runtime — thresholds, mappings,
execution profiles, and generation standards are read from the repo
instead of being hardcoded.

Public API
──────────
  load_policy(repo_root)          -> PerfPolicy
  get_domain_for_file(path, policy) -> str | None
  get_thresholds(domain, policy)  -> ThresholdConfig
  get_profile(trigger, policy)    -> ProfileConfig | None
  get_generation_rules(policy)    -> str
  get_routing_rules(policy)       -> str
"""

import fnmatch
import logging
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class ThresholdConfig:
    p95_ms: int = 2000
    warn_p95_ms: int = 3000
    error_rate: float = 0.05
    warn_error_rate: float = 0.10
    throughput_rps: int = 10
    risk: str = "low"


@dataclass
class MappingEntry:
    name: str
    paths: List[str]
    affects_k6: List[str]
    affects_selenium: List[str]
    affects_loadrunner: List[str]
    risk: str = "low"


@dataclass
class ProfileConfig:
    name: str
    k6_vus: int = 10
    k6_duration: str = "30s"
    selenium_suite: str = "smoke"
    loadrunner_enabled: bool = False
    update_baseline_on_green: bool = False


@dataclass
class PerfPolicy:
    """Container for all loaded policy data."""
    mappings: List[MappingEntry] = field(default_factory=list)
    thresholds: Dict[str, ThresholdConfig] = field(default_factory=dict)
    profiles: Dict[str, ProfileConfig] = field(default_factory=dict)
    generation_rules: str = ""
    routing_rules: str = ""
    execution_policy: str = ""
    regression_rules: str = ""
    loaded: bool = False


# ── Loaders ───────────────────────────────────────────────────────────────────

def _find_perf_dir(repo_root: str = "") -> Optional[str]:
    """Find .perf/ directory — checks repo_root first, then cwd."""
    candidates = []
    if repo_root:
        candidates.append(os.path.join(repo_root, ".perf"))
    candidates.append(os.path.join(os.getcwd(), ".perf"))
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def _read_file(path: str) -> str:
    """Read a file, return empty string if missing."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""
    except Exception as e:
        log.warning(f"[perf_policy] Could not read {path}: {e}")
        return ""


def _load_yaml(path: str) -> dict:
    """Load a YAML file, return empty dict if missing or parse error."""
    content = _read_file(path)
    if not content:
        return {}
    try:
        import yaml
        return yaml.safe_load(content) or {}
    except Exception as e:
        log.warning(f"[perf_policy] YAML parse error in {path}: {e}")
        return {}


def _parse_mappings(data: dict) -> List[MappingEntry]:
    entries = []
    for m in data.get("mappings", []):
        affects = m.get("affects", {})
        entries.append(MappingEntry(
            name=m.get("name", ""),
            paths=m.get("paths", []),
            affects_k6=affects.get("k6", []),
            affects_selenium=affects.get("selenium", []),
            affects_loadrunner=affects.get("loadrunner", []),
            risk=m.get("risk", "low"),
        ))
    return entries


def _parse_thresholds(data: dict) -> Dict[str, ThresholdConfig]:
    result = {}
    for domain, cfg in data.get("thresholds", {}).items():
        result[domain] = ThresholdConfig(
            p95_ms=cfg.get("p95_ms", 2000),
            warn_p95_ms=cfg.get("warn_p95_ms", 3000),
            error_rate=cfg.get("error_rate", 0.05),
            warn_error_rate=cfg.get("warn_error_rate", 0.10),
            throughput_rps=cfg.get("throughput_rps", 10),
            risk=cfg.get("risk", "low"),
        )
    return result


def _parse_profile(data: dict) -> ProfileConfig:
    k6 = data.get("k6", {})
    sel = data.get("selenium", {})
    lr = data.get("loadrunner", {})
    baseline = data.get("baseline", {})
    return ProfileConfig(
        name=data.get("name", "unknown"),
        k6_vus=k6.get("vus", 10),
        k6_duration=k6.get("duration", "30s"),
        selenium_suite=sel.get("suite", "smoke"),
        loadrunner_enabled=lr.get("enabled", False),
        update_baseline_on_green=baseline.get("update_on_green", False),
    )


def _load_profiles(perf_dir: str) -> Dict[str, ProfileConfig]:
    profiles_dir = os.path.join(perf_dir, "profiles")
    result = {}
    if not os.path.isdir(profiles_dir):
        return result
    for fname in os.listdir(profiles_dir):
        if fname.endswith(".yaml") or fname.endswith(".yml"):
            data = _load_yaml(os.path.join(profiles_dir, fname))
            if data:
                profile = _parse_profile(data)
                # Index by name and by trigger events
                result[profile.name] = profile
                for trigger in data.get("triggers", []):
                    result[trigger] = profile
    return result


# ── Public API ────────────────────────────────────────────────────────────────

def load_policy(repo_root: str = "") -> PerfPolicy:
    """
    Load all policy files from .perf/ directory.
    Returns a PerfPolicy with loaded=False if .perf/ not found (graceful).
    """
    perf_dir = _find_perf_dir(repo_root)
    if not perf_dir:
        log.info("[perf_policy] No .perf/ directory found — using defaults")
        return PerfPolicy()

    log.info(f"[perf_policy] Loading policy from {perf_dir}")

    rules_dir = os.path.join(perf_dir, "rules")

    policy = PerfPolicy(
        mappings=_parse_mappings(_load_yaml(os.path.join(perf_dir, "mappings.yaml"))),
        thresholds=_parse_thresholds(_load_yaml(os.path.join(perf_dir, "thresholds.yaml"))),
        profiles=_load_profiles(perf_dir),
        generation_rules=_read_file(os.path.join(rules_dir, "script-generation.md")),
        routing_rules=_read_file(os.path.join(rules_dir, "commit-routing.md")),
        execution_policy=_read_file(os.path.join(rules_dir, "execution-policy.md")),
        regression_rules=_read_file(os.path.join(rules_dir, "regression-thresholds.md")),
        loaded=True,
    )

    log.info(
        f"[perf_policy] Loaded: {len(policy.mappings)} mappings, "
        f"{len(policy.thresholds)} threshold domains, "
        f"{len(policy.profiles)} profiles"
    )
    return policy


def get_domain_for_file(changed_file: str, policy: PerfPolicy) -> Optional[str]:
    """
    Return the domain name for a changed file path using mappings.yaml.
    Returns None if no mapping matches (agent falls back to LLM reasoning).
    """
    for mapping in policy.mappings:
        for pattern in mapping.paths:
            if fnmatch.fnmatch(changed_file, pattern) or fnmatch.fnmatch(
                changed_file, pattern.replace("**", "*")
            ):
                return mapping.name
    return None


def get_affected_tests(changed_file: str, policy: PerfPolicy) -> Dict[str, List[str]]:
    """
    Return dict of {k6: [...], selenium: [...], loadrunner: [...]} for a changed file.
    Empty lists if no mapping found.
    """
    for mapping in policy.mappings:
        for pattern in mapping.paths:
            if fnmatch.fnmatch(changed_file, pattern) or fnmatch.fnmatch(
                changed_file, pattern.replace("**", "*")
            ):
                return {
                    "k6": mapping.affects_k6,
                    "selenium": mapping.affects_selenium,
                    "loadrunner": mapping.affects_loadrunner,
                    "risk": mapping.risk,
                    "domain": mapping.name,
                }
    return {"k6": [], "selenium": [], "loadrunner": [], "risk": "low", "domain": ""}


def get_thresholds(domain: str, policy: PerfPolicy) -> ThresholdConfig:
    """
    Return ThresholdConfig for a domain.
    Falls back to 'default' thresholds if domain not found.
    """
    # Try exact match first, then partial match
    if domain in policy.thresholds:
        return policy.thresholds[domain]
    for key in policy.thresholds:
        if key in domain or domain in key:
            return policy.thresholds[key]
    return policy.thresholds.get("default", ThresholdConfig())


def get_profile(trigger: str, policy: PerfPolicy) -> Optional[ProfileConfig]:
    """
    Return execution profile for a trigger event (pull_request, merge_main, nightly).
    Returns None if no profile found.
    """
    return policy.profiles.get(trigger) or policy.profiles.get("pr_smoke")


def get_generation_rules(policy: PerfPolicy) -> str:
    """Return script generation rules as a string for injection into AI prompts."""
    return policy.generation_rules


def get_routing_rules(policy: PerfPolicy) -> str:
    """Return commit routing rules as a string."""
    return policy.routing_rules


def load_agent_skill(skill_file: str) -> str:
    """
    Load an agent skill file (SCRIPTING_AGENT.md, PERF_EXEC_AGENT.md, PERF_AGENTS.md)
    from the repo root. Returns empty string if not found.
    These files are injected into AI system prompts to drive agent behaviour.
    """
    candidates = [
        os.path.join(os.getcwd(), skill_file),
        os.path.join(os.path.dirname(__file__), "..", skill_file),
    ]
    for path in candidates:
        content = _read_file(path)
        if content:
            log.info(f"[perf_policy] Loaded skill: {skill_file}")
            return content
    log.info(f"[perf_policy] Skill file not found: {skill_file}")
    return ""


def should_skip_file(changed_file: str, policy: PerfPolicy) -> bool:
    """
    Return True if this file change should be ignored per routing rules.
    Docs-only, test files, and config-only changes are skipped.
    """
    skip_patterns = [
        "*.md", "*.txt", "*.rst",          # docs
        "tests/**", "spec/**", "test/**",   # test files
        "*.log", "*.lock",                  # lock/log files
    ]
    for pattern in skip_patterns:
        if fnmatch.fnmatch(changed_file, pattern) or fnmatch.fnmatch(
            changed_file, pattern.replace("**", "*")
        ):
            return True
    return False


def build_impact_map(changed_files: List[str], policy: PerfPolicy) -> dict:
    """
    Build an impact map from a list of changed file paths.
    Returns structured dict describing what tests need updating.

    Example output:
    {
        "changed_domains": ["checkout-domain", "auth-domain"],
        "test_updates_needed": {"k6": ["checkout"], "selenium": ["login"]},
        "risk_level": "high",
        "skipped_files": ["README.md"]
    }
    """
    domains = set()
    k6_tests = set()
    selenium_tests = set()
    lr_tests = set()
    skipped = []
    risk_levels = []

    for f in changed_files:
        if should_skip_file(f, policy):
            skipped.append(f)
            continue
        affected = get_affected_tests(f, policy)
        if affected["domain"]:
            domains.add(affected["domain"])
            k6_tests.update(affected["k6"])
            selenium_tests.update(affected["selenium"])
            lr_tests.update(affected["loadrunner"])
            risk_levels.append(affected["risk"])

    # Determine overall risk level
    if "high" in risk_levels:
        overall_risk = "high"
    elif "medium" in risk_levels:
        overall_risk = "medium"
    else:
        overall_risk = "low"

    return {
        "changed_domains": list(domains),
        "test_updates_needed": {
            "k6": list(k6_tests),
            "selenium": list(selenium_tests),
            "loadrunner": list(lr_tests),
        },
        "risk_level": overall_risk,
        "skipped_files": skipped,
    }
