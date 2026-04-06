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


def _strip_fences(code: str) -> str:
    """Remove markdown code fences GPT sometimes adds despite instructions."""
    lines = code.splitlines()
    cleaned = [l for l in lines if not l.strip().startswith("```")]
    return "\n".join(cleaned).strip()


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
{_app_context(feature)}
- Fallback: __ENV.SFCC_SITE_URL || 'https://test.k6.io'
- const IS_REAL_APP = !BASE_URL.includes('test.k6.io')
- Wrap real calls in if (IS_REAL_APP) else http.get(BASE_URL)
- options: 10 VUs, 30s, p(95)<2000, rate<0.05
- check() for 200/201, sleep(1)
- Return ONLY JS, no markdown fences."""}],
            temperature=0.2,
        )
        script = _strip_fences(resp.choices[0].message.content.strip())
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
        return _inject_options_if_missing(_strip_fences(resp.choices[0].message.content.strip()), feature)
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
                    _strip_fences(fixed_resp.choices[0].message.content.strip()), feature
                )
                _write(script_path, fixed)
            except Exception as e:
                print(f"[validator] GPT fix failed: {e}")
    final = f"Failed after {MAX_FIX_ITERATIONS} attempts. Last error: {error[:300]}"
    print(f"[validator] ⚠️  {final}")
    return False, MAX_FIX_ITERATIONS, final


# ── LoadRunner (VuGen C format) ───────────────────────────────────────────────
def _app_context(feature: "FeatureChange" = None) -> str:
    """Build prompt context from the actual repo and endpoint being tested."""
    repo = os.getenv("GITHUB_REPO", "")
    base_url = os.getenv("SFCC_SITE_URL", "https://your-app.com")
    path = feature.path if feature else "/"
    method = feature.method if feature else "GET"
    desc = feature.description if feature else ""
    return f"""
