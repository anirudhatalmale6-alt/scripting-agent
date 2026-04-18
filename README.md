# AI Performance Agent

Watches your GitHub repo via webhooks, detects code changes, and automatically generates or updates k6, Selenium, and LoadRunner scripts — driven entirely by policy files and agent skill files, no hardcoded logic.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Docker / Kubernetes                          │
│                                                                     │
│  ┌──────────────────────┐        ┌──────────────────────────────┐  │
│  │  Test Trigger Agent  │        │         RCA Agent            │  │
│  │     port 5001        │        │         port 5000            │  │
│  │                      │        │                              │  │
│  │  code_change_detector│        │  AI root cause analysis      │  │
│  │  script_generator    │        │  Jira / Slack / Email        │  │
│  │  script_patcher      │        │  merge gate decision         │  │
│  │  script_health_check │        │  system diagnostics          │  │
│  │  selenium_index      │        │                              │  │
│  │  commit_tracker      │        │                              │  │
│  └──────────┬───────────┘        └──────────────┬───────────────┘  │
│             │                                   │                  │
│             │   call_tool(...)                  │  call_tool(...)  │
│             └──────────────────┬────────────────┘                  │
│                                ▼                                   │
│              ┌─────────────────────────────────┐                   │
│              │         MCP Server              │                   │
│              │         port 5002               │                   │
│              │                                 │                   │
│              │  GET  /tools                    │                   │
│              │  POST /tools/k6                 │                   │
│              │  POST /tools/run_tests          │                   │
│              │  POST /tools/generate_scripts   │                   │
│              │  POST /tools/get_impact_map     │                   │
│              │  POST /tools/compare_with_baseline                  │
│              │  POST /tools/get_selenium_index │                   │
│              │  POST /tools/find_affected_selenium                 │
│              │  POST /tools/jira               │                   │
│              │  POST /tools/slack              │                   │
│              │  POST /tools/grafana            │                   │
│              │  POST /tools/datadog            │                   │
│              │  POST /tools/github_commits     │                   │
│              └────────────────┬────────────────┘                   │
│                               │                                    │
│         ┌─────────────────────┼──────────────────────┐            │
│         ▼                     ▼                      ▼            │
│  ┌─────────────┐   ┌─────────────────┐   ┌──────────────────┐    │
│  │  Target App │   │  Prometheus     │   │    Grafana       │    │
│  │  (your app) │   │  port 9090      │   │    port 3000     │    │
│  └─────────────┘   └─────────────────┘   └──────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Policy + Skill Layer

All agent behaviour is driven by files — no hardcoded logic.

### Agent Skill Files

| File | Loaded by | Purpose |
|------|-----------|---------|
| `PERF_AGENTS.md` | RCA Agent | Platform rules, PR comment format, risk classification |
| `SCRIPTING_AGENT.md` | Script Generator, Orchestrator | Step-by-step scripting behaviour, routing rules |
| `PERF_EXEC_AGENT.md` | RCA Agent | Execution profile selection, regression classification, merge gate |

### Policy Files (`.perf/` directory)

| File | Purpose |
|------|---------|
| `.perf/mappings.yaml` | Maps code paths → affected test domains |
| `.perf/thresholds.yaml` | Domain-specific SLO thresholds (p95, error rate) |
| `.perf/profiles/pr-smoke.yaml` | PR profile: VUs, duration, merge gate rules |
| `.perf/profiles/main-regression.yaml` | Merge-to-main profile |
| `.perf/profiles/nightly-endurance.yaml` | Nightly soak test profile |
| `.perf/rules/commit-routing.md` | When to generate/skip scripts |
| `.perf/rules/script-generation.md` | Naming, threshold, tagging standards |
| `.perf/rules/execution-policy.md` | What runs on PR vs merge vs nightly |
| `.perf/rules/regression-thresholds.md` | Regression classification rules |

Edit any of these files — changes take effect on the next webhook with no rebuild.

---

## How It Works — Full Flow

