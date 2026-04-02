"""
agent/code_change_detector.py
─────────────────────────────
Analyses a GitHub pull-request diff and classifies every changed file into
one of two scenarios the original agent was blind to:

  SCENARIO A – Library / dependency upgrade
      Triggered when requirements.txt, package.json, pyproject.toml, etc.
      change.  We extract old→new version pairs so the script-patcher can
      adjust thresholds or add smoke tests for the upgraded package.

  SCENARIO B – New feature / new endpoint
      Triggered when a Python/JS/TS source file is added or modified and
      the diff contains new route decorators, function definitions, or
      exported symbols.  We extract the endpoint metadata so the
      script-generator can create brand-new test scripts.

Public API
──────────
  detect_changes(diff_text: str, changed_files: list[dict]) -> ChangeReport
"""

import re
import os
import json
from dataclasses import dataclass, field
from typing import List, Dict, Optional

from dotenv import load_dotenv

load_dotenv()

# ── OpenAI client (optional — graceful fallback when key not set) ─────────────
_openai_client = None
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _get_openai_client():
    """Lazy-init OpenAI client; returns None when key is absent/placeholder."""
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    key = os.getenv("OPENAI_API_KEY", "")
    if key and "xxxxxx" not in key and key.startswith("sk-"):
        try:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=key)
        except Exception:
            pass
    return _openai_client


def _openai_available() -> bool:
    """Return True only when a real (non-placeholder) API key is set."""
    key = os.getenv("OPENAI_API_KEY", "")
    return bool(key) and "xxxxxx" not in key and key.startswith("sk-")


# ── Constants ─────────────────────────────────────────────────────────────────

DEPENDENCY_FILES = {
    "requirements.txt", "requirements-dev.txt", "requirements-test.txt",
    "package.json", "package-lock.json", "yarn.lock",
    "pyproject.toml", "setup.cfg", "setup.py", "Pipfile", "Pipfile.lock",
    "Gemfile", "Gemfile.lock", "pom.xml", "build.gradle",
}

# Regex patterns to detect new HTTP route definitions across frameworks
ROUTE_PATTERNS = [
    # Flask / Quart:  @app.route('/path', methods=['GET'])
    r'@\w+\.route\(["\']([^"\']+)["\'].*?methods=\[.*?["\'](\w+)["\']',
    # Flask shorthand: @app.get('/path')  @app.post('/path')
    r'@\w+\.(get|post|put|patch|delete|head|options)\(["\']([^"\']+)["\']',
    # FastAPI / Starlette: @router.get('/path')
    r'@\w+\.(get|post|put|patch|delete)\(["\']([^"\']+)["\']',
    # FastAPI api_route: @router.api_route('/path', methods=['GET'])
    r'@\w+\.api_route\(["\']([^"\']+)["\']',
    # Express.js: router.get('/path', ...) / app.post('/path', ...)
    r'(?:router|app)\.(get|post|put|patch|delete)\(["\']([^"\']+)["\']',
    # Django path/re_path: path('api/orders/', view)
    r'(?:path|re_path)\(["\']([^"\']+)["\']',
    # Spring / JAX-RS: @GetMapping("/path") @PostMapping("/path")
    r'@(Get|Post|Put|Patch|Delete)Mapping\(["\']([^"\']+)["\']',
    # Generic @route decorator with method
    r'@route\(["\']([^"\']+)["\'].*?method=["\'](\w+)["\']',
    # nopCommerce / ASP.NET MVC: [HttpGet("path")] [HttpPost("path")]
    r'\[Http(Get|Post|Put|Patch|Delete)\(["\']([^"\']+)["\']',
    # nopCommerce Route attribute: [Route("api/product/{id}")]
    r'\[Route\(["\']([^"\']+)["\']',
    # ASP.NET minimal API: app.MapGet("/path") app.MapPost("/path")
    r'app\.Map(Get|Post|Put|Patch|Delete)\(["\']([^"\']+)["\']',
]