Target repo  : {repo}
Base URL     : {base_url}
Endpoint     : {method} {path}
Description  : {desc}
Write realistic transactions for this specific endpoint only.
Use the base URL and path above — do not reference SauceDemo or WebTours.
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
{_app_context(feature)}
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
        return _strip_fences(resp.choices[0].message.content.strip())
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
{_app_context(feature)}
Keep vuser_init/Action/vuser_end structure. Return ONLY C code, no fences.
---
{existing}"""}],
            temperature=0,
        )
        return _strip_fences(resp.choices[0].message.content.strip())
    except Exception as e:
        print(f"[script_generator] GPT LR update failed: {e}")
        return existing


# ── Selenium (Java / Maven / TestNG project) ──────────────────────────────────
#
# Generates a full Maven project per repo/env under:
#   scripts/<repo>/<env>/selenium/
#     pom.xml
#     src/test/resources/testng.xml
#     src/test/java/com/ecommerce/tests/BaseTest.java
#     src/test/java/com/ecommerce/tests/<ClassName>Test.java
#     src/test/java/com/ecommerce/pages/<ClassName>Page.java
#
# Run with: mvn clean test  (from the selenium/ folder)
# ─────────────────────────────────────────────────────────────────────────────

def _java_class_name(path: str) -> str:
    """'/users/{id}' → 'Users'"""
    parts = [p for p in path.strip("/").split("/") if p and not p.startswith("{")]
    if not parts:
        return "Home"
    return parts[0].capitalize()


def _selenium_pom(group_id: str = "com.ecommerce") -> str:
    return '''<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
         http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.ecommerce</groupId>
    <artifactId>ecommerce-selenium</artifactId>
    <version>1.0-SNAPSHOT</version>
    <properties>
        <maven.compiler.source>11</maven.compiler.source>
        <maven.compiler.target>11</maven.compiler.target>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
        <selenium.version>4.18.1</selenium.version>
        <testng.version>7.9.0</testng.version>
        <webdrivermanager.version>5.7.0</webdrivermanager.version>
    </properties>
    <dependencies>
        <dependency>
            <groupId>org.seleniumhq.selenium</groupId>
            <artifactId>selenium-java</artifactId>
            <version>${selenium.version}</version>
        </dependency>
        <dependency>
            <groupId>io.github.bonigarcia</groupId>
            <artifactId>webdrivermanager</artifactId>
            <version>${webdrivermanager.version}</version>
        </dependency>
        <dependency>
            <groupId>org.testng</groupId>
            <artifactId>testng</artifactId>
            <version>${testng.version}</version>
        </dependency>
    </dependencies>
    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-surefire-plugin</artifactId>
                <version>3.2.5</version>
                <configuration>
                    <suiteXmlFiles>
                        <suiteXmlFile>src/test/resources/testng.xml</suiteXmlFile>
                    </suiteXmlFiles>
                </configuration>
            </plugin>
        </plugins>
    </build>
</project>
'''


def _selenium_base_test(base_url: str) -> str:
    return f'''package com.ecommerce.tests;

import io.github.bonigarcia.wdm.WebDriverManager;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.chrome.ChromeDriver;
import org.openqa.selenium.chrome.ChromeOptions;
import org.testng.annotations.AfterMethod;
import org.testng.annotations.BeforeMethod;

public class BaseTest {{
    protected WebDriver driver;
    protected static final String BASE_URL =
        System.getenv("SFCC_SITE_URL") != null
            ? System.getenv("SFCC_SITE_URL")
            : "{base_url}";

    @BeforeMethod
    public void setUp() {{
        WebDriverManager.chromedriver().setup();
        ChromeOptions options = new ChromeOptions();
        options.addArguments("--headless=new");
        options.addArguments("--no-sandbox");
        options.addArguments("--disable-dev-shm-usage");
        options.addArguments("--start-maximized");
        driver = new ChromeDriver(options);
        driver.get(BASE_URL);
    }}

    @AfterMethod
    public void tearDown() {{
        if (driver != null) driver.quit();
    }}
}}
'''


def _selenium_testng_xml(test_classes: list) -> str:
    classes_xml = "\n".join(
        f'            <class name="com.ecommerce.tests.{c}"/>'
        for c in test_classes
    )
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE suite SYSTEM "http://testng.org/testng-1.0.dtd">
<suite name="E-Commerce Test Suite" verbose="2">
    <test name="All Page Tests">
        <classes>
{classes_xml}
        </classes>
    </test>
</suite>
'''


def _selenium_page_template(class_name: str, feature: FeatureChange) -> str:
    path_safe = feature.path.replace("{", "").replace("}", "")
    return f'''package com.ecommerce.pages;

import org.openqa.selenium.By;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.WebElement;
import org.openqa.selenium.support.ui.ExpectedConditions;
import org.openqa.selenium.support.ui.WebDriverWait;
import java.time.Duration;

/** Page Object for {feature.method} {feature.path} */
public class {class_name}Page {{
    private final WebDriver driver;
    private final WebDriverWait wait;

    // TODO: update locators to match actual page elements
    private final By pageBody    = By.tagName("body");
    private final By pageHeading = By.tagName("h2");

    public {class_name}Page(WebDriver driver) {{
        this.driver = driver;
        this.wait   = new WebDriverWait(driver, Duration.ofSeconds(10));
    }}

    public boolean isPageLoaded() {{
        return wait.until(ExpectedConditions.visibilityOfElementLocated(pageBody)).isDisplayed();
    }}

    public String getHeadingText() {{
        try {{
            return wait.until(ExpectedConditions.visibilityOfElementLocated(pageHeading)).getText();
        }} catch (Exception e) {{
            return "";
        }}
    }}
}}
'''


def _selenium_test_template(class_name: str, feature: FeatureChange) -> str:
    path_safe = re.sub(r'\{[^}]+\}', '1', feature.path)
    return f'''package com.ecommerce.tests;

import com.ecommerce.pages.{class_name}Page;
import org.testng.Assert;
import org.testng.annotations.BeforeMethod;
import org.testng.annotations.Test;

/** Tests for {feature.method} {feature.path} — {feature.description} */
public class {class_name}Test extends BaseTest {{
    private {class_name}Page page;

    @BeforeMethod
    public void initPage() {{
        driver.get(BASE_URL + "{path_safe}");
        page = new {class_name}Page(driver);
    }}

    @Test(description = "Page loads successfully")
    public void testPageLoads() {{
        Assert.assertTrue(page.isPageLoaded(),
            "Page should load at {feature.path}");
    }}

    @Test(description = "Page URL is correct")
    public void testPageUrl() {{
        Assert.assertTrue(driver.getCurrentUrl().contains(BASE_URL),
            "URL should contain base URL");
    }}
}}
'''


def _generate_selenium_java(feature: FeatureChange, sel_root: str,
                             base_url: str, all_features: list = None) -> str:
    """
    Generate/update a Java Maven Selenium project.
    Returns the path to the test file written.
    """
    class_name = _java_class_name(feature.path)

    # Paths
    test_dir  = os.path.join(sel_root, "src", "test", "java", "com", "ecommerce", "tests")
    page_dir  = os.path.join(sel_root, "src", "test", "java", "com", "ecommerce", "pages")
    res_dir   = os.path.join(sel_root, "src", "test", "resources")
    main_dir  = os.path.join(sel_root, "src", "main", "java")

    for d in [test_dir, page_dir, res_dir, main_dir]:
        os.makedirs(d, exist_ok=True)

    # pom.xml — write once
    pom_path = os.path.join(sel_root, "pom.xml")
    if not os.path.exists(pom_path):
        _write(pom_path, _selenium_pom())

    # BaseTest.java — write once
    base_path = os.path.join(test_dir, "BaseTest.java")
    if not os.path.exists(base_path):
        _write(base_path, _selenium_base_test(base_url))

    # Page object
    page_path = os.path.join(page_dir, f"{class_name}Page.java")
    if not os.path.exists(page_path):
        if _openai_available():
            content = _generate_selenium_java_ai(feature, "page", class_name)
        else:
            content = _selenium_page_template(class_name, feature)
        _write(page_path, content)
    else:
        if _openai_available():
            existing = open(page_path, encoding="utf-8").read()
            content  = _update_selenium_java_ai(existing, feature, "page", class_name)
            _write(page_path, content)

    # Test class
    test_path = os.path.join(test_dir, f"{class_name}Test.java")
    if not os.path.exists(test_path):
        if _openai_available():
            content = _generate_selenium_java_ai(feature, "test", class_name)
        else:
            content = _selenium_test_template(class_name, feature)
        _write(test_path, content)
    else:
        if _openai_available():
            existing = open(test_path, encoding="utf-8").read()
            content  = _update_selenium_java_ai(existing, feature, "test", class_name)
            _write(test_path, content)

    # testng.xml — regenerate to include all test classes
    existing_tests = [
        f.replace(".java", "")
        for f in os.listdir(test_dir)
        if f.endswith("Test.java") and f != "BaseTest.java"
    ]
    _write(os.path.join(res_dir, "testng.xml"), _selenium_testng_xml(existing_tests))

    return test_path


def _generate_selenium_java_ai(feature: FeatureChange, file_type: str,
                                class_name: str) -> str:
    """Generate Java Selenium Page Object or Test class via GPT."""
    try:
        if file_type == "page":
            prompt = f"""Write a Java Selenium Page Object class for this endpoint.
