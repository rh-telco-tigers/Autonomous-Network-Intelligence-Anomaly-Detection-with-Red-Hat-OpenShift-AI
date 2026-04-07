import json
import os
import socket
import time
from datetime import datetime, timezone
from statistics import mean
from typing import Dict, List, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI
from pydantic import BaseModel, Field
import requests

from shared.cluster_env import dataset_store_bucket, dataset_store_endpoint, dataset_store_prefix, ims_pcscf_host, ims_pcscf_port
from shared.cors import install_cors
from shared.incident_taxonomy import (
    NORMAL_ANOMALY_TYPE,
    canonical_anomaly_type,
    event_profiles,
    is_nominal,
    normalize_scenario_name,
    scenario_definition,
)
from shared.metrics import install_metrics


class Event(BaseModel):
    method: str
    latency_ms: float = 0.0
    response_code: int = 200
    node_id: str = "pcscf-1"
    node_role: str = "P-CSCF"
    payload_size: int = 0
    retransmission: bool = False
    timestamp: Optional[datetime] = None


class FeatureWindowRequest(BaseModel):
    scenario: str = "normal"
    node_id: str = "pcscf-1"
    node_role: str = "P-CSCF"
    duration_seconds: int = 30
    events: List[Event] = Field(default_factory=list)


app = FastAPI(title="feature-gateway", version="0.1.0")
install_cors(app)
install_metrics(app, "feature-gateway")

IMS_PCSCF_HOST = os.getenv("IMS_PCSCF_HOST", ims_pcscf_host())
IMS_PCSCF_PORT = int(os.getenv("IMS_PCSCF_PORT", str(ims_pcscf_port())))
IMS_PCSCF_TELEMETRY = os.getenv("IMS_PCSCF_TELEMETRY_URL", "")
IMS_SCSCF_TELEMETRY = os.getenv("IMS_SCSCF_TELEMETRY_URL", "")
IMS_HSS_TELEMETRY = os.getenv("IMS_HSS_TELEMETRY_URL", "")
IMS_TELEMETRY_RESET_TIMEOUT = float(os.getenv("IMS_TELEMETRY_RESET_TIMEOUT_SECONDS", "0.5"))
IMS_TELEMETRY_FETCH_TIMEOUT = float(os.getenv("IMS_TELEMETRY_FETCH_TIMEOUT_SECONDS", "1.5"))
FEATURE_WINDOW_DATASET_VERSION = os.getenv("FEATURE_WINDOW_DATASET_VERSION", "live-sipp-v1")
DEFAULT_DATASET_STORE_ENDPOINT = dataset_store_endpoint()
DEFAULT_DATASET_STORE_BUCKET = dataset_store_bucket()
DEFAULT_DATASET_STORE_PREFIX = dataset_store_prefix()


def _dataset_store_endpoint() -> str:
    return os.getenv("DATASET_STORE_ENDPOINT", os.getenv("MINIO_ENDPOINT", DEFAULT_DATASET_STORE_ENDPOINT))


def _dataset_store_bucket() -> str:
    return os.getenv("DATASET_STORE_BUCKET", os.getenv("MINIO_BUCKET", DEFAULT_DATASET_STORE_BUCKET))


def _dataset_store_prefix() -> str:
    return os.getenv("DATASET_STORE_PREFIX", DEFAULT_DATASET_STORE_PREFIX).strip("/")


def _dataset_store_access_key() -> str:
    return os.getenv("DATASET_STORE_ACCESS_KEY", os.getenv("MINIO_ACCESS_KEY", "minioadmin"))


def _dataset_store_secret_key() -> str:
    return os.getenv("DATASET_STORE_SECRET_KEY", os.getenv("MINIO_SECRET_KEY", "minioadmin"))


def _dataset_object_key(relative_path: str) -> str:
    normalized_relative = relative_path.lstrip("/")
    prefix = _dataset_store_prefix()
    return f"{prefix}/{normalized_relative}" if prefix else normalized_relative


