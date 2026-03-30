import os
import secrets
from typing import Dict

from fastapi import Header, HTTPException


def _expected_api_key() -> str:
    return os.getenv("API_KEY", "").strip()


def _extract_presented_key(x_api_key: str | None, authorization: str | None) -> str:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return ""


def require_api_key(
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> str | None:
    expected = _expected_api_key()
    if not expected:
        return None

    presented = _extract_presented_key(x_api_key, authorization)
    if not presented or not secrets.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return presented


def outbound_headers() -> Dict[str, str]:
    expected = _expected_api_key()
    return {"x-api-key": expected} if expected else {}