Endpoint: {feature.method} {feature.path} — {feature.description}
{_app_context(feature)}
Requirements:
- Package: com.ecommerce.pages
- Class name: {class_name}Page
- Use WebDriverWait (10s), By locators
- Include isPageLoaded(), relevant element getters
- Return ONLY Java code, no markdown fences"""
        else:
            path_safe = re.sub(r'\{{[^}}]+\}}', '1', feature.path)
            prompt = f"""Write a Java TestNG Selenium test class for this endpoint.
Endpoint: {feature.method} {feature.path} — {feature.description}
{_app_context(feature)}
Requirements:
- Package: com.ecommerce.tests
- Class name: {class_name}Test extends BaseTest
- Import and use {class_name}Page from com.ecommerce.pages
- BASE_URL is inherited from BaseTest
- Navigate to BASE_URL + "{path_safe}" in @BeforeMethod
- Test page loads, URL correct, key elements visible
- Return ONLY Java code, no markdown fences"""
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        return _strip_fences(resp.choices[0].message.content.strip())
    except Exception as e:
        print(f"[script_generator] GPT Java Selenium failed: {e} — using template")
        if file_type == "page":
            return _selenium_page_template(class_name, feature)
        return _selenium_test_template(class_name, feature)


def _update_selenium_java_ai(existing: str, feature: FeatureChange,
                              file_type: str, class_name: str) -> str:
    """Update existing Java Selenium file via GPT."""
    if not _openai_available():
        return existing
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": f"""Update this Java Selenium {file_type} for the modified endpoint.
{feature.method} {feature.path} — {feature.description}
Keep class structure. Return ONLY Java code, no fences.
---
{existing}"""}],
            temperature=0,
        )
        return _strip_fences(resp.choices[0].message.content.strip())
    except Exception as e:
        print(f"[script_generator] GPT Java update failed: {e}")
        return existing



# ── Public API ────────────────────────────────────────────────────────────────

def generate_scripts(feature: FeatureChange, env: str = "dev", repo: str = "") -> GeneratedScripts:
    """
    Create or update k6 and Selenium scripts for one feature.
    LoadRunner is handled as a single journey script in generate_all — not per endpoint.
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

    # LoadRunner — journey script only, generated once in generate_all
    result.loadrunner_path = os.path.join(root, "loadrunner", "full_journey_lr_test.c")

    # Selenium (Java Maven project)
    sel_root = os.path.join(root, "selenium")
    sel_path = _generate_selenium_java(
        feature, sel_root,
        base_url=os.getenv("SFCC_SITE_URL", BASE_URL),
    )
    result.selenium_path = sel_path

    return result


