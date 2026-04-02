# AI Performance Agent

Watches your GitHub repo via webhooks, detects code changes, and automatically
generates or patches k6, LoadRunner, and Selenium scripts — keeping CI green
without manual edits.

---

## How It Works — Full Flow

```
Developer pushes code to GitHub
          │
          ▼
GitHub sends webhook event
          │
          ├─────────────────────────────────────────────────────────┐
          ▼                                                         ▼
POST :5001/github-webhook                             POST :5000/github-webhook
Test Trigger Agent                                    RCA Agent
(test_trigger_agent.py)                               (rca_agent.py)
          │                                                         │
          │  queued via WebhookQueue                                │  runs immediately
          │  (multi-dev safe, FIFO)                                 │
          ▼                                                         ▼
test_orchestrator.py                                  run k6 perf tests
          │                                           collect system metrics
          │                                           AI root cause analysis
  ┌───────┴────────────────────────────────┐          create Jira ticket
  │                                        │          send Slack + Email report
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

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker / Kubernetes                       │
│                                                             │
│  ┌──────────────────────┐   ┌──────────────────────────┐   │
│  │  Test Trigger Agent  │   │       RCA Agent          │   │
│  │     port 5001        │   │       port 5000          │   │
│  │                      │   │                          │   │
│  │  code_change_detector│   │  tools/k6.py             │   │
│  │  script_generator    │   │  ai_root_cause.py        │   │
│  │  script_patcher      │   │  Jira / Slack / Email    │   │
│  │  script_health_check │   │                          │   │
│  │  commit_tracker      │   │                          │   │
│  └──────────┬───────────┘   └──────────────────────────┘   │
│             │                                               │
│  ┌──────────▼───────────────────────────────────────────┐  │
│  │                   mock-app :8080                     │  │
│  │              (simulated target app)                  │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─────────────────────┐   ┌──────────────────────────┐   │
│  │   Prometheus :9090  │◄──│   k6 remote-write        │   │
│  └──────────┬──────────┘   └──────────────────────────┘   │
│             │                                               │
│  ┌──────────▼──────────┐                                   │
│  │    Grafana :3000    │                                   │
│  └─────────────────────┘                                   │
└─────────────────────────────────────────────────────────────┘
```

---

## Script Folder Structure

All folders are created automatically — no pre-existing structure needed.

```
scripts/
  <repo_slug>/                     e.g. cold-starr__startup-launcher/
    dev/
      k6/
        api_orders_perf_test.js    ← generated for POST /api/orders
        api_products_perf_test.js  ← generated for GET /api/products
      loadrunner/
        api_orders_lr_test.py
        api_products_lr_test.py
      selenium/
        api_orders_selenium_test.py
        api_products_selenium_test.py
    stage/
      k6/ loadrunner/ selenium/
    prod/
      k6/ loadrunner/ selenium/

  owner__nopcommerce/              ← different repo, completely isolated
    dev/
      k6/ loadrunner/ selenium/
```

Switch `GITHUB_REPO` in `.env` → next push creates a new isolated folder tree.

---

## Commit Checkpoint

Stored in `.commit_checkpoint.json` (gitignored, persists at runtime):

```json
{
  "cold-starr/startup-launcher": {
    "last_processed_sha": "abc123...",
    "last_processed_at": "2026-04-02T10:00:00Z",
    "history": [
      {
        "sha": "abc123...",
        "processed_at": "2026-04-02T10:00:00Z",
        "scripts_created": ["scripts/cold-starr__startup-launcher/dev/k6/api_orders_perf_test.js"],
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

| Service            | URL                       | Purpose                        |
|--------------------|---------------------------|--------------------------------|
| Test Trigger Agent | http://localhost:5001     | Receives GitHub webhooks       |
| RCA Agent          | http://localhost:5000     | Runs k6, AI analysis           |
| Mock App           | http://localhost:8080     | Simulated target app           |
| Prometheus         | http://localhost:9090     | Metrics storage                |
| Grafana            | http://localhost:3000     | Dashboard (admin/admin)        |

### 4. Configure GitHub webhooks

Add two webhooks in your repo → Settings → Webhooks:

| Webhook URL                              | Events              |
|------------------------------------------|---------------------|
| `http://<host>:5001/github-webhook`      | Push + Pull request |
| `http://<host>:5000/github-webhook`      | Push + Pull request |

Content type: `application/json`

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

Two LoadBalancer services are created — point your GitHub webhooks at the
respective DNS names on port 80.

---

## Key Files

| File | Purpose |
|------|---------|
| `test_trigger_agent.py` | Test Trigger Agent — port 5001 |
| `rca_agent.py` | RCA Agent — port 5000 |
| `agent/test_orchestrator.py` | Full pipeline orchestration |
| `agent/code_change_detector.py` | Parses diffs → Scenario A / B |
| `agent/script_generator.py` | CREATE / UPDATE k6, LoadRunner, Selenium |
| `agent/script_patcher.py` | GPT patch for dep/feature changes |
| `agent/script_health_checker.py` | PRE / POST script health checks |
| `agent/commit_tracker.py` | Checkpoint JSON tracking |
| `agent/concurrency.py` | File locks + webhook queue |
| `tests/blackbox_test.py` | Full end-to-end test suite |
| `.commit_checkpoint.json` | Runtime state (gitignored) |