def _dataset_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=_dataset_store_endpoint(),
        aws_access_key_id=_dataset_store_access_key(),
        aws_secret_access_key=_dataset_store_secret_key(),
        region_name="us-east-1",
        config=Config(
            s3={"addressing_style": "path"},
            connect_timeout=3,
            read_timeout=10,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


def _normalize_condition_name(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _append_condition(conditions: List[str], value: object) -> None:
    normalized = _normalize_condition_name(value)
    if normalized and normalized not in conditions:
        conditions.append(normalized)


def _contributing_conditions(anomaly_type: str, features: Dict[str, float], response_codes: List[int]) -> List[str]:
    definition = scenario_definition(anomaly_type)
    conditions: List[str] = []
    for item in definition.get("base_conditions", []):
        _append_condition(conditions, item)
    if anomaly_type == NORMAL_ANOMALY_TYPE:
        return conditions

    if float(features.get("error_4xx_ratio", 0.0) or 0.0) >= 0.2:
        _append_condition(conditions, "4xx_burst")
    if float(features.get("error_5xx_ratio", 0.0) or 0.0) >= 0.1:
        _append_condition(conditions, "5xx_burst")
    if float(features.get("latency_p95", 0.0) or 0.0) >= 250.0:
        _append_condition(conditions, "latency_high")
    if float(features.get("retransmission_count", 0.0) or 0.0) >= 1.0:
        _append_condition(conditions, "retry_spike")
    if 401 in response_codes or 407 in response_codes:
        _append_condition(conditions, "auth_challenge_loop")
    if float(features.get("payload_variance", 0.0) or 0.0) >= 50.0:
        _append_condition(conditions, "payload_anomaly")
    if 404 in response_codes or 483 in response_codes:
        _append_condition(conditions, "route_unreachable")
    if 486 in response_codes:
        _append_condition(conditions, "destination_busy")
    if 408 in response_codes or 504 in response_codes:
        _append_condition(conditions, "session_setup_delay")
    if float(features.get("bye_rate", 0.0) or 0.0) >= 0.4:
        _append_condition(conditions, "session_drop")
    if 500 in response_codes or 503 in response_codes:
        _append_condition(conditions, "dependency_instability")
    return conditions


def _duration_seconds_from_window(window: Dict[str, object]) -> int:
    raw_value = window.get("duration_seconds")
    if raw_value is not None:
        try:
            return max(int(float(raw_value)), 1)
        except (TypeError, ValueError):
            pass
    duration = str(window.get("duration", "30s")).strip().lower()
    if duration.endswith("s"):
        duration = duration[:-1]
    try:
        return max(int(float(duration)), 1)
    except ValueError:
        return 30


def _template_prefix_candidates(scenario: str) -> List[str]:
    scenario_key = normalize_scenario_name(scenario)
    candidates: List[str] = []
    for item in [scenario_key, canonical_anomaly_type(scenario_key)]:
        normalized = str(item or "").strip()
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return [
        _dataset_object_key(f"datasets/{FEATURE_WINDOW_DATASET_VERSION}/feature-windows/{candidate}/").rstrip("/") + "/"
        for candidate in candidates
    ]


def _latest_template_window(scenario: str) -> Dict[str, object] | None:
    client = _dataset_s3_client()
    latest_item: Dict[str, object] | None = None
    bucket = _dataset_store_bucket()
    try:
        for prefix in _template_prefix_candidates(scenario):
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for item in page.get("Contents", []):
                    if not str(item.get("Key", "")).endswith(".json"):
                        continue
                    if latest_item is None or item.get("LastModified") > latest_item.get("LastModified"):
                        latest_item = item
        if latest_item is None:
            return None
        response = client.get_object(Bucket=bucket, Key=str(latest_item["Key"]))
        payload = json.loads(response["Body"].read())
        if not isinstance(payload, dict):
            return None
        payload["_template_object_key"] = str(latest_item["Key"])
        return payload
    except (BotoCoreError, ClientError, ValueError, TypeError):
        return None


def _window_from_template(scenario: str, template: Dict[str, object]) -> Dict[str, object]:
    scenario_key = normalize_scenario_name(scenario)
    definition = scenario_definition(scenario_key)
    anomaly_type = canonical_anomaly_type(scenario_key)
    features = template.get("features")
    if not isinstance(features, dict):
        features = {}
    response_codes = template.get("response_codes")
    if not isinstance(response_codes, list):
        response_codes = list(((template.get("sipp_summary") or {}) if isinstance(template.get("sipp_summary"), dict) else {}).get("response_codes") or [])
    response_codes = [int(code) for code in response_codes if str(code).isdigit()]
    duration_seconds = _duration_seconds_from_window(template)
    conditions = list(template.get("contributing_conditions") or [])
    if not conditions:
        conditions = _contributing_conditions(anomaly_type, {key: float(value) for key, value in features.items() if isinstance(value, (int, float))}, response_codes)
    now = datetime.now(tz=timezone.utc)
    is_normal = is_nominal(scenario_key)
    feature_snapshot = dict(features)
    feature_snapshot.setdefault("node_id", str(template.get("node_id", "pcscf-1")))
    feature_snapshot.setdefault("node_role", str(template.get("node_role", "P-CSCF")))
    return {
        "window_id": f"{scenario_key}-{feature_snapshot['node_id']}-{int(now.timestamp())}",
        "window_start": now.isoformat(),
        "window_end": now.isoformat(),
        "start_time": now.isoformat(),
        "duration": f"{duration_seconds}s",
        "duration_seconds": duration_seconds,
        "node_id": feature_snapshot["node_id"],
        "node_role": feature_snapshot["node_role"],
        "source": "feature-gateway-console",
        "feature_source": "sipp-window-template",
        "schema_version": str(template.get("schema_version", "feature_schema_v1")),
        "dataset_version": FEATURE_WINDOW_DATASET_VERSION,
        "scenario_name": scenario_key,
        "label": 0 if is_normal else 1,
        "anomaly_type": anomaly_type,
        "label_confidence": float(template.get("label_confidence", 0.95) or 0.95),
        "contributing_conditions": conditions,
        "transport": str(template.get("transport") or definition.get("transport", "udp")),
        "call_limit": int(template.get("call_limit") or definition.get("default_call_limit", 12)),
        "rate": int(template.get("rate") or definition.get("default_rate", 2)),
        "target": str(template.get("target") or f"{IMS_PCSCF_HOST}:{IMS_PCSCF_PORT}"),
        "response_codes": response_codes,
        "features": feature_snapshot,
        "labels": {
            "anomaly": not is_normal,
            "anomaly_type": None if is_normal else anomaly_type,
            "contributing_conditions": conditions,
        },
        "event_count": int(template.get("event_count") or ((template.get("sipp_summary") or {}) if isinstance(template.get("sipp_summary"), dict) else {}).get("event_count") or 0),
        "latency_mean": float(template.get("latency_mean") or 0.0),
        "template_window_id": str(template.get("window_id") or ""),
        "template_object_key": str(template.get("_template_object_key") or ""),
        "telemetry_sources": {
            "pcscf": IMS_PCSCF_TELEMETRY,
            "scscf": IMS_SCSCF_TELEMETRY,
            "hss": IMS_HSS_TELEMETRY,
        },
    }


def aggregate_features(events: List[Event], node_id: str, node_role: str, duration_seconds: int, scenario: str) -> Dict[str, object]:
    scenario_key = normalize_scenario_name(scenario)
    definition = scenario_definition(scenario_key)
    anomaly_type = canonical_anomaly_type(scenario_key)
    method_counts = {"REGISTER": 0, "INVITE": 0, "BYE": 0}
    latencies: List[float] = []
    error_4xx = 0
    error_5xx = 0
    retransmissions = 0
    payload_sizes: List[int] = []
    response_codes: List[int] = []

    for event in events:
        method_counts[event.method.upper()] = method_counts.get(event.method.upper(), 0) + 1
        latencies.append(event.latency_ms)
        payload_sizes.append(event.payload_size)
        retransmissions += int(event.retransmission)
        response_codes.append(int(event.response_code))
        if 400 <= event.response_code < 500:
            error_4xx += 1
        if event.response_code >= 500:
            error_5xx += 1

    total_events = max(len(events), 1)
    features = {
        "register_rate": round(method_counts.get("REGISTER", 0) / duration_seconds, 3),
        "invite_rate": round(method_counts.get("INVITE", 0) / duration_seconds, 3),
        "bye_rate": round(method_counts.get("BYE", 0) / duration_seconds, 3),
        "error_4xx_ratio": round(error_4xx / total_events, 4),
        "error_5xx_ratio": round(error_5xx / total_events, 4),
        "latency_p95": round(max(latencies) if latencies else 0.0, 2),
        "retransmission_count": round(retransmissions, 3),
        "inter_arrival_mean": round(duration_seconds / total_events, 4),
        "payload_variance": round(max(payload_sizes) - min(payload_sizes), 3) if payload_sizes else 0.0,
        "node_id": node_id,
        "node_role": node_role,
    }
    conditions = _contributing_conditions(anomaly_type, features, response_codes)
    captured_at = datetime.now(tz=timezone.utc)
    is_normal = is_nominal(scenario_key)

    return {
        "window_id": f"{scenario_key}-{node_id}-{int(captured_at.timestamp())}",
        "window_start": captured_at.isoformat(),
        "window_end": captured_at.isoformat(),
        "start_time": captured_at.isoformat(),
        "duration": f"{duration_seconds}s",
        "duration_seconds": duration_seconds,
        "node_id": node_id,
        "node_role": node_role,
        "source": "feature-gateway-console",
        "feature_source": "scenario-fallback",
        "schema_version": "feature_schema_v1",
        "dataset_version": FEATURE_WINDOW_DATASET_VERSION,
        "scenario_name": scenario_key,
        "label": 0 if is_normal else 1,
        "anomaly_type": anomaly_type,
        "label_confidence": 0.9,
        "contributing_conditions": conditions,
        "transport": str(definition.get("transport", "udp")),
        "call_limit": int(definition.get("default_call_limit", 12)),
        "rate": int(definition.get("default_rate", 2)),
        "target": f"{IMS_PCSCF_HOST}:{IMS_PCSCF_PORT}",
        "response_codes": response_codes,
        "features": features,
        "labels": {
            "anomaly": not is_normal,
            "anomaly_type": None if is_normal else anomaly_type,
            "contributing_conditions": conditions,
        },
    }


def scenario_events(scenario: str) -> List[Event]:
    scenario_key = normalize_scenario_name(scenario)
    definition = scenario_definition(scenario_key)
    events: List[Event] = []
    for profile in event_profiles(scenario_key):
        count = int(profile.get("count", 0))
        latency_step = float(profile.get("latency_step", 0.0) or 0.0)
        payload_step = int(profile.get("payload_step", 0) or 0)
        retransmission_every = int(profile.get("retransmission_every", 0) or 0)
        for index in range(count):
            events.append(
                Event(
                    method=str(profile.get("method", "REGISTER")),
                    latency_ms=float(profile.get("latency_ms", 0.0)) + (latency_step * index),
                    response_code=int(profile.get("response_code", 200)),
                    payload_size=int(profile.get("payload_size", 0)) + (payload_step * index),
                    retransmission=bool(retransmission_every and index % retransmission_every == 0),
                    node_id="pcscf-1",
                    node_role="P-CSCF",
                )
            )
    if events:
        return events
    return [
        Event(method="REGISTER", latency_ms=22.0, payload_size=220),
        Event(method="INVITE", latency_ms=30.0, payload_size=260),
        Event(method="BYE", latency_ms=25.0, payload_size=180),
    ]


def _sip_payload(method: str, malformed: bool = False, retransmission: bool = False) -> bytes:
    body = "MALFORMED SIP PAYLOAD" if malformed else "v=0"
    extras = "X-Retrans: true\r\n" if retransmission else ""
    return (
        f"{method} sip:ims-demo@example.com SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP feature-gateway\r\n"
        f"{extras}"
        f"Content-Length: {len(body)}\r\n\r\n{body}"
    ).encode("utf-8")


def _payloads_for_scenario(scenario: str) -> List[bytes]:
    payloads: List[bytes] = []
    for profile in event_profiles(scenario):
        count = int(profile.get("count", 0))
        retransmission_every = int(profile.get("retransmission_every", 0) or 0)
        malformed = bool(profile.get("malformed", False))
        for index in range(count):
            payloads.append(
                _sip_payload(
                    str(profile.get("method", "REGISTER")),
                    malformed=malformed,
                    retransmission=bool(retransmission_every and index % retransmission_every == 0),
                )
            )
    return payloads


def _send_udp_traffic(payloads: List[bytes]) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for payload in payloads:
            sock.sendto(payload, (IMS_PCSCF_HOST, IMS_PCSCF_PORT))


def _reset_telemetry() -> None:
    for base_url in [IMS_PCSCF_TELEMETRY, IMS_SCSCF_TELEMETRY, IMS_HSS_TELEMETRY]:
        if not base_url:
            continue
        try:
            requests.post(f"{base_url}/reset", timeout=IMS_TELEMETRY_RESET_TIMEOUT)
        except Exception:
            continue


def _fetch_telemetry(base_url: str) -> Dict[str, object]:
    if not base_url:
        raise RuntimeError("IMS telemetry endpoint not configured")
    response = requests.get(f"{base_url}/telemetry", timeout=IMS_TELEMETRY_FETCH_TIMEOUT)
    response.raise_for_status()
    return response.json()


def _telemetry_to_window(scenario: str, telemetry: Dict[str, object]) -> Dict[str, object]:
    scenario_key = normalize_scenario_name(scenario)
    definition = scenario_definition(scenario_key)
    anomaly_type = canonical_anomaly_type(scenario_key)
    duration_seconds = max(int(round(float(telemetry.get("duration_seconds", 30.0)))), 1)
    features = {
        "register_rate": float(telemetry.get("register_rate", 0.0)),
        "invite_rate": float(telemetry.get("invite_rate", 0.0)),
        "bye_rate": float(telemetry.get("bye_rate", 0.0)),
        "error_4xx_ratio": float(telemetry.get("error_4xx_ratio", 0.0)),
        "error_5xx_ratio": float(telemetry.get("error_5xx_ratio", 0.0)),
        "latency_p95": float(telemetry.get("latency_p95", 0.0)),
        "retransmission_count": float(telemetry.get("retransmission_count", 0.0)),
        "inter_arrival_mean": float(telemetry.get("inter_arrival_mean", 0.0)),
        "payload_variance": float(telemetry.get("payload_variance", 0.0)),
        "node_id": str(telemetry.get("node_id", "pcscf-1")),
        "node_role": str(telemetry.get("node_role", "P-CSCF")),
    }
    conditions = _contributing_conditions(
        anomaly_type,
        features,
        [int(code) for code in telemetry.get("response_codes", []) if str(code).isdigit()],
    )
    captured_at = datetime.now(tz=timezone.utc)
    is_normal = is_nominal(scenario_key)
    return {
        "window_id": f"{scenario_key}-{features['node_id']}-{int(captured_at.timestamp())}",
        "window_start": captured_at.isoformat(),
        "window_end": captured_at.isoformat(),
        "start_time": captured_at.isoformat(),
        "duration": f"{duration_seconds}s",
        "duration_seconds": duration_seconds,
        "node_id": features["node_id"],
        "node_role": features["node_role"],
        "source": "feature-gateway-console",
        "feature_source": "ims-telemetry",
        "schema_version": "feature_schema_v1",
        "dataset_version": FEATURE_WINDOW_DATASET_VERSION,
        "scenario_name": scenario_key,
        "label": 0 if is_normal else 1,
        "anomaly_type": anomaly_type,
        "label_confidence": 0.9,
        "contributing_conditions": conditions,
        "transport": str(definition.get("transport", "udp")),
        "call_limit": int(definition.get("default_call_limit", 12)),
        "rate": int(definition.get("default_rate", 2)),
        "target": f"{IMS_PCSCF_HOST}:{IMS_PCSCF_PORT}",
        "response_codes": telemetry.get("response_codes", []),
        "features": features,
        "labels": {
            "anomaly": not is_normal,
            "anomaly_type": None if is_normal else anomaly_type,
            "contributing_conditions": conditions,
        },
        "telemetry_sources": {
            "pcscf": IMS_PCSCF_TELEMETRY,
            "scscf": IMS_SCSCF_TELEMETRY,
            "hss": IMS_HSS_TELEMETRY,
        },
    }


def live_window_for_scenario(scenario: str) -> Dict[str, object]:
    scenario_key = normalize_scenario_name(scenario)
    _reset_telemetry()
    payloads = _payloads_for_scenario(scenario_key)
    if not payloads:
        payloads = [_sip_payload("REGISTER"), _sip_payload("INVITE"), _sip_payload("BYE")]

    _send_udp_traffic(payloads)
    time.sleep(1.0)
    try:
        telemetry = _fetch_telemetry(IMS_PCSCF_TELEMETRY)
        window = _telemetry_to_window(scenario_key, telemetry)
        window["event_count"] = int(telemetry.get("message_count", len(payloads)))
        window["latency_mean"] = round(float(telemetry.get("latency_mean", 0.0)), 2)
        return window
    except Exception:
        template_window = _latest_template_window(scenario_key)
        if template_window:
            return _window_from_template(scenario_key, template_window)
        events = scenario_events(scenario_key)
        window = aggregate_features(
            events=events,
            node_id="pcscf-1",
            node_role="P-CSCF",
            duration_seconds=30,
            scenario=scenario_key,
        )
        window["event_count"] = len(events)
        window["latency_mean"] = round(mean([event.latency_ms for event in events]) if events else 0.0, 2)
        window["telemetry_sources"] = {
            "pcscf": IMS_PCSCF_TELEMETRY,
            "scscf": IMS_SCSCF_TELEMETRY,
            "hss": IMS_HSS_TELEMETRY,
        }
        return window


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/sample-window/{scenario}")
def sample_window(scenario: str):
    scenario_key = normalize_scenario_name(scenario)
    return aggregate_features(
        events=scenario_events(scenario_key),
        node_id="pcscf-1",
        node_role="P-CSCF",
        duration_seconds=30,
        scenario=scenario_key,
    )


@app.get("/live-window/{scenario}")
def live_window(scenario: str):
    return live_window_for_scenario(scenario)


@app.post("/feature-windows")
def build_feature_window(request: FeatureWindowRequest):
    scenario_key = normalize_scenario_name(request.scenario)
    events = request.events or scenario_events(scenario_key)
    window = aggregate_features(
        events=events,
        node_id=request.node_id,
        node_role=request.node_role,
        duration_seconds=request.duration_seconds,
        scenario=scenario_key,
    )
    window["event_count"] = len(events)
    window["latency_mean"] = round(mean([event.latency_ms for event in events]) if events else 0.0, 2)
    return window
