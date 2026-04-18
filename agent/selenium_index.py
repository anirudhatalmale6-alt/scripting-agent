"""
agent/selenium_index.py
───────────────────────
Maintains a lightweight JSON index of all Selenium scripts so the agent
can instantly find which scripts are affected by a code change — without
scanning 2000 files on every webhook.

Index file: scripts/<repo_slug>/<env>/selenium/.selenium_index.json

Schema per entry:
{
  "CheckoutTest": {
    "test_path":  "scripts/.../selenium/src/test/java/.../CheckoutTest.java",
    "page_path":  "scripts/.../selenium/src/test/java/.../CheckoutPage.java",
    "domains":    ["checkout-domain"],
    "endpoints":  [{"method": "POST", "path": "/api/checkout"}],
    "last_updated": "2026-04-18T10:00:00",
    "line_count": 1240,
    "checksum":   "abc123"
  }
}

Public API
──────────
  load_index(sel_root)                          -> dict
  save_index(sel_root, index)                   -> None
  find_scripts_for_domain(domain, index)        -> list[dict]
  find_scripts_for_endpoint(method, path, index)-> list[dict]
  register_script(sel_root, class_name, test_path, page_path, feature, domains)
  build_index_from_disk(sel_root)               -> dict
"""

import hashlib
import json
import logging
import os
from datetime import datetime
from typing import List, Optional

log = logging.getLogger(__name__)

INDEX_FILENAME = ".selenium_index.json"


# ── Index I/O ─────────────────────────────────────────────────────────────────

def _index_path(sel_root: str) -> str:
    return os.path.join(sel_root, INDEX_FILENAME)


def load_index(sel_root: str) -> dict:
    """Load the index. Returns empty dict if not found."""
    path = _index_path(sel_root)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"[selenium_index] Could not load index: {e}")
        return {}


def save_index(sel_root: str, index: dict) -> None:
    """Persist the index atomically."""
    os.makedirs(sel_root, exist_ok=True)
    path = _index_path(sel_root)
    tmp  = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        log.error(f"[selenium_index] Could not save index: {e}")


def _file_checksum(path: str) -> str:
    """MD5 of file content — used to detect if a script actually changed."""
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()[:12]
    except Exception:
        return ""


def _line_count(path: str) -> int:
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


# ── Registration ──────────────────────────────────────────────────────────────

def register_script(
    sel_root: str,
    class_name: str,
    test_path: str,
    page_path: str,
    feature,                  # FeatureChange
    domains: List[str] = None,
) -> None:
    """
    Add or update an entry in the index after a script is created/updated.
    Called by script_generator after every Selenium write.
    """
    index = load_index(sel_root)
    index[class_name] = {
        "test_path":    test_path,
        "page_path":    page_path,
        "domains":      domains or [],
        "endpoints":    [{"method": feature.method, "path": feature.path}],
        "last_updated": datetime.utcnow().isoformat(),
        "line_count":   _line_count(test_path),
        "checksum":     _file_checksum(test_path),
    }
    save_index(sel_root, index)
    log.info(f"[selenium_index] Registered: {class_name} ({_line_count(test_path)} lines)")


def update_endpoint(sel_root: str, class_name: str, feature) -> None:
    """
    Append a new endpoint to an existing index entry (e.g. when a class
    covers multiple endpoints after an update).
    """
    index = load_index(sel_root)
    if class_name not in index:
        return
    existing_eps = {(e["method"], e["path"]) for e in index[class_name].get("endpoints", [])}
    new_ep = {"method": feature.method, "path": feature.path}
    if (feature.method, feature.path) not in existing_eps:
        index[class_name]["endpoints"].append(new_ep)
    index[class_name]["last_updated"] = datetime.utcnow().isoformat()
    index[class_name]["checksum"]     = _file_checksum(index[class_name]["test_path"])
    index[class_name]["line_count"]   = _line_count(index[class_name]["test_path"])
    save_index(sel_root, index)


