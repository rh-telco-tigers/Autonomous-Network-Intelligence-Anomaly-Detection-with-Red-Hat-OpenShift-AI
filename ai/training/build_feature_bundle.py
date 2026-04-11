#!/usr/bin/env python3
"""Build a feature-store-ready bundle dataset from persisted feature windows and control-plane history."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

import pandas as pd
import requests

from ai.training.train_and_register import (
    FEATURES,
    FEATURE_SCHEMA_VERSION,
    _data_store_mode,
    _dataset_object_key,
    _dataset_s3_client,
    _dataset_store_bucket,
    _download_file_reference,
    _ensure_dataset_bucket,
    _json_dump,
    _live_feature_window_prefix,
    _now,
    _read_json_from_s3,
    _s3_uri,
    _workspace_root,
    _write_directory_reference,
)
from shared.incident_taxonomy import NORMAL_ANOMALY_TYPE, canonical_anomaly_type

BUNDLE_CONTRACT_VERSION = "ani_feature_bundle_v1"
LABEL_TAXONOMY_VERSION = "ani_incident_taxonomy_v2"
DEFAULT_BUNDLE_WORKSPACE = "/tmp/ani-feature-bundle"
DEFAULT_CONTROL_PLANE_URL = "http://control-plane.ani-runtime.svc.cluster.local:8080"
DEFAULT_PROJECT = "ani-demo"
DEFAULT_APPROVAL_LIMIT = 1000
DEFAULT_AUDIT_LIMIT = 1000
FEATURE_ROW_COLUMNS = [
    "window_id",
    "event_timestamp",
    "created_timestamp",
    "dataset_version",
    "source_snapshot_id",
    "source",
    "feature_source",
    "scenario_name",
    "schema_version",
    "label",
    "anomaly_type",
    "transport",
    "call_limit",
    "rate",
    "incident_id",
    "approval_status",
    "rca_status",
    *FEATURES,
]
CONTEXT_ROW_COLUMNS = [
    "window_id",
    "event_timestamp",
    "created_timestamp",
    "dataset_version",
    "source_snapshot_id",
    "scenario_name",
    "source",
    "feature_source",
    "transport",
    "call_limit",
    "rate",
    "label_confidence",
    "contributing_conditions_json",
    "response_codes_json",
    "target",
    "scenario_file",
    "event_count",
    "auth_challenge_count",
    "return_code",
    "node_id",
    "node_role",
    "incident_id",
    "approval_status",
    "rca_status",
]
LABEL_ROW_COLUMNS = [
    "window_id",
    "event_timestamp",
    "created_timestamp",
    "dataset_version",
    "source_snapshot_id",
    "label",
    "anomaly_type",
    "label_confidence",
    "is_anomaly",
    "contributing_conditions_json",
    "incident_id",
    "approval_status",
    "approval_action",
    "approved_by",
    "approval_created_at",
    "rca_status",
]
INCIDENT_ROW_COLUMNS = [
    "incident_id",
    "window_id",
    "dataset_version",
    "project",
    "status",
    "approval_status",
    "approval_action",
    "approved_by",
    "approval_created_at",
    "rca_status",
    "anomaly_score",
    "anomaly_type",
    "model_version",
    "created_at",
    "updated_at",
    "contributing_conditions_json",
]
RCA_ROW_COLUMNS = [
    "incident_id",
    "window_id",
    "dataset_version",
    "project",
    "root_cause",
    "confidence",
    "recommendation",
    "generation_mode",
    "created_timestamp",
    "evidence_count",
    "retrieved_document_count",
    "evidence_json",
    "retrieved_documents_json",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-version", required=True)
    parser.add_argument("--source-dataset-version", action="append", dest="source_dataset_versions", required=True)
    parser.add_argument("--workspace-root", default=DEFAULT_BUNDLE_WORKSPACE)
    parser.add_argument("--source-snapshot-id")
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--approval-limit", type=int, default=DEFAULT_APPROVAL_LIMIT)
    parser.add_argument("--audit-limit", type=int, default=DEFAULT_AUDIT_LIMIT)
    parser.add_argument("--output")
    return parser.parse_args()


def _bundle_relative_root(bundle_version: str) -> str:
    return f"feature-bundles/{bundle_version}"


def _bundle_manifest_relative_path(bundle_version: str) -> str:
    return f"{_bundle_relative_root(bundle_version)}/manifest.json"


def _bundle_local_root(workspace_root: str, bundle_version: str) -> Path:
    return _workspace_root(workspace_root) / "feature-bundles" / bundle_version


def _bundle_root_uri(bundle_version: str, local_root: Path) -> str:
    if _data_store_mode() != "s3":
        return str(local_root)
    _ensure_dataset_bucket()
    bucket = _dataset_store_bucket()
    key = _dataset_object_key(_bundle_relative_root(bundle_version)).rstrip("/")
    return _s3_uri(bucket, key, is_directory=True)


def _artifact_uri(root_uri: str, relative_path: str) -> str:
    if root_uri.startswith("s3://"):
        return root_uri.rstrip("/") + "/" + relative_path.lstrip("/")
    return str(Path(root_uri) / relative_path)


def _control_plane_headers() -> dict[str, str]:
    api_key = os.getenv("CONTROL_PLANE_API_KEY", os.getenv("API_KEY", "")).strip()
    return {"x-api-key": api_key} if api_key else {}


def _control_plane_url(path: str) -> str:
    base = os.getenv("CONTROL_PLANE_URL", DEFAULT_CONTROL_PLANE_URL).rstrip("/")
    return f"{base}/{path.lstrip('/')}"


def _control_plane_get(path: str, params: dict[str, object] | None = None) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = requests.get(
                _control_plane_url(path),
                params=params,
                headers=_control_plane_headers(),
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


def _git_commit() -> str:
    for candidate in ("GIT_COMMIT", "SOURCE_GIT_COMMIT", "PIPELINE_GIT_COMMIT"):
        value = os.getenv(candidate, "").strip()
        if value:
            return value
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=REPO_ROOT,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            .strip()
        )
    except Exception:
        return "unknown"


def resolve_bundle_manifest(bundle_version: str, workspace_root: str = DEFAULT_BUNDLE_WORKSPACE) -> dict[str, str]:
    local_manifest = _bundle_local_root(workspace_root, bundle_version) / "manifest.json"
    if local_manifest.exists():
        return {
            "bundle_version": bundle_version,
            "bundle_manifest_path": str(local_manifest),
            "bundle_root": str(local_manifest.parent),
            "storage_mode": "filesystem",
        }

    bucket = _dataset_store_bucket()
    key = _dataset_object_key(_bundle_manifest_relative_path(bundle_version))
    uri = _s3_uri(bucket, key)
    try:
        _dataset_s3_client().head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        raise FileNotFoundError(f"Bundle manifest for {bundle_version} was not found at {uri}") from exc
    return {
        "bundle_version": bundle_version,
        "bundle_manifest_path": uri,
        "bundle_root": uri.rsplit("/", 1)[0],
        "storage_mode": "s3",
    }


def _iter_s3_windows(dataset_version: str) -> Iterable[dict[str, Any]]:
    bucket = _dataset_store_bucket()
    prefix = _dataset_object_key(_live_feature_window_prefix(dataset_version)).rstrip("/") + "/"
    paginator = _dataset_s3_client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            key = item["Key"]
            if not key.endswith(".json"):
                continue
            payload = _read_json_from_s3(_s3_uri(bucket, key))
            if isinstance(payload, dict):
                yield payload
            elif isinstance(payload, list):
                yield from (window for window in payload if isinstance(window, dict))


def _iter_filesystem_windows(dataset_version: str, workspace_root: str) -> Iterable[dict[str, Any]]:
    source_root = _filesystem_feature_window_root(dataset_version, workspace_root)
    if source_root is None:
        return
    for path in sorted(source_root.rglob("*.json")):
        payload = json.loads(path.read_text())
        if isinstance(payload, dict):
            yield payload
        elif isinstance(payload, list):
            yield from (window for window in payload if isinstance(window, dict))


def _filesystem_feature_window_root(dataset_version: str, workspace_root: str) -> Path | None:
    workspace = _workspace_root(workspace_root)
    candidate_roots = [
        workspace / "data" / "feature-windows" / dataset_version,
        workspace / "feature-windows" / dataset_version,
    ]
    for source_root in candidate_roots:
        if source_root.exists():
            return source_root
    return None


def _load_source_windows(dataset_version: str, workspace_root: str) -> list[dict[str, Any]]:
    if _data_store_mode() == "s3":
        return list(_iter_s3_windows(dataset_version))
    return list(_iter_filesystem_windows(dataset_version, workspace_root))


def _string_list(value: Any) -> str:
    if value is None:
        return "[]"
    if isinstance(value, list):
        return json.dumps(value)
    return json.dumps([value])


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _latest_by(records: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in sorted(records, key=lambda item: str(item.get("created_at") or item.get("updated_at") or "")):
        record_key = str(record.get(key) or "")
        if record_key:
            latest[record_key] = record
    return latest


def _load_control_plane_history(
    *,
    project: str,
    window_ids: set[str],
    approval_limit: int,
    audit_limit: int,
    require_control_plane_history: bool,
) -> dict[str, Any]:
    try:
        incidents_raw = _control_plane_get("/incidents", {"project": project})
        incidents = [
            item
            for item in incidents_raw
            if isinstance(item, dict) and str(item.get("feature_window_id") or "") in window_ids
        ]
        incident_ids = {str(item.get("id") or "") for item in incidents}

        approvals_raw = _control_plane_get("/approvals", {"limit": approval_limit})
        approvals = [
            item
            for item in approvals_raw
            if isinstance(item, dict) and str(item.get("incident_id") or "") in incident_ids
        ]

        audit_events_raw = _control_plane_get("/audit", {"limit": audit_limit})
        audit_events = [
            item
            for item in audit_events_raw
            if isinstance(item, dict) and str(item.get("incident_id") or "") in incident_ids
        ]
        return {
            "incidents": incidents,
            "approvals": approvals,
            "audit_events": audit_events,
            "source_status": {
                "control_plane": "ok",
                "incidents_url": _control_plane_url("/incidents"),
                "approvals_url": _control_plane_url("/approvals"),
                "audit_url": _control_plane_url("/audit"),
            },
        }
    except Exception as exc:
        if require_control_plane_history:
            raise
        return {
            "incidents": [],
            "approvals": [],
            "audit_events": [],
            "source_status": {
                "control_plane": "unavailable",
                "reason": str(exc),
                "incidents_url": _control_plane_url("/incidents"),
                "approvals_url": _control_plane_url("/approvals"),
                "audit_url": _control_plane_url("/audit"),
            },
        }


def _incident_rows(
    incidents: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    dataset_versions_by_window: dict[str, str],
) -> list[dict[str, Any]]:
    latest_approval_by_incident = _latest_by(approvals, "incident_id")
    rows: list[dict[str, Any]] = []
    for incident in sorted(incidents, key=lambda item: str(item.get("created_at") or "")):
        feature_snapshot = incident.get("feature_snapshot") if isinstance(incident.get("feature_snapshot"), dict) else {}
        rca_payload = incident.get("rca_payload") if isinstance(incident.get("rca_payload"), dict) else {}
        approval = latest_approval_by_incident.get(str(incident.get("id") or ""))
        window_id = str(incident.get("feature_window_id") or "")
        anomaly_type = canonical_anomaly_type(incident.get("anomaly_type"))
        rows.append(
            {
                "incident_id": str(incident.get("id") or ""),
                "window_id": window_id,
                "dataset_version": dataset_versions_by_window.get(window_id, ""),
                "project": str(incident.get("project") or DEFAULT_PROJECT),
                "status": str(incident.get("status") or "unknown"),
                "approval_status": str((approval or {}).get("status") or "none"),
                "approval_action": str((approval or {}).get("action") or ""),
                "approved_by": str((approval or {}).get("approved_by") or ""),
                "approval_created_at": str((approval or {}).get("created_at") or ""),
                "rca_status": "attached" if rca_payload else "none",
                "anomaly_score": _safe_float(incident.get("anomaly_score")),
                "anomaly_type": anomaly_type,
                "model_version": str(incident.get("model_version") or ""),
                "created_at": str(incident.get("created_at") or ""),
                "updated_at": str(incident.get("updated_at") or incident.get("created_at") or ""),
                "contributing_conditions_json": _string_list(feature_snapshot.get("contributing_conditions")),
            }
        )
    return rows


def _rca_rows(
    incidents: list[dict[str, Any]],
    audit_events: list[dict[str, Any]],
    dataset_versions_by_window: dict[str, str],
) -> list[dict[str, Any]]:
    latest_rca_event_by_incident: dict[str, dict[str, Any]] = {}
    for event in sorted(audit_events, key=lambda item: str(item.get("created_at") or "")):
        if str(event.get("event_type") or "") == "rca_attached":
            incident_id = str(event.get("incident_id") or "")
            if incident_id:
                latest_rca_event_by_incident[incident_id] = event

    rows: list[dict[str, Any]] = []
    for incident in sorted(incidents, key=lambda item: str(item.get("updated_at") or item.get("created_at") or "")):
        rca_payload = incident.get("rca_payload") if isinstance(incident.get("rca_payload"), dict) else {}
        if not rca_payload:
            continue
        incident_id = str(incident.get("id") or "")
        event = latest_rca_event_by_incident.get(incident_id, {})
        retrieved_documents = rca_payload.get("retrieved_documents") or []
        evidence = rca_payload.get("evidence") or []
        window_id = str(incident.get("feature_window_id") or "")
        rows.append(
            {
                "incident_id": incident_id,
                "window_id": window_id,
                "dataset_version": dataset_versions_by_window.get(window_id, ""),
                "project": str(incident.get("project") or DEFAULT_PROJECT),
                "root_cause": str(rca_payload.get("root_cause") or ""),
                "confidence": _safe_float(rca_payload.get("confidence")),
                "recommendation": str(rca_payload.get("recommendation") or ""),
                "generation_mode": str(rca_payload.get("generation_mode") or ""),
                "created_timestamp": str(event.get("created_at") or incident.get("updated_at") or incident.get("created_at") or ""),
                "evidence_count": len(evidence) if isinstance(evidence, list) else 0,
                "retrieved_document_count": len(retrieved_documents) if isinstance(retrieved_documents, list) else 0,
                "evidence_json": json.dumps(evidence if isinstance(evidence, list) else [], indent=2),
                "retrieved_documents_json": json.dumps(
                    retrieved_documents if isinstance(retrieved_documents, list) else [],
                    indent=2,
                ),
            }
        )
    return rows


def _incident_by_window(incidents: list[dict[str, Any]], approvals: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest_approval_by_incident = _latest_by(approvals, "incident_id")
    by_window: dict[str, dict[str, Any]] = {}
    for incident in sorted(incidents, key=lambda item: str(item.get("created_at") or "")):
        incident_id = str(incident.get("id") or "")
        approval = latest_approval_by_incident.get(incident_id, {})
        window_id = str(incident.get("feature_window_id") or "")
        if not window_id:
            continue
        payload = incident.get("rca_payload") if isinstance(incident.get("rca_payload"), dict) else {}
        by_window[window_id] = {
            "incident_id": incident_id,
            "incident_status": str(incident.get("status") or "unknown"),
            "approval_status": str(approval.get("status") or "none"),
            "approval_action": str(approval.get("action") or ""),
            "approved_by": str(approval.get("approved_by") or ""),
            "approval_created_at": str(approval.get("created_at") or ""),
            "rca_status": "attached" if payload else "none",
        }
    return by_window


def _window_feature_row(
    window: dict[str, Any],
    dataset_version: str,
    snapshot_id: str,
    incident_info: dict[str, Any],
) -> dict[str, Any]:
    features = window.get("features") or {}
    summary = window.get("sipp_summary") or {}
    event_timestamp = str(window.get("window_end") or window.get("captured_at") or _now())
    created_timestamp = str(window.get("captured_at") or event_timestamp)
    anomaly_type = canonical_anomaly_type(window.get("anomaly_type"))
    row = {
        "window_id": str(window.get("window_id") or ""),
        "event_timestamp": event_timestamp,
        "created_timestamp": created_timestamp,
        "dataset_version": dataset_version,
        "source_snapshot_id": snapshot_id,
        "source": str(window.get("source") or "openani-sipp-lab"),
        "feature_source": str(window.get("feature_source") or "sipp-shortmessages"),
        "scenario_name": str(window.get("scenario_name") or "unknown"),
        "schema_version": str(window.get("schema_version") or FEATURE_SCHEMA_VERSION),
        "label": 0 if anomaly_type == NORMAL_ANOMALY_TYPE else 1,
        "anomaly_type": anomaly_type,
        "transport": str(summary.get("transport") or ""),
        "call_limit": _safe_int(summary.get("call_limit")),
        "rate": _safe_float(summary.get("rate")),
        "incident_id": str(incident_info.get("incident_id") or ""),
        "approval_status": str(incident_info.get("approval_status") or "none"),
        "rca_status": str(incident_info.get("rca_status") or "none"),
    }
    for feature in FEATURES:
        row[feature] = _safe_float(features.get(feature))
    return row


def _window_context_row(
    window: dict[str, Any],
    dataset_version: str,
    snapshot_id: str,
    incident_info: dict[str, Any],
) -> dict[str, Any]:
    summary = window.get("sipp_summary") or {}
    return {
        "window_id": str(window.get("window_id") or ""),
        "event_timestamp": str(window.get("window_end") or window.get("captured_at") or _now()),
        "created_timestamp": str(window.get("captured_at") or window.get("window_end") or _now()),
        "dataset_version": dataset_version,
        "source_snapshot_id": snapshot_id,
        "scenario_name": str(window.get("scenario_name") or "unknown"),
        "source": str(window.get("source") or "openani-sipp-lab"),
        "feature_source": str(window.get("feature_source") or "sipp-shortmessages"),
        "transport": str(summary.get("transport") or ""),
        "call_limit": _safe_int(summary.get("call_limit")),
        "rate": _safe_float(summary.get("rate")),
        "label_confidence": _safe_float(window.get("label_confidence")),
        "contributing_conditions_json": _string_list(window.get("contributing_conditions")),
        "response_codes_json": _string_list(summary.get("response_codes")),
        "target": str(summary.get("target") or ""),
        "scenario_file": str(summary.get("scenario_file") or ""),
        "event_count": _safe_int(summary.get("event_count")),
        "auth_challenge_count": _safe_int(summary.get("auth_challenge_count")),
        "return_code": _safe_int(summary.get("return_code")),
        "node_id": str(window.get("node_id") or ""),
        "node_role": str(window.get("node_role") or ""),
        "incident_id": str(incident_info.get("incident_id") or ""),
        "approval_status": str(incident_info.get("approval_status") or "none"),
        "rca_status": str(incident_info.get("rca_status") or "none"),
    }


def _window_label_row(
    window: dict[str, Any],
    dataset_version: str,
    snapshot_id: str,
    incident_info: dict[str, Any],
) -> dict[str, Any]:
    labels = window.get("labels") or {}
    anomaly_type = canonical_anomaly_type(window.get("anomaly_type") or labels.get("anomaly_type"))
    return {
        "window_id": str(window.get("window_id") or ""),
        "event_timestamp": str(window.get("window_end") or window.get("captured_at") or _now()),
        "created_timestamp": str(window.get("captured_at") or window.get("window_end") or _now()),
        "dataset_version": dataset_version,
        "source_snapshot_id": snapshot_id,
        "label": 0 if anomaly_type == NORMAL_ANOMALY_TYPE else 1,
        "anomaly_type": anomaly_type,
        "label_confidence": _safe_float(window.get("label_confidence")),
        "is_anomaly": anomaly_type != NORMAL_ANOMALY_TYPE,
        "contributing_conditions_json": _string_list(
            labels.get("contributing_conditions") or window.get("contributing_conditions")
        ),
        "incident_id": str(incident_info.get("incident_id") or ""),
        "approval_status": str(incident_info.get("approval_status") or "none"),
        "approval_action": str(incident_info.get("approval_action") or ""),
        "approved_by": str(incident_info.get("approved_by") or ""),
        "approval_created_at": str(incident_info.get("approval_created_at") or ""),
        "rca_status": str(incident_info.get("rca_status") or "none"),
    }


def _quality_report(
    feature_rows: list[dict[str, Any]],
    incident_rows: list[dict[str, Any]],
    rca_rows: list[dict[str, Any]],
    source_dataset_versions: list[str],
    source_status: dict[str, Any],
) -> dict[str, Any]:
    label_counter = Counter(int(row["label"]) for row in feature_rows)
    anomaly_counter = Counter(str(row["anomaly_type"]) for row in feature_rows)
    scenario_counter = Counter(str(row["scenario_name"]) for row in feature_rows)
    approval_counter = Counter(str(row["approval_status"]) for row in incident_rows)
    return {
        "bundle_contract_version": BUNDLE_CONTRACT_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "label_taxonomy_version": LABEL_TAXONOMY_VERSION,
        "source_dataset_versions": source_dataset_versions,
        "generated_at": _now(),
        "row_count": len(feature_rows),
        "incident_count": len(incident_rows),
        "rca_count": len(rca_rows),
        "label_distribution": {str(key): value for key, value in sorted(label_counter.items())},
        "anomaly_type_distribution": dict(sorted(anomaly_counter.items())),
        "scenario_distribution": dict(sorted(scenario_counter.items())),
        "approval_status_distribution": dict(sorted(approval_counter.items())),
        "numeric_feature_columns": list(FEATURES),
        "source_status": source_status,
    }


def _coerce_timestamp_columns(frame: pd.DataFrame) -> pd.DataFrame:
    for column in ("event_timestamp", "created_timestamp", "created_at", "updated_at", "approval_created_at"):
        if column in frame.columns:
            frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
    return frame


def _dataset_card(
    *,
    bundle_version: str,
    snapshot_id: str,
    source_dataset_versions: list[str],
    quality: dict[str, Any],
    git_commit: str,
) -> str:
    anomaly_lines = "\n".join(
        f"- `{name}`: {count}" for name, count in quality["anomaly_type_distribution"].items()
    )
    approval_lines = "\n".join(
        f"- `{name}`: {count}" for name, count in quality["approval_status_distribution"].items()
    )
    return "\n".join(
        [
            f"# {bundle_version}",
            "",
            "## Summary",
            f"- snapshot: `{snapshot_id}`",
            f"- feature schema: `{FEATURE_SCHEMA_VERSION}`",
            f"- label taxonomy: `{LABEL_TAXONOMY_VERSION}`",
            f"- git commit: `{git_commit}`",
            f"- source datasets: {', '.join(f'`{value}`' for value in source_dataset_versions)}",
            f"- feature-window rows: `{quality['row_count']}`",
            f"- incident rows: `{quality['incident_count']}`",
            f"- RCA rows: `{quality['rca_count']}`",
            "",
            "## Numeric Features",
            *[f"- `{feature}`" for feature in FEATURES],
            "",
            "## Anomaly Distribution",
            anomaly_lines or "- none",
            "",
            "## Approval Distribution",
            approval_lines or "- none",
            "",
            "## Notes",
            "- this bundle snapshots feature windows, incidents, and RCA summaries for reproducible offline training",
            "- the serving contract remains numeric-first through `ani_anomaly_scoring_v1` while labels and RCA remain offline-only",
        ]
    )


def build_bundle(
    bundle_version: str,
    source_dataset_versions: list[str],
    workspace_root: str,
    source_snapshot_id: str | None = None,
    project: str = DEFAULT_PROJECT,
    approval_limit: int = DEFAULT_APPROVAL_LIMIT,
    audit_limit: int = DEFAULT_AUDIT_LIMIT,
    require_control_plane_history: bool = True,
) -> dict[str, Any]:
    snapshot_id = source_snapshot_id or f"snapshot-{bundle_version}"
    local_root = _bundle_local_root(workspace_root, bundle_version)
    parquet_root = local_root / "parquet"
    csv_root = local_root / "csv"
    feature_store_root = local_root / "feature_store"
    parquet_root.mkdir(parents=True, exist_ok=True)
    csv_root.mkdir(parents=True, exist_ok=True)
    feature_store_root.mkdir(parents=True, exist_ok=True)

    windows_by_dataset: dict[str, list[dict[str, Any]]] = {}
    dataset_versions_by_window: dict[str, str] = {}
    source_counts: dict[str, int] = {}
    for dataset_version in source_dataset_versions:
        windows = _load_source_windows(dataset_version, workspace_root)
        windows_by_dataset[dataset_version] = windows
        source_counts[f"{dataset_version}/feature_windows"] = len(windows)
        for window in windows:
            window_id = str(window.get("window_id") or "")
            if window_id:
                dataset_versions_by_window[window_id] = dataset_version

    control_plane_history = _load_control_plane_history(
        project=project,
        window_ids=set(dataset_versions_by_window),
        approval_limit=approval_limit,
        audit_limit=audit_limit,
        require_control_plane_history=require_control_plane_history,
    )
    incidents = control_plane_history["incidents"]
    approvals = control_plane_history["approvals"]
    audit_events = control_plane_history["audit_events"]
    source_counts["control_plane/incidents"] = len(incidents)
    source_counts["control_plane/approvals"] = len(approvals)
    source_counts["control_plane/audit_events"] = len(audit_events)

    incident_rows = _incident_rows(incidents, approvals, dataset_versions_by_window)
    rca_rows = _rca_rows(incidents, audit_events, dataset_versions_by_window)
    incident_by_window = _incident_by_window(incidents, approvals)

    feature_rows: list[dict[str, Any]] = []
    context_rows: list[dict[str, Any]] = []
    label_rows: list[dict[str, Any]] = []
    for dataset_version in source_dataset_versions:
        for window in windows_by_dataset.get(dataset_version, []):
            window_id = str(window.get("window_id") or "")
            incident_info = incident_by_window.get(window_id, {})
            feature_rows.append(
                _window_feature_row(window, dataset_version=dataset_version, snapshot_id=snapshot_id, incident_info=incident_info)
            )
            context_rows.append(
                _window_context_row(window, dataset_version=dataset_version, snapshot_id=snapshot_id, incident_info=incident_info)
            )
            label_rows.append(
                _window_label_row(window, dataset_version=dataset_version, snapshot_id=snapshot_id, incident_info=incident_info)
            )

    feature_rows.sort(key=lambda row: (row["event_timestamp"], row["window_id"]))
    context_rows.sort(key=lambda row: (row["event_timestamp"], row["window_id"]))
    label_rows.sort(key=lambda row: (row["event_timestamp"], row["window_id"]))
    incident_rows.sort(key=lambda row: (row["created_at"], row["incident_id"]))
    rca_rows.sort(key=lambda row: (row["created_timestamp"], row["incident_id"]))

    features_frame = _coerce_timestamp_columns(pd.DataFrame(feature_rows, columns=FEATURE_ROW_COLUMNS))
    context_frame = _coerce_timestamp_columns(pd.DataFrame(context_rows, columns=CONTEXT_ROW_COLUMNS))
    labels_frame = _coerce_timestamp_columns(pd.DataFrame(label_rows, columns=LABEL_ROW_COLUMNS))
    incidents_frame = _coerce_timestamp_columns(pd.DataFrame(incident_rows, columns=INCIDENT_ROW_COLUMNS))
    rca_frame = _coerce_timestamp_columns(pd.DataFrame(rca_rows, columns=RCA_ROW_COLUMNS))
    entity_frame = (
        features_frame[["window_id", "event_timestamp"]].copy()
        if not features_frame.empty
        else pd.DataFrame(columns=["window_id", "event_timestamp"])
    )
    entity_frame = _coerce_timestamp_columns(entity_frame)

    offline_source_frame = features_frame.copy()

    window_features_path = parquet_root / "window_features.parquet"
    window_context_path = parquet_root / "window_context.parquet"
    window_labels_path = parquet_root / "window_labels.parquet"
    incidents_path = parquet_root / "incidents.parquet"
    rca_summary_path = parquet_root / "rca_summary.parquet"
    offline_source_path = feature_store_root / "offline_source.parquet"
    entity_rows_path = feature_store_root / "entity_rows.parquet"
    window_features_csv_path = csv_root / "window_features.csv"
    window_context_csv_path = csv_root / "window_context.csv"
    window_labels_csv_path = csv_root / "window_labels.csv"
    incidents_csv_path = csv_root / "incidents.csv"
    rca_summary_csv_path = csv_root / "rca_summary.csv"
    quality_report_path = local_root / "quality_report.json"
    dataset_card_path = local_root / "dataset_card.md"
    manifest_path = local_root / "manifest.json"

    features_frame.to_parquet(window_features_path, index=False)
    context_frame.to_parquet(window_context_path, index=False)
    labels_frame.to_parquet(window_labels_path, index=False)
    incidents_frame.to_parquet(incidents_path, index=False)
    rca_frame.to_parquet(rca_summary_path, index=False)
    offline_source_frame.to_parquet(offline_source_path, index=False)
    entity_frame.to_parquet(entity_rows_path, index=False)
    features_frame.to_csv(window_features_csv_path, index=False)
    context_frame.to_csv(window_context_csv_path, index=False)
    labels_frame.to_csv(window_labels_csv_path, index=False)
    incidents_frame.to_csv(incidents_csv_path, index=False)
    rca_frame.to_csv(rca_summary_csv_path, index=False)

    quality = _quality_report(
        feature_rows,
        incident_rows,
        rca_rows,
        source_dataset_versions,
        control_plane_history["source_status"],
    )
    _json_dump(quality_report_path, quality)
    git_commit = _git_commit()
    dataset_card_path.write_text(
        _dataset_card(
            bundle_version=bundle_version,
            snapshot_id=snapshot_id,
            source_dataset_versions=source_dataset_versions,
            quality=quality,
            git_commit=git_commit,
        )
    )

    root_uri = _bundle_root_uri(bundle_version, local_root)
    validation = {
        "status": "passed",
        "required_tables": [
            "window_features_parquet",
            "window_context_parquet",
            "window_labels_parquet",
            "incidents_parquet",
            "rca_summary_parquet",
            "offline_source_parquet",
            "entity_rows_parquet",
        ],
        "control_plane_reachable": control_plane_history["source_status"].get("control_plane") == "ok",
        "warnings": [] if control_plane_history["source_status"].get("control_plane") == "ok" else [control_plane_history["source_status"].get("reason", "control-plane unavailable")],
    }
    manifest = {
        "bundle_version": bundle_version,
        "bundle_contract_version": BUNDLE_CONTRACT_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "label_taxonomy_version": LABEL_TAXONOMY_VERSION,
        "source_snapshot_id": snapshot_id,
        "source_dataset_versions": source_dataset_versions,
        "project": project,
        "generated_at": _now(),
        "git_commit": git_commit,
        "row_counts": {
            "window_features": len(feature_rows),
            "window_context": len(context_rows),
            "window_labels": len(label_rows),
            "incidents": len(incident_rows),
            "rca_summary": len(rca_rows),
            "entity_rows": len(entity_frame.index),
        },
        "source_counts": source_counts,
        "source_status": control_plane_history["source_status"],
        "source_uris": {
            "feature_windows": {
                dataset_version: _s3_uri(
                    _dataset_store_bucket(),
                    _dataset_object_key(_live_feature_window_prefix(dataset_version)),
                    is_directory=True,
                )
                if _data_store_mode() == "s3"
                else str(_filesystem_feature_window_root(dataset_version, workspace_root) or "")
                for dataset_version in source_dataset_versions
            },
            "control_plane": {
                "incidents": _control_plane_url("/incidents"),
                "approvals": _control_plane_url("/approvals"),
                "audit": _control_plane_url("/audit"),
            },
        },
        "validation": validation,
        "artifacts": {
            "bundle_root": root_uri,
            "manifest": _artifact_uri(root_uri, "manifest.json"),
            "dataset_card": _artifact_uri(root_uri, "dataset_card.md"),
            "quality_report": _artifact_uri(root_uri, "quality_report.json"),
            "tables": {
                "window_features_parquet": _artifact_uri(root_uri, "parquet/window_features.parquet"),
                "window_context_parquet": _artifact_uri(root_uri, "parquet/window_context.parquet"),
                "window_labels_parquet": _artifact_uri(root_uri, "parquet/window_labels.parquet"),
                "incidents_parquet": _artifact_uri(root_uri, "parquet/incidents.parquet"),
                "rca_summary_parquet": _artifact_uri(root_uri, "parquet/rca_summary.parquet"),
            },
            "csv": {
                "window_features_csv": _artifact_uri(root_uri, "csv/window_features.csv"),
                "window_context_csv": _artifact_uri(root_uri, "csv/window_context.csv"),
                "window_labels_csv": _artifact_uri(root_uri, "csv/window_labels.csv"),
                "incidents_csv": _artifact_uri(root_uri, "csv/incidents.csv"),
                "rca_summary_csv": _artifact_uri(root_uri, "csv/rca_summary.csv"),
            },
            "feature_store": {
                "offline_source_parquet": _artifact_uri(root_uri, "feature_store/offline_source.parquet"),
                "entity_rows_parquet": _artifact_uri(root_uri, "feature_store/entity_rows.parquet"),
            },
        },
        "quality_summary": quality,
    }
    _json_dump(manifest_path, manifest)

    _write_directory_reference(local_root, _bundle_relative_root(bundle_version))
    return manifest


def localize_bundle_manifest(bundle_manifest_path: str | Path, workspace_root: str) -> dict[str, Any]:
    if isinstance(bundle_manifest_path, Path):
        manifest = json.loads(bundle_manifest_path.read_text())
    elif str(bundle_manifest_path).startswith("s3://"):
        manifest = _read_json_from_s3(str(bundle_manifest_path))
    else:
        manifest = json.loads(Path(str(bundle_manifest_path)).read_text())

    bundle_version = str(manifest["bundle_version"])
    local_root = _bundle_local_root(workspace_root, bundle_version) / "localized"
    local_root.mkdir(parents=True, exist_ok=True)

    localized = json.loads(json.dumps(manifest))
    artifacts = localized.setdefault("localized_artifacts", {})
    artifacts["manifest"] = str(_download_file_reference(manifest["artifacts"]["manifest"], local_root / "manifest.json"))
    artifacts["dataset_card"] = str(
        _download_file_reference(manifest["artifacts"]["dataset_card"], local_root / "dataset_card.md")
    )
    artifacts["quality_report"] = str(
        _download_file_reference(manifest["artifacts"]["quality_report"], local_root / "quality_report.json")
    )
    artifacts["tables"] = {
        "window_features_parquet": str(
            _download_file_reference(
                manifest["artifacts"]["tables"]["window_features_parquet"],
                local_root / "parquet" / "window_features.parquet",
            )
        ),
        "window_context_parquet": str(
            _download_file_reference(
                manifest["artifacts"]["tables"]["window_context_parquet"],
                local_root / "parquet" / "window_context.parquet",
            )
        ),
        "window_labels_parquet": str(
            _download_file_reference(
                manifest["artifacts"]["tables"]["window_labels_parquet"],
                local_root / "parquet" / "window_labels.parquet",
            )
        ),
        "incidents_parquet": str(
            _download_file_reference(
                manifest["artifacts"]["tables"]["incidents_parquet"],
                local_root / "parquet" / "incidents.parquet",
            )
        ),
        "rca_summary_parquet": str(
            _download_file_reference(
                manifest["artifacts"]["tables"]["rca_summary_parquet"],
                local_root / "parquet" / "rca_summary.parquet",
            )
        ),
    }
    artifacts["csv"] = {
        "window_features_csv": str(
            _download_file_reference(
                manifest["artifacts"]["csv"]["window_features_csv"],
                local_root / "csv" / "window_features.csv",
            )
        ),
        "window_context_csv": str(
            _download_file_reference(
                manifest["artifacts"]["csv"]["window_context_csv"],
                local_root / "csv" / "window_context.csv",
            )
        ),
        "window_labels_csv": str(
            _download_file_reference(
                manifest["artifacts"]["csv"]["window_labels_csv"],
                local_root / "csv" / "window_labels.csv",
            )
        ),
        "incidents_csv": str(
            _download_file_reference(
                manifest["artifacts"]["csv"]["incidents_csv"],
                local_root / "csv" / "incidents.csv",
            )
        ),
        "rca_summary_csv": str(
            _download_file_reference(
                manifest["artifacts"]["csv"]["rca_summary_csv"],
                local_root / "csv" / "rca_summary.csv",
            )
        ),
    }
    artifacts["feature_store"] = {
        "offline_source_parquet": str(
            _download_file_reference(
                manifest["artifacts"]["feature_store"]["offline_source_parquet"],
                local_root / "feature_store" / "offline_source.parquet",
            )
        ),
        "entity_rows_parquet": str(
            _download_file_reference(
                manifest["artifacts"]["feature_store"]["entity_rows_parquet"],
                local_root / "feature_store" / "entity_rows.parquet",
            )
        ),
    }
    return localized


def main() -> None:
    args = _parse_args()
    manifest = build_bundle(
        bundle_version=args.bundle_version,
        source_dataset_versions=args.source_dataset_versions,
        workspace_root=args.workspace_root,
        source_snapshot_id=args.source_snapshot_id,
        project=args.project,
        approval_limit=args.approval_limit,
        audit_limit=args.audit_limit,
    )
    target = Path(args.output) if args.output else _bundle_local_root(args.workspace_root, args.bundle_version) / "manifest.json"
    _json_dump(target, manifest)
    print(target.read_text())


if __name__ == "__main__":
    main()
