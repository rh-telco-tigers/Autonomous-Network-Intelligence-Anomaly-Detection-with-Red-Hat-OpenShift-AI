from __future__ import annotations

import os
import time
from typing import Any

import requests


DEFAULT_CONTROL_PLANE_URL = "http://control-plane.ani-runtime.svc.cluster.local:8080"


def control_plane_url(path: str) -> str:
    base = os.getenv("CONTROL_PLANE_URL", DEFAULT_CONTROL_PLANE_URL).rstrip("/")
    return f"{base}/{path.lstrip('/')}"


def control_plane_headers() -> dict[str, str]:
    api_key = os.getenv("CONTROL_PLANE_API_KEY", os.getenv("API_KEY", "")).strip()
    return {"x-api-key": api_key} if api_key else {}


def control_plane_get(path: str, params: dict[str, object] | None = None) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = requests.get(
                control_plane_url(path),
                params=params,
                headers=control_plane_headers(),
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            last_error = exc
            if attempt == 3:
                break
            time.sleep(attempt * 2)
    raise RuntimeError(f"Control-plane export failed for {path}") from last_error


def export_control_plane_history(
    *,
    project: str,
    approval_limit: int,
    audit_limit: int,
) -> dict[str, list[dict[str, Any]]]:
    incidents = control_plane_get("/incidents", {"project": project})
    incident_ids = {str(item.get("id")) for item in incidents if isinstance(item, dict)}

    approvals_raw = control_plane_get("/approvals", {"limit": approval_limit})
    approvals = [
        item
        for item in approvals_raw
        if isinstance(item, dict) and str(item.get("incident_id") or "") in incident_ids
    ]

    audit_events_raw = control_plane_get("/audit", {"limit": audit_limit})
    audit_events = [
        item
        for item in audit_events_raw
        if isinstance(item, dict) and str(item.get("incident_id") or "") in incident_ids
    ]

    return {
        "incidents": incidents,
        "approvals": approvals,
        "audit_events": audit_events,
    }