# ── Lookup ────────────────────────────────────────────────────────────────────

def find_scripts_for_domain(domain: str, index: dict) -> List[dict]:
    """
    Return all index entries whose domains list contains this domain.
    domain can be a full name ('checkout-domain') or keyword ('checkout').
    """
    keyword = domain.replace("-domain", "").lower()
    results = []
    for class_name, entry in index.items():
        entry_domains = [d.lower() for d in entry.get("domains", [])]
        if any(keyword in d for d in entry_domains):
            results.append({**entry, "class_name": class_name})
    return results


def find_scripts_for_endpoint(method: str, path: str, index: dict) -> List[dict]:
    """Return all index entries that cover this endpoint."""
    results = []
    for class_name, entry in index.items():
        for ep in entry.get("endpoints", []):
            if ep.get("method", "").upper() == method.upper() and ep.get("path") == path:
                results.append({**entry, "class_name": class_name})
                break
    return results


def find_scripts_for_changed_files(changed_files: List[str], index: dict, policy=None) -> List[dict]:
    """
    Given a list of changed source file paths, return all Selenium scripts
    that are affected — using domain mapping from policy if available,
    falling back to keyword matching on file paths.
    """
    affected = {}

    for changed_file in changed_files:
        path_lower = changed_file.lower()

        # Try policy domain mapping first
        if policy:
            from agent.perf_policy import get_affected_tests
            affected_tests = get_affected_tests(changed_file, policy)
            for domain in affected_tests.get("selenium", []):
                for entry in find_scripts_for_domain(domain, index):
                    affected[entry["class_name"]] = entry
            if affected_tests.get("selenium"):
                continue  # policy matched — no need for keyword fallback

        # Keyword fallback — match file path against script domains/endpoints
        for class_name, entry in index.items():
            for ep in entry.get("endpoints", []):
                ep_path = ep.get("path", "").lower().strip("/")
                # e.g. /api/checkout → checkout; check if changed file path contains it
                parts = [p for p in ep_path.split("/") if p and not p.startswith("{")]
                if any(part in path_lower for part in parts if len(part) > 3):
                    affected[class_name] = {**entry, "class_name": class_name}
                    break

    return list(affected.values())


# ── Surgical diff extraction ──────────────────────────────────────────────────

def extract_changed_methods(existing_java: str, feature) -> str:
    """
    Extract only the methods/locators likely affected by this feature change.
    Returns a focused snippet instead of the full file — keeps GPT prompt small.

    Strategy:
    - Find methods that reference the changed endpoint path keywords
    - Return those methods + their surrounding context (±5 lines)
    - If nothing specific found, return first 80 lines (class header + key locators)
    """
    lines = existing_java.splitlines()
    path_keywords = [
        p for p in feature.path.lower().strip("/").split("/")
        if p and not p.startswith("{") and len(p) > 3
    ]

    if not path_keywords:
        # No useful keywords — return class header
        return "\n".join(lines[:80])

    # Find lines containing any keyword
    hit_lines = set()
    for i, line in enumerate(lines):
        line_lower = line.lower()
        if any(kw in line_lower for kw in path_keywords):
            # Include ±10 lines of context
            for j in range(max(0, i - 10), min(len(lines), i + 10)):
                hit_lines.add(j)

    if not hit_lines:
        return "\n".join(lines[:80])

    # Always include class declaration (first ~10 lines)
    for j in range(min(10, len(lines))):
        hit_lines.add(j)

    snippet_lines = [lines[i] for i in sorted(hit_lines)]
    snippet = "\n".join(snippet_lines)

    # Cap at 200 lines to stay within token budget
    if len(snippet_lines) > 200:
        snippet = "\n".join(snippet_lines[:200]) + "\n// ... (truncated for context)"

    return snippet


# ── Build index from existing scripts on disk ─────────────────────────────────

