"""
agent/test_case_generator.py
----------------------------
Scans source code to generate positive and negative test cases.
Outputs CSV files that can be consumed by the MCP test generator (Mode 2)
or used standalone for manual test planning.

CSV columns:
  test_id, type, test_name, page_or_endpoint, method, input_data,
  expected_result, priority, description

Usage:
  from agent.test_case_generator import generate_test_cases
  csv_path = generate_test_cases(repo_path, output_dir, repo_name)
"""

import csv
import json
import os
import re
import logging
from dataclasses import dataclass
from typing import List, Optional

from agent.llm_provider import llm_available, llm_chat

logger = logging.getLogger(__name__)


@dataclass
class TestCase:
    test_id: str
    type: str          # "positive" or "negative"
    test_name: str
    page_or_endpoint: str
    method: str
    input_data: str    # JSON string
    expected_result: str
    priority: str      # "high", "medium", "low"
    description: str


def _scan_endpoints(repo_path: str) -> List[dict]:
    """Walk the repo and extract HTTP endpoints/routes from source files."""
    endpoints = []
    route_patterns = [
        (r'@\w+\.route\(["\']([^"\']+)["\'].*?methods=\[.*?["\'](\w+)["\']', "flask_route"),
        (r'@\w+\.(get|post|put|patch|delete)\(["\']([^"\']+)["\']', "decorator"),
        (r'app\.(get|post|put|patch|delete)\(["\']([^"\']+)["\']', "express"),
        (r'router\.(get|post|put|patch|delete)\(["\']([^"\']+)["\']', "router"),
    ]

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in (
            "node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build")]
        for fname in files:
            if not fname.endswith((".py", ".js", ".ts", ".java")):
                continue
            fpath = os.path.join(root, fname)
            try:
                content = open(fpath, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue

            for pattern, style in route_patterns:
                for match in re.finditer(pattern, content):
                    if style == "flask_route":
                        path, method = match.group(1), match.group(2).upper()
                    elif style in ("decorator", "express", "router"):
                        method, path = match.group(1).upper(), match.group(2)
                    else:
                        continue
                    endpoints.append({
                        "path": path,
                        "method": method,
                        "file": os.path.relpath(fpath, repo_path),
                    })

    seen = set()
    unique = []
    for ep in endpoints:
        key = (ep["method"], ep["path"])
        if key not in seen:
            seen.add(key)
            unique.append(ep)
    return unique


def _scan_html_forms(repo_path: str) -> List[dict]:
    """Scan HTML/template files for forms, inputs, buttons."""
    forms = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in (
            "node_modules", ".git", "__pycache__", "venv", "dist")]
        for fname in files:
            if not fname.endswith((".html", ".htm", ".jinja2", ".ejs")):
                continue
            fpath = os.path.join(root, fname)
            try:
                content = open(fpath, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue

            form_matches = re.finditer(
                r'<form[^>]*id=["\']([^"\']*)["\'][^>]*>', content, re.IGNORECASE)
            for m in form_matches:
                form_id = m.group(1)
                form_block = content[m.start():m.start() + 2000]
                inputs = re.findall(
                    r'<input[^>]*id=["\']([^"\']*)["\'][^>]*>', form_block, re.IGNORECASE)
                buttons = re.findall(
                    r'<button[^>]*>(.*?)</button>', form_block, re.IGNORECASE)
                forms.append({
                    "form_id": form_id,
                    "file": os.path.relpath(fpath, repo_path),
                    "inputs": inputs,
                    "buttons": [b.strip() for b in buttons],
                })
    return forms


def _generate_cases_with_llm(endpoints: list, forms: list) -> List[TestCase]:
    """Use LLM to generate test cases from scanned endpoints and forms."""
    context_parts = []
    if endpoints:
        ep_desc = "\n".join(f"  {e['method']} {e['path']} (file: {e['file']})" for e in endpoints)
        context_parts.append(f"API Endpoints:\n{ep_desc}")
    if forms:
        form_desc = "\n".join(
            f"  Form #{f['form_id']} in {f['file']}: inputs={f['inputs']}, buttons={f['buttons']}"
            for f in forms)
        context_parts.append(f"HTML Forms:\n{form_desc}")

    context = "\n\n".join(context_parts)

    prompt = f"""Analyze these application endpoints and forms. Generate BOTH positive and negative test cases.

{context}

Generate a JSON array of test cases. For EACH endpoint/form, create:
- 2-3 positive test cases (valid inputs, expected success)
- 2-3 negative test cases (invalid inputs, boundary values, empty fields, wrong types, SQL injection, XSS)

Each test case object must have ALL these fields:
{{
  "type": "positive" or "negative",
  "test_name": "short_snake_case_name",
  "page_or_endpoint": "the endpoint path or page",
  "method": "GET/POST/PUT/DELETE",
  "input_data": {{"field1": "value1"}},
  "expected_result": "what should happen",
  "priority": "high/medium/low",
  "description": "what this test verifies"
}}

Example positive:
{{
  "type": "positive",
  "test_name": "create_user_valid",
  "page_or_endpoint": "/users/",
  "method": "POST",
  "input_data": {{"name": "John Doe", "email": "john@test.com"}},
  "expected_result": "201 Created, user object returned with id",
  "priority": "high",
  "description": "Create user with valid name and email"
}}

Example negative:
{{
  "type": "negative",
  "test_name": "create_user_missing_email",
  "page_or_endpoint": "/users/",
  "method": "POST",
  "input_data": {{"name": "John Doe"}},
  "expected_result": "400 or 422 validation error",
  "priority": "high",
  "description": "Attempt to create user without required email field"
}}

Output ONLY the JSON array, nothing else."""

    try:
        response = llm_chat(prompt, temperature=0.3,
                            system="You are a QA engineer generating comprehensive test cases. Output only valid JSON.")
        start = response.find("[")
        end = response.rfind("]") + 1
        if start == -1 or end == 0:
            logger.warning("No JSON array in LLM response for test cases")
            return []
        raw_cases = json.loads(response[start:end])
        cases = []
        for i, c in enumerate(raw_cases):
            if not isinstance(c, dict):
                continue
            input_data = c.get("input_data", {})
            if isinstance(input_data, dict):
                input_data = json.dumps(input_data)
            elif not isinstance(input_data, str):
                input_data = str(input_data)
            cases.append(TestCase(
                test_id=f"TC_{i+1:03d}",
                type=c.get("type", "positive"),
                test_name=c.get("test_name", f"test_{i+1}"),
                page_or_endpoint=c.get("page_or_endpoint", "/"),
                method=c.get("method", "GET"),
                input_data=input_data,
                expected_result=c.get("expected_result", "success"),
                priority=c.get("priority", "medium"),
                description=c.get("description", ""),
            ))
        return cases
    except Exception as e:
        logger.error(f"LLM test case generation failed: {e}")
        return []


def _generate_cases_from_templates(endpoints: list, forms: list) -> List[TestCase]:
    """Fallback: generate test cases from templates without LLM."""
    cases = []
    tc_num = 0

    for ep in endpoints:
        method = ep["method"].upper()
        path = ep["path"]

        # Positive cases
        tc_num += 1
        if method == "GET":
            cases.append(TestCase(
                test_id=f"TC_{tc_num:03d}", type="positive",
                test_name=f"get_{path.strip('/').replace('/', '_')}_success",
                page_or_endpoint=path, method=method,
                input_data="{}",
                expected_result="200 OK with response body",
                priority="high",
                description=f"GET {path} returns successful response",
            ))
        elif method == "POST":
            cases.append(TestCase(
                test_id=f"TC_{tc_num:03d}", type="positive",
                test_name=f"create_{path.strip('/').replace('/', '_')}_valid",
                page_or_endpoint=path, method=method,
                input_data='{"name": "test", "email": "test@test.com"}',
                expected_result="201 Created with resource object",
                priority="high",
                description=f"POST {path} with valid data creates resource",
            ))
        elif method in ("PUT", "PATCH"):
            cases.append(TestCase(
                test_id=f"TC_{tc_num:03d}", type="positive",
                test_name=f"update_{path.strip('/').replace('/', '_')}_valid",
                page_or_endpoint=path, method=method,
                input_data='{"name": "updated"}',
                expected_result="200 OK with updated resource",
                priority="high",
                description=f"{method} {path} with valid data updates resource",
            ))
        elif method == "DELETE":
            cases.append(TestCase(
                test_id=f"TC_{tc_num:03d}", type="positive",
                test_name=f"delete_{path.strip('/').replace('/', '_')}_success",
                page_or_endpoint=path, method=method,
                input_data="{}",
                expected_result="200 or 204 resource deleted",
                priority="high",
                description=f"DELETE {path} removes the resource",
            ))

        # Negative cases
        tc_num += 1
        if method in ("POST", "PUT", "PATCH"):
            cases.append(TestCase(
                test_id=f"TC_{tc_num:03d}", type="negative",
                test_name=f"{method.lower()}_{path.strip('/').replace('/', '_')}_empty_body",
                page_or_endpoint=path, method=method,
                input_data="{}",
                expected_result="400 or 422 validation error",
                priority="high",
                description=f"{method} {path} with empty body returns validation error",
            ))
        else:
            cases.append(TestCase(
                test_id=f"TC_{tc_num:03d}", type="negative",
                test_name=f"{method.lower()}_{path.strip('/').replace('/', '_')}_not_found",
                page_or_endpoint=path.rstrip("/") + "/99999",
                method=method,
                input_data="{}",
                expected_result="404 Not Found",
                priority="medium",
                description=f"{method} {path} with non-existent ID returns 404",
            ))

        tc_num += 1
        cases.append(TestCase(
            test_id=f"TC_{tc_num:03d}", type="negative",
            test_name=f"{method.lower()}_{path.strip('/').replace('/', '_')}_invalid_type",
            page_or_endpoint=path, method=method,
            input_data='{"id": "not_a_number"}',
            expected_result="400 or 422 type error",
            priority="medium",
            description=f"{method} {path} with wrong data types returns error",
        ))

    for form in forms:
        tc_num += 1
        cases.append(TestCase(
            test_id=f"TC_{tc_num:03d}", type="positive",
            test_name=f"submit_{form['form_id']}_valid",
            page_or_endpoint=f"#{form['form_id']}",
            method="FORM_SUBMIT",
            input_data=json.dumps({inp: "valid_value" for inp in form.get("inputs", [])}),
            expected_result="Form submits successfully, toast/confirmation shown",
            priority="high",
            description=f"Submit form {form['form_id']} with all valid inputs",
        ))
        tc_num += 1
        cases.append(TestCase(
            test_id=f"TC_{tc_num:03d}", type="negative",
            test_name=f"submit_{form['form_id']}_empty",
            page_or_endpoint=f"#{form['form_id']}",
            method="FORM_SUBMIT",
            input_data=json.dumps({inp: "" for inp in form.get("inputs", [])}),
            expected_result="Validation error shown, form not submitted",
            priority="high",
            description=f"Submit form {form['form_id']} with all empty fields",
        ))

    return cases


def _write_csv(cases: List[TestCase], output_path: str) -> str:
    """Write test cases to CSV file."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "test_id", "type", "test_name", "page_or_endpoint", "method",
            "input_data", "expected_result", "priority", "description",
        ])
        for tc in cases:
            writer.writerow([
                tc.test_id, tc.type, tc.test_name, tc.page_or_endpoint,
                tc.method, tc.input_data, tc.expected_result, tc.priority,
                tc.description,
            ])
    logger.info(f"Written {len(cases)} test cases to {output_path}")
    return output_path


def generate_test_cases(
    repo_path: str,
    output_dir: str,
    repo_name: str = "app",
) -> str:
    """
    Main entry point: scan code, generate test cases, write CSV.
    Returns path to the generated CSV file.
    """
    logger.info(f"Scanning {repo_path} for endpoints and forms...")
    endpoints = _scan_endpoints(repo_path)
    forms = _scan_html_forms(repo_path)
    logger.info(f"Found {len(endpoints)} endpoints, {len(forms)} forms")

    if not endpoints and not forms:
        logger.warning("No endpoints or forms found to generate test cases")
        return ""

    if llm_available():
        logger.info("Using LLM to generate comprehensive test cases")
        cases = _generate_cases_with_llm(endpoints, forms)
        if not cases:
            logger.warning("LLM generation returned no cases, falling back to templates")
            cases = _generate_cases_from_templates(endpoints, forms)
    else:
        logger.info("LLM not available, using template-based test case generation")
        cases = _generate_cases_from_templates(endpoints, forms)

    positive = [c for c in cases if c.type == "positive"]
    negative = [c for c in cases if c.type == "negative"]
    logger.info(f"Generated {len(positive)} positive, {len(negative)} negative test cases")

    csv_path = os.path.join(output_dir, repo_name, "test_cases.csv")
    return _write_csv(cases, csv_path)


def load_test_cases(csv_path: str) -> List[TestCase]:
    """Load test cases from CSV file for use by MCP generator."""
    cases = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cases.append(TestCase(
                test_id=row["test_id"],
                type=row["type"],
                test_name=row["test_name"],
                page_or_endpoint=row["page_or_endpoint"],
                method=row["method"],
                input_data=row["input_data"],
                expected_result=row["expected_result"],
                priority=row["priority"],
                description=row["description"],
            ))
    return cases