```
Developer pushes code to GitHub
          │
          ▼
GitHub sends webhook event
          │
          ├──────────────────────────────────────────────────────────┐
          ▼                                                          ▼
POST :5001/github-webhook                            POST :5000/github-webhook
Test Trigger Agent                                   RCA Agent
(test_trigger_agent.py)                              (rca_agent.py)
          │                                                          │
          │  queued via WebhookQueue (FIFO)                          │  runs immediately
          ▼                                                          ▼
test_orchestrator.py                                 run k6 via MCP → /tools/k6
          │                                          AI RCA using PERF_EXEC_AGENT.md
  ┌───────┴────────────────────────────────┐         merge gate from .perf/profiles/
  │                                        │         Jira / Slack / Email / report
  │  STEP 1 — PRE Health Check             │
  │  Run all existing k6 scripts           │
  │                                        │
  │  STEP 2 — Fix Pre-existing Failures    │
  │  GPT fixes failing scripts (max 5x)    │
  │                                        │
  │  STEP 3 — Build Impact Map             │
  │  .perf/mappings.yaml                   │
  │  → which domains are affected?         │
  │  → skip docs/config-only changes       │
  │                                        │
  │  STEP 4 — Detect Changes               │
  │  code_change_detector.py               │
  │  → Scenario A: dep upgrade             │
  │  → Scenario B: new/changed endpoint    │
  │                                        │
  │  STEP 5 — Route by Domain              │
  │  Only generate for affected domains    │
  │  (not all features in the diff)        │
  │                                        │
  │  STEP 6 — Generate / Update Scripts    │
  │  k6: policy thresholds + profile VUs   │
  │  Selenium: index lookup → surgical     │
  │    update (50 lines not 1000)          │
  │  LoadRunner: combined journey          │
  │                                        │
  │  STEP 7 — POST Health Check            │
  │  Re-run all k6 scripts                 │
  │                                        │
  │  STEP 8 — Merge Gate                   │
  │  .perf/profiles/pr-smoke.yaml          │
  │  → pass / warn / fail                  │
  │  → block merge if high-risk failure    │
  │                                        │
  │  STEP 9 — Save Checkpoint              │
  │                                        │
  └───────────────────────────────────────┘
          │
          ▼
  Post PR comment + merge gate status
  Send Slack notification
```

---

## Selenium Index (for large script sets)

For repos with hundreds or thousands of Selenium scripts, the agent maintains a lightweight JSON index at:

```
scripts/<repo>/dev/selenium/.selenium_index.json
```

This means on every webhook the agent does an O(1) lookup to find affected scripts instead of scanning all files. Updates are surgical — only the relevant 30-50 lines of a 1000-line file are sent to GPT, not the whole file.

```
Code change detected
      │
      ▼
find_affected_selenium(changed_files, index)
→ reads one JSON file
→ returns only matching scripts
      │
      ▼
extract_changed_methods(full_file, feature)
→ extracts ~50 relevant lines
→ GPT updates only that section
→ spliced back into full file
```

Query the index via MCP:
```bash
curl -X POST http://localhost:5002/tools/find_affected_selenium \
  -H "Content-Type: application/json" \
  -d '{"changed_files": ["services/checkout/payment.py"]}'
```

---

## First Boot — Full History Scan

```
Container starts — no scripts, no checkpoint
      │
      ▼
scan_full_repo()
  → fetch all commits (paginated, oldest first)
  → detect_changes() on every commit diff
  → accumulate unique endpoints
  → generate k6 + Selenium + LoadRunner for each
  → build selenium index
  → save checkpoint at HEAD
      │
      ▼
Normal webhook-driven mode
(full scan never runs again unless checkpoint is reset)
```

Reset: `POST http://localhost:5001/checkpoint/reset`

---

## Script Folder Structure

```
scripts/
  <repo_slug>/
    dev/
      k6/
        api_checkout_perf_test.js
        api_login_perf_test.js
      loadrunner/
        full_journey_lr_test.c        ← single combined journey
      selenium/
        .selenium_index.json          ← index of all scripts
        src/test/java/com/ecommerce/
          tests/
            CheckoutTest.java
            LoginTest.java
          pages/
            CheckoutPage.java
            LoginPage.java
        src/test/resources/testng.xml
        pom.xml
    stage/
    prod/
```

---

## Setup

### 1. Clone and configure

```bash
git clone <your-repo>
cp .env.example .env
# Fill in OPENAI_API_KEY, GITHUB_TOKEN, GITHUB_REPOS, SFCC_SITE_URL
```

