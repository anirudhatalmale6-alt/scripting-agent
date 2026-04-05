import urllib.request
import json
import os
import time

def get_datadog_metrics():
    api_key = os.getenv("DATADOG_API_KEY", "")
    if not api_key:
        return {"status": "skipped", "reason": "DATADOG_API_KEY not configured"}
    now  = int(time.time())
    past = now - 3600
    url  = f"https://api.datadoghq.com/api/v1/query?from={past}&to={now}&query=avg:system.cpu.user"
    req  = urllib.request.Request(url)
    req.add_header("DD-API-KEY", api_key)
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode())