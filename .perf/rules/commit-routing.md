# Commit Routing Rules

Defines when performance scripts should be created or updated.

## Trigger Conditions

- If API contract changes (new route, modified endpoint signature) → update API k6 tests
- If UI checkout flow changes → update Selenium checkout flow
- If search endpoint changes → update search load scenario
- If auth/login endpoint changes → update login k6 + Selenium login flow
- If cart/order endpoint changes → update cart k6 tests
- If dependency version changes (requirements.txt, package.json) → patch thresholds, add smoke test for upgraded package
- Ignore docs-only changes (*.md, *.txt, *.rst)
- Ignore test file changes (tests/**, spec/**)
- Ignore config-only changes (.env, *.yml, *.yaml) unless they affect API routes

## Risk Classification

- `high`: checkout, payment, auth, order endpoints
- `medium`: search, cart, product, user profile endpoints
- `low`: static content, health checks, admin endpoints

## Script Update Strategy

- Incremental edits only — do NOT regenerate stable scripts unnecessarily
- Preserve reusable modules (common/auth.js, common/helpers.js)
- Only update scripts mapped to changed domains
- Generate PR notes describing what changed and why
