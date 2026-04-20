# ngrok Setup Guide — Local Development Webhooks

This guide shows how to expose your local AI Performance Agent to GitHub webhooks using ngrok.

---

## Why ngrok?

GitHub webhooks need a public URL to send events to. When running the agent locally (Docker or native), your machine is behind a firewall. ngrok creates a secure tunnel from a public URL to your local port.

```
GitHub.com
    │
    ▼
https://abc123.ngrok-free.app  (public)
    │
    │ (secure tunnel)
    ▼
localhost:5001  (your machine)
    │
    ▼
Test Trigger Agent (Docker container)
```

---

## Step 1 — Sign Up (Free Account)

1. Go to: https://dashboard.ngrok.com/signup
2. Sign up with email or GitHub
3. Verify your email

Free plan includes:
- 1 online tunnel at a time
- 40 connections/minute
- Random URLs (changes on restart)

---

## Step 2 — Get Your Authtoken

1. After signing in, go to: https://dashboard.ngrok.com/get-started/your-authtoken
2. Copy the token — looks like: `2abc123xyz_xxxxxxxxxxxxxxxx`

---

## Step 3 — Install Authtoken

Open a terminal in your project folder and run:

```bash
cd ~/Downloads/client-code
./ngrok.exe config add-authtoken YOUR_TOKEN_HERE
```

This saves the token to `~/.ngrok2/ngrok.yml` — you only need to do this once.

---

## Step 4 — Start Docker Containers

Make sure the agents are running:

```bash
docker-compose up --build -d
```

Verify all containers are up:

```bash
docker ps
```

You should see:
- `test-trigger-agent` on port 5001
- `rca-agent` on port 5010 (mapped from 5000)
- `mcp-server` on port 5002

Test the agent is reachable locally:

```bash
curl http://localhost:5001/
```

Should return:
```json
{"status": "Test Trigger Agent running", "repos": ["cold-starr/testrepo2"], ...}
```

---

## Step 5 — Start ngrok Tunnel

### For Test Trigger Agent (script generation)

Open a **new terminal** (keep docker-compose running in the first one):

```bash
cd ~/Downloads/client-code
./ngrok.exe http 5001
```

You'll see:

```
Session Status    online
Account           your@email.com (Plan: Free)
Forwarding        https://abc123-random.ngrok-free.app -> http://localhost:5001

Connections       ttl     opn     rt1     rt5     p50     p90
                  0       0       0.00    0.00    0.00    0.00
```

Copy the `Forwarding` URL: `https://abc123-random.ngrok-free.app`

**Keep this terminal open** — if you close it, the tunnel dies.

---

### For RCA Agent (optional — for test execution and reporting)

If you want GitHub to also trigger the RCA Agent, open a **third terminal**:

```bash
./ngrok.exe http 5010
```

Copy that URL too.

**Note:** Free ngrok only allows 1 tunnel at a time. To run both, upgrade to a paid plan or use localtunnel for the second agent (see Alternative section below).

---

## Step 6 — Configure GitHub Webhooks

Go to your repo: `https://github.com/cold-starr/testrepo2/settings/hooks`

Click **Add webhook**.

### Test Trigger Agent Webhook

| Field | Value |
|-------|-------|
| Payload URL | `https://abc123-random.ngrok-free.app/github-webhook` |
| Content type | `application/json` |
| Secret | (leave blank or set a value and add to `.env` as `GITHUB_SECRET=`) |
| SSL verification | ✅ Enable SSL verification |
| Which events | **Let me select individual events** → check ✅ **Pushes** + ✅ **Pull requests** |
| Active | ✅ |

Click **Add webhook**.

GitHub sends a ping immediately — you should see a **green checkmark** next to the webhook.

### RCA Agent Webhook (optional)

Repeat the same steps with the RCA Agent ngrok URL:

| Field | Value |
|-------|-------|
| Payload URL | `https://xyz789-random.ngrok-free.app/github-webhook` |
| Content type | `application/json` |
| Events | ✅ Pushes + ✅ Pull requests |

---

## Step 7 — Test It

### Push a commit

```bash
cd ~/Downloads/testrepo2
echo "test" >> README.md
git add .
git commit -m "test webhook"
git push origin main
```

### Watch the logs

```bash
docker-compose logs -f test-trigger-agent
```

You should see:

```
[webhook] Received from cold-starr/testrepo2 — PR: None, commit: abc12345
[orchestrator] Processing commit abc12345...
[detector] Feature changes in app.py: 1 endpoints
[script_generator] CREATE k6: scripts/.../k6/api_test_perf_test.js
[selenium_index] Registered: Test (45 lines)
[orchestrator] Done. Created 3 scripts; gate: pass.
```

