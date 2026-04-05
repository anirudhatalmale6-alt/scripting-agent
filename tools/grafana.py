import urllib.request
import json
import os

def get_dashboards():
    grafana_url = os.getenv("GRAFANA_URL", "")
    api_key     = os.getenv("GRAFANA_API_KEY", "")
    if not grafana_url or not api_key:
        return {"status": "skipped", "reason": "GRAFANA_URL or GRAFANA_API_KEY not configured"}
    url = f"{grafana_url}/api/search"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode())

def get_dashboard(uid):
    grafana_url = os.getenv("GRAFANA_URL", "")
    api_key     = os.getenv("GRAFANA_API_KEY", "")
    if not grafana_url or not api_key:
        return {"status": "skipped", "reason": "GRAFANA_URL or GRAFANA_API_KEY not configured"}
    url = f"{grafana_url}/api/dashboards/uid/{uid}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode())