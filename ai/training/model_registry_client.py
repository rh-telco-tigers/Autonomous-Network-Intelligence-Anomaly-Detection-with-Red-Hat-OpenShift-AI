"""Helpers for publishing feature-store model versions to a registry contract."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


DEFAULT_MODEL_REGISTRY_ENDPOINT = "https://model-catalog.rhoai-model-registries.svc.cluster.local:8443"
DEFAULT_MODEL_REGISTRY_CUSTOM_CA = "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt"


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _env_flag(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _pipeline_run_id() -> str:
    for candidate in (
        "PIPELINE_RUN_ID",
        "KFP_RUN_ID",
        "KFP_RUN_NAME",
        "WORKFLOW_NAME",
        "ARGO_WORKFLOW_NAME",
        "KFP_POD_NAME",
    ):
        value = os.getenv(candidate, "").strip()
        if value:
            return value
    return "unknown"


def build_model_registry_payload(
    *,
    model_name: str,
    model_version_name: str,
    artifact_uri: str,
    bundle_version: str,
    feature_schema_version: str,
    feature_service_name: str,
    model_format_name: str,
    model_format_version: str,
    pipeline_name: str,
    metrics: Dict[str, Any],
    deployment_readiness_status: str,
    pipeline_run_id: str | None = None,
    registry_endpoint: str | None = None,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "model_name": model_name,
        "model_version_name": model_version_name,
        "artifact_uri": artifact_uri,
        "model_format_name": model_format_name,
        "model_format_version": model_format_version,
        "bundle_version": bundle_version,
        "feature_schema_version": feature_schema_version,
        "feature_service_name": feature_service_name,
        "pipeline_name": pipeline_name,
        "pipeline_run_id": pipeline_run_id or _pipeline_run_id(),
        "deployment_readiness_status": deployment_readiness_status,
        "registry_endpoint": registry_endpoint or os.getenv("RHOAI_MODEL_REGISTRY_ENDPOINT", DEFAULT_MODEL_REGISTRY_ENDPOINT),
        "generated_at": _now(),
        "metrics": metrics,
        "metadata": metadata or {},
    }


def _read_default_token() -> str | None:
    explicit = os.getenv("RHOAI_MODEL_REGISTRY_TOKEN", "").strip()
    if explicit:
        return explicit
    token_path = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
    if token_path.exists():
        return token_path.read_text().strip()
    return None


def _read_default_custom_ca() -> str | None:
    explicit = os.getenv("RHOAI_MODEL_REGISTRY_CUSTOM_CA", "").strip()
    if explicit:
        return explicit
    default_ca = Path(DEFAULT_MODEL_REGISTRY_CUSTOM_CA)
    if default_ca.exists():
        return str(default_ca)
    return None


def _coerce_result(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    payload: Dict[str, Any] = {}
    for name in ("name", "id", "version", "uri", "description"):
        raw = getattr(value, name, None)
        if raw is not None:
            payload[name] = raw
    if not payload:
        payload["repr"] = repr(value)
    return payload


def _metadata_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return json.dumps(value, sort_keys=True)


def _try_register_with_kubeflow_hub(payload: Dict[str, Any]) -> Dict[str, Any]:
    endpoint = str(payload.get("registry_endpoint") or os.getenv("RHOAI_MODEL_REGISTRY_ENDPOINT", DEFAULT_MODEL_REGISTRY_ENDPOINT)).strip()
    if not endpoint:
        return {
            "status": "failed",
            "reason": "Model registry endpoint is not configured",
        }

    try:
        from kubeflow.hub import ModelRegistryClient
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "endpoint": endpoint,
            "reason": f"kubeflow hub client unavailable: {exc}",
        }

    token = _read_default_token()
    author = os.getenv("RHOAI_MODEL_REGISTRY_AUTHOR", "featurestore-pipeline").strip() or "featurestore-pipeline"
    custom_ca = _read_default_custom_ca()
    try:
        registry_metadata = {
            key: _metadata_value(value)
            for key, value in {
                **payload["metadata"],
                "bundle_version": payload["bundle_version"],
                "feature_schema_version": payload["feature_schema_version"],
                "feature_service_name": payload["feature_service_name"],
                "pipeline_name": payload["pipeline_name"],
                "pipeline_run_id": payload["pipeline_run_id"],
                "deployment_readiness_status": payload["deployment_readiness_status"],
                "metrics": payload["metrics"],
            }.items()
        }
        client = ModelRegistryClient(
            base_url=endpoint,
            author=author,
            user_token=token,
            custom_ca=custom_ca,
        )
        result = client.register_model(
            name=payload["model_name"],
            uri=payload["artifact_uri"],
            version=payload["model_version_name"],
            model_format_name=payload["model_format_name"],
            model_format_version=payload["model_format_version"],
            owner=os.getenv("RHOAI_MODEL_REGISTRY_OWNER", "ani-demo"),
            version_description=payload["metadata"].get("description", ""),
            metadata=registry_metadata,
        )
        return {
            "status": "registered",
            "endpoint": endpoint,
            "result": _coerce_result(result),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "endpoint": endpoint,
            "reason": str(exc),
        }


def publish_model_version(payload: Dict[str, Any], output_path: str | Path) -> Dict[str, Any]:
    registration_result = _try_register_with_kubeflow_hub(payload)
    output = {
        "registration_payload": payload,
        "registration_result": registration_result,
    }
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, indent=2))
    if _env_flag("RHOAI_MODEL_REGISTRY_REQUIRED", False) and registration_result["status"] != "registered":
        raise RuntimeError(
            f"Model registry registration failed for {payload['model_name']}:{payload['model_version_name']}: "
            f"{registration_result.get('reason', 'unknown error')}"
        )
    return output
