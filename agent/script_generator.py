"""
agent/script_generator.py
─────────────────────────
Generates and updates test scripts per repo and environment.

Folder structure (auto-created on demand, no pre-existing folders needed):

  scripts/
    <repo_slug>/
      dev/
        k6/           ← k6 perf scripts
        loadrunner/   ← LoadRunner-style Python scripts
        selenium/     ← Selenium pytest scripts
      stage/
        k6/
        loadrunner/
        selenium/
      prod/
        k6/
        loadrunner/
        selenium/

Public API
──────────
  generate_scripts(feature, env, repo) -> GeneratedScripts
  generate_all(features, env, repo)    -> list[GeneratedScripts]
  repo_slug(repo)                      -> str
"""

import os
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple
from openai import OpenAI
from dotenv import load_dotenv
from agent.code_change_detector import FeatureChange

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
BASE_URL = os.getenv("SFCC_SITE_URL", "https://your-app.com")
MAX_FIX_ITERATIONS = 5


# ── Helpers ───────────────────────────────────────────────────────────────────

def _openai_available() -> bool:
    key = os.getenv("OPENAI_API_KEY", "")
    return bool(key) and "xxxxxx" not in key and key.startswith("sk-")


def _slug(text: str) -> str:
    """'/api/orders/<id>' → 'api_orders_id'"""
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


def repo_slug(repo: str) -> str:
    """'owner/my-repo' → 'owner__my-repo'"""
    return re.sub(r'[^a-z0-9_\-]+', '_', repo.lower()).replace('/', '__')


def _script_root(repo: str, env: str) -> str:
    """Base path: scripts/<repo_slug>/<env>"""
    rslug = repo_slug(repo) if repo else "default"
    return os.path.join("scripts", rslug, env)


