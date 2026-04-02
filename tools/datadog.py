import urllib.request
import json
import os
import time

API_KEY = os.environ["DATADOG_API_KEY"]

def get_datadog_metrics():

    now = int(time.time())
    past = now - 3600

    url = f"https://api.datadoghq.com/api/v1/query?from={past}&to={now}&query=avg:system.cpu.user"

    req = urllib.request.Request(url)
    req.add_header("DD-API-KEY", API_KEY)

    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())

    return data