import urllib.request
import json
import os

GRAFANA_URL = os.environ["GRAFANA_URL"]
GRAFANA_API_KEY = os.environ["GRAFANA_API_KEY"]

def get_dashboards():

    url = f"{GRAFANA_URL}/api/search"

    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {GRAFANA_API_KEY}")

    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())

    return data


def get_dashboard(uid):

    url = f"{GRAFANA_URL}/api/dashboards/uid/{uid}"

    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {GRAFANA_API_KEY}")

    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())

    return data