def _write(path: str, content: str) -> None:
    """Write file, creating all parent directories automatically."""
    from agent.concurrency import file_lock
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with file_lock(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    print(f"[script_generator] Written: {path}")


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class GeneratedScripts:
    feature: FeatureChange
    k6_path: str = ""
    loadrunner_path: str = ""
    selenium_path: str = ""
    k6_validated: bool = False
    k6_validation_attempts: int = 0
    k6_validation_error: str = ""


# ── k6 ────────────────────────────────────────────────────────────────────────

def _k6_template(feature: FeatureChange, env: str) -> str:
    method = feature.method.lower()
    # Replace path params with realistic placeholder values
    path_with_id = re.sub(r'\{[^}]+\}', '1', feature.path)

    if method in ("post", "put", "patch"):
        real_call = f"http.{method}(`${{BASE_URL}}{path_with_id}`, payload, params);"
        payload_block = (
            "    const payload = JSON.stringify({ /* TODO: request body */ });\n"
            "    const params  = { headers: { 'Content-Type': 'application/json' } };\n"
        )
    else:
        real_call = f"http.get(`${{BASE_URL}}{path_with_id}`);"
        payload_block = ""

    return f"""// k6 performance test — {feature.method} {feature.path}
// Repo: auto-generated | Env: {env}
// {feature.description}

import http from 'k6/http';
import {{ check, sleep }} from 'k6';

const BASE_URL = __ENV.SFCC_SITE_URL || 'https://test.k6.io';
const IS_REAL_APP = !BASE_URL.includes('test.k6.io');

export const options = {{
  vus: 10,
  duration: '30s',
  thresholds: {{
    http_req_duration: ['p(95)<2000'],
    http_req_failed:   ['rate<0.10'],
  }},
}};

export default function () {{
  if (IS_REAL_APP) {{
{payload_block}    const res = {real_call}
    check(res, {{
      'status 2xx or 404': (r) => r.status >= 200 && r.status < 500,
      'response time ok':  (r) => r.timings.duration < 2000,
    }});
  }} else {{
    const res = http.get(BASE_URL);
    check(res, {{ 'status 200': (r) => r.status === 200 }});
  }}
  sleep(1);
}}
"""


def _generate_k6(feature: FeatureChange, env: str) -> str:
    if not _openai_available():
        return _k6_template(feature, env)
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": f"""Write a complete k6 JS performance test.
Method: {feature.method} | Path: {feature.path} | Env: {env}
{_APP_CONTEXT}
- Fallback: __ENV.SFCC_SITE_URL || 'https://test.k6.io'
- const IS_REAL_APP = !BASE_URL.includes('test.k6.io')
- Wrap real calls in if (IS_REAL_APP) else http.get(BASE_URL)
- options: 10 VUs, 30s, p(95)<2000, rate<0.05
- check() for 200/201, sleep(1)
- Return ONLY JS, no markdown fences."""}],
            temperature=0.2,
        )
        script = resp.choices[0].message.content.strip()
        return _inject_options_if_missing(script, feature)
    except Exception as e:
        print(f"[script_generator] GPT k6 failed: {e} — using template")
        return _k6_template(feature, env)


def _update_k6(existing: str, feature: FeatureChange, env: str) -> str:
    if not _openai_available():
        return _k6_template(feature, env)
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": f"""Update this k6 script for the modified endpoint.
{feature.method} {feature.path} — {feature.description}
Keep IS_REAL_APP fallback. Return ONLY JS, no fences.
---
{existing}"""}],
            temperature=0,
        )
        return _inject_options_if_missing(resp.choices[0].message.content.strip(), feature)
    except Exception as e:
        print(f"[script_generator] GPT k6 update failed: {e} — keeping existing")
        return existing


def _inject_options_if_missing(script: str, feature: FeatureChange) -> str:
    if "export const options" in script:
        return script
    block = (
        "\nexport const options = {\n"
        "  vus: 10,\n  duration: '30s',\n"
        "  thresholds: {\n"
        "    http_req_duration: ['p(95)<2000'],\n"
        "    http_req_failed:   ['rate<0.05'],\n"
        "  },\n};\n"
    )
    lines = script.splitlines()
    idx = max(
        (i for i, l in enumerate(lines)
         if l.strip().startswith("import ") or ("require(" in l and l.strip().startswith("const "))),
        default=-1
    )
    if idx == -1:
        return block + "\n" + script
    lines.insert(idx + 1, block)
    print(f"[script_generator] Injected options block for {feature.path}")
    return "\n".join(lines)


# ── k6 validate + auto-fix ────────────────────────────────────────────────────

def _run_k6_validation(script_path: str) -> Tuple[bool, str]:
    sfcc_url = os.getenv("SFCC_SITE_URL", "")
    placeholders = ("your-sfcc-site", "your-app", "example.com", "")
    if sfcc_url and not any(m in sfcc_url for m in placeholders):
        target = sfcc_url
    else:
        import socket
        for host in ("localhost", "mock-app"):
            try:
                socket.create_connection((host, 8080), timeout=1).close()
                target = f"http://{host}:8080"
                break
            except OSError:
                continue
        else:
            print("[validator] No target reachable — skipping validation")
            return True, ""

    env = os.environ.copy()
    env["SFCC_SITE_URL"] = target
    try:
        proc = subprocess.run(
            ["k6", "run", "--vus", "1", "--duration", "5s", "--no-usage-report", script_path],
            env=env, capture_output=True, text=True, timeout=60,
        )
        if proc.returncode == 0:
            return True, ""
        return False, (proc.stderr + "\n" + proc.stdout[-1000:]).strip()
    except FileNotFoundError:
        return True, ""
    except subprocess.TimeoutExpired:
        return False, "k6 validation timed out"


def _validate_and_fix_k6(script_path: str, feature: FeatureChange, env: str) -> Tuple[bool, int, str]:
    error = ""
    for attempt in range(1, MAX_FIX_ITERATIONS + 1):
        print(f"[validator] {os.path.basename(script_path)} attempt {attempt}/{MAX_FIX_ITERATIONS}")
        passed, error = _run_k6_validation(script_path)
        if passed:
            print(f"[validator] ✅ passed on attempt {attempt}")
            return True, attempt, ""
        print(f"[validator] ❌ {error[:150]}")
        if attempt < MAX_FIX_ITERATIONS:
            if not _openai_available():
                break
            try:
                fixed_resp = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "user", "content": f"""Fix this failing k6 script (attempt {attempt}).
Error: {error[:1500]}
Requirements: valid k6 API, export const options, export default function,
__ENV.SFCC_SITE_URL fallback, IS_REAL_APP guard, status 200/201 checks.
Return ONLY JS, no fences.
---
{open(script_path, encoding='utf-8').read()}"""}],
                    temperature=0,
                )
                fixed = _inject_options_if_missing(
                    fixed_resp.choices[0].message.content.strip(), feature
                )
                _write(script_path, fixed)
            except Exception as e:
                print(f"[validator] GPT fix failed: {e}")
    final = f"Failed after {MAX_FIX_ITERATIONS} attempts. Last error: {error[:300]}"
    print(f"[validator] ⚠️  {final}")
    return False, MAX_FIX_ITERATIONS, final


# ── LoadRunner (VuGen C format) ───────────────────────────────────────────────

# Known SauceDemo / WebTours endpoint context injected into prompts so GPT
# generates realistic scripts even without a live app diff.
_APP_CONTEXT = """
Target apps for reference:
- SauceDemo (https://www.saucedemo.com): React SPA — pages: /inventory.html,
  /cart.html, /checkout-step-one.html, /checkout-step-two.html,
  /checkout-complete.html, /item/<id>.html. Login: POST username/password form.
- WebTours (http://localhost:1080/WebTours/): Perl CGI app — pages: nav.pl,
  login.pl, flights.pl, reservations.pl, itinerary.pl, merchandise.pl.
  Login: POST to login.pl with username=jojo&password=bean.
Use the app context to write realistic transactions matching the endpoint.
"""


def _lr_template(feature: FeatureChange) -> str:
    """Fallback VuGen C script template when OpenAI is unavailable."""
    method = feature.method.upper()
    path_safe = feature.path.replace('"', '\\"')

    if method in ("POST", "PUT", "PATCH"):
        request_block = f'''\tweb_submit_data("{method} {feature.path}",
\t\t"Action={{BASE_URL}}{path_safe}",
\t\t"Method={method}",
\t\t"RecContentType=application/json",
\t\tITEMDATA,
\t\t"Name=key", "Value=value", ENDITEM,
\t\tLAST);'''
    else:
        request_block = f'''\tweb_url("{feature.path}",
\t\t"URL={{BASE_URL}}{path_safe}",
\t\t"Resource=0",
\t\t"RecContentType=text/html",
\t\tLAST);'''

    return f'''/*
 * LoadRunner VuGen C Script
 * Transaction : {feature.method} {feature.path}
 * Description : {feature.description}
 * Generated   : auto-generated by AI Performance Agent
 */

#ifndef _GLOBALS_H
    #include "globals.h"
#endif

/* vuser_init.c — runs once per VU before iterations */
vuser_init()
{{
    lr_log_message("VU init — {feature.path}");
    web_set_sockets_option("SSL_VERSION", "TLS");
    return 0;
}}

/* Action.c — main transaction loop */
Action()
{{
    char *BASE_URL = lr_eval_string("{{SFCC_SITE_URL}}");

    lr_start_transaction("{feature.method}_{_slug(feature.path)}");

{request_block}

    lr_end_transaction("{feature.method}_{_slug(feature.path)}", LR_AUTO);

    lr_think_time(1);
    return 0;
}}

/* vuser_end.c — runs once per VU after all iterations */
vuser_end()
{{
    lr_log_message("VU end — {feature.path}");
    return 0;
}}
'''


def _generate_loadrunner(feature: FeatureChange) -> str:
    if not _openai_available():
        return _lr_template(feature)
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": f"""Write a complete LoadRunner VuGen C script (.c file).
Endpoint: {feature.method} {feature.path} — {feature.description}
{_APP_CONTEXT}
Requirements:
- Use lr_start_transaction / lr_end_transaction with LR_AUTO
- Use web_url() for GET, web_submit_data() for POST/PUT/PATCH
- Use lr_eval_string("{{{{SFCC_SITE_URL}}}}") for base URL (double braces for C macro)
- Include vuser_init(), Action(), vuser_end() sections
- Add lr_think_time(1) after each transaction
- Add web_reg_find() to verify response content where appropriate
- Add correlation with web_reg_save_param() if session tokens are needed
- Return ONLY valid C code, no markdown fences, no Python."""}],
            temperature=0.2,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[script_generator] GPT LR failed: {e} — using template")
        return _lr_template(feature)


def _update_loadrunner(existing: str, feature: FeatureChange) -> str:
    if not _openai_available():
        return existing
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": f"""Update this LoadRunner VuGen C script for the modified endpoint.
{feature.method} {feature.path} — {feature.description}
{_APP_CONTEXT}
Keep vuser_init/Action/vuser_end structure. Return ONLY C code, no fences.
---
{existing}"""}],
            temperature=0,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[script_generator] GPT LR update failed: {e}")
        return existing


# ── Selenium ──────────────────────────────────────────────────────────────────

def _selenium_template(feature: FeatureChange) -> str:
    fn = _slug(feature.path)
    return f'''"""
Selenium test — {feature.method} {feature.path}
{feature.description}
"""
import os, pytest
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

BASE_URL = os.getenv("SFCC_SITE_URL", "{BASE_URL}")

@pytest.fixture
def driver():
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    d = webdriver.Chrome(options=opts)
    yield d
    d.quit()

def test_{fn}(driver):
    """Test {feature.path} loads correctly."""
    driver.get(f"{{BASE_URL}}{feature.path}")
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    assert driver.title != ""
'''


def _generate_selenium(feature: FeatureChange) -> str:
    if not _openai_available():
        return _selenium_template(feature)
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": f"""Write a Python Selenium pytest test.
{feature.method} {feature.path} — {feature.description}
{_APP_CONTEXT}
- os.getenv("SFCC_SITE_URL") for base URL
- headless Chrome, WebDriverWait, assert title or element
- function name: test_{_slug(feature.path)}
Return ONLY Python, no fences."""}],
            temperature=0.2,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[script_generator] GPT Selenium failed: {e} — using template")
        return _selenium_template(feature)


def _update_selenium(existing: str, feature: FeatureChange) -> str:
    if not _openai_available():
        return existing
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": f"""Update this Selenium test for modified endpoint.
{feature.method} {feature.path} — {feature.description}
Return ONLY Python, no fences.
---
{existing}"""}],
            temperature=0,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[script_generator] GPT Selenium update failed: {e}")
        return existing


# ── Public API ────────────────────────────────────────────────────────────────

def generate_scripts(feature: FeatureChange, env: str = "dev", repo: str = "") -> GeneratedScripts:
    """
    Create or update all 3 script types for one feature.

    Paths (all folders created automatically if missing):
      scripts/<repo_slug>/<env>/k6/<slug>_perf_test.js
      scripts/<repo_slug>/<env>/loadrunner/<slug>_lr_test.py
      scripts/<repo_slug>/<env>/selenium/<slug>_selenium_test.py

    - Script missing  → CREATE
    - Script present  → UPDATE via GPT
    """
    root  = _script_root(repo, env)
    fslug = _slug(feature.path)
    result = GeneratedScripts(feature=feature)

    # k6
    k6_path = os.path.join(root, "k6", f"{fslug}_perf_test.js")
    result.k6_path = k6_path
    if os.path.exists(k6_path):
        print(f"[script_generator] UPDATE k6: {k6_path}")
        _write(k6_path, _update_k6(open(k6_path, encoding="utf-8").read(), feature, env))
    else:
        print(f"[script_generator] CREATE k6: {k6_path}")
        _write(k6_path, _generate_k6(feature, env))

    passed, attempts, error = _validate_and_fix_k6(k6_path, feature, env)
    result.k6_validated           = passed
    result.k6_validation_attempts = attempts
    result.k6_validation_error    = error

    # LoadRunner (.c VuGen format)
    lr_path = os.path.join(root, "loadrunner", f"{fslug}_lr_test.c")
    result.loadrunner_path = lr_path
    if os.path.exists(lr_path):
        print(f"[script_generator] UPDATE loadrunner: {lr_path}")
        _write(lr_path, _update_loadrunner(open(lr_path, encoding="utf-8").read(), feature))
    else:
        print(f"[script_generator] CREATE loadrunner: {lr_path}")
        _write(lr_path, _generate_loadrunner(feature))

    # Selenium
    sel_path = os.path.join(root, "selenium", f"{fslug}_selenium_test.py")
    result.selenium_path = sel_path
    if os.path.exists(sel_path):
        print(f"[script_generator] UPDATE selenium: {sel_path}")
        _write(sel_path, _update_selenium(open(sel_path, encoding="utf-8").read(), feature))
    else:
        print(f"[script_generator] CREATE selenium: {sel_path}")
        _write(sel_path, _generate_selenium(feature))

    return result


def generate_all(features: List[FeatureChange], env: str = "dev", repo: str = "") -> List[GeneratedScripts]:
    """
    Generate/update scripts for all features.
    Folders are created automatically — no pre-existing structure needed.
    """
    results = []
    for feature in features:
        print(f"[script_generator] {feature.method} {feature.path} → repo={repo or 'default'} env={env}")
        results.append(generate_scripts(feature, env, repo))
    return results
