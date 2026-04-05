import os
import threading
import time
import uuid
from typing import Dict

import requests

from shared.aap import controller_status
from shared.eda import status as eda_status
from shared.tickets import ticketing_status


_STATUS_CACHE_LOCK = threading.Lock()
_STATUS_CACHE: Dict[str, Dict[str, object]] | None = None
_STATUS_CACHE_EXPIRES_AT = 0.0


def _demo_integrations_enabled() -> bool:
    return os.getenv("DEMO_INTEGRATIONS_ENABLED", "true").lower() == "true"


def _status_cache_ttl_seconds() -> float:
    raw_value = os.getenv("INTEGRATION_STATUS_CACHE_SECONDS", "20").strip()
    try:
        value = float(raw_value)
    except ValueError:
        return 20.0
    return value if value >= 0 else 20.0


def clear_integration_status_cache() -> None:
    global _STATUS_CACHE, _STATUS_CACHE_EXPIRES_AT
    with _STATUS_CACHE_LOCK:
        _STATUS_CACHE = None
        _STATUS_CACHE_EXPIRES_AT = 0.0


def _build_integration_status() -> Dict[str, Dict[str, object]]:
    slack_configured = bool(os.getenv("SLACK_WEBHOOK_URL", "").strip())
    return {
        "aap": controller_status(),
        "eda": eda_status(),
        "slack": {
            "configured": slack_configured or _demo_integrations_enabled(),
            "mode": "webhook" if slack_configured else "demo-relay",
            "live_configured": slack_configured,
        },
    } | ticketing_status()


def integration_status(force_refresh: bool = False) -> Dict[str, Dict[str, object]]:
    global _STATUS_CACHE, _STATUS_CACHE_EXPIRES_AT
    ttl_seconds = _status_cache_ttl_seconds()
    now = time.time()
    with _STATUS_CACHE_LOCK:
        if (
            not force_refresh
            and ttl_seconds > 0
            and _STATUS_CACHE is not None
            and now < _STATUS_CACHE_EXPIRES_AT
        ):
            return _STATUS_CACHE
    status = _build_integration_status()
    with _STATUS_CACHE_LOCK:
        _STATUS_CACHE = status
        _STATUS_CACHE_EXPIRES_AT = now + ttl_seconds if ttl_seconds > 0 else 0.0
    return status


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
