import os
import requests

OPENSPEC_URL = os.getenv("OPENSPEC_URL")

def get_spec():

    try:
        response = requests.get(OPENSPEC_URL)
        return response.json()

    except Exception as e:
        return {"error": str(e)}