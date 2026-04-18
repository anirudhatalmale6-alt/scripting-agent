# Deployment Guide — AI Performance Agent Platform

## What This System Does

Every time a developer pushes code or opens a PR, this platform automatically:
1. Detects which endpoints or dependencies changed
2. Generates or updates k6, Selenium, and LoadRunner test scripts
3. Runs the affected tests and compares against baselines
4. Posts a performance summary to the PR with pass/warn/fail gate
5. Creates Jira tickets and sends Slack alerts on regressions

All behaviour is driven by policy files in `.perf/` — no code changes needed to tune thresholds, routing, or execution profiles.

---

## System Architecture

```
GitHub Webhook
      │
      ├──────────────────────────────────────────────┐
      ▼                                              ▼
Test Trigger Agent :5001                    RCA Agent :5000
  - Reads .perf/mappings.yaml               - Runs k6 tests
  - Builds impact map                       - AI root cause analysis
  - Detects changed endpoints               - Jira / Slack / Email
  - Generates/updates scripts               - Merge gate decision
  - PRE/POST health checks                  - Report artifacts
      │                                              │
      └──────────────────┬───────────────────────────┘
                         ▼
                  MCP Server :5002
                  (tool gateway)
                  /tools/k6
                  /tools/jira
                  /tools/slack
                  /tools/grafana
                  /tools/datadog
                  /tools/github_commits
                  /tools/generate_scripts
                  /tools/get_impact_map
                  /tools/compare_with_baseline
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
          Prometheus  Grafana   External APIs
          :9090       :3000     (Jira, Slack, DD)
```

### Agent Skill Files (loaded at runtime)

| File | Loaded by | Purpose |
|------|-----------|---------|
| `PERF_AGENTS.md` | RCA Agent | Platform-level rules, PR comment format |
| `SCRIPTING_AGENT.md` | Script Generator, Orchestrator | Script generation standards, routing rules |
| `PERF_EXEC_AGENT.md` | RCA Agent | Execution profile selection, regression classification |

### Policy Files (`.perf/` directory)

| File | Purpose |
|------|---------|
| `.perf/mappings.yaml` | Maps code paths → affected test domains |
| `.perf/thresholds.yaml` | Domain-specific SLO thresholds (p95, error rate) |
| `.perf/profiles/pr-smoke.yaml` | PR execution profile (VUs, duration, merge gate) |
| `.perf/profiles/main-regression.yaml` | Merge-to-main profile |
| `.perf/profiles/nightly-endurance.yaml` | Nightly soak test profile |
| `.perf/rules/commit-routing.md` | When to generate/skip scripts |
| `.perf/rules/script-generation.md` | Naming, threshold, tagging standards |
| `.perf/rules/execution-policy.md` | What runs on PR vs merge vs nightly |
| `.perf/rules/regression-thresholds.md` | Regression classification rules |

---

## Prerequisites

| Tool | Version | Required for |
|------|---------|-------------|
| Docker | 20+ | All deployments |
| Docker Compose | 2+ | Local deployment |
| Python | 3.11+ | Local run without Docker |
| kubectl | 1.25+ | Kubernetes deployment |
| AWS CLI | 2+ | EKS deployment |
| k6 | latest | Script validation (auto-installed in Docker) |

---

## Step 1 — Configure Environment

Copy `.env.example` to `.env` and fill in the required values:

```bash
cp .env.example .env
```

### Required fields

```env
# OpenAI — powers script generation and RCA
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# GitHub — repo monitoring and webhook verification
GITHUB_TOKEN=ghp_...
TOKEN=ghp_...                          # same value as GITHUB_TOKEN
GITHUB_REPOS=org/repo1,org/repo2       # comma-separated, each gets isolated scripts/
GITHUB_REPO_URL=https://api.github.com/repos/org/repo1
GITHUB_SECRET=your-webhook-secret      # must match GitHub webhook secret

# Target application
SFCC_SITE_URL=https://your-app.com     # URL k6 tests will hit
ENV=dev                                # dev | stage | prod

# MCP server location
MCP_URL=http://mcp-server:5002         # Docker internal; use http://localhost:5002 for local
```

