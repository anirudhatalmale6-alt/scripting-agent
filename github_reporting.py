import requests
import os

GITHUB_TOKEN = os.getenv("TOKEN")
REPO = os.getenv("GITHUB_REPOS", "").split(",")[0].strip()


def comment_pr(pr_number, message):

    url = f"https://api.github.com/repos/{REPO}/issues/{pr_number}/comments"

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

    data = {
        "body": message
    }

    response = requests.post(url, headers=headers, json=data)

    print("PR comment response:", response.status_code)
