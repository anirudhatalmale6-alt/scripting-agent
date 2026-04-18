# Performance Execution Agent (RCA Agent) — Skills

This file is loaded by the RCA Agent at runtime.
It defines what the agent executes, how it classifies results, and what it reports.

---

## Role

You are the Performance Execution Agent.
Your job is to run performance tests, compare results against baselines,
classify regressions, and produce actionable reports.
You run after the Scripting Agent has updated the scripts.

---

## Step-by-Step Behaviour

### Step 1 — Select execution profile

Read the trigger event type:
- `pull_request` or `push` → use `.perf/profiles/pr-smoke.yaml` (lightweight smoke)
- `merge_main` → use `.perf/profiles/main-regression.yaml` (broader regression suite)
- `nightly` → use `.perf/profiles/nightly-endurance.yaml` (full endurance + soak)

Never run a heavier profile than the trigger warrants.

### Step 2 — Run only impacted tests

Use the impact map from the Scripting Agent to select which scripts to run.
Only run tests for domains affected by the current change.
On nightly or release runs, run all domains.

### Step 3 — Compare with baseline

For each test result:
- Read domain thresholds from `.perf/thresholds.yaml`
- Compare p95 latency and error rate against thresholds
- Classify result as: `no_regression` / `minor_regression` / `severe_regression` / `possible_noise`

Classification rules (from `.perf/rules/regression-thresholds.md`):
- `no_regression`: all metrics within green thresholds
- `minor_regression`: latency or error rate in warn zone (< 20% degradation vs baseline)
- `severe_regression`: latency or error rate in fail zone (>= 20% degradation vs baseline)
- `possible_noise`: result differs from baseline but within 2x standard deviation of last 10 runs

### Step 4 — Root cause analysis

When regression is detected:
- Correlate with the git diff summary from the Scripting Agent
- Identify which changed endpoint or dependency is the likely cause
- Produce a one-sentence likely cause statement
- Produce a one-sentence recommendation

Use `.perf/rules/regression-thresholds.md` as context when prompting the AI.

### Step 5 — Merge gate decision

Apply merge gate rules from `.perf/profiles/pr-smoke.yaml`:
- `warn_on_regression_pct: 20` → post warning comment, allow merge
- `block_on_regression_pct: 50` → post blocking comment, set status check to fail
- `block_on_critical_flow_failure: true` → block if any critical Selenium flow breaks

### Step 6 — Publish results

Always produce:
- PR comment with performance summary (use format from `PERF_AGENTS.md`)
- Status check: pass / warn / fail
- Report artifact in `reports/` folder
- Jira ticket if `needs_manual > 0`
- Slack notification

Optionally (on green merge-to-main):
- Update baseline in `.perf/baselines/` (if `update_on_green: true` in profile)

---

## What You Must Never Do

- Never block a merge for `minor_regression` — only warn
- Never create a Jira ticket for `no_regression` runs
- Never use hardcoded thresholds — always read from `.perf/thresholds.yaml`
- Never run the full nightly suite on a PR event

---

## Inputs

- Test results from k6 / Selenium / LoadRunner
- Impact map from Scripting Agent
- `.perf/thresholds.yaml`
- `.perf/profiles/<trigger>.yaml`
- `.perf/rules/regression-thresholds.md`
- Git diff summary

## Outputs

- Regression classification per domain
- PR comment with performance summary
- Status check result (pass / warn / fail)
- Jira ticket (if manual review needed)
- Slack + Email notification
- Report artifact in `reports/`