### 2. Run

```bash
docker-compose up --build
```

| Service | URL | Purpose |
|---------|-----|---------|
| Test Trigger Agent | http://localhost:5001 | Webhooks, script generation |
| RCA Agent | http://localhost:5010 | k6 runs, AI RCA, reports |
| MCP Server | http://localhost:5002 | Tool gateway |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3000 | Dashboards (admin/admin) |
| Filebrowser | http://localhost:8888 | Browse scripts/logs/reports |

### 3. Configure GitHub webhooks

| Webhook URL | Events |
|-------------|--------|
| `http://<host>:5001/github-webhook` | Push + Pull requests |
| `http://<host>:5010/github-webhook` | Push + Pull requests |

---

## MCP Tool Reference

```bash
curl http://localhost:5002/tools   # list all tools
```

| Tool | Description |
|------|-------------|
| `k6` | Run k6 performance tests |
| `run_tests` | Run all k6 + Selenium with AI self-heal |
| `generate_scripts` | Generate scripts for changed files |
| `get_impact_map` | Get affected domains for changed files |
| `compare_with_baseline` | Classify regression vs baseline |
| `get_selenium_index` | View all Selenium scripts + metadata |
| `find_affected_selenium` | Find scripts affected by changed files |
| `jira` | Create Jira ticket |
| `slack` | Send Slack message |
| `grafana` | Fetch Grafana dashboards |
| `datadog` | Query Datadog metrics |
| `github_commits` | Fetch latest commits |

---

## Key Files

| File | Purpose |
|------|---------|
| `test_trigger_agent.py` | Test Trigger Agent — port 5001 |
| `rca_agent.py` | RCA Agent — port 5000 |
| `mcp_server.py` | MCP Tool Server — port 5002 |
| `agent/mcp_client.py` | HTTP client for MCP server |
| `agent/test_orchestrator.py` | Full pipeline: routing, generation, merge gate |
| `agent/code_change_detector.py` | Parses diffs → Scenario A / B |
| `agent/script_generator.py` | CREATE / UPDATE k6, LoadRunner, Selenium |
| `agent/selenium_index.py` | Index for fast lookup across 2000+ scripts |
| `agent/perf_policy.py` | Loads all policy + skill files |
| `agent/script_patcher.py` | GPT patch for dep/feature changes |
| `agent/script_health_checker.py` | PRE / POST script health checks |
| `agent/commit_tracker.py` | Checkpoint JSON tracking |
| `agent/concurrency.py` | File locks + webhook queue |
| `agent/regression_engine.py` | Policy-driven regression detection |
| `regression_detector.py` | Baseline comparison + classification |
| `PERF_AGENTS.md` | Platform-level agent skill |
| `SCRIPTING_AGENT.md` | Scripting agent skill |
| `PERF_EXEC_AGENT.md` | Execution agent skill |
| `.perf/` | All policy files (mappings, thresholds, profiles, rules) |

---

## Deploy to AWS (EKS)

```bash
# Build and push to ECR
aws ecr get-login-password | docker login --username AWS \
  --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
docker build -t ai-agent .
docker tag ai-agent:latest <account>.dkr.ecr.<region>.amazonaws.com/ai-agent:latest
docker push <account>.dkr.ecr.<region>.amazonaws.com/ai-agent:latest

# Deploy
kubectl apply -f k8s-secret.yaml
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
kubectl get services   # get LoadBalancer URLs for webhooks
```


