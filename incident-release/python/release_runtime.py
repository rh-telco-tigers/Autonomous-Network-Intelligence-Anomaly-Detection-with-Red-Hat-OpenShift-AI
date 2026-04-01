from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Iterable

import boto3
import pandas as pd
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from control_plane_export import export_control_plane_history


DEFAULT_DATASET_STORE_ENDPOINT = "http://model-storage-minio.ims-demo-lab.svc.cluster.local:9000"
DEFAULT_DATASET_STORE_BUCKET = "ims-models"
DEFAULT_DATASET_STORE_PREFIX = "pipelines/ims-demo-lab/datasets"
DEFAULT_RELEASE_PREFIX = "incident-release/releases"
DEFAULT_PUBLIC_RECORD_TARGET = 10_000
DEFAULT_KAFKA_BOOTSTRAP_SERVERS = "ims-release-kafka-kafka-bootstrap.ims-demo-lab.svc.cluster.local:9092"
DEFAULT_KAFKA_INCIDENTS_TOPIC = "ims-incidents-bronze"
DEFAULT_KAFKA_FEATURE_WINDOWS_TOPIC = "ims-feature-windows-bronze"
DEFAULT_KAFKA_RELEASE_ARTIFACTS_TOPIC = "ims-release-artifacts"
DEFAULT_KAFKA_MAX_EVENT_BYTES = 900_000
DEFAULT_WARNING_MIN_UNIQUE_FEATURE_WINDOWS = 100_000
DEFAULT_WARNING_MIN_ANOMALY_TYPES = 10
DEFAULT_WARNING_MIN_NORMAL_RATIO = 0.40
DEFAULT_WARNING_MAX_NORMAL_RATIO = 0.60
DEFAULT_WARNING_MAX_ELIGIBLE_RATIO = 0.95
DEFAULT_WARNING_MIN_AUTHORITATIVE_WINDOW_RATIO = 0.95
DEFAULT_WARNING_MAX_NON_AUTHORITATIVE_TRAINING_RATIO = 0.10
DEFAULT_BLOCKING_MIN_UNIQUE_FEATURE_WINDOWS = 1
DEFAULT_BLOCKING_MIN_ANOMALY_TYPES = 3
DEFAULT_BLOCKING_MIN_AUTHORITATIVE_WINDOW_RATIO = 0.50
DEFAULT_BLOCKING_MAX_NON_AUTHORITATIVE_TRAINING_RATIO = 0.50
DEFAULT_BLOCKING_MIN_NONZERO_VARIANCE_FEATURES = 3

FEATURE_SCHEMA_VERSION = "feature_schema_v1"
LABEL_TAXONOMY_VERSION = "label_taxonomy_v1"
SPLIT_POLICY_VERSION = "split_policy_v1"
PRIVACY_POLICY_VERSION = "privacy_policy_v1"
MODEL_CONTRACT_VERSION = "model_contract_v1"
SCHEMA_DOCUMENT_VERSION = "public_release_schema_v1"
JOIN_COVERAGE_WARNING_THRESHOLD = 0.80
JOIN_COVERAGE_HEALTHY_THRESHOLD = 0.90

NUMERIC_FEATURES = [
    "register_rate",
    "invite_rate",
    "bye_rate",
    "error_4xx_ratio",
    "error_5xx_ratio",
    "latency_p95",
    "retransmission_count",
    "inter_arrival_mean",
    "payload_variance",
]

NON_AUTHORITATIVE_FEATURE_SOURCES = {
    "control_plane_snapshot",
    "scenario-fallback",
    "synthetic",
}

LABEL_NORMALIZATION = {
    "normal": "normal",
    "registration_storm": "registration_storm",
    "registration_failure": "registration_failure",
    "authentication_failure": "authentication_failure",
    "register_storm": "registration_storm",
    "malformed_invite": "malformed_sip",
    "malformed_sip": "malformed_sip",
    "routing_error": "routing_error",
    "call_setup_timeout": "call_setup_timeout",
    "call_drop_mid_session": "call_drop_mid_session",
    "server_internal_error": "server_internal_error",
    "network_degradation": "network_degradation",
    "retransmission_spike": "retransmission_spike",
    "service_degradation": "service_degradation",
    "hss_latency": "service_degradation",
    "hss_overload": "service_degradation",
}

PUBLIC_FIELD_MAPPING = [
    {
        "internal_field": "incident_id",
        "public_field": "incident_public_id",
        "rule": "hashed stable public identifier",
    },
    {
        "internal_field": "feature_window_id",
        "public_field": "feature_window_public_id",
        "rule": "hashed stable public identifier",
    },
    {
        "internal_field": "raw feature_snapshot JSON",
        "public_field": "flattened numeric feature columns",
        "rule": "only allowlisted numeric features published",
    },
    {
        "internal_field": "raw RCA payload",
        "public_field": "redacted RCA summary columns",
        "rule": "free text redacted before publication",
    },
    {
        "internal_field": "audit payload JSON",
        "public_field": "latest_audit_event_type",
        "rule": "safe derived aggregate only",
    },
]

INCIDENT_HISTORY_COLUMNS = [
    "incident_public_id",
    "source_snapshot_id",
    "release_version",
    "project",
    "status",
    "anomaly_type",
    "source_anomaly_type",
    "anomaly_score",
    "model_version",
    "feature_window_public_id",
    "feature_window_available_flag",
    "rca_available_flag",
    "milvus_sync_status",
    "approval_count",
    "latest_audit_event_type",
    "created_at",
    "updated_at",
    *NUMERIC_FEATURES,
    "rca_root_cause_redacted",
    "rca_confidence",
    "rca_recommendation_redacted",
    "linkage_status",
    "training_eligibility_status",
    "normalized_scenario_family",
    "split_group_id",
    "source_dataset_version",
    "feature_schema_version",
]

TRAINING_EXAMPLE_BASE_COLUMNS = [
    "record_public_id",
    "incident_public_id",
    "feature_window_public_id",
    "source_snapshot_id",
    "release_version",
    "source_dataset_version",
    "feature_schema_version",
    "label_taxonomy_version",
    "privacy_policy_version",
    "model_contract_version",
    "scenario_name",
    "source_anomaly_type",
    "anomaly_type",
    "label",
    "label_confidence",
    "linkage_status",
    "training_eligibility_status",
    "model_version",
    "normalized_scenario_family",
    "split_group_id",
    "split",
    "window_start",
    "window_end",
    "captured_at",
]

BALANCED_TRAINING_PREFIX_COLUMNS = [
    "balanced_record_public_id",
    "balanced_copy_index",
]


def _timestamp() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _compact_timestamp() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slug(value: object, default: str = "unknown") -> str:
    text = str(value or "").strip().lower()
    if not text:
        return default
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or default


