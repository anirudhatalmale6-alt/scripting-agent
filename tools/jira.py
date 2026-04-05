import requests
import os
from requests.auth import HTTPBasicAuth

def create_jira_ticket(summary, description):
    jira_url    = os.getenv("JIRA_URL", "")
    email       = os.getenv("JIRA_EMAIL", "")
    api_token   = os.getenv("JIRA_API_TOKEN", "")
    project_key = os.getenv("JIRA_PROJECT_KEY", "")
    if not all([jira_url, email, api_token, project_key]):
        return {"status": "skipped", "reason": "Jira credentials not configured"}
    url  = f"{jira_url}/rest/api/3/issue"
    auth = HTTPBasicAuth(email, api_token)
    payload = {
        "fields": {
            "project":     {"key": project_key},
            "summary":     summary,
            "description": description,
            "issuetype":   {"name": "Bug"},
        }
    }
    response = requests.post(url, json=payload,
                             headers={"Accept": "application/json", "Content-Type": "application/json"},
                             auth=auth)
    return response.json()