---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Docker / Kubernetes                          │
│                                                                     │
│  ┌──────────────────────┐        ┌──────────────────────────────┐  │
│  │  Test Trigger Agent  │        │         RCA Agent            │  │
│  │     port 5001        │        │         port 5000            │  │
│  │                      │        │                              │  │
│  │  code_change_detector│        │  AI root cause analysis      │  │
│  │  script_generator    │        │  Jira / Slack / Email        │  │
│  │  script_patcher      │        │  system diagnostics          │  │
│  │  script_health_check │        │                              │  │
│  │  commit_tracker      │        │                              │  │
│  └──────────┬───────────┘        └──────────────┬───────────────┘  │
│             │                                   │                  │
│             │   call_tool(...)                  │  call_tool(...)  │
│             └──────────────────┬────────────────┘                  │
│                                ▼                                   │
│              ┌─────────────────────────────────┐                   │
│              │         MCP Server              │                   │
│              │         port 5002               │                   │
│              │                                 │                   │
│              │  GET  /tools      ← discovery   │                   │
│              │  POST /tools/k6                 │                   │
│              │  POST /tools/grafana            │                   │
│              │  POST /tools/jira               │                   │
│              │  POST /tools/datadog            │                   │
│              │  POST /tools/speedcurve         │                   │
│              │  POST /tools/github_commits     │                   │
│              │  POST /tools/slack              │                   │
│              └────────────────┬────────────────┘                   │
│                               │                                    │
│         ┌─────────────────────┼──────────────────────┐            │
│         ▼                     ▼                      ▼            │
│  ┌─────────────┐   ┌─────────────────┐   ┌──────────────────┐    │
│  │  mock-app   │   │  Prometheus     │   │    Grafana       │    │
│  │  port 8080  │   │  port 9090      │   │    port 3000     │    │
│  └─────────────┘   └─────────────────┘   └──────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

The MCP server is the single integration point for all external tools.
Any agent — current or future — calls `call_tool("k6")` via `agent/mcp_client.py`
instead of importing tools directly. Adding a new tool means one handler in
`mcp_server.py`, instantly available to all agents.

---

## How It Works — Full Flow

```
Developer pushes code to GitHub
          │
          ▼
GitHub sends webhook event
          │
          ├──────────────────────────────────────────────────────────┐
          ▼                                                          ▼
POST :5001/github-webhook                            POST :5000/github-webhook
Test Trigger Agent                                   RCA Agent
(test_trigger_agent.py)                              (rca_agent.py)
          │                                                          │
          │  queued via WebhookQueue                                 │  runs immediately
          │  (multi-dev safe, FIFO)                                  │
          ▼                                                          ▼
test_orchestrator.py                                 run k6 via MCP → /tools/k6
          │                                          collect system metrics
          │                                          AI root cause analysis
  ┌───────┴────────────────────────────────┐         create Jira via MCP → /tools/jira
  │                                        │         send Slack via MCP → /tools/slack
  │  STEP 1 — PRE Health Check             │
  │  Run all existing k6 scripts           │
  │  Record: passing / failing / skipped   │
  │                                        │
  │  STEP 2 — Fix Pre-existing Failures    │
  │  Any script already failing?           │
  │  → GPT rewrites it + re-validates      │
  │                                        │
  │  STEP 3 — Fetch Changed Files          │
  │  GitHub API: /commits/{sha}/files      │
  │  or /pulls/{n}/files for PRs           │
  │                                        │
  │  STEP 4 — Detect Changes               │
  │  code_change_detector.py               │
  │    │                                   │
  │    ├── Scenario A: dep file changed?   │
  │    │   requirements.txt / package.json │
  │    │   → extract old→new versions      │
  │    │   → DependencyChange list         │
  │    │                                   │
  │    └── Scenario B: source file added   │
  │        or modified?                    │
  │        .py / .cs / .js / .ts etc.      │
  │        → regex scan for route decs     │
  │          @app.post, [HttpGet], MapGet  │
  │          Flask, FastAPI, ASP.NET,      │
  │          Express, Django, Spring       │
  │        → GPT fallback if no match      │
  │        → FeatureChange list            │
  │                                        │
  │  STEP 5 — Generate / Update Scripts    │
  │  script_generator.py                   │
  │    │                                   │
  │    ├── Script missing? → CREATE        │
  │    │   GPT generates all 3 types       │
  │    │                                   │
  │    └── Script exists?  → UPDATE        │
  │        GPT rewrites for new changes    │
  │                                        │
  │    Output path (auto-created):         │
  │    scripts/<repo>/<env>/k6/            │
  │    scripts/<repo>/<env>/loadrunner/    │
  │    scripts/<repo>/<env>/selenium/      │
  │                                        │
  │  STEP 6 — Patch Existing Scripts       │
  │  script_patcher.py                     │
  │    Scenario A: find scripts that       │
  │    reference upgraded package →        │
  │    GPT rewrites for new version        │
  │                                        │
  │  STEP 7 — POST Health Check            │
  │  Re-run all k6 scripts                 │
  │  Compare before vs after:              │
  │    PRE:  3 passing, 1 failing          │
  │    POST: 4 passing, 0 failing ✅       │
  │                                        │
  │  STEP 8 — Save Checkpoint              │
  │  .commit_checkpoint.json               │
  │    sha, scripts_created,               │
  │    scripts_updated, timestamp          │
  │                                        │
  └───────────────────────────────────────┘
          │
          ▼
  Post PR comment (GitHub)
  Send Slack notification
```

