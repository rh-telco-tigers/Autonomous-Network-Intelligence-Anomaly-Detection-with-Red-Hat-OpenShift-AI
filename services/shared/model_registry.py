import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def registry_path() -> Path:
    return Path(os.getenv("MODEL_REGISTRY_PATH", "/app/ai/registry/model_registry.json"))


def _default_registry() -> Dict[str, Any]:
    return {
        "feature_schema_version": "feature_schema_v1",
        "feature_schemas": [
            {
                "version": "feature_schema_v1",
                "status": "active",
                "created_at": _now(),
            }
        ],
        "dataset_version": None,
        "selected_model_version": None,
        "deployment_source_model_version": None,
        "datasets": [],
        "deployed_model_version": None,
        "promotion_gate": {
            "min_precision": 0.8,
            "max_false_positive_rate": 0.2,
            "max_latency_p95_ms": 50,
            "min_stability_score": 0.85,
            "status": "unknown",
        },
        "serving_artifact": "models/serving/predictive/ims-predictive/1/weights.json",
        "serving_repository": "models/serving/predictive",
        "serving_runtime": "nvidia-triton-runtime",
        "serving_model_name": "ims-predictive",
        "promotion_history": [],
        "models": [],
    }


def _normalize_registry(registry: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _default_registry() | registry
    normalized.setdefault("models", [])
    normalized.setdefault("datasets", [])
    normalized.setdefault("feature_schemas", [])
    normalized.setdefault("promotion_history", [])
    normalized.setdefault("promotion_gate", _default_registry()["promotion_gate"])

    if not normalized["feature_schemas"] and normalized.get("feature_schema_version"):
        normalized["feature_schemas"] = [
            {
                "version": normalized["feature_schema_version"],
                "status": "active",
                "created_at": _now(),
            }
        ]

    if not normalized["datasets"]:
        dataset_versions = sorted(
            {
                model.get("dataset_version")
                for model in normalized["models"]
                if model.get("dataset_version")
            }
        )
        normalized["datasets"] = [
            {
                "version": version,
                "feature_schema_version": normalized.get("feature_schema_version", "feature_schema_v1"),
                "status": "registered",
            }
            for version in dataset_versions
        ]

    if not normalized.get("deployed_model_version") and normalized["models"]:
        normalized["deployed_model_version"] = normalized["models"][0]["version"]

    return normalized


def load_registry() -> Dict[str, Any]:
    path = registry_path()
    if not path.exists():
        return _default_registry()
    return _normalize_registry(json.loads(path.read_text()))


def save_registry(registry: Dict[str, Any]) -> Dict[str, Any]:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_registry(registry)
    path.write_text(json.dumps(normalized, indent=2))
    return normalized


def list_models() -> List[Dict[str, Any]]:
    return load_registry().get("models", [])


def get_model(version: str) -> Dict[str, Any] | None:
    for model in list_models():
        if model.get("version") == version:
            return model
    return None


def list_datasets() -> List[Dict[str, Any]]:
    return load_registry().get("datasets", [])


def list_feature_schemas() -> List[Dict[str, Any]]:
    return load_registry().get("feature_schemas", [])


def gate_result(model: Dict[str, Any], gate: Dict[str, Any] | None = None) -> Dict[str, Any]:
    active_gate = gate or load_registry().get("promotion_gate", {})
    metrics = model.get("metrics", {})
    precision_ok = float(metrics.get("precision", 0.0)) >= float(active_gate.get("min_precision", 0.8))
    fpr_ok = float(metrics.get("false_positive_rate", 1.0)) <= float(active_gate.get("max_false_positive_rate", 0.2))
    latency_ok = float(metrics.get("latency_p95_ms", 10_000.0)) <= float(active_gate.get("max_latency_p95_ms", 50))
    stability_ok = float(metrics.get("stability_score", 0.0)) >= float(active_gate.get("min_stability_score", 0.85))
    status = "passed" if precision_ok and fpr_ok and latency_ok and stability_ok else "failed"
    return {
        "status": status,
        "precision_ok": precision_ok,
        "false_positive_rate_ok": fpr_ok,
        "latency_ok": latency_ok,
        "stability_ok": stability_ok,
        "gate": active_gate,
    }


def promote_model(version: str, actor: str, stage: str = "prod") -> Dict[str, Any]:
    registry = load_registry()
    model = get_model(version)
    if not model:
        raise ValueError(f"Model version {version} not found")

    gate = gate_result(model, registry.get("promotion_gate"))
    if gate["status"] != "passed":
        raise ValueError(f"Model version {version} does not satisfy the promotion gate")

    registry["selected_model_version"] = version
    registry["deployment_source_model_version"] = version
    runtime_version = next(
        (
            entry.get("version")
            for entry in registry.get("models", [])
            if entry.get("kind") in {"sklearn_logistic_regression", "triton_python_logistic_regression"}
        ),
        None,
    )
    registry["deployed_model_version"] = runtime_version or version
    registry.setdefault("promotion_history", []).append(
        {
            "version": version,
            "deployment_version": registry["deployed_model_version"],
            "stage": stage,
            "promoted_by": actor,
            "promoted_at": _now(),
        }
    )
    return save_registry(registry)
