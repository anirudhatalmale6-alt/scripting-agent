"""
agent/script_patcher.py
───────────────────────
Patches EXISTING test scripts when a dependency upgrade or feature change
is detected, so CI stays green without manual edits.

Two patch strategies:

  STRATEGY 1 – Dependency upgrade
      Scans all k6 / LoadRunner / Selenium scripts for hardcoded version
      strings or import references to the upgraded package and rewrites them.
      Also adjusts latency thresholds when a known-slow library is upgraded
      (e.g. an ORM upgrade that changes query patterns).

  STRATEGY 2 – Feature modification
      When an existing endpoint changes signature (new required param, renamed
      path segment, changed HTTP method), GPT rewrites the relevant test block
      in-place, preserving surrounding code.

Public API
──────────
  patch_for_dependency(dep: DependencyChange) -> list[PatchResult]
  patch_for_feature(feature: FeatureChange)   -> list[PatchResult]
  patch_all(report: ChangeReport)             -> list[PatchResult]
"""

import os
import re
import glob
from dataclasses import dataclass
from typing import List
from openai import OpenAI
from dotenv import load_dotenv
from agent.code_change_detector import DependencyChange, FeatureChange, ChangeReport

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _openai_available() -> bool:
    key = os.getenv("OPENAI_API_KEY", "")
    return bool(key) and "xxxxxx" not in key and key.startswith("sk-")

# Directories that contain test scripts
SCRIPT_DIRS = ["scripts", "scripts/loadrunner", "scripts/selenium"]


@dataclass
class PatchResult:
    """Records what was changed in a single file."""
    file: str
    patched: bool
    reason: str


# ── File discovery ────────────────────────────────────────────────────────────

def _find_scripts(repo: str = "") -> List[str]:
    """Return all .js and .py test script paths, scoped to repo if provided."""
    from agent.script_generator import repo_slug
    base = os.path.join("scripts", repo_slug(repo)) if repo else "scripts"
    paths = []
    for pattern in [f"{base}/**/*.js", f"{base}/**/*.py"]:
        paths.extend(glob.glob(pattern, recursive=True))
    return paths

def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write(path: str, content: str) -> None:
    from agent.concurrency import file_lock
    with file_lock(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


# ── Strategy 1: dependency upgrade ───────────────────────────────────────────

def patch_for_dependency(dep: DependencyChange, repo: str = "") -> List[PatchResult]:
    """
    For each test script that references the upgraded package, ask GPT to
    update version strings, import aliases, or threshold values.
    """
    results: List[PatchResult] = []
    scripts = _find_scripts(repo)

    for script_path in scripts:
        content = _read(script_path)

        # Quick check: does this script mention the package at all?
        if dep.package.lower() not in content.lower():
            continue

        print(f"[script_patcher] Patching {script_path} for {dep.package} upgrade")

        if not _openai_available():
            results.append(PatchResult(
                file=script_path,
                patched=False,
                reason=f"Skipped — OPENAI_API_KEY not configured (manual review needed for {dep.package} upgrade)",
            ))
            continue

        patched_content = _ai_patch_dependency(script_path, content, dep)

        if patched_content and patched_content != content:
            _write(script_path, patched_content)
            results.append(PatchResult(
                file=script_path,
                patched=True,
                reason=f"{dep.package} {dep.old_version} → {dep.new_version}",
            ))
        else:
            results.append(PatchResult(
                file=script_path,
                patched=False,
                reason=f"No changes needed for {dep.package}",
            ))

    return results


def _ai_patch_dependency(path: str, content: str, dep: DependencyChange) -> str:
    """Use GPT to rewrite a script after a dependency version change."""
    prompt = f"""
You are a test automation engineer.

The package '{dep.package}' was upgraded from version '{dep.old_version}'
to version '{dep.new_version}'.

Update the following test script so it works correctly with the new version.
Changes may include:
- Updated import paths or API method names that changed between versions.
- Adjusted latency thresholds if the new version is known to be faster/slower.
- Any breaking-change adaptations.

If no changes are needed, return the original script UNCHANGED.
Return ONLY the updated script code, no markdown fences, no explanations.

File: {path}
Script:
{content}
"""
    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[script_patcher] AI patch failed for {path}: {e}")
        return content  # return original on failure


# ── Strategy 2: feature / endpoint change ────────────────────────────────────

def patch_for_feature(feature: FeatureChange, repo: str = "") -> List[PatchResult]:
    """
    Find scripts that test the same path and update them to match the new
    method, path, or parameters.
    """
    results: List[PatchResult] = []
    scripts = _find_scripts(repo)

    # Normalise path for matching (strip leading slash, replace params)
    path_slug = re.sub(r'[^a-z0-9]', '', feature.path.lower())

    for script_path in scripts:
        content = _read(script_path)

        # Check if this script tests the same endpoint (loose match)
        if path_slug not in re.sub(r'[^a-z0-9]', '', content.lower()):
            continue

        print(f"[script_patcher] Patching {script_path} for changed feature {feature.path}")

        if not _openai_available():
            results.append(PatchResult(
                file=script_path,
                patched=False,
                reason=f"Skipped — OPENAI_API_KEY not configured (manual review needed for {feature.path})",
            ))
            continue

        patched_content = _ai_patch_feature(script_path, content, feature)

        if patched_content and patched_content != content:
            _write(script_path, patched_content)
            results.append(PatchResult(
                file=script_path,
                patched=True,
                reason=f"Updated for {feature.method} {feature.path}: {feature.description}",
            ))
        else:
            results.append(PatchResult(
                file=script_path,
                patched=False,
                reason="No changes needed",
            ))

    return results


def _ai_patch_feature(path: str, content: str, feature: FeatureChange) -> str:
    """Use GPT to update a test script for a changed endpoint."""
    prompt = f"""
You are a test automation engineer.

The following endpoint was added or modified in the application:
  Method     : {feature.method}
  Path       : {feature.path}
  Description: {feature.description}

Update the test script below so it correctly tests this endpoint.
Preserve all other tests in the file.
If the endpoint is already correctly tested, return the script UNCHANGED.
Return ONLY the updated script code, no markdown fences, no explanations.

File: {path}
Script:
{content}
"""
    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[script_patcher] AI patch failed for {path}: {e}")
        return content


# ── Public API ────────────────────────────────────────────────────────────────

def patch_all(report: ChangeReport, repo: str = "") -> List[PatchResult]:
    """Apply all patches scoped to the given repo's script folder."""
    results: List[PatchResult] = []

    for dep in report.dependency_changes:
        results.extend(patch_for_dependency(dep, repo))

    for feature in report.feature_changes:
        results.extend(patch_for_feature(feature, repo))

    patched_count = sum(1 for r in results if r.patched)
    print(f"[script_patcher] Patched {patched_count}/{len(results)} scripts")
    return results
