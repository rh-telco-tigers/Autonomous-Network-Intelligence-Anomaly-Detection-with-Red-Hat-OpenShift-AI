import os
from typing import Dict

import requests

from shared.security import outbound_headers


def _base_url() -> str:
    return os.getenv("CONTROL_PLANE_URL", "").rstrip("/")


def create_incident(payload: Dict[str, object]) -> Dict[str, object] | None:
    base_url = _base_url()
    if not base_url:
        return None
    try:
        response = requests.post(
            f"{base_url}/incidents",
            json=payload,
            headers=outbound_headers(),
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return None


def attach_rca(incident_id: str, payload: Dict[str, object]) -> Dict[str, object] | None:
    base_url = _base_url()
    if not base_url:
        return None
    try:
        response = requests.post(
            f"{base_url}/incidents/{incident_id}/rca",
            json=payload,
            headers=outbound_headers(),
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return None