### Optional integrations (leave blank to skip gracefully)

```env
SLACK_WEBHOOK=https://hooks.slack.com/services/...
JIRA_URL=https://yourorg.atlassian.net
JIRA_EMAIL=you@yourorg.com
JIRA_API_TOKEN=...
JIRA_PROJECT_KEY=PERF
DATADOG_API_KEY=...
GRAFANA_URL=https://yourorg.grafana.net
GRAFANA_API_KEY=...
```

---

## Step 2 — Local Deployment (Docker Compose)

```bash
docker-compose up --build
```

### Services started

| Service | Port | URL |
|---------|------|-----|
| Test Trigger Agent | 5001 | http://localhost:5001 |
| RCA Agent | 5010 | http://localhost:5010 |
| MCP Server | 5002 | http://localhost:5002 |
| Prometheus | 9090 | http://localhost:9090 |
| Grafana | 3000 | http://localhost:3000 (admin/admin) |
| Filebrowser | 8888 | http://localhost:8888 (browse scripts/logs) |

### Verify all services are healthy

```bash
curl http://localhost:5001/          # Test Trigger Agent
curl http://localhost:5010/          # RCA Agent (mapped to 5010 externally)
curl http://localhost:5002/tools     # MCP Server — lists all available tools
```

### View logs

```bash
docker-compose logs -f test-trigger-agent
docker-compose logs -f rca-agent
docker-compose logs -f mcp-server
```

---

## Step 3 — Configure GitHub Webhooks

In each monitored repo: **Settings → Webhooks → Add webhook**

### Test Trigger Agent webhook

| Field | Value |
|-------|-------|
| Payload URL | `http://<your-host>:5001/github-webhook` |
| Content type | `application/json` |
| Secret | value of `GITHUB_SECRET` in `.env` |
| Events | Push + Pull requests |

### RCA Agent webhook

| Field | Value |
|-------|-------|
| Payload URL | `http://<your-host>:5010/github-webhook` |
| Content type | `application/json` |
| Secret | value of `GITHUB_SECRET` in `.env` |
| Events | Push + Pull requests |