def _stable_hash(*parts: object, length: int = 16) -> str:
    payload = "||".join(str(part or "") for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def _public_id(prefix: str, release_version: str, source_id: object) -> str:
    return f"{prefix}-{_stable_hash(release_version, source_id, length=12)}"


def _workspace_root(path: str) -> Path:
    root = Path(path)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _json_dump(path: Path, payload: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return str(path)


def _text_dump(path: Path, payload: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload)
    return str(path)


def _load_json_payload(reference: str | Path) -> Any:
    ref = str(reference).strip()
    if ref.startswith("{") or ref.startswith("["):
        return json.loads(ref)
    if ref.startswith("s3://"):
        return _read_json_from_s3(ref)
    return json.loads(Path(ref).read_text())


def _load_json_reference(reference: str | Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(reference, dict):
        return reference
    payload = _load_json_payload(reference)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object at {reference}")
    return payload


def _dataset_store_endpoint() -> str:
    return os.getenv("DATASET_STORE_ENDPOINT", os.getenv("MINIO_ENDPOINT", DEFAULT_DATASET_STORE_ENDPOINT))


def _dataset_store_bucket() -> str:
    return os.getenv("DATASET_STORE_BUCKET", os.getenv("MINIO_BUCKET", DEFAULT_DATASET_STORE_BUCKET))


def _dataset_store_prefix() -> str:
    return os.getenv("DATASET_STORE_PREFIX", DEFAULT_DATASET_STORE_PREFIX).strip("/")


def _dataset_object_key(relative_path: str) -> str:
    prefix = _dataset_store_prefix()
    normalized = relative_path.strip("/")
    return f"{prefix}/{normalized}" if prefix else normalized


def _dataset_store_access_key() -> str:
    return os.getenv("DATASET_STORE_ACCESS_KEY", os.getenv("MINIO_ACCESS_KEY", "minioadmin"))


def _dataset_store_secret_key() -> str:
    return os.getenv("DATASET_STORE_SECRET_KEY", os.getenv("MINIO_SECRET_KEY", "minioadmin"))


def _dataset_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=_dataset_store_endpoint(),
        aws_access_key_id=_dataset_store_access_key(),
        aws_secret_access_key=_dataset_store_secret_key(),
        config=Config(signature_version="s3v4"),
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )


def _s3_uri(bucket: str, key: str, is_directory: bool = False) -> str:
    normalized_key = key.strip("/")
    if is_directory and normalized_key:
        normalized_key = f"{normalized_key}/"
    return f"s3://{bucket}/{normalized_key}"


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    stripped = uri.removeprefix("s3://")
    bucket, _, key = stripped.partition("/")
    return bucket, key


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _include_non_authoritative_release_rows() -> bool:
    return _env_flag("RELEASE_INCLUDE_NON_AUTHORITATIVE", False)


def _kafka_enabled() -> bool:
    return _env_flag("KAFKA_ENABLED", False)


def _kafka_required() -> bool:
    return _env_flag("KAFKA_REQUIRED", False)


def _kafka_bootstrap_servers() -> list[str]:
    raw = os.getenv("KAFKA_BOOTSTRAP_SERVERS", DEFAULT_KAFKA_BOOTSTRAP_SERVERS).strip()
    return [item.strip() for item in raw.split(",") if item.strip()]


def _kafka_max_event_bytes() -> int:
    raw = os.getenv("KAFKA_MAX_EVENT_BYTES", str(DEFAULT_KAFKA_MAX_EVENT_BYTES)).strip()
    return max(int(raw), 1_024)


def _kafka_topic(env_name: str, default: str) -> str:
    return os.getenv(env_name, default).strip() or default


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _payload_digest(payload: object) -> str:
    return hashlib.sha256(_json_bytes(payload)).hexdigest()


def _payload_mode_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        mode = str(event.get("payload_mode") or "unknown")
        counts[mode] = counts.get(mode, 0) + 1
    return counts


def _kafka_payload_summary(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        summary = {
            key: payload.get(key)
            for key in (
                "id",
                "incident_id",
                "window_id",
                "feature_window_id",
                "project",
                "status",
                "anomaly_type",
                "model_version",
                "created_at",
                "updated_at",
                "captured_at",
                "scenario_name",
                "dataset_version",
                "schema_version",
                "label",
                "label_confidence",
            )
            if payload.get(key) not in (None, "")
        }
        if isinstance(payload.get("feature_snapshot"), dict):
            summary["feature_snapshot_keys"] = sorted(payload["feature_snapshot"].keys())
        if isinstance(payload.get("features"), dict):
            summary["feature_keys"] = sorted(payload["features"].keys())
        if isinstance(payload.get("rca_payload"), dict):
            summary["has_rca_payload"] = True
            summary["rca_confidence"] = payload["rca_payload"].get("confidence")
            summary["retrieved_document_count"] = len((payload["rca_payload"].get("retrieved_documents") or []))
        if not summary:
            summary = {"payload_type": "dict", "keys": sorted(payload.keys())[:50]}
        return summary
    if isinstance(payload, list):
        return {"payload_type": "list", "count": len(payload)}
    return {"payload_type": type(payload).__name__, "preview": str(payload)[:200]}


def _prepare_kafka_event(base_event: dict[str, Any], payload: Any) -> dict[str, Any]:
    payload_size_bytes = len(_json_bytes(payload))
    payload_sha256 = _payload_digest(payload)
    max_event_bytes = _kafka_max_event_bytes()

    full_event = base_event | {
        "payload_mode": "full",
        "payload_size_bytes": payload_size_bytes,
        "payload_sha256": payload_sha256,
        "payload": payload,
    }
    if len(_json_bytes(full_event)) <= max_event_bytes:
        return full_event

    summary_event = base_event | {
        "payload_mode": "summary",
        "payload_size_bytes": payload_size_bytes,
        "payload_sha256": payload_sha256,
        "payload": _kafka_payload_summary(payload),
    }
    if len(_json_bytes(summary_event)) <= max_event_bytes:
        return summary_event

    return base_event | {
        "payload_mode": "reference",
        "payload_size_bytes": payload_size_bytes,
        "payload_sha256": payload_sha256,
        "payload_keys": sorted(payload.keys())[:50] if isinstance(payload, dict) else None,
    }


def _new_kafka_producer():
    from kafka import KafkaProducer

    return KafkaProducer(
        bootstrap_servers=_kafka_bootstrap_servers(),
        client_id=os.getenv("KAFKA_CLIENT_ID", "ims-incident-release"),
        security_protocol=os.getenv("KAFKA_SECURITY_PROTOCOL", "PLAINTEXT"),
        acks=os.getenv("KAFKA_ACKS", "all"),
        retries=max(int(os.getenv("KAFKA_RETRIES", "3")), 0),
        linger_ms=max(int(os.getenv("KAFKA_LINGER_MS", "0")), 0),
        request_timeout_ms=max(int(os.getenv("KAFKA_REQUEST_TIMEOUT_MS", "20000")), 1_000),
        max_block_ms=max(int(os.getenv("KAFKA_MAX_BLOCK_MS", "20000")), 1_000),
        value_serializer=_json_bytes,
        key_serializer=lambda value: str(value).encode("utf-8"),
    )


def _publish_kafka_topic_events(topic: str, events: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    payload_modes = _payload_mode_counts([event for _, event in events])
    summary = {
        "status": "disabled" if not _kafka_enabled() else "skipped",
        "attempted_records": len(events),
        "published_records": 0,
        "payload_modes": payload_modes,
    }
    if not _kafka_enabled():
        return summary
    if not events:
        return summary

    producer = None
    try:
        producer = _new_kafka_producer()
        send_timeout = float(os.getenv("KAFKA_SEND_TIMEOUT_SECONDS", "20"))
        flush_timeout = float(os.getenv("KAFKA_FLUSH_TIMEOUT_SECONDS", "20"))
        for key, event in events:
            producer.send(topic, key=key, value=event).get(timeout=send_timeout)
            summary["published_records"] += 1
        producer.flush(timeout=flush_timeout)
        summary["status"] = "published"
        return summary
    except Exception as exc:
        summary["status"] = "error_required" if _kafka_required() else "error_non_blocking"
        summary["error"] = str(exc)
        if _kafka_required():
            raise RuntimeError(f"Kafka publish failed for topic {topic}") from exc
        return summary
    finally:
        if producer is not None:
            producer.close()


def _publish_kafka_topics(topic_events: dict[str, list[tuple[str, dict[str, Any]]]]) -> dict[str, Any]:
    return {
        "enabled": _kafka_enabled(),
        "required": _kafka_required(),
        "bootstrap_servers": _kafka_bootstrap_servers(),
        "topics": {
            topic: _publish_kafka_topic_events(topic, events)
            for topic, events in topic_events.items()
        },
    }


def _build_incident_kafka_events(
    *,
    release_version: str,
    snapshot_id: str,
    source_dataset_version: str,
    project: str,
    incidents_path: str,
    incidents: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    emitted_at = _timestamp()
    events: list[tuple[str, dict[str, Any]]] = []
    for index, incident in enumerate(incidents):
        incident_id = str(incident.get("id") or f"incident-{index}")
        event = _prepare_kafka_event(
            {
                "event_type": "incident_snapshot_exported",
                "event_version": "v1",
                "emitted_at": emitted_at,
                "release_version": release_version,
                "source_snapshot_id": snapshot_id,
                "source_dataset_version": source_dataset_version,
                "project": project,
                "incident_id": incident_id,
                "snapshot_artifact_path": incidents_path,
                "source": "control-plane",
            },
            incident,
        )
        events.append((incident_id, event))
    return events


def _build_feature_window_kafka_events(
    *,
    release_version: str,
    snapshot_id: str,
    source_dataset_version: str,
    project: str,
    feature_windows: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any]]]:
    emitted_at = _timestamp()
    events: list[tuple[str, dict[str, Any]]] = []
    for index, item in enumerate(feature_windows):
        window_id = str(item.get("window_id") or f"window-{index}")
        event = _prepare_kafka_event(
            {
                "event_type": "feature_window_snapshot_exported",
                "event_version": "v1",
                "emitted_at": emitted_at,
                "release_version": release_version,
                "source_snapshot_id": snapshot_id,
                "source_dataset_version": source_dataset_version,
                "project": project,
                "feature_window_id": window_id,
                "object_key": item.get("object_key"),
                "s3_uri": item.get("s3_uri"),
                "source": "minio-feature-window-store",
            },
            item.get("payload"),
        )
        events.append((window_id, event))
    return events


def _build_release_artifact_kafka_events(final_manifest: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    release_version = str(final_manifest.get("release_version") or "")
    emitted_at = _timestamp()
    release_manifest_uri = str((final_manifest.get("artifacts") or {}).get("release_manifest") or "")
    events: list[tuple[str, dict[str, Any]]] = []
    for artifact_name, artifact_uri in sorted((final_manifest.get("artifacts") or {}).items()):
        payload = {
            "artifact_name": artifact_name,
            "artifact_uri": artifact_uri,
            "bundle_prefix": final_manifest.get("bundle_prefix"),
            "release_manifest_uri": release_manifest_uri,
        }
        event = _prepare_kafka_event(
            {
                "event_type": "release_artifact_published",
                "event_version": "v1",
                "emitted_at": emitted_at,
                "release_version": release_version,
                "project": final_manifest.get("project"),
                "source_snapshot_id": final_manifest.get("source_snapshot_id"),
                "artifact_name": artifact_name,
            },
            payload,
        )
        events.append((f"{release_version}:{artifact_name}", event))

    summary_event = _prepare_kafka_event(
        {
            "event_type": "release_published",
            "event_version": "v1",
            "emitted_at": emitted_at,
            "release_version": release_version,
            "project": final_manifest.get("project"),
            "source_snapshot_id": final_manifest.get("source_snapshot_id"),
        },
        final_manifest,
    )
    events.append((release_version, summary_event))
    return events


def _read_bytes_from_s3(uri: str) -> bytes:
    bucket, key = _parse_s3_uri(uri)
    response = _dataset_s3_client().get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def _read_json_from_s3(uri: str) -> Any:
    return json.loads(_read_bytes_from_s3(uri).decode("utf-8"))


def _read_parquet_reference(reference: str | Path) -> pd.DataFrame:
    ref = str(reference)
    if ref.startswith("s3://"):
        return pd.read_parquet(BytesIO(_read_bytes_from_s3(ref)))
    return pd.read_parquet(ref)


def _upload_file_to_s3(path: Path, prefix: str) -> str:
    bucket = _dataset_store_bucket()
    key = f"{prefix.rstrip('/')}/{path.name}"
    _dataset_s3_client().upload_file(str(path), bucket, key)
    return _s3_uri(bucket, key)


def _list_s3_objects(prefix: str) -> list[dict[str, Any]]:
    paginator = _dataset_s3_client().get_paginator("list_objects_v2")
    objects: list[dict[str, Any]] = []
    for page in paginator.paginate(Bucket=_dataset_store_bucket(), Prefix=prefix):
        objects.extend(page.get("Contents", []))
    return objects


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw.strip())


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return float(raw.strip())


def _release_quality_enforcement_mode() -> str:
    mode = os.getenv("RELEASE_QUALITY_ENFORCEMENT", "strict").strip().lower()
    return mode if mode in {"strict", "advisory"} else "strict"


def _object_exists_s3(key: str) -> bool:
    try:
        _dataset_s3_client().head_object(Bucket=_dataset_store_bucket(), Key=key)
        return True
    except ClientError:
        return False


def _parse_ts(value: object) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _time_bucket(value: object) -> str:
    return _parse_ts(value).strftime("%Y-%m-%dT%H")


def _safe_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _canonical_anomaly_type(payload: dict[str, Any]) -> str:
    labels = payload.get("labels") if isinstance(payload.get("labels"), dict) else {}
    raw = _slug(
        payload.get("anomaly_type")
        or labels.get("anomaly_type")
        or payload.get("scenario_name")
        or "normal",
        default="normal",
    )
    return LABEL_NORMALIZATION.get(raw, raw)


def _normalized_scenario_family(payload: dict[str, Any], fallback: str = "incident_backfill") -> str:
    labels = payload.get("labels") if isinstance(payload.get("labels"), dict) else {}
    return _slug(
        payload.get("scenario_name")
        or payload.get("scenario")
        or labels.get("scenario_name")
        or labels.get("anomaly_type")
        or payload.get("anomaly_type")
        or fallback,
        default=fallback,
    )


def _window_event_time(window: dict[str, Any]) -> datetime:
    return _parse_ts(window.get("captured_at") or window.get("window_end") or window.get("window_start"))


def _window_payload_hash(window: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(window, sort_keys=True).encode("utf-8")).hexdigest()


def _choose_window(current: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    current_time = _window_event_time(current)
    candidate_time = _window_event_time(candidate)
    if candidate_time > current_time:
        return candidate
    if candidate_time < current_time:
        return current
    return candidate if _window_payload_hash(candidate) > _window_payload_hash(current) else current


def _list_feature_window_objects(dataset_version: str) -> list[str]:
    bucket = _dataset_store_bucket()
    prefix = _dataset_object_key(f"datasets/{dataset_version}/feature-windows/").rstrip("/") + "/"
    paginator = _dataset_s3_client().get_paginator("list_objects_v2")
    keys: list[str] = []
    try:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                key = str(item.get("Key") or "")
                if key.endswith(".json"):
                    keys.append(key)
    except (BotoCoreError, ClientError) as exc:
        raise RuntimeError(f"Unable to enumerate feature windows for {dataset_version}") from exc
    return sorted(keys)


def _window_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _feature_window_id(window: dict[str, Any], fallback: str) -> str:
    return str(window.get("window_id") or fallback)


def _feature_source(window: dict[str, Any] | None) -> str:
    if not isinstance(window, dict):
        return "missing"
    return str(window.get("feature_source") or "unspecified")


def _is_authoritative_feature_window(window: dict[str, Any] | None) -> bool:
    return isinstance(window, dict) and _feature_source(window) not in NON_AUTHORITATIVE_FEATURE_SOURCES


def _window_linkage_status(window: dict[str, Any] | None) -> str:
    if _is_authoritative_feature_window(window):
        return "linked_feature_window"
    return "linked_non_authoritative_window"


def _window_features(window: dict[str, Any]) -> dict[str, Any]:
    features = window.get("features")
    return features if isinstance(features, dict) else {}


def _extract_numeric_features(source: dict[str, Any] | None) -> dict[str, float | None]:
    payload = source or {}
    features = payload.get("features") if isinstance(payload.get("features"), dict) else payload
    values = {feature: _safe_float(features.get(feature)) for feature in NUMERIC_FEATURES}
    latency_alias = _safe_float(features.get("latency_p95_ms")) if isinstance(features, dict) else None
    if values["latency_p95"] is None and latency_alias is not None:
        values["latency_p95"] = latency_alias
    return values


def _flatten_numeric_features(source: dict[str, Any] | None) -> dict[str, float | None]:
    return {feature: value for feature, value in _extract_numeric_features(source).items()}


def _redact_text(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    redacted = re.sub(r"https?://\S+", "[redacted-url]", text)
    redacted = re.sub(r"\b[a-z0-9-]+(?:\.[a-z0-9-]+){2,}\b", "[redacted-host]", redacted, flags=re.I)
    redacted = re.sub(r"\b[0-9a-f]{8}-[0-9a-f-]{27}\b", "[redacted-id]", redacted, flags=re.I)
    redacted = redacted.replace("ims-demo-lab", "[redacted-namespace]")
    return redacted


def _latest_audit_event_type(events: Iterable[dict[str, Any]]) -> str | None:
    materialized = list(events)
    if not materialized:
        return None
    latest = sorted(materialized, key=lambda item: str(item.get("created_at") or ""))[-1]
    return str(latest.get("event_type") or "")


def _milvus_sync_status(rca_payload: dict[str, Any] | None) -> str:
    payload = rca_payload or {}
    documents = payload.get("retrieved_documents") if isinstance(payload.get("retrieved_documents"), list) else []
    if documents:
        return "attached"
    if payload:
        return "none"
    return "not_available"


def _rca_summary(rca_payload: dict[str, Any] | None) -> dict[str, object]:
    payload = rca_payload or {}
    return {
        "rca_root_cause_redacted": _redact_text(payload.get("root_cause")),
        "rca_confidence": _safe_float(payload.get("confidence")),
        "rca_recommendation_redacted": _redact_text(payload.get("recommendation")),
    }


def _split_assignment(split_group_id: str) -> str:
    bucket = int(hashlib.sha256(split_group_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    if bucket < 70:
        return "train"
    if bucket < 85:
        return "validation"
    return "test"


def _incident_linkage_status(incident: dict[str, Any], matched_window: dict[str, Any] | None) -> str:
    if matched_window is not None:
        return _window_linkage_status(matched_window)
    feature_snapshot = incident.get("feature_snapshot")
    if isinstance(feature_snapshot, dict) and any(value is not None for value in _extract_numeric_features(feature_snapshot).values()):
        return "reconstructed_from_incident_snapshot"
    return "no_feature_data"


def _incident_training_status(matched_window: dict[str, Any] | None) -> str:
    if matched_window is None:
        return "ineligible_missing_features"
    return "eligible" if _is_authoritative_feature_window(matched_window) else "ineligible_non_authoritative_window"


def _training_status(linkage_status: str, feature_values: dict[str, float | None], anomaly_type: str) -> str:
    if linkage_status == "linked_non_authoritative_window":
        return "ineligible_non_authoritative_window"
    if linkage_status == "reconstructed_from_incident_snapshot":
        return "ineligible_missing_features"
    if not any(value is not None for value in feature_values.values()):
        return "ineligible_missing_features"
    if not anomaly_type:
        return "ineligible_ambiguous_label"
    return "eligible"


def _target_record_count(value: int | None) -> int:
    if value is not None:
        return max(int(value), 0)
    return max(int(os.getenv("PUBLIC_RECORD_TARGET", str(DEFAULT_PUBLIC_RECORD_TARGET))), 0)


def _release_owner() -> str:
    return os.getenv("RELEASE_OWNER", "IMS anomaly platform team")


def _release_contact() -> str:
    return os.getenv("RELEASE_CONTACT", "platform-owner@example.com")


def _previous_release_manifest_ref(release_version: str, release_prefix: str) -> str | None:
    explicit = os.getenv("PREVIOUS_RELEASE_VERSION", "").strip()
    if explicit:
        return _s3_uri(_dataset_store_bucket(), f"{release_prefix.rstrip('/')}/{explicit}/release_manifest.json")

    manifests = [
        item
        for item in _list_s3_objects(f"{release_prefix.rstrip('/')}/")
        if str(item.get("Key") or "").endswith("/release_manifest.json")
    ]
    candidates = [
        item
        for item in manifests
        if f"/{release_version}/release_manifest.json" not in str(item.get("Key") or "")
    ]
    if not candidates:
        return None
    latest = sorted(candidates, key=lambda item: item.get("LastModified"))[-1]
    return _s3_uri(_dataset_store_bucket(), str(latest["Key"]))


def _feature_summary(frame: pd.DataFrame) -> dict[str, dict[str, float | None]]:
    summary: dict[str, dict[str, float | None]] = {}
    for feature in NUMERIC_FEATURES:
        if feature not in frame.columns:
            continue
        series = pd.to_numeric(frame[feature], errors="coerce")
        if series.dropna().empty:
            summary[feature] = {"mean": None, "min": None, "max": None}
            continue
        summary[feature] = {
            "mean": round(float(series.mean()), 6),
            "min": round(float(series.min()), 6),
            "max": round(float(series.max()), 6),
        }
    return summary


def _feature_variability(frame: pd.DataFrame) -> dict[str, dict[str, float | int | None]]:
    variability: dict[str, dict[str, float | int | None]] = {}
    for feature in NUMERIC_FEATURES:
        if feature not in frame.columns:
            continue
        series = pd.to_numeric(frame[feature], errors="coerce")
        non_null = series.dropna()
        if non_null.empty:
            variability[feature] = {
                "std": None,
                "unique_values": 0,
                "non_null_ratio": 0.0,
            }
            continue
        variability[feature] = {
            "std": round(float(non_null.std(ddof=0)), 6),
            "unique_values": int(non_null.nunique()),
            "non_null_ratio": round(float(non_null.notna().sum() / max(len(series), 1)), 6),
        }
    return variability


def _quality_check(
    *,
    name: str,
    actual: float | int,
    description: str,
    warning_min: float | int | None = None,
    warning_max: float | int | None = None,
    blocking_min: float | int | None = None,
    blocking_max: float | int | None = None,
) -> dict[str, Any]:
    status = "pass"
    message = None
    if blocking_min is not None and actual < blocking_min:
        status = "blocking"
        message = f"{name}={actual} is below the blocking minimum {blocking_min}"
    elif blocking_max is not None and actual > blocking_max:
        status = "blocking"
        message = f"{name}={actual} is above the blocking maximum {blocking_max}"
    elif warning_min is not None and actual < warning_min:
        status = "warning"
        message = f"{name}={actual} is below the warning target {warning_min}"
    elif warning_max is not None and actual > warning_max:
        status = "warning"
        message = f"{name}={actual} is above the warning target {warning_max}"
    return {
        "description": description,
        "actual": actual,
        "warning_min": warning_min,
        "warning_max": warning_max,
        "blocking_min": blocking_min,
        "blocking_max": blocking_max,
        "status": status,
        "message": message,
    }


def _quality_scorecard(
    *,
    incident_df: pd.DataFrame,
    training_df: pd.DataFrame,
    windows: list[dict[str, Any]],
) -> dict[str, Any]:
    anomaly_type_counts = training_df["anomaly_type"].value_counts().sort_index().to_dict() if not training_df.empty else {}
    scenario_family_counts = (
        training_df["normalized_scenario_family"].value_counts().sort_index().to_dict()
        if not training_df.empty
        else {}
    )
    incident_status_counts = incident_df["status"].value_counts().sort_index().to_dict() if not incident_df.empty else {}
    linkage_counts = training_df["linkage_status"].value_counts().sort_index().to_dict() if not training_df.empty else {}
    window_source_counts: dict[str, int] = {}
    for window in windows:
        source = _feature_source(window)
        window_source_counts[source] = window_source_counts.get(source, 0) + 1

    unique_feature_window_count = (
        int(training_df["feature_window_public_id"].dropna().nunique())
        if "feature_window_public_id" in training_df.columns and not training_df.empty
        else 0
    )
    anomaly_type_count = int(len(anomaly_type_counts))
    normal_ratio = (
        float((training_df["anomaly_type"] == "normal").sum() / max(len(training_df), 1))
        if not training_df.empty
        else 0.0
    )
    eligible_ratio = (
        float((training_df["training_eligibility_status"] == "eligible").sum() / max(len(training_df), 1))
        if not training_df.empty
        else 0.0
    )
    authoritative_window_count = sum(1 for window in windows if _is_authoritative_feature_window(window))
    authoritative_window_ratio = authoritative_window_count / max(len(windows), 1) if windows else 0.0
    non_authoritative_training_rows = int(
        (training_df["linkage_status"] != "linked_feature_window").sum()
    ) if not training_df.empty else 0
    non_authoritative_training_ratio = (
        non_authoritative_training_rows / max(len(training_df), 1) if not training_df.empty else 0.0
    )
    variability = _feature_variability(training_df)
    nonzero_variance_features = sum(
        1
        for feature in variability.values()
        if feature.get("std") not in (None, 0.0) and int(feature.get("unique_values") or 0) > 1
    )
    missing_feature_ratio = {
        feature: round(float(pd.to_numeric(training_df[feature], errors="coerce").isna().mean()), 6)
        for feature in NUMERIC_FEATURES
        if feature in training_df.columns
    } if not training_df.empty else {}

    checks = {
        "minimum_unique_feature_windows": _quality_check(
            name="minimum_unique_feature_windows",
            actual=unique_feature_window_count,
            description="Release should contain enough distinct feature windows to represent real traffic variation.",
            warning_min=_env_int("QUALITY_WARNING_MIN_UNIQUE_FEATURE_WINDOWS", DEFAULT_WARNING_MIN_UNIQUE_FEATURE_WINDOWS),
            blocking_min=_env_int("QUALITY_BLOCKING_MIN_UNIQUE_FEATURE_WINDOWS", DEFAULT_BLOCKING_MIN_UNIQUE_FEATURE_WINDOWS),
        ),
        "minimum_anomaly_types": _quality_check(
            name="minimum_anomaly_types",
            actual=anomaly_type_count,
            description="Release should cover multiple incident categories rather than only a narrow demo subset.",
            warning_min=_env_int("QUALITY_WARNING_MIN_ANOMALY_TYPES", DEFAULT_WARNING_MIN_ANOMALY_TYPES),
            blocking_min=_env_int("QUALITY_BLOCKING_MIN_ANOMALY_TYPES", DEFAULT_BLOCKING_MIN_ANOMALY_TYPES),
        ),
        "normal_ratio_band": _quality_check(
            name="normal_ratio",
            actual=round(normal_ratio, 6),
            description="Real corpora should keep a meaningful normal baseline instead of only anomalous cases.",
            warning_min=_env_float("QUALITY_WARNING_MIN_NORMAL_RATIO", DEFAULT_WARNING_MIN_NORMAL_RATIO),
            warning_max=_env_float("QUALITY_WARNING_MAX_NORMAL_RATIO", DEFAULT_WARNING_MAX_NORMAL_RATIO),
        ),
        "maximum_eligible_ratio": _quality_check(
            name="eligible_ratio",
            actual=round(eligible_ratio, 6),
            description="A corpus that is too clean can hide the incomplete and ambiguous rows seen in real operations.",
            warning_max=_env_float("QUALITY_WARNING_MAX_ELIGIBLE_RATIO", DEFAULT_WARNING_MAX_ELIGIBLE_RATIO),
        ),
        "minimum_authoritative_window_ratio": _quality_check(
            name="authoritative_window_ratio",
            actual=round(authoritative_window_ratio, 6),
            description="Most feature windows should come from authoritative persisted traffic captures, not reconstructed snapshots.",
            warning_min=_env_float(
                "QUALITY_WARNING_MIN_AUTHORITATIVE_WINDOW_RATIO",
                DEFAULT_WARNING_MIN_AUTHORITATIVE_WINDOW_RATIO,
            ),
            blocking_min=_env_float(
                "QUALITY_BLOCKING_MIN_AUTHORITATIVE_WINDOW_RATIO",
                DEFAULT_BLOCKING_MIN_AUTHORITATIVE_WINDOW_RATIO,
            ),
        ),
        "maximum_non_authoritative_training_ratio": _quality_check(
            name="non_authoritative_training_ratio",
            actual=round(non_authoritative_training_ratio, 6),
            description="Training rows should be dominated by authoritative feature windows rather than reconstructed or fallback sources.",
            warning_max=_env_float(
                "QUALITY_WARNING_MAX_NON_AUTHORITATIVE_TRAINING_RATIO",
                DEFAULT_WARNING_MAX_NON_AUTHORITATIVE_TRAINING_RATIO,
            ),
            blocking_max=_env_float(
                "QUALITY_BLOCKING_MAX_NON_AUTHORITATIVE_TRAINING_RATIO",
                DEFAULT_BLOCKING_MAX_NON_AUTHORITATIVE_TRAINING_RATIO,
            ),
        ),
        "minimum_nonzero_variance_features": _quality_check(
            name="minimum_nonzero_variance_features",
            actual=nonzero_variance_features,
            description="Feature columns should show real variation across the corpus instead of collapsing to repeated values.",
            warning_min=len(NUMERIC_FEATURES),
            blocking_min=_env_int(
                "QUALITY_BLOCKING_MIN_NONZERO_VARIANCE_FEATURES",
                DEFAULT_BLOCKING_MIN_NONZERO_VARIANCE_FEATURES,
            ),
        ),
    }
    score_lookup = {"pass": 1.0, "warning": 0.5, "blocking": 0.0}
    quality_score = round(
        100.0 * sum(score_lookup[check["status"]] for check in checks.values()) / max(len(checks), 1),
        2,
    )

    return {
        "quality_score": quality_score,
        "metrics": {
            "unique_feature_window_count": unique_feature_window_count,
            "anomaly_type_count": anomaly_type_count,
            "anomaly_type_counts": anomaly_type_counts,
            "scenario_family_counts": scenario_family_counts,
            "incident_status_counts": incident_status_counts,
            "linkage_counts": linkage_counts,
            "window_source_counts": window_source_counts,
            "authoritative_window_count": authoritative_window_count,
            "authoritative_window_ratio": round(authoritative_window_ratio, 6),
            "eligible_ratio": round(eligible_ratio, 6),
            "normal_ratio": round(normal_ratio, 6),
            "non_authoritative_training_rows": non_authoritative_training_rows,
            "non_authoritative_training_ratio": round(non_authoritative_training_ratio, 6),
            "nonzero_variance_features": nonzero_variance_features,
            "feature_missingness": missing_feature_ratio,
            "feature_variability": variability,
        },
        "checks": checks,
    }


def _distribution_delta(current: pd.Series, previous: pd.Series) -> dict[str, dict[str, float]]:
    current_counts = current.value_counts(normalize=True).sort_index()
    previous_counts = previous.value_counts(normalize=True).sort_index()
    labels = sorted(set(current_counts.index.tolist()) | set(previous_counts.index.tolist()))
    result: dict[str, dict[str, float]] = {}
    for label in labels:
        current_ratio = float(current_counts.get(label, 0.0))
        previous_ratio = float(previous_counts.get(label, 0.0))
        result[str(label)] = {
            "current_ratio": round(current_ratio, 6),
            "previous_ratio": round(previous_ratio, 6),
            "delta": round(current_ratio - previous_ratio, 6),
        }
    return result


def _compute_drift(
    *,
    current_incident_df: pd.DataFrame,
    current_training_df: pd.DataFrame,
    release_prefix: str,
    release_version: str,
) -> dict[str, Any]:
    previous_manifest_ref = _previous_release_manifest_ref(release_version, release_prefix)
    if not previous_manifest_ref:
        return {
            "status": "reporting_only",
            "evaluated": False,
            "previous_release_reference": None,
        }

    previous_manifest = _load_json_reference(previous_manifest_ref)
    previous_artifacts = previous_manifest.get("artifacts") if isinstance(previous_manifest.get("artifacts"), dict) else None
    if not previous_artifacts:
        return {
            "status": "reporting_only",
            "evaluated": False,
            "previous_release_reference": previous_manifest_ref,
        }
    previous_incident_df = _read_parquet_reference(previous_artifacts["incident_history_parquet"])
    previous_training_df = _read_parquet_reference(previous_artifacts["training_examples_parquet"])

    current_eligible_ratio = float(
        (current_training_df["training_eligibility_status"] == "eligible").sum() / max(len(current_training_df), 1)
    )
    previous_eligible_ratio = float(
        (previous_training_df["training_eligibility_status"] == "eligible").sum() / max(len(previous_training_df), 1)
    )

    current_features = current_training_df[[feature for feature in NUMERIC_FEATURES if feature in current_training_df.columns]]
    previous_features = previous_training_df[[feature for feature in NUMERIC_FEATURES if feature in previous_training_df.columns]]

    feature_drift: dict[str, dict[str, float | None]] = {}
    current_feature_summary = _feature_summary(current_features)
    previous_feature_summary = _feature_summary(previous_features)
    for feature in NUMERIC_FEATURES:
        current_mean = current_feature_summary.get(feature, {}).get("mean")
        previous_mean = previous_feature_summary.get(feature, {}).get("mean")
        feature_drift[feature] = {
            "current_mean": current_mean,
            "previous_mean": previous_mean,
            "mean_delta": round(float(current_mean or 0.0) - float(previous_mean or 0.0), 6)
            if current_mean is not None or previous_mean is not None
            else None,
        }

    return {
        "status": "reporting_only",
        "evaluated": True,
        "previous_release_reference": previous_manifest_ref,
        "incident_volume": {
            "current": int(len(current_incident_df)),
            "previous": int(len(previous_incident_df)),
            "delta": int(len(current_incident_df) - len(previous_incident_df)),
        },
        "anomaly_type_distribution": _distribution_delta(
            current_incident_df["anomaly_type"],
            previous_incident_df["anomaly_type"],
        ),
        "training_eligibility_ratio": {
            "current": round(current_eligible_ratio, 6),
            "previous": round(previous_eligible_ratio, 6),
            "delta": round(current_eligible_ratio - previous_eligible_ratio, 6),
        },
        "feature_summary_drift": feature_drift,
    }


def _schema_payload(
    *,
    incident_columns: list[str],
    training_columns: list[str],
    balanced_columns: list[str],
) -> dict[str, Any]:
    return {
        "schema_document_version": SCHEMA_DOCUMENT_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "label_taxonomy_version": LABEL_TAXONOMY_VERSION,
        "privacy_policy_version": PRIVACY_POLICY_VERSION,
        "artifacts": {
            "ims_incident_history": {
                "formats": ["parquet", "csv"],
                "columns": incident_columns,
            },
            "ims_training_examples": {
                "formats": ["parquet", "csv"],
                "columns": training_columns,
            },
            "ims_training_examples_balanced": {
                "formats": ["parquet", "csv"],
                "columns": balanced_columns,
            },
        },
    }


def _dataset_card(
    *,
    release_version: str,
    source_snapshot_id: str,
    project: str,
    source_dataset_version: str,
    public_record_target: int,
    counts: dict[str, int],
    join_coverage: dict[str, Any],
    drift: dict[str, Any],
    normalized_labels: list[str],
) -> str:
    previous_release = drift.get("previous_release_reference") or "none"
    normalized_labels_text = ", ".join(f"`{label}`" for label in normalized_labels) if normalized_labels else "`normal`"
    return (
        f"# IMS Incident Release Dataset\n\n"
        f"## Origin\n\n"
        f"This dataset is derived from the IMS anomaly demo control-plane history and persisted feature-window data.\n\n"
        f"- release_version: `{release_version}`\n"
        f"- source_snapshot_id: `{source_snapshot_id}`\n"
        f"- source_dataset_version: `{source_dataset_version}`\n"
        f"- project: `{project}`\n\n"
        f"## Public field and privacy policy\n\n"
        f"- allowlist-based publication\n"
        f"- internal identifiers replaced with stable public IDs\n"
        f"- free-text RCA content redacted before publication\n"
        f"- internal routes, namespaces, tokens, and object-store paths are not published\n\n"
        f"## Label taxonomy\n\n"
        f"- taxonomy version: `{LABEL_TAXONOMY_VERSION}`\n"
        f"- normalized labels include {normalized_labels_text}\n\n"
        f"## Linkage and eligibility\n\n"
        f"- join coverage status: `{join_coverage['status']}`\n"
        f"- incident_to_feature_window_ratio: `{join_coverage['incident_to_feature_window_ratio']}`\n"
        f"- balanced convenience export target rows: `{public_record_target}`\n\n"
        f"## Intended use\n\n"
        f"- incident analytics\n"
        f"- retrieval and RCA experiments\n"
        f"- offline dataset exploration and benchmarking\n\n"
        f"## Non-goals\n\n"
        f"- live model deployment\n"
        f"- serving configuration\n"
        f"- automation approval execution\n\n"
        f"## Known limitations and bias\n\n"
        f"- balanced export is a convenience artifact and may oversample rare labels\n"
        f"- the source corpus reflects demo traffic and operational lab conditions\n"
        f"- sandbox releases may have lower incident-to-feature linkage coverage than a full production dataset\n\n"
        f"## Reproducibility\n\n"
        f"- release rows are anchored to `source_snapshot_id`\n"
        f"- split assignment uses deterministic hashing of lineage plus scenario family and time bucket\n"
        f"- previous release reference used for drift reporting: `{previous_release}`\n\n"
        f"## Ownership\n\n"
        f"- owner: `{_release_owner()}`\n"
        f"- contact: `{_release_contact()}`\n\n"
        f"## Counts\n\n"
        f"- incidents: `{counts.get('incidents', 0)}`\n"
        f"- training_examples: `{counts.get('training_examples', 0)}`\n"
        f"- eligible_training_examples: `{counts.get('eligible_training_examples', 0)}`\n"
        f"- balanced_training_examples: `{counts.get('balanced_training_examples', 0)}`\n"
    )


def _build_balanced_training_dataset(training_df: pd.DataFrame, target_count: int, release_version: str) -> pd.DataFrame:
    eligible = training_df[training_df["training_eligibility_status"] == "eligible"].copy()
    if target_count <= 0 or eligible.empty:
        return pd.DataFrame(columns=BALANCED_TRAINING_PREFIX_COLUMNS + list(training_df.columns))

    eligible = eligible.sort_values(by=["anomaly_type", "record_public_id"], kind="mergesort").reset_index(drop=True)
    anomaly_types = sorted(eligible["anomaly_type"].dropna().unique().tolist())
    if not anomaly_types:
        return pd.DataFrame(columns=BALANCED_TRAINING_PREFIX_COLUMNS + list(training_df.columns))

    base = target_count // len(anomaly_types)
    remainder = target_count % len(anomaly_types)
    parts: list[pd.DataFrame] = []

    for index, anomaly_type in enumerate(anomaly_types):
        desired = base + (1 if index < remainder else 0)
        if desired <= 0:
            continue
        label_rows = eligible[eligible["anomaly_type"] == anomaly_type].reset_index(drop=True)
        positions = [position % len(label_rows) for position in range(desired)]
        sampled = label_rows.iloc[positions].copy().reset_index(drop=True)
        sampled["balanced_copy_index"] = list(range(desired))
        sampled["balanced_record_public_id"] = [
            _public_id("bal", release_version, f"{sampled.at[row, 'record_public_id']}:{row}")
            for row in range(len(sampled))
        ]
        parts.append(sampled)

    if not parts:
        return pd.DataFrame(columns=BALANCED_TRAINING_PREFIX_COLUMNS + list(training_df.columns))

    balanced = pd.concat(parts, ignore_index=True)
    balanced = balanced[BALANCED_TRAINING_PREFIX_COLUMNS + list(training_df.columns)]
    return balanced.sort_values(
        by=["anomaly_type", "balanced_copy_index", "record_public_id"],
        kind="mergesort",
    ).reset_index(drop=True)


def _write_dataframe_artifacts(frame: pd.DataFrame, parquet_path: Path, csv_path: Path) -> None:
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(parquet_path, index=False)
    frame.to_csv(csv_path, index=False)


def snapshot_sources(
    *,
    release_version: str,
    source_dataset_version: str,
    project: str,
    workspace_root: str,
    source_snapshot_id: str | None = None,
    approval_limit: int = 50_000,
    audit_limit: int = 50_000,
) -> dict[str, Any]:
    snapshot_id = source_snapshot_id or f"snapshot-{_compact_timestamp()}"
    cutoff_ts = _timestamp()
    root = _workspace_root(workspace_root)
    bronze_root = root / "bronze" / snapshot_id
    feature_root = bronze_root / "feature-windows"

    control_plane = export_control_plane_history(
        project=project,
        approval_limit=approval_limit,
        audit_limit=audit_limit,
    )
    incidents = control_plane["incidents"]
    approvals = control_plane["approvals"]
    audit_events = control_plane["audit_events"]
    rca_enrichment = [
        {
            "incident_id": str(incident.get("id") or ""),
            "has_rca_payload": bool(incident.get("rca_payload")),
            "retrieved_document_count": len(
                (incident.get("rca_payload") or {}).get("retrieved_documents", [])
            )
            if isinstance(incident.get("rca_payload"), dict)
            else 0,
        }
        for incident in incidents
    ]

    incidents_path = _json_dump(bronze_root / "control-plane" / "incidents.json", incidents)
    approvals_path = _json_dump(bronze_root / "control-plane" / "approvals.json", approvals)
    audit_path = _json_dump(bronze_root / "control-plane" / "audit-events.json", audit_events)
    rca_path = _json_dump(bronze_root / "control-plane" / "rca-enrichment.json", rca_enrichment)

    feature_windows: list[dict[str, Any]] = []
    relative_prefix = f"datasets/{source_dataset_version}/feature-windows/"
    prefix = _dataset_object_key(relative_prefix).rstrip("/") + "/"
    for key in _list_feature_window_objects(source_dataset_version):
        payload = _read_json_from_s3(_s3_uri(_dataset_store_bucket(), key))
        relative_key = key.removeprefix(prefix)
        local_path = feature_root / relative_key
        _json_dump(local_path, payload)
        feature_windows.append(
            {
                "object_key": key,
                "s3_uri": _s3_uri(_dataset_store_bucket(), key),
                "local_path": str(local_path),
                "window_id": str(payload.get("window_id") or Path(key).stem) if isinstance(payload, dict) else Path(key).stem,
                "captured_at": payload.get("captured_at") if isinstance(payload, dict) else None,
                "payload": payload,
            }
        )

    kafka_summary = _publish_kafka_topics(
        {
            _kafka_topic("KAFKA_INCIDENTS_TOPIC", DEFAULT_KAFKA_INCIDENTS_TOPIC): _build_incident_kafka_events(
                release_version=release_version,
                snapshot_id=snapshot_id,
                source_dataset_version=source_dataset_version,
                project=project,
                incidents_path=incidents_path,
                incidents=incidents,
            ),
            _kafka_topic("KAFKA_FEATURE_WINDOWS_TOPIC", DEFAULT_KAFKA_FEATURE_WINDOWS_TOPIC): _build_feature_window_kafka_events(
                release_version=release_version,
                snapshot_id=snapshot_id,
                source_dataset_version=source_dataset_version,
                project=project,
                feature_windows=feature_windows,
            ),
        }
    )

    feature_windows_manifest = [
        {key: value for key, value in item.items() if key != "payload"}
        for item in feature_windows
    ]

    return {
        "release_version": release_version,
        "source_snapshot_id": snapshot_id,
        "snapshot_cutoff_ts": cutoff_ts,
        "project": project,
        "source_dataset_version": source_dataset_version,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "artifacts": {
            "incidents_path": incidents_path,
            "approvals_path": approvals_path,
            "audit_events_path": audit_path,
            "rca_enrichment_path": rca_path,
            "feature_windows_dir": str(feature_root),
        },
        "feature_window_source_prefix": prefix,
        "counts": {
            "incidents": len(incidents),
            "approvals": len(approvals),
            "audit_events": len(audit_events),
            "feature_window_objects": len(feature_windows),
            "rca_enrichment_records": len(rca_enrichment),
        },
        "feature_windows": feature_windows_manifest,
        "kafka": kafka_summary,
    }


def normalize_release(
    *,
    snapshot_manifest_ref: str,
    workspace_root: str,
    public_record_target: int | None = None,
) -> dict[str, Any]:
    snapshot = _load_json_reference(snapshot_manifest_ref)
    root = _workspace_root(workspace_root)
    release_version = str(snapshot["release_version"])
    snapshot_id = str(snapshot["source_snapshot_id"])
    source_dataset_version = str(snapshot["source_dataset_version"])
    record_target = _target_record_count(public_record_target)

    work_root = root / "silver" / snapshot_id
    gold_root = root / "gold" / release_version
    work_root.mkdir(parents=True, exist_ok=True)
    gold_root.mkdir(parents=True, exist_ok=True)

    incidents = _load_json_payload(snapshot["artifacts"]["incidents_path"])
    approvals = _load_json_payload(snapshot["artifacts"]["approvals_path"])
    audit_events = _load_json_payload(snapshot["artifacts"]["audit_events_path"])

    approvals_by_incident: dict[str, list[dict[str, Any]]] = {}
    for approval in approvals:
        incident_id = str(approval.get("incident_id") or "")
        approvals_by_incident.setdefault(incident_id, []).append(approval)

    audit_by_incident: dict[str, list[dict[str, Any]]] = {}
    for event in audit_events:
        incident_id = str(event.get("incident_id") or "")
        audit_by_incident.setdefault(incident_id, []).append(event)

    deduped_windows: dict[tuple[str, str], dict[str, Any]] = {}
    duplicate_window_conflicts: list[dict[str, str]] = []
    for item in snapshot.get("feature_windows", []):
        payload = _load_json_payload(item["local_path"])
        for window in _window_records(payload):
            window_id = _feature_window_id(window, Path(str(item["local_path"])).stem)
            dedup_key = (source_dataset_version, window_id)
            existing = deduped_windows.get(dedup_key)
            if existing is None:
                deduped_windows[dedup_key] = window
                continue
            winner = _choose_window(existing, window)
            if _window_payload_hash(existing) != _window_payload_hash(window):
                duplicate_window_conflicts.append(
                    {
                        "feature_window_id": window_id,
                        "source_dataset_version": source_dataset_version,
                        "kept_captured_at": str(winner.get("captured_at") or winner.get("window_end") or ""),
                    }
                )
            deduped_windows[dedup_key] = winner

    windows = []
    for index, window in enumerate(deduped_windows.values()):
        normalized = {
            **window,
            "window_id": _feature_window_id(window, f"{source_dataset_version}-{index}"),
            "dataset_version": str(window.get("dataset_version") or source_dataset_version),
            "schema_version": str(window.get("schema_version") or FEATURE_SCHEMA_VERSION),
            "scenario_name": str(window.get("scenario_name") or window.get("anomaly_type") or "normal"),
            "anomaly_type": _canonical_anomaly_type(window),
            "label": int(window.get("label", 0 if _canonical_anomaly_type(window) == "normal" else 1)),
            "label_confidence": float(window.get("label_confidence", 0.95)),
            "window_start": str(window.get("window_start") or ""),
            "window_end": str(window.get("window_end") or ""),
            "captured_at": str(window.get("captured_at") or ""),
            "features": _window_features(window),
        }
        windows.append(normalized)

    include_non_authoritative_rows = _include_non_authoritative_release_rows()
    existing_window_ids = {str(window["window_id"]) for window in windows}
    control_plane_snapshot_windows_added = 0
    if include_non_authoritative_rows:
        for incident in incidents:
            feature_window_id = str(incident.get("feature_window_id") or "")
            snapshot_features = _flatten_numeric_features(
                incident.get("feature_snapshot") if isinstance(incident.get("feature_snapshot"), dict) else None
            )
            if (
                not feature_window_id
                or feature_window_id in existing_window_ids
                or not any(value is not None for value in snapshot_features.values())
            ):
                continue
            source_anomaly_type = _slug(incident.get("anomaly_type") or "normal")
            anomaly_type = LABEL_NORMALIZATION.get(source_anomaly_type, source_anomaly_type)
            windows.append(
                {
                    "window_id": feature_window_id,
                    "dataset_version": source_dataset_version,
                    "schema_version": str(snapshot.get("feature_schema_version", FEATURE_SCHEMA_VERSION)),
                    "scenario_name": source_anomaly_type,
                    "anomaly_type": anomaly_type,
                    "label": 0 if anomaly_type == "normal" else 1,
                    "label_confidence": 0.9,
                    "window_start": str(incident.get("created_at") or ""),
                    "window_end": str(incident.get("updated_at") or incident.get("created_at") or ""),
                    "captured_at": str(incident.get("updated_at") or incident.get("created_at") or ""),
                    "features": {feature: value for feature, value in snapshot_features.items() if value is not None},
                    "model_version": str(incident.get("model_version") or "unknown"),
                    "feature_source": "control_plane_snapshot",
                }
            )
            existing_window_ids.add(feature_window_id)
            control_plane_snapshot_windows_added += 1
    windows.sort(key=lambda item: (str(item["window_id"]), str(item["captured_at"]), str(item["window_end"])))

    windows_by_id = {str(window["window_id"]): window for window in windows}
    incident_contexts: list[dict[str, Any]] = []
    filtered_non_authoritative_incident_row_count = 0
    for incident in sorted(incidents, key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or ""))):
        incident_id = str(incident.get("id") or "")
        source_anomaly_type = _slug(incident.get("anomaly_type") or "unknown")
        anomaly_type = LABEL_NORMALIZATION.get(source_anomaly_type, source_anomaly_type)
        feature_window_id = str(
            incident.get("feature_window_id")
            or (incident.get("feature_snapshot") or {}).get("feature_window_id")
            or ""
        )
        matched_window = windows_by_id.get(feature_window_id)
        linkage_status = _incident_linkage_status(incident, matched_window)
        if not include_non_authoritative_rows and linkage_status != "linked_feature_window":
            filtered_non_authoritative_incident_row_count += 1
            continue
        scenario_family = (
            _normalized_scenario_family(matched_window, fallback=source_anomaly_type)
            if matched_window is not None
            else _normalized_scenario_family(incident.get("feature_snapshot") or {}, fallback=source_anomaly_type)
        )
        split_group_id = _stable_hash(
            incident_id or feature_window_id,
            scenario_family,
            _time_bucket(incident.get("created_at")),
            length=24,
        )
        incident_public_id = _public_id("inc", release_version, incident_id)
        feature_window_public_id = _public_id("win", release_version, feature_window_id) if feature_window_id else None
        feature_values = _flatten_numeric_features(matched_window or incident.get("feature_snapshot"))
        rca_payload = incident.get("rca_payload") if isinstance(incident.get("rca_payload"), dict) else {}
        model_version = str(
            incident.get("model_version")
            or (matched_window or {}).get("model_version")
            or "unknown"
        )
        incident_contexts.append(
            {
                "incident": incident,
                "incident_id": incident_id,
                "source_anomaly_type": source_anomaly_type,
                "anomaly_type": anomaly_type,
                "feature_window_id": feature_window_id,
                "matched_window": matched_window,
                "linkage_status": linkage_status,
                "scenario_family": scenario_family,
                "split_group_id": split_group_id,
                "incident_public_id": incident_public_id,
                "feature_window_public_id": feature_window_public_id,
                "feature_values": feature_values,
                "rca_payload": rca_payload,
                "model_version": model_version,
            }
        )

    incidents_by_window_id: dict[str, list[dict[str, Any]]] = {}
    for context in incident_contexts:
        feature_window_id = str(context["feature_window_id"] or "")
        if feature_window_id:
            incidents_by_window_id.setdefault(feature_window_id, []).append(context["incident"])

    incident_records: list[dict[str, Any]] = []
    training_records: list[dict[str, Any]] = []
    id_mapping: list[dict[str, str]] = []

    for context in incident_contexts:
        incident = context["incident"]
        incident_id = context["incident_id"]
        source_anomaly_type = context["source_anomaly_type"]
        anomaly_type = context["anomaly_type"]
        feature_window_id = context["feature_window_id"]
        matched_window = context["matched_window"]
        linkage_status = context["linkage_status"]
        scenario_family = context["scenario_family"]
        split_group_id = context["split_group_id"]
        incident_public_id = context["incident_public_id"]
        feature_window_public_id = context["feature_window_public_id"]
        feature_values = context["feature_values"]
        rca_payload = context["rca_payload"]
        model_version = context["model_version"]

        id_mapping.append({"kind": "incident", "source_id": incident_id, "public_id": incident_public_id})
        if feature_window_id:
            id_mapping.append(
                {
                    "kind": "feature_window",
                    "source_id": feature_window_id,
                    "public_id": feature_window_public_id or "",
                }
            )

        incident_records.append(
            {
                "incident_public_id": incident_public_id,
                "source_snapshot_id": snapshot_id,
                "release_version": release_version,
                "project": str(incident.get("project") or snapshot.get("project") or "ims-demo"),
                "status": str(incident.get("status") or "open"),
                "anomaly_type": anomaly_type,
                "source_anomaly_type": str(incident.get("anomaly_type") or source_anomaly_type),
                "anomaly_score": _safe_float(incident.get("anomaly_score")),
                "model_version": model_version,
                "feature_window_public_id": feature_window_public_id,
                "feature_window_available_flag": matched_window is not None,
                "rca_available_flag": bool(rca_payload),
                "milvus_sync_status": _milvus_sync_status(rca_payload),
                "approval_count": len(approvals_by_incident.get(incident_id, [])),
                "latest_audit_event_type": _latest_audit_event_type(audit_by_incident.get(incident_id, [])),
                "created_at": str(incident.get("created_at") or ""),
                "updated_at": str(incident.get("updated_at") or incident.get("created_at") or ""),
                **feature_values,
                **_rca_summary(rca_payload),
                "linkage_status": linkage_status,
                "training_eligibility_status": _incident_training_status(matched_window),
                "normalized_scenario_family": scenario_family,
                "split_group_id": split_group_id,
                "source_dataset_version": source_dataset_version,
                "feature_schema_version": str(
                    (matched_window or {}).get("schema_version") or snapshot.get("feature_schema_version", FEATURE_SCHEMA_VERSION)
                ),
            }
        )

        if linkage_status == "reconstructed_from_incident_snapshot":
            training_status = _training_status(linkage_status, feature_values, anomaly_type)
            training_records.append(
                {
                    "record_public_id": _public_id("rec", release_version, f"reconstructed:{incident_id}"),
                    "incident_public_id": incident_public_id,
                    "feature_window_public_id": None,
                    "source_snapshot_id": snapshot_id,
                    "release_version": release_version,
                    "source_dataset_version": source_dataset_version,
                    "feature_schema_version": str(snapshot.get("feature_schema_version", FEATURE_SCHEMA_VERSION)),
                    "label_taxonomy_version": LABEL_TAXONOMY_VERSION,
                    "privacy_policy_version": PRIVACY_POLICY_VERSION,
                    "model_contract_version": MODEL_CONTRACT_VERSION,
                    "scenario_name": scenario_family,
                    "source_anomaly_type": str(incident.get("anomaly_type") or source_anomaly_type),
                    "anomaly_type": anomaly_type,
                    "label": 0 if anomaly_type == "normal" else 1,
                    "label_confidence": 0.5,
                    "linkage_status": linkage_status,
                    "training_eligibility_status": training_status,
                    "model_version": model_version,
                    "normalized_scenario_family": scenario_family,
                    "split_group_id": split_group_id,
                    "split": None,
                    "window_start": None,
                    "window_end": None,
                    "captured_at": None,
                    **feature_values,
                }
            )

    for window in windows:
        feature_window_id = str(window["window_id"])
        linked_incidents = incidents_by_window_id.get(feature_window_id, [])
        linked_incident = sorted(
            linked_incidents,
            key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")),
        )[0] if linked_incidents else None
        incident_public_id = (
            _public_id("inc", release_version, linked_incident.get("id"))
            if linked_incident is not None
            else None
        )
        feature_window_public_id = _public_id("win", release_version, feature_window_id)
        source_anomaly_type = _slug(window.get("anomaly_type") or "normal")
        anomaly_type = LABEL_NORMALIZATION.get(source_anomaly_type, source_anomaly_type)
        scenario_family = _normalized_scenario_family(window, fallback=source_anomaly_type)
        split_group_id = _stable_hash(
            linked_incident.get("id") if linked_incident is not None else feature_window_id,
            scenario_family,
            _time_bucket(window.get("window_start") or window.get("captured_at")),
            length=24,
        )
        feature_values = _flatten_numeric_features(window)
        linkage_status = _window_linkage_status(window)
        training_records.append(
            {
                "record_public_id": _public_id("rec", release_version, feature_window_id),
                "incident_public_id": incident_public_id,
                "feature_window_public_id": feature_window_public_id,
                "source_snapshot_id": snapshot_id,
                "release_version": release_version,
                "source_dataset_version": source_dataset_version,
                "feature_schema_version": str(window.get("schema_version") or FEATURE_SCHEMA_VERSION),
                "label_taxonomy_version": LABEL_TAXONOMY_VERSION,
                "privacy_policy_version": PRIVACY_POLICY_VERSION,
                "model_contract_version": MODEL_CONTRACT_VERSION,
                "scenario_name": str(window.get("scenario_name") or scenario_family),
                "source_anomaly_type": str(window.get("anomaly_type") or source_anomaly_type),
                "anomaly_type": anomaly_type,
                "label": int(window.get("label", 0 if anomaly_type == "normal" else 1)),
                "label_confidence": float(window.get("label_confidence", 0.95)),
                "linkage_status": linkage_status,
                "training_eligibility_status": _training_status(linkage_status, feature_values, anomaly_type),
                "model_version": str(
                    (linked_incident or {}).get("model_version")
                    or window.get("model_version")
                    or "unknown"
                ),
                "normalized_scenario_family": scenario_family,
                "split_group_id": split_group_id,
                "split": _split_assignment(split_group_id),
                "window_start": str(window.get("window_start") or ""),
                "window_end": str(window.get("window_end") or ""),
                "captured_at": str(window.get("captured_at") or ""),
                **feature_values,
            }
        )

    incident_df = pd.DataFrame(incident_records, columns=INCIDENT_HISTORY_COLUMNS)
    if not incident_df.empty:
        incident_df = incident_df.sort_values(by=["created_at", "incident_public_id"], kind="mergesort").reset_index(drop=True)

    feature_columns = list(NUMERIC_FEATURES)
    training_columns = TRAINING_EXAMPLE_BASE_COLUMNS + feature_columns
    training_df = pd.DataFrame(training_records, columns=training_columns)
    if not training_df.empty:
        training_df = training_df.sort_values(by=["window_start", "record_public_id"], kind="mergesort").reset_index(drop=True)

    eligible_training_df = training_df[training_df["training_eligibility_status"] == "eligible"].copy()
    split_manifest_df = eligible_training_df[["record_public_id", "split_group_id", "split"]].copy() if not eligible_training_df.empty else pd.DataFrame(
        columns=["record_public_id", "split_group_id", "split"]
    )
    split_manifest_df = split_manifest_df.sort_values(by=["split", "split_group_id", "record_public_id"], kind="mergesort").reset_index(drop=True)

    balanced_training_df = _build_balanced_training_dataset(training_df, record_target, release_version)
    balanced_columns = BALANCED_TRAINING_PREFIX_COLUMNS + training_columns
    if balanced_training_df.empty:
        balanced_training_df = pd.DataFrame(columns=balanced_columns)
    else:
        balanced_training_df = balanced_training_df[balanced_columns]

    label_dictionary_records = sorted(
        {
            (
                str(row.get("source_anomaly_type") or ""),
                str(row.get("anomaly_type") or ""),
                LABEL_TAXONOMY_VERSION,
            )
            for row in incident_records + training_records
        }
    )
    label_dictionary_df = pd.DataFrame(
        label_dictionary_records,
        columns=["source_anomaly_type", "anomaly_type", "label_taxonomy_version"],
    )

    schema_payload = _schema_payload(
        incident_columns=INCIDENT_HISTORY_COLUMNS,
        training_columns=training_columns,
        balanced_columns=balanced_columns,
    )

    linked_incident_rows = int((incident_df["linkage_status"] == "linked_feature_window").sum()) if not incident_df.empty else 0
    incident_row_count = int(len(incident_df))
    incident_to_feature_window_ratio = linked_incident_rows / incident_row_count if incident_row_count else 0.0
    if incident_to_feature_window_ratio >= JOIN_COVERAGE_HEALTHY_THRESHOLD:
        join_coverage_status = "healthy"
    elif incident_to_feature_window_ratio >= JOIN_COVERAGE_WARNING_THRESHOLD:
        join_coverage_status = "warning"
    else:
        join_coverage_status = "blocking"

    eligibility_counts = (
        training_df["training_eligibility_status"].value_counts().sort_index().to_dict()
        if not training_df.empty
        else {}
    )

    counts = {
        "incidents": incident_row_count,
        "training_examples": int(len(training_df)),
        "eligible_training_examples": int((training_df["training_eligibility_status"] == "eligible").sum()) if not training_df.empty else 0,
        "balanced_training_examples": int(len(balanced_training_df)),
    }

    dataset_card_text = _dataset_card(
        release_version=release_version,
        source_snapshot_id=snapshot_id,
        project=str(snapshot.get("project") or "ims-demo"),
        source_dataset_version=source_dataset_version,
        public_record_target=record_target,
        counts=counts,
        join_coverage={
            "incident_to_feature_window_ratio": round(incident_to_feature_window_ratio, 4),
            "status": join_coverage_status,
        },
        drift={"previous_release_reference": os.getenv("PREVIOUS_RELEASE_VERSION", "").strip() or None},
        normalized_labels=sorted(label_dictionary_df["anomaly_type"].dropna().unique().tolist()),
    )

    quality_report = {
        "release_version": release_version,
        "source_snapshot_id": snapshot_id,
        "incident_row_count": incident_row_count,
        "training_row_count": int(len(training_df)),
        "eligible_training_row_count": counts["eligible_training_examples"],
        "balanced_training_row_count": counts["balanced_training_examples"],
        "public_record_target": record_target,
        "linked_incident_rows": linked_incident_rows,
        "incident_to_feature_window_ratio": round(incident_to_feature_window_ratio, 4),
        "join_coverage_status": join_coverage_status,
        "eligibility_counts": eligibility_counts,
        "filtered_non_authoritative_incident_row_count": filtered_non_authoritative_incident_row_count,
        "duplicate_window_conflicts": duplicate_window_conflicts,
        "control_plane_snapshot_windows_added": control_plane_snapshot_windows_added,
        "feature_window_source_prefix": snapshot.get("feature_window_source_prefix"),
        "quality_scorecard": _quality_scorecard(
            incident_df=incident_df,
            training_df=training_df,
            windows=windows,
        ),
        "validation_results": {},
    }

    incident_history_parquet = gold_root / "ims_incident_history.parquet"
    incident_history_csv = gold_root / "ims_incident_history.csv"
    training_examples_parquet = gold_root / "ims_training_examples.parquet"
    training_examples_csv = gold_root / "ims_training_examples.csv"
    balanced_training_parquet = gold_root / "ims_training_examples_balanced.parquet"
    balanced_training_csv = gold_root / "ims_training_examples_balanced.csv"
    split_manifest_json = gold_root / "training_split_manifest.json"
    split_manifest_csv = gold_root / "training_split_manifest.csv"
    quality_report_path = gold_root / "quality_report.json"
    schema_path = gold_root / "schema.json"
    dataset_card_path = gold_root / "dataset_card.md"
    label_dictionary_path = gold_root / "label_dictionary.csv"
    public_field_mapping_path = gold_root / "public_field_mapping.csv"
    id_mapping_path = work_root / "id_mapping.json"

    _write_dataframe_artifacts(incident_df, incident_history_parquet, incident_history_csv)
    _write_dataframe_artifacts(training_df, training_examples_parquet, training_examples_csv)
    _write_dataframe_artifacts(balanced_training_df, balanced_training_parquet, balanced_training_csv)
    _json_dump(split_manifest_json, split_manifest_df.to_dict(orient="records"))
    split_manifest_df.to_csv(split_manifest_csv, index=False)
    _json_dump(quality_report_path, quality_report)
    _json_dump(schema_path, schema_payload)
    _text_dump(dataset_card_path, dataset_card_text)
    label_dictionary_df.to_csv(label_dictionary_path, index=False)
    pd.DataFrame(PUBLIC_FIELD_MAPPING).to_csv(public_field_mapping_path, index=False)
    _json_dump(id_mapping_path, sorted(id_mapping, key=lambda item: (item["kind"], item["source_id"])))

    return {
        "release_version": release_version,
        "source_snapshot_id": snapshot_id,
        "snapshot_cutoff_ts": snapshot["snapshot_cutoff_ts"],
        "project": snapshot["project"],
        "source_dataset_version": source_dataset_version,
        "source_kafka": snapshot.get("kafka", {}),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "label_taxonomy_version": LABEL_TAXONOMY_VERSION,
        "split_policy_version": SPLIT_POLICY_VERSION,
        "privacy_policy_version": PRIVACY_POLICY_VERSION,
        "model_contract_version": MODEL_CONTRACT_VERSION,
        "public_record_target": record_target,
        "artifacts": {
            "incident_history_parquet": str(incident_history_parquet),
            "incident_history_csv": str(incident_history_csv),
            "training_examples_parquet": str(training_examples_parquet),
            "training_examples_csv": str(training_examples_csv),
            "training_examples_balanced_parquet": str(balanced_training_parquet),
            "training_examples_balanced_csv": str(balanced_training_csv),
            "split_manifest_json": str(split_manifest_json),
            "split_manifest_csv": str(split_manifest_csv),
            "quality_report": str(quality_report_path),
            "schema_json": str(schema_path),
            "dataset_card_md": str(dataset_card_path),
            "label_dictionary_csv": str(label_dictionary_path),
            "public_field_mapping_csv": str(public_field_mapping_path),
            "id_mapping_json": str(id_mapping_path),
        },
        "counts": counts,
        "join_coverage": {
            "incident_to_feature_window_ratio": round(incident_to_feature_window_ratio, 4),
            "status": join_coverage_status,
            "warning_threshold": JOIN_COVERAGE_WARNING_THRESHOLD,
            "healthy_threshold": JOIN_COVERAGE_HEALTHY_THRESHOLD,
        },
        "public_columns": {
            "incident_history": INCIDENT_HISTORY_COLUMNS,
            "training_examples": training_columns,
            "training_examples_balanced": balanced_columns,
        },
    }


def validate_release(*, normalized_manifest_ref: str) -> dict[str, Any]:
    manifest = _load_json_reference(normalized_manifest_ref)
    incident_df = _read_parquet_reference(manifest["artifacts"]["incident_history_parquet"])
    training_df = _read_parquet_reference(manifest["artifacts"]["training_examples_parquet"])
    balanced_df = _read_parquet_reference(manifest["artifacts"]["training_examples_balanced_parquet"])
    split_manifest = _load_json_payload(manifest["artifacts"]["split_manifest_json"])
    quality_report = _load_json_reference(manifest["artifacts"]["quality_report"])
    quality_enforcement_mode = _release_quality_enforcement_mode()
    advisory_quality_mode = quality_enforcement_mode == "advisory"

    validation_errors: list[str] = []
    validation_warnings: list[str] = []

    if incident_df.empty:
        validation_errors.append("Incident export is empty")
    if training_df.empty:
        validation_errors.append("Training example export is empty")

    if set(incident_df.columns) != set(INCIDENT_HISTORY_COLUMNS):
        validation_errors.append("Incident history columns drifted from the allowlist")

    expected_training_columns = set(manifest["public_columns"]["training_examples"])
    if set(training_df.columns) != expected_training_columns:
        validation_errors.append("Training example columns drifted from the allowlist")

    expected_balanced_columns = set(manifest["public_columns"]["training_examples_balanced"])
    if set(balanced_df.columns) != expected_balanced_columns:
        validation_errors.append("Balanced training columns drifted from the allowlist")

    if incident_df["incident_public_id"].duplicated().any():
        validation_errors.append("Duplicate incident_public_id values detected")
    if training_df["record_public_id"].duplicated().any():
        validation_errors.append("Duplicate record_public_id values detected")
    if balanced_df["balanced_record_public_id"].duplicated().any():
        validation_errors.append("Duplicate balanced_record_public_id values detected")

    if incident_df["source_snapshot_id"].isna().any():
        validation_errors.append("Incident rows missing source_snapshot_id")
    if training_df["source_snapshot_id"].isna().any():
        validation_errors.append("Training rows missing source_snapshot_id")

    if training_df["training_eligibility_status"].isna().any():
        validation_errors.append("Training rows missing training_eligibility_status")

    split_groups = pd.DataFrame(split_manifest)
    if not split_groups.empty:
        split_group_conflicts = split_groups.groupby("split_group_id")["split"].nunique()
        if (split_group_conflicts > 1).any():
            validation_errors.append("Split manifest contains overlapping split_group_id values")
        ineligible_ids = set(
            training_df.loc[training_df["training_eligibility_status"] != "eligible", "record_public_id"].dropna().tolist()
        )
        if set(split_groups["record_public_id"].tolist()) & ineligible_ids:
            validation_errors.append("Split manifest contains ineligible rows")

    join_coverage_status = str(quality_report.get("join_coverage_status") or "unknown")
    override_reason = os.getenv("JOIN_COVERAGE_OVERRIDE_REASON", "").strip()
    if join_coverage_status == "blocking" and advisory_quality_mode:
        validation_warnings.append("Join coverage is below the blocking threshold; advisory quality mode is active")
    elif join_coverage_status == "blocking" and not override_reason:
        validation_errors.append("Join coverage is below the blocking threshold")
    elif join_coverage_status == "blocking" and override_reason:
        validation_warnings.append("Join coverage override applied")
    elif join_coverage_status == "warning":
        validation_warnings.append("Join coverage is below the healthy threshold")

    target = int(manifest.get("public_record_target", 0))
    if target > 0 and len(balanced_df) != target:
        validation_errors.append("Balanced training export does not match the requested public_record_target")

    if not (balanced_df["training_eligibility_status"] == "eligible").all() if not balanced_df.empty else False:
        validation_errors.append("Balanced training export contains ineligible rows")

    quality_scorecard = quality_report.get("quality_scorecard") if isinstance(quality_report.get("quality_scorecard"), dict) else {}
    quality_checks = quality_scorecard.get("checks") if isinstance(quality_scorecard.get("checks"), dict) else {}
    for name, check in quality_checks.items():
        status = str(check.get("status") or "pass")
        message = str(check.get("message") or name)
        if status == "blocking" and advisory_quality_mode:
            validation_warnings.append(f"Quality gate advisory: {message}")
        elif status == "blocking":
            validation_errors.append(f"Quality gate failed: {message}")
        elif status == "warning":
            validation_warnings.append(f"Quality gate warning: {message}")

    status = "failed" if validation_errors else "passed"
    validation_results = {
        "status": status,
        "errors": validation_errors,
        "warnings": validation_warnings,
        "incident_rows": int(len(incident_df)),
        "training_rows": int(len(training_df)),
        "eligible_training_rows": int((training_df["training_eligibility_status"] == "eligible").sum()) if not training_df.empty else 0,
        "balanced_training_rows": int(len(balanced_df)),
        "quality_enforcement_mode": quality_enforcement_mode,
        "join_coverage_override_reason": override_reason or None,
    }

    quality_report["validation_results"] = validation_results
    Path(manifest["artifacts"]["quality_report"]).write_text(json.dumps(quality_report, indent=2))
    return manifest | {"validation_results": validation_results}


def publish_release(
    *,
    validation_manifest_ref: str,
    workspace_root: str,
    release_mode: str = "draft",
    previous_release_version: str | None = None,
) -> dict[str, Any]:
    manifest = _load_json_reference(validation_manifest_ref)
    validation_status = str((manifest.get("validation_results") or {}).get("status") or "unknown")
    if validation_status != "passed":
        raise RuntimeError(f"Release validation must pass before publish; current status={validation_status}")
    release_version = str(manifest["release_version"])
    release_prefix = os.getenv("RELEASE_ARTIFACT_PREFIX", DEFAULT_RELEASE_PREFIX).strip("/") or DEFAULT_RELEASE_PREFIX
    upload_prefix = f"{release_prefix}/{release_version}"
    publish_root = _workspace_root(workspace_root) / "published" / release_version
    publish_root.mkdir(parents=True, exist_ok=True)

    manifest_key = f"{upload_prefix}/release_manifest.json"
    if _object_exists_s3(manifest_key) and release_mode != "draft-replacement":
        raise RuntimeError(f"Release version {release_version} is already published")

    incident_df = _read_parquet_reference(manifest["artifacts"]["incident_history_parquet"])
    training_df = _read_parquet_reference(manifest["artifacts"]["training_examples_parquet"])
    quality_report = _load_json_reference(manifest["artifacts"]["quality_report"])

    if previous_release_version:
        os.environ["PREVIOUS_RELEASE_VERSION"] = previous_release_version
    drift = _compute_drift(
        current_incident_df=incident_df,
        current_training_df=training_df,
        release_prefix=release_prefix,
        release_version=release_version,
    )
    quality_report["distribution_drift"] = drift
    Path(manifest["artifacts"]["quality_report"]).write_text(json.dumps(quality_report, indent=2))

    bundle_path = publish_root / "ims_incident_release_bundle.zip"
    release_manifest_path = publish_root / "release_manifest.json"

    release_manifest = {
        "release_version": release_version,
        "release_mode": release_mode,
        "source_snapshot_id": manifest["source_snapshot_id"],
        "snapshot_cutoff_ts": manifest["snapshot_cutoff_ts"],
        "project": manifest["project"],
        "source_dataset_version": manifest["source_dataset_version"],
        "feature_schema_version": manifest["feature_schema_version"],
        "label_taxonomy_version": manifest["label_taxonomy_version"],
        "split_policy_version": manifest["split_policy_version"],
        "privacy_policy_version": manifest["privacy_policy_version"],
        "model_contract_version": manifest["model_contract_version"],
        "previous_release_version": previous_release_version or drift.get("previous_release_reference"),
        "distribution_drift": drift,
        "eligibility_counts": quality_report.get("eligibility_counts", {}),
        "join_coverage": manifest["join_coverage"],
        "validation_results": manifest.get("validation_results", {}),
        "counts": manifest["counts"],
        "public_record_target": manifest.get("public_record_target", DEFAULT_PUBLIC_RECORD_TARGET),
        "published_at": _timestamp(),
    }

    uploaded_artifacts: dict[str, str] = {}
    for key, value in manifest["artifacts"].items():
        uploaded_artifacts[key] = _upload_file_to_s3(Path(value), upload_prefix)

    final_manifest = release_manifest | {
        "artifacts": uploaded_artifacts,
        "bundle_prefix": _s3_uri(_dataset_store_bucket(), upload_prefix, is_directory=True),
        "snapshot_kafka": manifest.get("source_kafka", {}),
    }
    _json_dump(release_manifest_path, final_manifest)

    bundle_members = [
        Path(manifest["artifacts"]["incident_history_parquet"]),
        Path(manifest["artifacts"]["incident_history_csv"]),
        Path(manifest["artifacts"]["training_examples_parquet"]),
        Path(manifest["artifacts"]["training_examples_csv"]),
        Path(manifest["artifacts"]["training_examples_balanced_parquet"]),
        Path(manifest["artifacts"]["training_examples_balanced_csv"]),
        Path(manifest["artifacts"]["split_manifest_json"]),
        Path(manifest["artifacts"]["split_manifest_csv"]),
        Path(manifest["artifacts"]["quality_report"]),
        Path(manifest["artifacts"]["schema_json"]),
        Path(manifest["artifacts"]["dataset_card_md"]),
        Path(manifest["artifacts"]["label_dictionary_csv"]),
        Path(manifest["artifacts"]["public_field_mapping_csv"]),
        release_manifest_path,
    ]
    with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member in bundle_members:
            archive.write(member, arcname=member.name)

    final_manifest["artifacts"]["bundle_archive"] = _upload_file_to_s3(bundle_path, upload_prefix)
    final_manifest["artifacts"]["release_manifest"] = _upload_file_to_s3(release_manifest_path, upload_prefix)
    final_manifest["kafka"] = _publish_kafka_topics(
        {
            _kafka_topic("KAFKA_RELEASE_ARTIFACTS_TOPIC", DEFAULT_KAFKA_RELEASE_ARTIFACTS_TOPIC): _build_release_artifact_kafka_events(
                final_manifest
            )
        }
    )
    _json_dump(release_manifest_path, final_manifest)
    final_manifest["artifacts"]["release_manifest"] = _upload_file_to_s3(release_manifest_path, upload_prefix)
    return final_manifest
