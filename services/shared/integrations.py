import os
from typing import Dict

import requests


def send_slack_notification(text: str) -> Dict[str, str]:
    webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        return {"status": "skipped", "reason": "SLACK_WEBHOOK_URL not configured"}

    response = requests.post(webhook, json={"text": text}, timeout=15)
    response.raise_for_status()
    return {"status": "sent"}


def create_jira_issue(summary: str, description: str) -> Dict[str, str]:
    base_url = os.getenv("JIRA_BASE_URL", "").rstrip("/")
    email = os.getenv("JIRA_EMAIL", "")
    api_token = os.getenv("JIRA_API_TOKEN", "")
    project_key = os.getenv("JIRA_PROJECT_KEY", "")
    if not all([base_url, email, api_token, project_key]):
        return {"status": "skipped", "reason": "JIRA credentials not configured"}

    response = requests.post(
        f"{base_url}/rest/api/3/issue",
        auth=(email, api_token),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        json={
            "fields": {
                "project": {"key": project_key},
                "summary": summary,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": description}],
                        }
                    ],
                },
                "issuetype": {"name": "Task"},
            }
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    return {"status": "created", "issue_key": payload.get("key", "")}