---

## First Boot — Full History Scan

On first startup with no scripts and no checkpoint, the agent scans the entire
commit history (oldest → newest) to bootstrap scripts for all existing endpoints:

```
Container starts — no scripts, no checkpoint
          │
          ▼
scan_full_repo()
  → fetch all commits (paginated, oldest first)
  → detect_changes() on every commit diff
  → accumulate unique endpoints by (method, path)
  → generate k6 + LoadRunner + Selenium for each
  → save checkpoint at HEAD
          │
          ▼
Normal webhook-driven mode from here on
(full scan never runs again unless checkpoint is reset)
```

Reset via API: `POST http://localhost:5001/checkpoint/reset`

---

## Multi-Developer Concurrent Push Handling

```
Dev A pushes commit abc123  ──┐
Dev B pushes commit def456  ──┤──► WebhookQueue (FIFO)
Dev C pushes commit ghi789  ──┘         │
                                         │  processes one at a time
                                    ┌────▼────┐
                                    │ abc123  │ → detect → generate → checkpoint
                                    └────┬────┘
                                    ┌────▼────┐
                                    │ def456  │ → detect → generate → checkpoint
                                    └────┬────┘
                                    ┌────▼────┐
                                    │ ghi789  │ → detect → generate → checkpoint
                                    └─────────┘

If Dev C and D push before agent wakes up:
  checkpoint = abc123
  current    = ghi789
  → compare abc123...ghi789 via GitHub API
  → process def456, ghi789 in order (oldest first)
  → no commits missed
```

---

## Script Lifecycle

```
New endpoint detected in commit
          │
          ▼
scripts/<repo>/<env>/k6/<slug>_perf_test.js   ← does it exist?
          │
    ┌─────┴──────┐
    │ NO         │ YES
    ▼            ▼
  CREATE       UPDATE
  GPT writes   GPT rewrites
  from scratch for new changes
    │            │
    └─────┬──────┘
          ▼
  k6 validation (1 VU / 5s)
          │
    ┌─────┴──────┐
    │ PASS       │ FAIL
    ▼            ▼
  Done ✅     GPT auto-fix
              retry up to 5x
                  │
            ┌─────┴──────┐
            │ PASS       │ FAIL
            ▼            ▼
          Done ✅    Flag for
                    manual review
                    + notify Slack
```

---

## Script Folder Structure

All folders are created automatically — no pre-existing structure needed.

```
scripts/
  <repo_slug>/                     e.g. cold-starr__testrepo/
    dev/
      k6/
        api_orders_perf_test.js    ← generated for POST /api/orders
        api_products_perf_test.js  ← generated for GET /api/products
      loadrunner/
        api_orders_lr_test.c       ← VuGen C format
        api_products_lr_test.c
      selenium/
        api_orders_selenium_test.py
        api_products_selenium_test.py
    stage/
      k6/ loadrunner/ selenium/
    prod/
      k6/ loadrunner/ selenium/
```

Switch `GITHUB_REPO` in `.env` → next push creates a new isolated folder tree.

---

## Commit Checkpoint

Stored in `.commit_checkpoint.json` (gitignored, persists at runtime):

```json
{
  "cold-starr/testrepo": {
    "last_processed_sha": "abc123...",
    "last_processed_at": "2026-04-04T10:00:00Z",
    "history": [
      {
        "sha": "abc123...",
        "processed_at": "2026-04-04T10:00:00Z",
        "scripts_created": ["scripts/cold-starr__testrepo/dev/k6/api_orders_perf_test.js"],
        "scripts_updated": [],
        "feature_changes": [{"method": "POST", "path": "/api/orders"}],
        "dependency_changes": [],
        "summary": "Created 3 scripts."
      }
    ]
  }
}
```

