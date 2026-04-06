"""
tools/test_runner.py
────────────────────
Runs test scripts (k6, Selenium Java/Maven) and captures structured results.
AI is only called when a test FAILS — not on every run.

Flow per script:
  1. Run script (subprocess)
  2. PASS  → done, zero AI credits used
  3. FAIL  → send script + error to AI for fix → retry (max MAX_RETRIES)
  4. Still failing → flag needs_manual_review, create Jira ticket

Public API
──────────
  run_k6_script(path)        -> TestResult
  run_selenium_script(path)  -> TestResult   (Java Maven project root)
  run_all_k6(repo, env)      -> list[TestResult]
  run_all_selenium(repo, env)-> list[TestResult]
  summarise(results)         -> dict
"""

import glob
import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger("test_runner")

MAX_RETRIES   = 3
K6_VUS        = int(os.getenv("K6_VUS", "10"))
K6_DURATION   = os.getenv("K6_DURATION", "30s")
SFCC_SITE_URL = os.getenv("SFCC_SITE_URL", "")
OPENAI_MODEL  = os.getenv("OPENAI_MODEL", "gpt-4o-mini")


@dataclass
class TestResult:
    script:       str
    type:         str   # "k6" | "selenium"
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
    return "\n".join(
        l for l in code.splitlines() if not l.strip().startswith("```")
    ).strip()


# ── AI fix — only called on failure ──────────────────────────────────────────