def build_index_from_disk(sel_root: str, policy=None) -> dict:
    """
    Scan existing Selenium scripts on disk and build a fresh index.
    Uses .perf/mappings.yaml for domain inference when available,
    falls back to keyword matching on class names and endpoint paths.
    """
    test_dir = os.path.join(sel_root, "src", "test", "java", "com", "ecommerce", "tests")
    page_dir = os.path.join(sel_root, "src", "test", "java", "com", "ecommerce", "pages")

    if not os.path.isdir(test_dir):
        return {}

    # Load policy for domain mapping if not provided
    if policy is None:
        try:
            from agent.perf_policy import load_policy as _load_policy
            policy = _load_policy()
        except Exception:
            policy = None

    # Build keyword list from policy mappings + built-in fallbacks
    policy_keywords = []
    if policy and getattr(policy, "loaded", False):
        policy_keywords = [m.name.replace("-domain", "").lower() for m in policy.mappings]
    builtin_keywords = [
        "checkout", "auth", "login", "cart", "search", "order",
        "product", "user", "payment", "account", "transaction",
        "add", "delete", "update",
    ]
    all_keywords = list(dict.fromkeys(policy_keywords + builtin_keywords))  # dedup, preserve order

    index = {}

    for fname in os.listdir(test_dir):
        if not fname.endswith("Test.java") or fname == "BaseTest.java":
            continue

        class_name = fname.replace(".java", "")
        test_path  = os.path.join(test_dir, fname)
        page_path  = os.path.join(page_dir, f"{class_name.replace('Test', 'Page')}.java")
        name_lower = class_name.lower()

        # Extract endpoints from file content first
        endpoints = []
        content = ""
        try:
            content = open(test_path, encoding="utf-8").read()
            import re as _re
            for m in _re.finditer(r'BASE_URL\s*\+\s*"([^"]+)"', content):
                endpoints.append({"method": "GET", "path": m.group(1)})
            for m in _re.finditer(r'description\s*=\s*"([^"]+)"', content):
                desc = m.group(1).lower()
                for method in ("post", "get", "put", "delete", "patch"):
                    if method in desc:
                        endpoints.append({"method": method.upper(), "path": f"/{name_lower.replace('test', '')}"})
                        break
        except Exception:
            pass

        if not endpoints:
            endpoints = [{"method": "GET", "path": f"/{name_lower.replace('test', '')}"}]

        # Infer domains — check policy mappings against endpoint paths + class name
        domains = set()

        # 1. Check policy mappings by endpoint path
        if policy and getattr(policy, "loaded", False):
            from agent.perf_policy import get_domain_for_file
            for ep in endpoints:
                ep_path = ep.get("path", "")
                for mapping in policy.mappings:
                    kw = mapping.name.replace("-domain", "").lower()
                    if kw in ep_path.lower() or kw in name_lower:
                        domains.add(mapping.name)

        # 2. Keyword fallback on class name and endpoint paths
        if not domains:
            for kw in all_keywords:
                if kw in name_lower:
                    domains.add(f"{kw}-domain")
                    break
            if not domains:
                for ep in endpoints:
                    for kw in all_keywords:
                        if kw in ep.get("path", "").lower():
                            domains.add(f"{kw}-domain")
                            break

        if not domains:
            domains.add("default-domain")

        index[class_name] = {
            "test_path":    test_path,
            "page_path":    page_path if os.path.exists(page_path) else "",
            "domains":      list(domains),
            "endpoints":    endpoints,
            "last_updated": datetime.utcfromtimestamp(
                os.path.getmtime(test_path)
            ).isoformat(),
            "line_count":   _line_count(test_path),
            "checksum":     _file_checksum(test_path),
        }

    log.info(f"[selenium_index] Built index from disk: {len(index)} scripts in {sel_root}")
    save_index(sel_root, index)
    return index


def get_or_build_index(sel_root: str) -> dict:
    """Load existing index or build from disk if missing."""
    index = load_index(sel_root)
    if not index and os.path.isdir(sel_root):
        log.info(f"[selenium_index] No index found — building from disk...")
        index = build_index_from_disk(sel_root)
    return index
