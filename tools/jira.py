import requests
import os
from requests.auth import HTTPBasicAuth

JIRA_URL = os.environ["JIRA_URL"]
EMAIL = os.environ["JIRA_EMAIL"]
API_TOKEN = os.environ["JIRA_API_TOKEN"]
PROJECT_KEY = os.environ["JIRA_PROJECT_KEY"]

def create_jira_ticket(summary, description):

    url = f"{JIRA_URL}/rest/api/3/issue"

    auth = HTTPBasicAuth(EMAIL, API_TOKEN)

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    payload = {
        "fields": {
            "project": {"key": PROJECT_KEY},
            "summary": summary,
            "description": description,
            "issuetype": {"name": "Bug"}
        }
    }

    response = requests.post(
        url,
        json=payload,
        headers=headers,
        auth=auth
    )

    return response.json()