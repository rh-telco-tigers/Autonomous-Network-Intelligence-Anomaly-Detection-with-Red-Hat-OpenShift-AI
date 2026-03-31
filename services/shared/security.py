import json
import os
import secrets
from dataclasses import dataclass
from typing import Dict, List

from fastapi import Header, HTTPException


@dataclass
class AuthContext:
    token: str
    subject: str
    projects: List[str]
    roles: List[str]


def _api_key_catalog() -> Dict[str, Dict[str, object]]:
    raw = os.getenv("API_KEYS_JSON", "").strip()
    if raw:
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            return {}

    expected = os.getenv("API_KEY", "").strip()
    if not expected:
        return {}

    default_project = os.getenv("DEFAULT_PROJECT", "ims-demo")
    return {
        expected: {
            "subject": os.getenv("API_KEY_SUBJECT", "demo-operator"),
            "projects": [default_project],
            "roles": ["admin", "operator", "automation"],
        }
    }


def _extract_presented_key(x_api_key: str | None, authorization: str | None) -> str:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return ""


def require_api_key(
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> AuthContext | None:
    catalog = _api_key_catalog()
    if not catalog:
        return None

    presented = _extract_presented_key(x_api_key, authorization)
    if not presented:
        raise HTTPException(status_code=401, detail="Invalid API key")

    for token, metadata in catalog.items():
        if secrets.compare_digest(presented, token):
            return AuthContext(
                token=token,
                subject=str(metadata.get("subject", "unknown")),
                projects=[str(project) for project in metadata.get("projects", ["ims-demo"])],
                roles=[str(role) for role in metadata.get("roles", ["operator"])],
            )
    raise HTTPException(status_code=401, detail="Invalid API key")


def ensure_project_access(auth: AuthContext | None, project: str) -> None:
    if auth is None:
        return
    if "*" in auth.projects or project in auth.projects:
        return
    raise HTTPException(status_code=403, detail=f"Access denied for project {project}")


def ensure_role(auth: AuthContext | None, required_role: str) -> None:
    if auth is None:
        return
    if "admin" in auth.roles or required_role in auth.roles:
        return
    raise HTTPException(status_code=403, detail=f"Role {required_role} required")


def outbound_headers() -> Dict[str, str]:
    catalog = _api_key_catalog()
    if not catalog:
        return {}
    first_token = next(iter(catalog))
    return {"x-api-key": first_token}