def _ai_fix(script_path: str, error: str, script_type: str) -> Optional[str]:
    if not _openai_available():
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        current = _read(script_path)
        if script_type == "k6":
            lang = "k6 JavaScript"
            req  = "Valid k6 JS, export const options, export default function, no markdown fences"
        else:
            lang = "Java TestNG Selenium"
            req  = "Valid Java, fix imports/assertions/locators, no markdown fences"
        prompt = f"""Fix this failing {lang} test script.
Error:
{error[:2000]}
Requirements: {req}
Do NOT change what the test is testing — only fix the error.
Return ONLY the fixed code, no explanation, no fences.
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
    result = TestResult(script=path, type="k6", passed=False)
    env = os.environ.copy()
    if SFCC_SITE_URL:
        env["SFCC_SITE_URL"] = SFCC_SITE_URL

    for attempt in range(1, MAX_RETRIES + 1):
        result.attempts = attempt
        log.info(f"[test_runner] k6 {os.path.basename(path)} "
                 f"attempt {attempt}/{MAX_RETRIES} ({K6_VUS}VUs/{K6_DURATION})")
        try:
            proc = subprocess.run(
                ["k6", "run", "--vus", str(K6_VUS), "--duration", K6_DURATION,
                 "--no-usage-report", path],
                env=env, capture_output=True, text=True, timeout=300,
            )
            result.output = (proc.stdout + proc.stderr)[-3000:]
            if proc.returncode == 0:
                result.passed = True
                log.info(f"[test_runner] ✅ k6 PASSED: {os.path.basename(path)}")
                return result
            error = (proc.stderr + proc.stdout[-1000:]).strip()
            result.error = error[:500]
            log.warning(f"[test_runner] ❌ k6 FAILED: {error[:150]}")
            if attempt < MAX_RETRIES:
                fixed = _ai_fix(path, error, "k6")
                if fixed:
                    _write(path, fixed)
                    result.ai_fixes += 1
                else:
                    break
        except FileNotFoundError:
            result.error = "k6 not installed"
            return result
        except subprocess.TimeoutExpired:
            result.error = "k6 timed out"
            return result

    result.needs_manual = not result.passed
    return result


# ── Selenium Java/Maven runner ────────────────────────────────────────────────

def run_selenium_script(path: str) -> TestResult:
    """
    Run a Java Maven Selenium project via mvn clean test.
    path = selenium project root (containing pom.xml) or any file inside it.
    """
    # Resolve project root
    project_root = path if os.path.isdir(path) else os.path.dirname(path)
    d = project_root
    while d and d != os.path.dirname(d):
        if os.path.exists(os.path.join(d, "pom.xml")):
            project_root = d
            break
        d = os.path.dirname(d)

    result = TestResult(script=project_root, type="selenium", passed=False)

    if not os.path.exists(os.path.join(project_root, "pom.xml")):
        result.error = f"No pom.xml in {project_root}"
        result.needs_manual = True
        return result

    env = os.environ.copy()
    if SFCC_SITE_URL:
        env["SFCC_SITE_URL"] = SFCC_SITE_URL

    for attempt in range(1, MAX_RETRIES + 1):
        result.attempts = attempt
        log.info(f"[test_runner] mvn test {os.path.basename(project_root)} "
                 f"attempt {attempt}/{MAX_RETRIES}")
        try:
            proc = subprocess.run(
                ["mvn", "clean", "test", "-q"],
                cwd=project_root, env=env,
                capture_output=True, text=True, timeout=300,
            )
            result.output = (proc.stdout + proc.stderr)[-3000:]
            if proc.returncode == 0:
                result.passed = True
                log.info(f"[test_runner] ✅ mvn PASSED: {project_root}")
                return result
            error = (proc.stdout + proc.stderr)[-2000:]
            result.error = error[:500]
            log.warning(f"[test_runner] ❌ mvn FAILED: {error[:150]}")
            if attempt < MAX_RETRIES:
                test_dir = os.path.join(project_root, "src", "test", "java",
                                        "com", "ecommerce", "tests")
                fixed_any = False
                if os.path.isdir(test_dir):
                    for jf in glob.glob(f"{test_dir}/*Test.java"):
                        if "BaseTest" in jf:
                            continue
                        fixed = _ai_fix(jf, error, "selenium_java")
                        if fixed:
                            _write(jf, fixed)
                            result.ai_fixes += 1
                            fixed_any = True
                if not fixed_any:
                    break
        except FileNotFoundError:
            result.error = "mvn not found — install Java + Maven"
            return result
        except subprocess.TimeoutExpired:
            result.error = "mvn timed out"
            return result

    result.needs_manual = not result.passed
    return result


# ── Batch runners ─────────────────────────────────────────────────────────────

def run_all_k6(repo: str = "", env: str = "dev") -> list:
    from agent.script_generator import repo_slug
    base = os.path.join("scripts", repo_slug(repo) if repo else "default", env, "k6")
    scripts = sorted(glob.glob(f"{base}/**/*.js", recursive=True) +
                     glob.glob(f"{base}/*.js"))
    if not scripts:
        log.info(f"[test_runner] No k6 scripts in {base}")
        return []
    log.info(f"[test_runner] Running {len(scripts)} k6 scripts...")
    return [run_k6_script(s) for s in scripts]


def run_all_selenium(repo: str = "", env: str = "dev") -> list:
    """Find all Maven selenium project roots and run each once."""
    from agent.script_generator import repo_slug
    base = os.path.join("scripts", repo_slug(repo) if repo else "default", env, "selenium")
    # Each selenium/ folder IS the Maven project root
    pom_files = glob.glob(f"{base}/**/pom.xml", recursive=True) + \
                glob.glob(f"{base}/pom.xml")
    project_roots = list({os.path.dirname(p) for p in pom_files})
    if not project_roots:
        log.info(f"[test_runner] No Maven selenium projects in {base}")
        return []
    log.info(f"[test_runner] Running {len(project_roots)} selenium project(s)...")
    return [run_selenium_script(r) for r in project_roots]


def summarise(results: list) -> dict:
    passed  = [r for r in results if r.passed]
    manual  = [r for r in results if r.needs_manual]
    ai_used = sum(r.ai_fixes for r in results)
    return {
        "total":         len(results),
        "passed":        len(passed),
        "needs_manual":  len(manual),
        "ai_fixes_used": ai_used,
        "manual_review": [r.script for r in manual],
    }
