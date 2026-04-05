import urllib.request
import json
import os

def get_speedcurve_data():
    api_key = os.getenv("SPEEDCURVE_API_KEY", "")
    site_id = os.getenv("SPEEDCURVE_SITE_ID", "")
    if not api_key or not site_id:
        return {"status": "skipped", "reason": "SPEEDCURVE credentials not configured"}
    url = f"https://api.speedcurve.com/v1/sites/{site_id}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode())