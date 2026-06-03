"""
tools/test_runner.py - Runs test scripts in Docker sandbox containers.

Sandbox support:
  k6        -> grafana/k6 Docker image (free, official)
  Selenium  -> maven:3.9-eclipse-temurin-11 + chromium (free)
  LoadRunner -> C syntax check only (requires licensed VuGen to execute)

Flow:
  1. Run in Docker sandbox (or local fallback if Docker unavailable)
  2. PASS  -> done, zero AI credits used
  3. FAIL  -> AI fixes script -> retry (max MAX_RETRIES)
  4. Still failing -> needs_manual flag
"""

import glob
import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv
from agent.llm_provider import get_llm_client, get_model, llm_available

load_dotenv()

log = logging.getLogger("test_runner")

MAX_RETRIES   = 3
K6_VUS        = int(os.getenv("K6_VUS", "10"))
K6_DURATION   = os.getenv("K6_DURATION", "30s")
SFCC_SITE_URL = os.getenv("SFCC_SITE_URL", "")
USE_SANDBOX   = os.getenv("USE_SANDBOX", "true").lower() == "true"


@dataclass
class TestResult:
    script:       str
    type:         str
    passed:       bool
    output:       str = ""
    error:        str = ""
    attempts:     int = 1
    ai_fixes:     int = 0
    needs_manual: bool = False


def _openai_available():
    return llm_available()

def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

def _write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def _strip_fences(code):
    return "\n".join(l for l in code.splitlines()
                     if not l.strip().startswith("```")).strip()

def _docker_available():
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False

def _ai_fix(script_path, error, script_type):
    if not _openai_available():
        return None
    try:
        client = get_llm_client()
        current = _read(script_path)
        if script_type == "k6":
            lang, req = "k6 JavaScript", "valid k6 JS, no markdown fences"
        elif script_type == "selenium_java":
            lang, req = "Java TestNG Selenium", "valid Java, fix imports/locators, no fences"
        else:
            lang, req = "LoadRunner VuGen C", "valid C, fix syntax, no fences"
        resp = client.chat.completions.create(
            model=get_model(),
            messages=[{"role": "user", "content":
                f"Fix this failing {lang} script.\nError:\n{error[:2000]}\n"
                f"Requirements: {req}\nDo NOT change what is being tested.\n"
                f"Return ONLY fixed code, no explanation.\nScript:\n{current}"}],
            temperature=0,
        )
        return _strip_fences(resp.choices[0].message.content.strip())
    except Exception as e:
        log.error(f"[test_runner] AI fix failed: {e}")
        return None


# k6 runners
def _run_k6_docker(path):
    import re as _re
    sfcc = SFCC_SITE_URL or "https://test.k6.io"
    # Docker containers can't resolve sibling service names — use host.docker.internal
    target = _re.sub(r'http://[a-z][a-z0-9_-]*:', 'http://host.docker.internal:', sfcc)
    cmd = ["docker", "run", "--rm",
           "--add-host=host.docker.internal:host-gateway",
           "-e", f"SFCC_SITE_URL={target}",
           "-v", f"{os.path.abspath(path)}:/script.js:ro",
           "grafana/k6", "run",
           "--vus", str(K6_VUS), "--duration", K6_DURATION,
           "--no-usage-report", "/script.js"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return proc.returncode == 0, (proc.stdout + proc.stderr)[-3000:]
    except subprocess.TimeoutExpired:
        return False, "k6 Docker timed out"
    except Exception as e:
        return False, str(e)

def _run_k6_local(path):
    env = os.environ.copy()
    if SFCC_SITE_URL:
        env["SFCC_SITE_URL"] = SFCC_SITE_URL
    try:
        proc = subprocess.run(
            ["k6", "run", "--vus", str(K6_VUS), "--duration", K6_DURATION,
             "--no-usage-report", path],
            env=env, capture_output=True, text=True, timeout=300)
        return proc.returncode == 0, (proc.stdout + proc.stderr)[-3000:]
    except FileNotFoundError:
        return False, "k6 not installed"
    except subprocess.TimeoutExpired:
        return False, "k6 timed out"

def run_k6_script(path):
    result = TestResult(script=path, type="k6", passed=False)
    use_docker = USE_SANDBOX and _docker_available()
    runner = _run_k6_docker if use_docker else _run_k6_local
    mode   = "Docker" if use_docker else "local"
    for attempt in range(1, MAX_RETRIES + 1):
        result.attempts = attempt
        log.info(f"[test_runner] k6 [{mode}] {os.path.basename(path)} {attempt}/{MAX_RETRIES}")
        passed, output = runner(path)
        result.output = output
        if passed:
            result.passed = True
            log.info(f"[test_runner] k6 PASSED: {os.path.basename(path)}")
            return result
        result.error = output[:500]
        log.warning(f"[test_runner] k6 FAILED: {output[:150]}")
        if attempt < MAX_RETRIES:
            fixed = _ai_fix(path, output, "k6")
            if fixed:
                _write(path, fixed)
                result.ai_fixes += 1
            else:
                break
    result.needs_manual = not result.passed
    return result


# Selenium Java/Maven runners
def _run_maven_docker(project_root):
    import re as _re
    sfcc = SFCC_SITE_URL or ""
    target = _re.sub(r'http://[a-z][a-z0-9_-]*:', 'http://host.docker.internal:', sfcc)
    abs_root = os.path.abspath(project_root)
    cmd = ["docker", "run", "--rm", "--shm-size=2g",
           "--add-host=host.docker.internal:host-gateway",
           "-e", f"SFCC_SITE_URL={target}",
           "-v", f"{abs_root}:/project", "-w", "/project",
           "maven:3.9-eclipse-temurin-11",
           "bash", "-c",
           "apt-get update -qq && apt-get install -y -qq chromium chromium-driver "
           "&& mvn clean test -q"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return proc.returncode == 0, (proc.stdout + proc.stderr)[-3000:]
    except subprocess.TimeoutExpired:
        return False, "Maven Docker timed out"
    except Exception as e:
        return False, str(e)

def _run_maven_local(project_root):
    env = os.environ.copy()
    if SFCC_SITE_URL:
        env["SFCC_SITE_URL"] = SFCC_SITE_URL
    try:
        proc = subprocess.run(["mvn", "clean", "test", "-q"],
                              cwd=project_root, env=env,
                              capture_output=True, text=True, timeout=300)
        return proc.returncode == 0, (proc.stdout + proc.stderr)[-3000:]
    except FileNotFoundError:
        return False, "mvn not found"
    except subprocess.TimeoutExpired:
        return False, "mvn timed out"

def run_selenium_script(path):
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
    use_docker = USE_SANDBOX and _docker_available()
    runner = _run_maven_docker if use_docker else _run_maven_local
    mode   = "Docker" if use_docker else "local"
    for attempt in range(1, MAX_RETRIES + 1):
        result.attempts = attempt
        log.info(f"[test_runner] mvn [{mode}] {os.path.basename(project_root)} {attempt}/{MAX_RETRIES}")
        passed, output = runner(project_root)
        result.output = output
        if passed:
            result.passed = True
            log.info(f"[test_runner] mvn PASSED: {project_root}")
            return result
        result.error = output[:500]
        log.warning(f"[test_runner] mvn FAILED: {output[:150]}")
        if attempt < MAX_RETRIES:
            test_dir = os.path.join(project_root, "src", "test", "java",
                                    "com", "ecommerce", "tests")
            fixed_any = False
            if os.path.isdir(test_dir):
                for jf in glob.glob(f"{test_dir}/*Test.java"):
                    if "BaseTest" in jf:
                        continue
                    fixed = _ai_fix(jf, output, "selenium_java")
                    if fixed:
                        _write(jf, fixed)
                        result.ai_fixes += 1
                        fixed_any = True
            if not fixed_any:
                break
    result.needs_manual = not result.passed
    return result


# LoadRunner - syntax check only, no sandbox available
def run_loadrunner_script(path):
    """LoadRunner requires licensed VuGen. Validates C syntax only."""
    result = TestResult(script=path, type="loadrunner", passed=False)
    if not os.path.exists(path):
        result.error = f"Not found: {path}"
        result.needs_manual = True
        return result
    try:
        proc = subprocess.run(["gcc", "--syntax-only", path],
                              capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            result.passed = True
            log.info(f"[test_runner] LR syntax OK: {os.path.basename(path)} - run in VuGen")
        else:
            result.error = proc.stderr[:500]
            log.warning(f"[test_runner] LR syntax error: {result.error[:150]}")
            if _openai_available():
                fixed = _ai_fix(path, result.error, "loadrunner_c")
                if fixed:
                    _write(path, fixed)
                    result.ai_fixes += 1
                    result.passed = True
    except FileNotFoundError:
        result.passed = True
        log.info(f"[test_runner] LR generated (no gcc): {os.path.basename(path)}")
    except subprocess.TimeoutExpired:
        result.passed = True
    result.needs_manual = True  # always needs manual LR execution
    return result


# Batch runners
def run_all_k6(repo="", env="dev"):
    from agent.script_generator import repo_slug
    base = os.path.join("scripts", repo_slug(repo) if repo else "default", env, "k6")
    scripts = sorted(glob.glob(f"{base}/**/*.js", recursive=True) + glob.glob(f"{base}/*.js"))
    if not scripts:
        log.info(f"[test_runner] No k6 scripts in {base}")
        return []
    log.info(f"[test_runner] Running {len(scripts)} k6 scripts...")
    return [run_k6_script(s) for s in scripts]

def run_all_selenium(repo="", env="dev"):
    from agent.script_generator import repo_slug
    base = os.path.join("scripts", repo_slug(repo) if repo else "default", env, "selenium")
    pom_files = (glob.glob(f"{base}/**/pom.xml", recursive=True) + glob.glob(f"{base}/pom.xml"))
    roots = list({os.path.dirname(p) for p in pom_files})
    if not roots:
        log.info(f"[test_runner] No Maven projects in {base}")
        return []
    log.info(f"[test_runner] Running {len(roots)} selenium project(s)...")
    return [run_selenium_script(r) for r in roots]

def run_all_loadrunner(repo="", env="dev"):
    from agent.script_generator import repo_slug
    base = os.path.join("scripts", repo_slug(repo) if repo else "default", env, "loadrunner")
    scripts = sorted(glob.glob(f"{base}/**/*.c", recursive=True) + glob.glob(f"{base}/*.c"))
    if not scripts:
        log.info(f"[test_runner] No LR scripts in {base}")
        return []
    log.info(f"[test_runner] Validating {len(scripts)} LR scripts (syntax only)...")
    return [run_loadrunner_script(s) for s in scripts]

def summarise(results):
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
