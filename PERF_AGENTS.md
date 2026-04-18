# Performance Agent Platform — Skills & Rules

This file is loaded by all performance agents at runtime as their core instruction set.
It defines the platform behaviour, agent responsibilities, and decision rules.

---

## Platform Overview

This is a repo-installed performance control plane.
Two agents work together on every code change:

1. **Scripting Agent** — reads code changes, updates test scripts
2. **Execution Agent (RCA Agent)** — runs scripts, compares baselines, reports regressions

All agent behaviour is governed by policy files in `.perf/`:
- `.perf/mappings.yaml` — maps code paths to affected test domains
- `.perf/thresholds.yaml` — domain-specific SLO thresholds
- `.perf/profiles/` — execution profiles per trigger (PR, merge, nightly)
- `.perf/rules/` — natural language rules for routing, generation, execution, regression

---

## Agent Decision Rules

### When to act

- Act on: new endpoint added, existing endpoint modified, dependency version changed
- Skip: docs-only changes (*.md, *.txt), test file changes, config-only changes
- Always check `.perf/rules/commit-routing.md` before deciding to generate or update

### What to update

- Use `.perf/mappings.yaml` first — deterministic path-to-test mapping
- Only fall back to LLM reasoning when mapping is ambiguous or missing
- Only update scripts mapped to the changed domain — never touch unrelated scripts

### Risk classification

- `high`: checkout, payment, auth, order endpoints → block merge on severe regression
- `medium`: search, cart, product, user → warn on regression
- `low`: static content, health checks → informational only

---

## Shared Constraints

- Never regenerate a stable script from scratch — do incremental edits only
- Never hardcode thresholds — always read from `.perf/thresholds.yaml`
- Never hardcode VUs or duration — always read from `.perf/profiles/`
- Always tag generated scripts with `{ domain, env }` for traceability
- Always produce a human-readable summary for PR comments
- Graceful degradation: if policy files are missing, use sensible defaults — never crash

---

## Output Format (PR Comment)

Every agent run must produce a summary in this format:

```
Performance impact summary

Changed areas: <domains>
Scripts updated: <list>
Tests executed: <list>
Outcome: Pass / Warn / Fail

Observed deltas:
- <metric>: <before> → <after> (<delta%>)

Likely cause: <one sentence>
Recommendation: <one sentence>
```
