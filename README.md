# AI Performance Agent

Watches your GitHub repo via webhooks, detects code changes, and automatically
generates or patches k6, LoadRunner, and Selenium scripts — keeping CI green
without manual edits.

---

## Architecture

Two decoupled agents:

```
GitHub Push / PR
      │
      ├─► Test Trigger Agent  (port 5001)
      │         │
      │         ├─ PRE health check   — run all existing scripts, record baseline
      │         ├─ Fix pre-failures   — auto-fix any already-broken scripts
      │         ├─ Detect changes     — Scenario A (dep upgrade) / B (new feature)
      │         ├─ Generate scripts   — CREATE new or UPDATE existing
      │         ├─ Patch scripts      — update for dep/feature changes
      │         ├─ POST health check  — confirm everything passes after changes
      │         └─ Save checkpoint    — track processed commits in JSON
      │
      └─► RCA Agent           (port 5000)
                │
                ├─ Run k6 performance tests
                ├─ Collect system metrics
                ├─ AI root cause analysis
                ├─ Create Jira tickets
                └─ Send Slack + Email reports
```

### Script folder structure (auto-created, repo + env namespaced)

```
scripts/
  <repo_slug>/          e.g. cold-starr__startup-launcher/
    dev/
      k6/               <slug>_perf_test.js
      loadrunner/       <slug>_lr_test.py
      selenium/         <slug>_selenium_test.py
    stage/
      k6/ loadrunner/ selenium/
    prod/
      k6/ loadrunner/ selenium/
```

No pre-existing folders needed — all directories are created on demand.

### Commit checkpoint tracking

Every processed commit is recorded in `.commit_checkpoint.json`:
- Tracks which scripts were created vs updated per commit
- On next push, processes ALL commits since last checkpoint (not just latest)
- Handles multi-developer concurrent pushes correctly

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

### 3. Run locally (Docker)

```bash
docker-compose up --build
```

| Service             | URL                          |
|---------------------|------------------------------|
| Test Trigger Agent  | http://localhost:5001        |
| RCA Agent           | http://localhost:5000        |
| Mock App            | http://localhost:8080        |
| Prometheus          | http://localhost:9090        |
| Grafana             | http://localhost:3000        |

### 4. Configure GitHub webhooks

Point two webhooks at your agent host:
- `POST /github-webhook` on port 5001 → Test Trigger Agent
- `POST /github-webhook` on port 5000 → RCA Agent

Events: **Push** + **Pull requests**, content type `application/json`.

---

## Deploy to AWS (EKS)

```bash
# Build and push image
aws ecr get-login-password | docker login --username AWS --password-stdin <account>.dkr.ecr.<region>.amazonaws.com
docker build -t ai-agent .
docker tag ai-agent:latest <account>.dkr.ecr.<region>.amazonaws.com/ai-agent:latest
docker push <account>.dkr.ecr.<region>.amazonaws.com/ai-agent:latest

# Deploy
kubectl apply -f k8s-secret.yaml
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
```

Two LoadBalancer services are created — one per agent. Point your GitHub webhooks
at the respective LoadBalancer DNS names.

---

## Key files

| File | Purpose |
|------|---------|
| `test_trigger_agent.py` | Test Trigger Agent Flask app (port 5001) |
| `rca_agent.py` | RCA Agent Flask app (port 5000) |
| `agent/test_orchestrator.py` | Full pipeline: PRE check → detect → generate → POST check |
| `agent/script_health_checker.py` | Runs existing scripts before/after changes |
| `agent/script_generator.py` | CREATE / UPDATE k6, LoadRunner, Selenium scripts |
| `agent/script_patcher.py` | GPT-powered in-place script patching |
| `agent/code_change_detector.py` | Parses diffs, classifies Scenario A / B |
| `agent/commit_tracker.py` | Checkpoint JSON — tracks processed commits |
| `agent/concurrency.py` | File locks + webhook queue for multi-dev safety |
| `.commit_checkpoint.json` | Runtime state — gitignored |
| `tests/blackbox_test.py` | Full end-to-end test suite |
