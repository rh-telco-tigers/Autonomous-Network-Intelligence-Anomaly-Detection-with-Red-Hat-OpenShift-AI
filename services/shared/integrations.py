import os
import uuid
from typing import Dict

import requests

from shared.tickets import ticketing_status


def _demo_integrations_enabled() -> bool:
    return os.getenv("DEMO_INTEGRATIONS_ENABLED", "true").lower() == "true"


def integration_status() -> Dict[str, Dict[str, object]]:
    slack_configured = bool(os.getenv("SLACK_WEBHOOK_URL", "").strip())
    return {
        "slack": {
            "configured": slack_configured or _demo_integrations_enabled(),
            "mode": "webhook" if slack_configured else "demo-relay",
            "live_configured": slack_configured,
        },
    } | ticketing_status()


def send_slack_notification(text: str) -> Dict[str, str]:
    webhook = os.getenv("SLACK_WEBHOOK_URL", "").strip()
    if not webhook:
        if not _demo_integrations_enabled():
            return {"status": "skipped", "reason": "SLACK_WEBHOOK_URL not configured"}
        return {
            "status": "simulated",
            "channel": "#ims-demo",
            "message_id": f"slack-{uuid.uuid4().hex[:12]}",
            "mode": "demo-relay",
            "text": text,
        }

    response = requests.post(webhook, json={"text": text}, timeout=15)
    response.raise_for_status()
    return {"status": "sent", "mode": "webhook"}


def create_jira_issue(summary: str, description: str) -> Dict[str, str]:
    base_url = os.getenv("JIRA_BASE_URL", "").rstrip("/")
    email = os.getenv("JIRA_EMAIL", "")
    api_token = os.getenv("JIRA_API_TOKEN", "")
    project_key = os.getenv("JIRA_PROJECT_KEY", "")
    if not all([base_url, email, api_token, project_key]):
        if not _demo_integrations_enabled():
            return {"status": "skipped", "reason": "JIRA credentials not configured"}
        return {
            "status": "simulated",
            "issue_key": f"DEMO-{uuid.uuid4().hex[:6].upper()}",
            "mode": "demo-relay",
            "summary": summary,
        }

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
    return {"status": "created", "issue_key": payload.get("key", ""), "mode": "rest-api"}
