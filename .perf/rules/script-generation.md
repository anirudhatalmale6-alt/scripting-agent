# Script Generation Standards

Defines standards for all generated performance test scripts.

## Naming Conventions

- k6 scripts: `<endpoint_slug>_perf_test.js` (e.g., `api_checkout_perf_test.js`)
- Selenium tests: `<ClassName>Test.java` with matching `<ClassName>Page.java`
- LoadRunner: `full_journey_lr_test.c` (single combined journey per repo/env)

## k6 Script Standards

Every generated k6 script MUST include:

- `export const options` block with vus, duration, thresholds
- `const BASE_URL = __ENV.SFCC_SITE_URL || 'https://test.k6.io'`
- `const IS_REAL_APP = !BASE_URL.includes('test.k6.io')` guard
- `check()` assertions for status codes and response time
- `sleep(1)` between iterations
- Tags: `{ domain, owner, type, triggerPaths }`
- Correlation/token handling for authenticated endpoints
- Test data separation (no hardcoded credentials)

## Threshold Standards

Default thresholds (override per domain in thresholds.yaml):
- `http_req_duration`: p(95) < 2000ms
- `http_req_failed`: rate < 0.05 (5%)
- High-risk endpoints: p(95) < 1000ms, rate < 0.01

## Scenario Templates

- `smoke`: 10 VUs, 30s — quick sanity check on PR
- `regression`: 30 VUs, 5m — broader coverage on merge
- `endurance`: 75 VUs, 30m — nightly soak test
- `spike`: ramp 0→100→0 VUs over 5m — stress test

## Reusable Modules

Always import from common/ when available:
- `common/auth.js` — authentication helpers
- `common/config.js` — environment config
- `common/helpers.js` — shared utilities
- `common/thresholds.js` — shared threshold definitions
- `common/data.js` — test data helpers

## Selenium Standards

- Page Object Model pattern (Page class + Test class)
- Java/Maven project structure with TestNG
- BaseTest.java for shared setup/teardown
- Locators in Page class only (never in Test class)
- Assertions in Test class only
- No hardcoded waits — use explicit waits

## LoadRunner Standards

- VuGen C format
- Single combined journey script per environment
- Ordered: GET list → POST create → GET by id → PUT update → DELETE
- Correlation rules for dynamic values (tokens, IDs)
- Parameter files for test data