View via API: `GET http://localhost:5001/checkpoint`
Reset via API: `POST http://localhost:5001/checkpoint/reset`

---

## Setup

### 1. Clone & install

```bash
git clone <your-repo>
pip install -r requirements.txt
```

### 2. Configure `.env`

```env
OPENAI_API_KEY=sk-...
GITHUB_TOKEN=ghp_...
GITHUB_REPO=owner/repo
SFCC_SITE_URL=https://your-app.com
ENV=dev
SLACK_WEBHOOK=https://hooks.slack.com/...
```

### 3. Run locally

```bash
docker-compose up --build
```

| Service            | URL                       | Purpose                              |
|--------------------|---------------------------|--------------------------------------|
| Test Trigger Agent | http://localhost:5001     | Receives webhooks, generates scripts |
| RCA Agent          | http://localhost:5000     | Runs k6, AI analysis, reports        |
| MCP Server         | http://localhost:5002     | Tool server (k6, Jira, Slack, etc.)  |
| Mock App           | http://localhost:8080     | Simulated target app                 |
| Prometheus         | http://localhost:9090     | Metrics storage                      |
| Grafana            | http://localhost:3000     | Dashboard (admin/admin)              |

### 4. Configure GitHub webhooks

Add two webhooks in your repo → Settings → Webhooks:

| Webhook URL                              | Events              |
|------------------------------------------|---------------------|
| `http://<host>:5001/github-webhook`      | Push + Pull request |
| `http://<host>:5000/github-webhook`      | Push + Pull request |

Content type: `application/json`

---

## MCP Tool Discovery

Query the MCP server to see all available tools and their input schemas:

```bash
curl http://localhost:5002/tools
```

```json
{
  "tools": {
    "k6":             { "description": "Run k6 performance tests", "input": {} },
    "grafana":        { "description": "Fetch Grafana dashboards", "input": {"uid": "string (optional)"} },
    "jira":           { "description": "Create a Jira bug ticket", "input": {"summary": "string", "description": "string"} },
    "datadog":        { "description": "Query Datadog metrics",    "input": {} },
    "speedcurve":     { "description": "Fetch SpeedCurve data",    "input": {} },
    "github_commits": { "description": "Fetch latest commits",     "input": {"per_page": "int"} },
    "slack":          { "description": "Send Slack message",       "input": {"message": "string"} }
  }
}
```

Any agent can plug in by importing `agent/mcp_client.py`:

```python
from agent.mcp_client import call_tool

result = call_tool("k6")
result = call_tool("jira", {"summary": "Regression", "description": "p95 > 2s"})
result = call_tool("slack", {"message": "Tests complete"})
```

---

## Deploy to AWS (EKS)

```bash
# 1. Fill in real values in k8s-secret.yaml

# 2. Build and push image to ECR
aws ecr get-login-password | docker login --username AWS \
  --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
docker build -t ai-agent .
docker tag ai-agent:latest <account>.dkr.ecr.<region>.amazonaws.com/ai-agent:latest
docker push <account>.dkr.ecr.<region>.amazonaws.com/ai-agent:latest

# 3. Update image URL in deployment.yaml, then apply
kubectl apply -f k8s-secret.yaml
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml

# 4. Get LoadBalancer URLs
kubectl get services
```

---

## Key Files

| File | Purpose |
|------|---------|
| `test_trigger_agent.py` | Test Trigger Agent — port 5001 |
| `rca_agent.py` | RCA Agent — port 5000 |
| `mcp_server.py` | MCP Tool Server — port 5002 |
| `agent/mcp_client.py` | Thin HTTP client for MCP server |
| `agent/test_orchestrator.py` | Full pipeline orchestration + history scan |
| `agent/code_change_detector.py` | Parses diffs → Scenario A / B |
| `agent/script_generator.py` | CREATE / UPDATE k6, LoadRunner (.c), Selenium |
| `agent/script_patcher.py` | GPT patch for dep/feature changes |
| `agent/script_health_checker.py` | PRE / POST script health checks |
| `agent/commit_tracker.py` | Checkpoint JSON tracking |
| `agent/concurrency.py` | File locks + webhook queue |
| `.commit_checkpoint.json` | Runtime state (gitignored) |