@dataclass
class DependencyChange:
    """Represents a single library version change."""
    file: str
    package: str
    old_version: Optional[str]
    new_version: Optional[str]


@dataclass
class FeatureChange:
    """Represents a newly detected route or feature."""
    file: str
    method: str          # GET / POST / etc.
    path: str            # /api/checkout
    description: str     # AI-generated one-liner


@dataclass
class ChangeReport:
    """Aggregated result returned to the orchestrator."""
    dependency_changes: List[DependencyChange] = field(default_factory=list)
    feature_changes: List[FeatureChange] = field(default_factory=list)
    raw_diff_summary: str = ""

    @property
    def has_dependency_changes(self) -> bool:
        return len(self.dependency_changes) > 0

    @property
    def has_feature_changes(self) -> bool:
        return len(self.feature_changes) > 0

    @property
    def needs_action(self) -> bool:
        return self.has_dependency_changes


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_dependency_diff(filename: str, diff_text: str) -> List[DependencyChange]:
    """
    Extract added/changed dependency lines from a diff block.
    Works for requirements.txt style (pkg==version) and package.json.
    """
    changes: List[DependencyChange] = []

    # requirements.txt  →  package==1.2.3
    req_pattern = re.compile(r'^[+-]([A-Za-z0-9_\-\.]+)==([^\s]+)', re.MULTILINE)
    added: Dict[str, str] = {}
    removed: Dict[str, str] = {}

    for m in req_pattern.finditer(diff_text):
        line = m.group(0)
        pkg, ver = m.group(1), m.group(2)
        if line.startswith('+'):
            added[pkg] = ver
        else:
            removed[pkg] = ver

    # Pair up removed→added as upgrades; lone additions are new deps
    all_pkgs = set(added) | set(removed)
    for pkg in all_pkgs:
        changes.append(DependencyChange(
            file=filename,
            package=pkg,
            old_version=removed.get(pkg),
            new_version=added.get(pkg),
        ))

    # package.json  →  "express": "^4.18.0"
    npm_pattern = re.compile(r'^[+]\s+"([^"]+)":\s+"([^"]+)"', re.MULTILINE)
    npm_old = re.compile(r'^[-]\s+"([^"]+)":\s+"([^"]+)"', re.MULTILINE)
    npm_added = {m.group(1): m.group(2) for m in npm_pattern.finditer(diff_text)}
    npm_removed = {m.group(1): m.group(2) for m in npm_old.finditer(diff_text)}
    for pkg in set(npm_added) | set(npm_removed):
        if pkg not in all_pkgs:  # avoid duplicates
            changes.append(DependencyChange(
                file=filename,
                package=pkg,
                old_version=npm_removed.get(pkg),
                new_version=npm_added.get(pkg),
            ))

    return changes


def _parse_feature_diff(filename: str, diff_text: str) -> List[FeatureChange]:
    """
    Scan added lines (+) in a diff for new route decorators.
    Works on both raw diff text (lines starting with +) and plain source.
    Falls back to GPT when patterns find nothing and OpenAI is configured.
    """
    changes: List[FeatureChange] = []
    seen: set = set()  # deduplicate (method, path) pairs

    # Support both raw diff (+line) and plain source code
    added_lines = "\n".join(
        line[1:] if line.startswith('+') else line
        for line in diff_text.splitlines()
        if not line.startswith('---') and not line.startswith('+++')
    )

    for pattern in ROUTE_PATTERNS:
        for m in re.finditer(pattern, added_lines, re.MULTILINE | re.IGNORECASE):
            groups = m.groups()
            if len(groups) == 2:
                method, path = groups[0], groups[1]
            else:
                # Single-group patterns (Django path, FastAPI api_route)
                method, path = "GET", groups[0]

            method = method.upper()
            key = (method, path)
            if key in seen:
                continue
            seen.add(key)

            changes.append(FeatureChange(
                file=filename,
                method=method,
                path=path,
                description=f"New {method} endpoint at {path}",
            ))

    # GPT fallback — only when regex found nothing AND OpenAI key looks real
    api_key = os.getenv("OPENAI_API_KEY", "")
    openai_available = api_key and "xxxxxx" not in api_key and api_key.startswith("sk-")

    if not changes and _is_source_file(filename) and len(added_lines) > 50 and openai_available:
        changes.extend(_ai_extract_features(filename, added_lines))

    return changes


