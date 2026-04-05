"""
tools/test_runner.py
────────────────────
Runs test scripts (k6, Selenium/pytest) and captures structured results.
AI is only called when a test FAILS — not on every run.

Flow per script:
  1. Run script (subprocess)
  2. PASS  → return result, done. No AI used.
  3. FAIL  → send script + error to AI for a fix
  4. Re-run fixed script
  5. Repeat up to MAX_RETRIES
  6. Still failing → mark as needs_manual_review

Public API
──────────
  run_k6_script(path)        -> TestResult
  run_selenium_script(path)  -> TestResult
  run_all_k6(repo, env)      -> list[TestResult]
  run_all_selenium(repo, env)-> list[TestResult]
"""

import glob
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("test_runner")

MAX_RETRIES    = 3
K6_VUS         = int(os.getenv("K6_VUS", "10"))
K6_DURATION    = os.getenv("K6_DURATION", "30s")
SFCC_SITE_URL  = os.getenv("SFCC_SITE_URL", "")
OPENAI_MODEL   = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass
class TestResult:
    script:       str
    type:         str          # "k6" | "selenium"
    passed:       bool
    output:       str = ""
    error:        str = ""
    attempts:     int = 1
    ai_fixes:     int = 0
    needs_manual: bool = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _openai_available() -> bool:
    key = os.getenv("OPENAI_API_KEY", "")
    return bool(key) and "xxxxxx" not in key and key.startswith("sk-")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _strip_fences(code: str) -> str:
    lines = code.splitlines()
    return "\n".join(l for l in lines if not l.strip().startswith("```")).strip()


# ── AI fix (only called on failure) ──────────────────────────────────────────

def _ai_fix(script_path: str, error: str, script_type: str) -> Optional[str]:
    """Ask AI to fix a failing script. Returns fixed code or None."""
    if not _openai_available():
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        current = _read(script_path)
        lang = "k6 JavaScript" if script_type == "k6" else "Python pytest/Selenium"
        prompt = f"""Fix this failing {lang} test script.

Error output:
{error[:2000]}

Requirements:
{"- Valid k6 JS, export const options, export default function, no markdown fences" if script_type == "k6" else "- Valid Python pytest, fix imports/assertions, no markdown fences"}
- Do NOT change what the test is testing, only fix the syntax/runtime error
- Return ONLY the fixed code, no explanation, no markdown fences

Script:
{current}"""
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        return _strip_fences(resp.choices[0].message.content.strip())
    except Exception as e:
        log.error(f"[test_runner] AI fix failed for {script_path}: {e}")
        return None


# ── k6 runner ─────────────────────────────────────────────────────────────────

def run_k6_script(path: str) -> TestResult:
    """
    Run a k6 script with real load (K6_VUS / K6_DURATION from env).
    On failure: AI fixes script and retries up to MAX_RETRIES.
    AI is NOT called if the test passes.
    """
    result = TestResult(script=path, type="k6", passed=False)
    env = os.environ.copy()
    if SFCC_SITE_URL:
        env["SFCC_SITE_URL"] = SFCC_SITE_URL

    for attempt in range(1, MAX_RETRIES + 1):
        result.attempts = attempt
        log.info(f"[test_runner] k6 run {os.path.basename(path)} attempt {attempt}/{MAX_RETRIES} "
                 f"({K6_VUS} VUs / {K6_DURATION})")
        try:
            proc = subprocess.run(
                ["k6", "run", "--vus", str(K6_VUS), "--duration", K6_DURATION,
                 "--no-usage-report", path],
                env=env, capture_output=True, text=True, timeout=300,
            )
            output = proc.stdout + "\n" + proc.stderr
            result.output = output[-3000:]

            if proc.returncode == 0:
                result.passed = True
                log.info(f"[test_runner] ✅ k6 PASSED: {os.path.basename(path)}")
                return result

            # FAIL — try AI fix
            error = (proc.stderr + "\n" + proc.stdout[-1000:]).strip()
            result.error = error[:500]
            log.warning(f"[test_runner] ❌ k6 FAILED attempt {attempt}: {error[:150]}")

            if attempt < MAX_RETRIES:
                fixed = _ai_fix(path, error, "k6")
                if fixed:
                    _write(path, fixed)
                    result.ai_fixes += 1
                    log.info(f"[test_runner] 🔧 AI fix applied, retrying...")
                else:
                    log.warning(f"[test_runner] No AI fix available")
                    break

        except FileNotFoundError:
            result.error = "k6 binary not found"
            log.error("[test_runner] k6 not installed")
            return result
        except subprocess.TimeoutExpired:
            result.error = "k6 timed out after 300s"
            log.error(f"[test_runner] k6 timeout: {path}")
            return result

    result.needs_manual = not result.passed
    if result.needs_manual:
        log.warning(f"[test_runner] ⚠️  {os.path.basename(path)} needs manual review after {MAX_RETRIES} attempts")
    return result


