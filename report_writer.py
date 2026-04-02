"""
report_writer.py
────────────────
Writes every agent run as a structured report file under reports/
Always runs — regardless of whether Slack / Email / Jira are configured.

Output files:
  reports/report_YYYYMMDD_HHMMSS.md   ← human-readable Markdown
  reports/report_YYYYMMDD_HHMMSS.json ← raw JSON for programmatic use
"""

import os
import json
from datetime import datetime

REPORTS_DIR = "reports"


def _ensure_dir():
    os.makedirs(REPORTS_DIR, exist_ok=True)


def _k6_summary(k6: dict) -> str:
    """Format k6 results into a readable table."""
    results = k6.get("results", [])
    if not results:
        return f"Status: {k6.get('status', 'unknown')}\n"

    lines = ["| Script | Result |", "|--------|--------|"]
    for r in results:
        status = "✅ passed" if r.get("exit_code") == 0 else "❌ FAILED"
        lines.append(f"| `{r['script']}` | {status} |")
    return "\n".join(lines)


def write_report(result: dict, orch_markdown: str = "") -> str:
    """
    Write a Markdown + JSON report for one agent run.

    Parameters
    ----------
    result        : the dict returned by run_agent()
    orch_markdown : optional orchestration summary from test_orchestrator

    Returns
    -------
    Path to the written Markdown report file.
    """
    _ensure_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path   = os.path.join(REPORTS_DIR, f"report_{timestamp}.md")
    json_path = os.path.join(REPORTS_DIR, f"report_{timestamp}.json")

    # ── Build Markdown ────────────────────────────────────────────────────────
    issues    = result.get("issues", [])
    anomalies = result.get("anomalies", {})
    ai        = result.get("ai_analysis", "N/A")
    diag      = result.get("diagnostics", {})
    k6_data   = result.get("tools_output", {}).get("k6", {})
    system    = result.get("system", {})

    issue_lines = "\n".join(f"- {i}" for i in issues) if issues else "- None detected"

    md = f"""# AI Performance Report
Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

---

## k6 Test Results
{_k6_summary(k6_data)}

---

## Issues Detected
{issue_lines}

---

## System Diagnostics
| Metric | Value | Status |
|--------|-------|--------|
| CPU | {system.get('cpu', 'N/A')}% | {'⚠️ high' if float(system.get('cpu', 0)) > 80 else '✅ normal'} |
| Memory | {system.get('memory', 'N/A')}% | {'⚠️ high' if float(system.get('memory', 0)) > 85 else '✅ normal'} |
| Latency p95 | {diag.get('latency', {}).get('p95_threshold', 'N/A')}ms | {diag.get('latency', {}).get('status', 'N/A')} |

---

## AI Root Cause Analysis
{ai}

---

## Anomalies
```json
{json.dumps(anomalies, indent=2)}
```
"""

    # Append orchestration section if present
    if orch_markdown:
        md += f"\n---\n\n## Code Change Detection\n{orch_markdown}\n"

    # ── Write files ───────────────────────────────────────────────────────────
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str, ensure_ascii=False)

    print(f"[report] Written: {md_path}")
    print(f"[report] Written: {json_path}")

    return md_path
