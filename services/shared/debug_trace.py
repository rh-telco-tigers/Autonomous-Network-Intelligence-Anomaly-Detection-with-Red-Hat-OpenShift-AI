from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


def trace_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def make_trace_packet(
    category: str,
    phase: str,
    *,
    title: str,
    service: str,
    timestamp: str | None = None,
    target: str = "",
    endpoint: str = "",
    method: str = "",
    payload: Any = None,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "category": str(category or "workflow"),
        "phase": str(phase or "event"),
        "title": str(title or "Trace event"),
        "timestamp": str(timestamp or trace_now()),
        "service": str(service or ""),
        "target": str(target or ""),
        "endpoint": str(endpoint or ""),
        "method": str(method or "").upper(),
        "payload": _json_safe(payload),
        "metadata": _json_safe(metadata or {}),
    }


def interaction_trace_packets(
    *,
    category: str,
    service: str,
    target: str,
    method: str,
    endpoint: str,
    request_payload: Any,
    response_payload: Any,
    request_timestamp: str | None = None,
    response_timestamp: str | None = None,
    metadata: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    upper_method = str(method or "").upper()
    title_prefix = f"{upper_method} {endpoint}".strip()
    return [
        make_trace_packet(
            category,
            "request",
            title=f"{title_prefix} request",
            service=service,
            target=target,
            endpoint=endpoint,
            method=upper_method,
            timestamp=request_timestamp,
            payload=request_payload,
            metadata=metadata,
        ),
        make_trace_packet(
            category,
            "response",
            title=f"{title_prefix} response",
            service=service,
            target=target,
            endpoint=endpoint,
            method=upper_method,
            timestamp=response_timestamp,
            payload=response_payload,
            metadata=metadata,
        ),
    ]