> For local testing use [ngrok](https://ngrok.com): `./ngrok.exe http 5001`

---

## Step 4 — First Boot Behaviour

On first startup with no scripts and no checkpoint, the Test Trigger Agent automatically scans the full commit history to bootstrap scripts:

```
Container starts
      │
      ▼
No scripts found + no checkpoint
      │
      ▼
scan_full_repo()
  → fetches all commits (paginated, oldest first)
  → detects endpoints across all commits
  → generates k6 + Selenium + LoadRunner for each unique endpoint
  → saves checkpoint at HEAD
      │
      ▼
Normal webhook-driven mode
(full scan never runs again unless checkpoint is reset)
```

Scripts are written to:
```
scripts/
  <repo_slug>/
    dev/
      k6/           ← k6 JS performance tests
      selenium/     ← Java Maven TestNG tests
      loadrunner/   ← VuGen C scripts
```

---

## Step 5 — Verify End-to-End

### 1. Check policy loaded correctly

```bash
docker exec test-trigger-agent python -c "
from agent.perf_policy import load_policy
p = load_policy()
print('Policy loaded:', p.loaded)
print('Mappings:', len(p.mappings))
print('Thresholds:', list(p.thresholds.keys()))
print('Profiles:', list(p.profiles.keys()))
"
```

Expected output:
```
Policy loaded: True
Mappings: 8
Thresholds: ['default', 'checkout', 'auth', 'login', 'cart', 'search', 'order', 'product', 'user']
Profiles: ['main_regression', 'merge_main', 'nightly_endurance', 'nightly', 'pr_smoke', 'pull_request', 'push']
```

### 2. Trigger a manual test run

```bash
curl -X POST http://localhost:5001/run-tests
```

### 3. Trigger manual RCA

```bash
curl -X POST http://localhost:5010/trigger
```

### 4. Check checkpoint state

```bash
curl http://localhost:5001/checkpoint
```

### 5. Reset checkpoint (re-run full scan)

```bash
curl -X POST http://localhost:5001/checkpoint/reset
```

### 6. Trigger self-heal (fix failing scripts)

```bash
curl -X POST http://localhost:5001/self-heal
```

---

## Kubernetes Deployment (EKS)

### 1. Fill in k8s-secret.yaml

Edit `k8s-secret.yaml` with real values for all required fields. Do not commit this file.

### 2. Build and push Docker image

```bash
# Authenticate with ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS \
  --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com

# Build and push
docker build -t ai-agent .
docker tag ai-agent:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/ai-agent:latest
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/ai-agent:latest
```

### 3. Update image URL in deployment.yaml

Replace `xxxxx.dkr.ecr.us-east-1.amazonaws.com/ai-agent:latest` with your actual ECR URL in `deployment.yaml`.

### 4. Deploy

```bash
kubectl apply -f k8s-secret.yaml
kubectl apply -f deployment.yaml
kubectl apply -f service.yaml
```

### 5. Get webhook URLs

```bash
kubectl get services
```

Use the `EXTERNAL-IP` values from `test-trigger-agent-service` and `rca-agent-service` as your GitHub webhook URLs.

### 6. Verify pods are running

```bash
kubectl get pods
kubectl logs deployment/test-trigger-agent
kubectl logs deployment/rca-agent
```

---

## Customising Agent Behaviour

All behaviour is controlled by files — no code changes needed.

### Change thresholds for a domain

Edit `.perf/thresholds.yaml`:
```yaml
checkout:
  p95_ms: 600        # was 800 — tighten the SLO
  error_rate: 0.002  # was 0.005
```

### Add a new domain mapping

Edit `.perf/mappings.yaml`:
```yaml
- name: payments-domain
  paths:
    - "services/payments/**"
    - "api/payments/**"
  affects:
    k6:
      - "payments"
    selenium:
      - "payments"
  risk: high
```

### Change PR smoke profile (VUs, duration, merge gate)

Edit `.perf/profiles/pr-smoke.yaml`:
```yaml
k6:
  vus: 20          # was 10
  duration: "1m"   # was 30s
merge_gate:
  warn_on_regression_pct: 15    # was 20
  block_on_regression_pct: 40   # was 50
```

### Change agent instructions

Edit the skill files directly — changes take effect on next agent invocation (no rebuild needed when using Docker volumes):
- `SCRIPTING_AGENT.md` — scripting rules
- `PERF_EXEC_AGENT.md` — execution and regression rules
- `PERF_AGENTS.md` — platform-wide rules

---

## API Reference

### Test Trigger Agent (port 5001)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Health check + repo status |
| POST | `/github-webhook` | Receives GitHub push/PR events |
| GET | `/checkpoint` | View commit processing history |
| POST | `/checkpoint/reset` | Reset checkpoint — triggers full rescan |
| POST | `/self-heal` | Find and fix all failing scripts |
| POST | `/run-tests` | Run all scripts, AI fixes failures |

### RCA Agent (port 5000)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Health check |
| POST | `/github-webhook` | Receives GitHub push/PR events |
| POST | `/trigger` | Manual RCA trigger |

### MCP Server (port 5002)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/tools` | List all tools + input schemas |
| POST | `/tools/k6` | Run k6 tests |
| POST | `/tools/run_tests` | Run all k6 + Selenium with AI self-heal |
| POST | `/tools/generate_scripts` | Generate scripts for changed files |
| POST | `/tools/get_impact_map` | Get affected domains for changed files |
| POST | `/tools/compare_with_baseline` | Classify regression vs baseline |
| POST | `/tools/jira` | Create Jira ticket |
| POST | `/tools/slack` | Send Slack message |
| POST | `/tools/grafana` | Fetch Grafana dashboards |
| POST | `/tools/datadog` | Query Datadog metrics |
| POST | `/tools/github_commits` | Fetch latest commits |

---

## Troubleshooting

### Scripts not being generated

1. Check the impact map — is the changed file matched by `.perf/mappings.yaml`?
   ```bash
   curl -X POST http://localhost:5002/tools/get_impact_map \
     -H "Content-Type: application/json" \
     -d '{"changed_files": ["services/checkout/cart.py"]}'
   ```
2. Check routing rules — is the file being skipped?
   ```bash
   docker exec test-trigger-agent python -c "
   from agent.perf_policy import load_policy, should_skip_file
   p = load_policy()
   print(should_skip_file('services/checkout/cart.py', p))
   "
   ```
3. Check logs: `docker-compose logs -f test-trigger-agent`

### Webhook not received

1. Verify ngrok/public URL is reachable from GitHub
2. Check `GITHUB_SECRET` matches the webhook secret in GitHub settings
3. Check logs for signature verification errors

### k6 scripts failing validation

1. Check `SFCC_SITE_URL` is reachable from inside the container
2. Trigger self-heal: `curl -X POST http://localhost:5001/self-heal`
3. Check logs for GPT fix attempts

### Policy not loading

1. Verify `.perf/` directory exists and is mounted:
   ```bash
   docker exec test-trigger-agent ls /app/.perf/
   ```
2. Check YAML syntax in `mappings.yaml` and `thresholds.yaml`

### MCP server unreachable

1. Verify `MCP_URL=http://mcp-server:5002` in `.env` (Docker) or `http://localhost:5002` (local)
2. Check MCP server health: `curl http://localhost:5002/`
3. Agents fall back to local tool implementations automatically if MCP is down

---

## Volumes and Persistence

| Volume | Path in container | Purpose |
|--------|------------------|---------|
| `./scripts` | `/app/scripts` | Generated test scripts |
| `./logs` | `/app/logs` | Agent + MCP logs |
| `./reports` | `/app/reports` | RCA report JSON + markdown |
| `./checkpoints` | `/app/checkpoints` | Commit checkpoint state |
| `./.perf` | `/app/.perf` | Policy files (read-only) |
| `./SCRIPTING_AGENT.md` | `/app/SCRIPTING_AGENT.md` | Agent skill (read-only) |
| `./PERF_EXEC_AGENT.md` | `/app/PERF_EXEC_AGENT.md` | Agent skill (read-only) |
| `./PERF_AGENTS.md` | `/app/PERF_AGENTS.md` | Agent skill (read-only) |

Policy and skill files are mounted read-only. Edit them on the host and the running containers pick up changes immediately — no rebuild required.

---

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | Yes | — | OpenAI API key (sk-...) |
| `OPENAI_MODEL` | No | gpt-4o-mini | Model for script generation and RCA |
| `ENABLE_AI` | No | true | false = templates only, no AI calls |
| `GITHUB_TOKEN` | Yes | — | GitHub PAT for API access |
| `TOKEN` | Yes | — | Same as GITHUB_TOKEN |
| `GITHUB_REPOS` | Yes | — | Comma-separated repos to monitor |
| `GITHUB_REPO_URL` | Yes | — | Primary repo API URL |
| `GITHUB_SECRET` | No | — | Webhook signature secret |
| `SFCC_SITE_URL` | Yes | — | Target app URL for k6 tests |
| `ENV` | No | dev | Environment label (dev/stage/prod) |
| `MCP_URL` | Yes | — | MCP server URL |
| `ENABLE_K6` | No | true | Generate k6 scripts |
| `ENABLE_SELENIUM` | No | true | Generate Selenium scripts |
| `ENABLE_LOADRUNNER` | No | true | Generate LoadRunner scripts |
| `CHECKPOINT_DIR` | No | ./checkpoints | Checkpoint storage path |
| `AUTO_TEST_ON_STARTUP` | No | false | Run tests after first-boot generation |
| `SELF_HEAL_INTERVAL_MINUTES` | No | 60 | How often to auto-fix failing scripts |
| `SLACK_WEBHOOK` | No | — | Slack incoming webhook URL |
| `JIRA_URL` | No | — | Jira instance URL |
| `JIRA_EMAIL` | No | — | Jira account email |
| `JIRA_API_TOKEN` | No | — | Jira API token |
| `JIRA_PROJECT_KEY` | No | — | Jira project key (e.g. PERF) |
| `DATADOG_API_KEY` | No | — | Datadog API key |
| `GRAFANA_URL` | No | — | Grafana instance URL |
| `GRAFANA_API_KEY` | No | — | Grafana API key |