# ── Selenium/pytest runner ────────────────────────────────────────────────────

def run_selenium_script(path: str) -> TestResult:
    """
    Run a Selenium pytest script.
    On failure: AI fixes script and retries up to MAX_RETRIES.
    AI is NOT called if the test passes.
    """
    result = TestResult(script=path, type="selenium", passed=False)
    env = os.environ.copy()
    if SFCC_SITE_URL:
        env["BASE_URL"] = SFCC_SITE_URL

    for attempt in range(1, MAX_RETRIES + 1):
        result.attempts = attempt
        log.info(f"[test_runner] pytest run {os.path.basename(path)} attempt {attempt}/{MAX_RETRIES}")
        try:
            proc = subprocess.run(
                ["python", "-m", "pytest", path, "-v", "--tb=short", "--no-header",
                 "--json-report", "--json-report-file=/tmp/pytest_result.json"],
                env=env, capture_output=True, text=True, timeout=300,
            )
            output = proc.stdout + "\n" + proc.stderr
            result.output = output[-3000:]

            if proc.returncode == 0:
                result.passed = True
                log.info(f"[test_runner] ✅ pytest PASSED: {os.path.basename(path)}")
                return result

            error = output[-2000:]
            result.error = error[:500]
            log.warning(f"[test_runner] ❌ pytest FAILED attempt {attempt}: {error[:150]}")

            if attempt < MAX_RETRIES:
                fixed = _ai_fix(path, error, "selenium")
                if fixed:
                    _write(path, fixed)
                    result.ai_fixes += 1
                    log.info(f"[test_runner] 🔧 AI fix applied, retrying...")
                else:
                    log.warning(f"[test_runner] No AI fix available")
                    break

        except FileNotFoundError:
            result.error = "pytest not found"
            log.error("[test_runner] pytest not installed")
            return result
        except subprocess.TimeoutExpired:
            result.error = "pytest timed out after 300s"
            return result

    result.needs_manual = not result.passed
    if result.needs_manual:
        log.warning(f"[test_runner] ⚠️  {os.path.basename(path)} needs manual review")
    return result


# ── Batch runners ─────────────────────────────────────────────────────────────

def run_all_k6(repo: str = "", env: str = "dev") -> list:
    from agent.script_generator import repo_slug
    base = os.path.join("scripts", repo_slug(repo) if repo else "default", env, "k6")
    scripts = sorted(glob.glob(f"{base}/**/*.js", recursive=True) + glob.glob(f"{base}/*.js"))
    if not scripts:
        log.info(f"[test_runner] No k6 scripts found in {base}")
        return []
    log.info(f"[test_runner] Running {len(scripts)} k6 scripts...")
    return [run_k6_script(s) for s in scripts]


def run_all_selenium(repo: str = "", env: str = "dev") -> list:
    from agent.script_generator import repo_slug
    base = os.path.join("scripts", repo_slug(repo) if repo else "default", env, "selenium")
    scripts = sorted(glob.glob(f"{base}/**/*.py", recursive=True) + glob.glob(f"{base}/*.py"))
    if not scripts:
        log.info(f"[test_runner] No selenium scripts found in {base}")
        return []
    log.info(f"[test_runner] Running {len(scripts)} selenium scripts...")
    return [run_selenium_script(s) for s in scripts]


def summarise(results: list) -> dict:
    """Produce a summary dict from a list of TestResult."""
    passed  = [r for r in results if r.passed]
    failed  = [r for r in results if not r.passed and not r.needs_manual]
    manual  = [r for r in results if r.needs_manual]
    ai_used = sum(r.ai_fixes for r in results)
    return {
        "total":        len(results),
        "passed":       len(passed),
        "failed":       len(failed),
        "needs_manual": len(manual),
        "ai_fixes_used": ai_used,
        "manual_review": [r.script for r in manual],
    }