def _ai_extract_features(filename: str, added_code: str) -> List[FeatureChange]:
    """
    Use GPT-4o-mini to extract new endpoints/features from added code when
    regex patterns don't match (e.g. custom frameworks, decorators, etc.).
    """
    try:
        prompt = f"""
You are a code analysis assistant.

Analyse the following newly added code from file '{filename}' and extract
any new HTTP endpoints, API routes, or significant new features.

Return ONLY a JSON array (no markdown) like:
[
  {{"method": "POST", "path": "/api/orders", "description": "Creates a new order"}}
]

If there are no new endpoints or features, return an empty array: []

Code:
{added_code[:3000]}
"""
        openai_client = _get_openai_client()
        if not openai_client:
            return []
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        items = json.loads(raw)
        return [
            FeatureChange(
                file=filename,
                method=item.get("method", "GET").upper(),
                path=item.get("path", "/"),
                description=item.get("description", ""),
            )
            for item in items
        ]
    except Exception as e:
        print(f"[code_change_detector] AI feature extraction failed: {e}")
        return []


def _is_source_file(filename: str) -> bool:
    """Returns True for Python, JS, TS, Go, Java, C# source files."""
    return filename.endswith((".py", ".js", ".ts", ".go", ".java", ".rb", ".cs", ".cshtml"))


def _is_dependency_file(filename: str) -> bool:
    return os.path.basename(filename) in DEPENDENCY_FILES


# ── Public API ────────────────────────────────────────────────────────────────

def detect_changes(diff_text: str, changed_files: list) -> ChangeReport:
    """
    Main entry point.

    Parameters
    ----------
    diff_text     : Full unified diff string from the GitHub API.
    changed_files : List of dicts from GitHub's /pulls/{n}/files endpoint,
                    each with at least {"filename": str, "status": str, "patch": str}.

    Returns
    -------
    ChangeReport with dependency_changes and feature_changes populated.
    """
    report = ChangeReport()

    for file_info in changed_files:
        filename = file_info.get("filename", "")
        patch = file_info.get("patch", "") or ""
        status = file_info.get("status", "modified")  # added | modified | removed

        # ── Scenario A: dependency upgrade ───────────────────────────────────
        if _is_dependency_file(filename):
            deps = _parse_dependency_diff(filename, patch)
            report.dependency_changes.extend(deps)
            print(f"[detector] Dependency changes in {filename}: {len(deps)} packages")

        # ── Scenario B: new feature / endpoint ───────────────────────────────
        elif _is_source_file(filename) and status in ("added", "modified"):
            features = _parse_feature_diff(filename, patch)
            report.feature_changes.extend(features)
            print(f"[detector] Feature changes in {filename}: {len(features)} endpoints")

    # Generate a human-readable diff summary via GPT for the final report
    if diff_text:
        report.raw_diff_summary = _summarise_diff(diff_text)

    return report


def _summarise_diff(diff_text: str) -> str:
    """One-paragraph plain-English summary of the diff for the PR comment."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or "xxxxxx" in api_key or not api_key.startswith("sk-"):
        return "Diff summary unavailable (OPENAI_API_KEY not configured)."
    try:
        openai_client = _get_openai_client()
        if not openai_client:
            return "Diff summary unavailable (OPENAI_API_KEY not configured)."
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{
                "role": "user",
                "content": (
                    "Summarise this git diff in 2-3 sentences for a QA engineer. "
                    "Focus on what changed functionally.\n\n"
                    + diff_text[:4000]
                ),
            }],
            temperature=0,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return "Diff summary unavailable."