### Check ngrok dashboard

Open http://127.0.0.1:4040 in your browser — ngrok's web UI shows all requests in real-time.

---

## Troubleshooting

### ngrok says "authentication failed"

You didn't add the authtoken. Run:

```bash
./ngrok.exe config add-authtoken YOUR_TOKEN
```

### GitHub webhook shows 404

The URL is missing `/github-webhook` at the end. Edit the webhook and add it:

```
https://abc123.ngrok-free.app/github-webhook
```

### GitHub webhook shows 500

The agent crashed. Check logs:

```bash
docker-compose logs test-trigger-agent
```

Look for `[ERROR]` lines.

### ngrok says "connection refused" or "ERR_NGROK_8012"

The Docker container is not running or port 5001 is not exposed. Run:

```bash
docker-compose up --build -d
curl http://localhost:5001/
```

If curl fails, the container is not running.

### Webhook received but no scripts generated

1. Check the changed file is not being skipped:
   ```bash
   docker exec test-trigger-agent python -c "
   from agent.perf_policy import load_policy, should_skip_file
   p = load_policy()
   print(should_skip_file('app.py', p))
   "
   ```
2. Check logs for `[detector] Feature changes` — if it says 0 endpoints, the change detector didn't find any routes
3. Check `.env` has `OPENAI_API_KEY` set correctly

---

## Alternative — localtunnel (no account needed)

If you don't want to sign up for ngrok, use localtunnel:

```bash
# Install once
npm install -g localtunnel

# Run
lt --port 5001
```

You'll get a URL like `https://something-random.loca.lt` — use that as your webhook URL.

**Note:** localtunnel URLs also change on restart and may show a warning page on first visit.

---

## Production Deployment (no ngrok needed)

For production, deploy to a server with a public IP or use AWS EKS with a LoadBalancer. See `DEPLOYMENT.md` for full instructions.

Once deployed, your webhook URLs are permanent:

```
http://<your-server-ip>:5001/github-webhook
http://<your-server-ip>:5010/github-webhook
```

Or with EKS:

```bash
kubectl get services
# Use the EXTERNAL-IP from test-trigger-agent-service
```

---

## ngrok URL Changes on Restart

Free ngrok gives you a random URL every time you restart. If you restart ngrok, you must:

1. Copy the new URL
2. Edit the GitHub webhook
3. Update the Payload URL

To get a permanent URL, upgrade to ngrok's paid plan ($8/month) which includes:
- Static domain (e.g. `myagent.ngrok.app`)
- 3 simultaneous tunnels
- No restart = no URL change

---

## Multiple Agents — Two Options

### Option 1 — Two ngrok tunnels (requires paid plan)

```bash
# Terminal 1
./ngrok.exe http 5001 --subdomain=myagent-trigger

# Terminal 2
./ngrok.exe http 5010 --subdomain=myagent-rca
```

Add two webhooks in GitHub, one for each URL.

### Option 2 — One tunnel, route internally (free plan)

Only expose Test Trigger Agent via ngrok. RCA Agent can be triggered manually or via Slack:

```bash
curl -X POST http://localhost:5010/trigger
```

---

## Security Notes

- ngrok URLs are public — anyone with the URL can send requests
- Set `GITHUB_SECRET` in `.env` and configure it in the GitHub webhook to verify signatures
- Never commit your ngrok authtoken to git
- Regenerate your authtoken if it's exposed (like in chat logs)
- For production, use proper authentication and HTTPS with your own domain

---

## Quick Reference

| Command | Purpose |
|---------|---------|
| `./ngrok.exe config add-authtoken TOKEN` | Add authtoken (once) |
| `./ngrok.exe http 5001` | Start tunnel for Test Trigger Agent |
| `./ngrok.exe http 5010` | Start tunnel for RCA Agent |
| `docker-compose up --build -d` | Start all agent containers |
| `docker-compose logs -f test-trigger-agent` | Watch agent logs |
| `curl http://localhost:5001/` | Test agent is running |
| Open http://127.0.0.1:4040 | ngrok web UI (request inspector) |

---

## What Each Agent Does

| Agent | Port | Webhook Path | Purpose |
|-------|------|--------------|---------|
| Test Trigger Agent | 5001 | `/github-webhook` | Detects changes, generates scripts, updates index |
| RCA Agent | 5010 | `/github-webhook` | Runs k6 tests, AI analysis, Jira/Slack alerts |
| MCP Server | 5002 | `/tools/*` | Tool gateway (not exposed to GitHub) |

For script generation, you only need the Test Trigger Agent webhook (port 5001).
