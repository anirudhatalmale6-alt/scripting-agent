import urllib.request
import json
import os

API_KEY = os.environ["SPEEDCURVE_API_KEY"]
SITE_ID = os.environ["SPEEDCURVE_SITE_ID"]

def get_speedcurve_data():

    url = f"https://api.speedcurve.com/v1/sites/{SITE_ID}"

    req = urllib.request.Request(url)

    req.add_header("Authorization", f"Bearer {API_KEY}")
    req.add_header("Accept", "application/json")

    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())

    return data