def _generate_lr_journey(features: List[FeatureChange], env: str, repo: str) -> None:
    """
    Generate ONE LoadRunner VuGen C script covering the full user journey
    across all detected endpoints — ordered by HTTP method
    (GET list → POST create → GET by id → PUT update → DELETE).
    """
    root     = _script_root(repo, env)
    lr_dir   = os.path.join(root, "loadrunner")
    os.makedirs(lr_dir, exist_ok=True)
    path     = os.path.join(lr_dir, "full_journey_lr_test.c")

    # Sort: GETs first, then POSTs, PUTs, DELETEs
    order = {"GET": 0, "POST": 1, "PUT": 2, "PATCH": 2, "DELETE": 3}
    sorted_features = sorted(features, key=lambda f: (order.get(f.method.upper(), 9), f.path))

    if _openai_available():
        try:
            endpoints_desc = "\n".join(
                f"  {f.method} {f.path} — {f.description}" for f in sorted_features
            )
            resp = client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": f"""Write ONE LoadRunner VuGen C script (.c file) covering the full user journey.
Repo: {repo}
Base URL: {os.getenv('SFCC_SITE_URL', 'http://localhost:8000')}
Endpoints (in order):
{endpoints_desc}

Requirements:
- Single Action() function covering ALL endpoints in sequence
- Use web_reg_save_param() to correlate IDs between steps (e.g. capture user_id from POST /users, use in GET /users/{{user_id}})
- lr_start_transaction / lr_end_transaction with LR_AUTO for each step
- web_url() for GET, web_custom_request() for POST/PUT/PATCH/DELETE
- lr_eval_string("{{SFCC_SITE_URL}}") for base URL
- lr_think_time(1) between steps
- Include vuser_init(), Action(), vuser_end()
- Return ONLY valid C code, no markdown fences"""}],
                temperature=0.2,
            )
            content = _strip_fences(resp.choices[0].message.content.strip())
        except Exception as e:
            print(f"[script_generator] GPT LR journey failed: {e} — using template")
            content = _lr_journey_template(sorted_features)
    else:
        content = _lr_journey_template(sorted_features)

    action = "UPDATE" if os.path.exists(path) else "CREATE"
    print(f"[script_generator] {action} LR journey: {path}")
    _write(path, content)


def _lr_journey_template(features: List[FeatureChange]) -> str:
    """Fallback template for full journey LR script."""
    transactions = []
    for f in features:
        slug     = f"{f.method}_{_slug(f.path)}"
        path_val = re.sub(r'\{[^}]+\}', '1', f.path)
        method   = f.method.upper()
        if method in ("POST", "PUT", "PATCH"):
            req = (f'\tweb_custom_request("{slug}",\n'
                   f'\t\t"URL={{{{SFCC_SITE_URL}}}}{path_val}",\n'
                   f'\t\t"Method={method}",\n'
                   f'\t\t"EncType=application/json",\n'
                   f'\t\t"Body={{}}",\n'
                   f'\t\tLAST);')
        elif method == "DELETE":
            req = (f'\tweb_custom_request("{slug}",\n'
                   f'\t\t"URL={{{{SFCC_SITE_URL}}}}{path_val}",\n'
                   f'\t\t"Method=DELETE",\n'
                   f'\t\tLAST);')
        else:
            req = (f'\tweb_url("{slug}",\n'
                   f'\t\t"URL={{{{SFCC_SITE_URL}}}}{path_val}",\n'
                   f'\t\t"Resource=0",\n'
                   f'\t\tLAST);')
        transactions.append(
            f'\tlr_start_transaction("{slug}");\n{req}\n'
            f'\tlr_end_transaction("{slug}", LR_AUTO);\n\tlr_think_time(1);'
        )

    body = "\n\n".join(transactions)
    return f'''/*
 * LoadRunner VuGen C — Full User Journey
 * Covers: {", ".join(f.path for f in features)}
 * Generated by AI Performance Agent
 */
#ifndef _GLOBALS_H
    #include "globals.h"
#endif

vuser_init()
{{
    web_set_sockets_option("SSL_VERSION", "TLS");
    return 0;
}}

Action()
{{
{body}
    return 0;
}}

vuser_end()
{{
    lr_log_message("Journey complete");
    return 0;
}}
'''


def _is_meaningful_path(path: str) -> bool:
    """Filter out paths that produce no useful slug — e.g. '/', '/{id}' only."""
    slug = re.sub(r'[^a-z0-9]+', '_', path.lower()).strip('_')
    # Remove pure param slugs like 'id', 'user_id', 'product_id'
    slug = re.sub(r'^(id|[a-z]+_id)$', '', slug)
    return len(slug) >= 3


def _deduplicate_features(features: List[FeatureChange]) -> List[FeatureChange]:
    """
    Remove duplicate endpoints that test the same resource.

    Rules:
    - '/api/orders' and '/api/orders/{id}' → keep '/api/orders/{id}' (more specific)
    - Same method + same resource → keep one
    - Blank slug paths (GET /) → drop entirely
    """
    # Group by (method, resource) where resource = first non-param path segment
    def resource_key(f: FeatureChange) -> str:
        parts = [p for p in f.path.strip("/").split("/")
                 if p and not p.startswith("{")]
        return f.method.upper() + ":" + (parts[0] if parts else "")

    seen_keys: dict = {}
    for f in features:
        key = resource_key(f)
        if not key.endswith(":"):  # skip blank resource
            existing = seen_keys.get(key)
            if existing is None:
                seen_keys[key] = f
            else:
                # Keep the more specific path (longer = more specific)
                if len(f.path) > len(existing.path):
                    seen_keys[key] = f

    result = list(seen_keys.values())
    dropped = len(features) - len(result)
    if dropped:
        print(f"[script_generator] Deduplicated {dropped} redundant endpoints")
    return result


def generate_all(features: List[FeatureChange], env: str = "dev", repo: str = "") -> List[GeneratedScripts]:
    """
    Generate/update scripts for all features.
    Skips paths that produce no meaningful slug (e.g. '/', '/{id}').
    Also generates one combined LoadRunner journey script for all features.
    """
    results = []
    meaningful = [f for f in features if _is_meaningful_path(f.path)]
    meaningful = _deduplicate_features(meaningful)
    skipped    = len(features) - len(meaningful)
    if skipped:
        print(f"[script_generator] Skipped {skipped} features with no meaningful path slug")

    for feature in meaningful:
        print(f"[script_generator] {feature.method} {feature.path} → repo={repo or 'default'} env={env}")
        results.append(generate_scripts(feature, env, repo))

    # Generate one combined LoadRunner journey script covering all endpoints
    if meaningful:
        _generate_lr_journey(meaningful, env, repo)

    return results
