import urllib.request
import json
import os
import time

# Get API key from environment variables
API_KEY = os.getenv("DATADOG_API_KEY")

def get_datadog_metrics():
    # Ensure API key is available
    if not API_KEY:
        raise ValueError("DATADOG_API_KEY not set")

    # Current time (now) and past 1 hour timestamp
    now = int(time.time())
    past = now - 3600  # last 1 hour

    # Datadog query URL (CPU usage example)
    url = f"https://api.datadoghq.com/api/v1/query?from={past}&to={now}&query=avg:system.cpu.user"

    # Create HTTP request
    req = urllib.request.Request(url)
    req.add_header("DD-API-KEY", API_KEY)

    try:
        # Send request to Datadog API with timeout
        with urllib.request.urlopen(req, timeout=10) as response:
            # Parse JSON response
            data = json.loads(response.read().decode())
            return data
    except Exception as e:
        # Handle API failure gracefully
        print("Datadog API failed:", e)
        return {}