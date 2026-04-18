# Execution Policy

Defines what performance tests run on each trigger event.

## On Pull Request / Code Push

Run:
- Impacted k6 smoke tests (10 VUs, 30s) for changed domains only
- Impacted Selenium smoke flows for critical journeys
- Config/schema validation

Goal: Catch obvious perf and journey breakage quickly.
Merge gate: WARN on >20% regression, BLOCK on >50% regression or critical flow failure.

## On Merge to Main

Run:
- Broader service-level k6 regression tests (30 VUs, 5m)
- Higher concurrency flows for impacted domains
- Cross-service scenarios

Goal: Detect regression before release.
Baseline update: Update baseline if run is green.

## Nightly

Run:
- Endurance tests (75 VUs, 30m)
- Spike tests (ramp 0→100→0 VUs)
- Mixed workload scenarios
- Expanded Selenium browser matrix
- LoadRunner enterprise scenarios (if enabled)

Goal: Trend analysis and baseline refresh.

## Pre-Release / Release Certification

Run:
- Full certification suite
- Capacity and soak tests
- Comparison against release threshold
- All domains, all profiles

Goal: Release confidence sign-off.

## Skipped Conditions

- Docs-only changes (*.md, *.txt)
- Test file changes only (tests/**)
- Non-main branch pushes (unless PR)
- Dependency-only changes with no risk classification
