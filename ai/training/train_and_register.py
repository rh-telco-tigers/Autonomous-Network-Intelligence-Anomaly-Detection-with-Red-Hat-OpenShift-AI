import argparse
import json
import os
import random
import shutil
import sys
import tempfile
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

import boto3
from botocore.config import Config
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, confusion_matrix, log_loss, precision_recall_fscore_support
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from shared.incident_taxonomy import (
    NORMAL_ANOMALY_TYPE,
    canonical_anomaly_type,
    canonical_anomaly_types,
    normalize_scenario_name,
    scenario_definition,
)


FEATURES = [
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
FEATURE_SCHEMA_VERSION = "feature_schema_v1"
DEFAULT_DATASET_VERSION = "live-sipp-v1"
DEFAULT_MIN_REAL_WINDOWS = 9
DEFAULT_MAX_REAL_WINDOWS = 200
DEFAULT_MIN_WINDOWS_PER_CLASS = 3
TRITON_MODEL_NAME = "ims-predictive"
TRITON_MODEL_VERSION = "1"
PROMOTION_GATE = {
    "min_macro_f1": 0.65,
    "min_weighted_f1": 0.75,
    "min_balanced_accuracy": 0.65,
    "min_class_recall": 0.45,
    "max_normal_false_alarm_rate": 0.2,
    "max_multiclass_log_loss": 2.5,
    "max_latency_p95_ms": 50,
    "min_stability_score": 0.85,
}
DEFAULT_DATASET_STORE_ENDPOINT = "http://model-storage-minio.ims-data.svc.cluster.local:9000"
DEFAULT_DATASET_STORE_BUCKET = "ims-models"
DEFAULT_DATASET_STORE_PREFIX = "pipelines/ims-datascience/datasets"
_AUTOGLUON_PREDICTOR_CACHE: Dict[str, Any] = {}
CANONICAL_LABELS = canonical_anomaly_types()


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _json_dump(path: Path, payload: Dict[str, Any] | List[Dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return path


def _json_load(path: str | Path) -> Any:
    if isinstance(path, Path):
        return json.loads(path.read_text())
    raw = str(path)
    stripped = raw.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return json.loads(stripped)
    if raw.startswith("s3://"):
        return _read_json_from_s3(raw)
    return json.loads(Path(raw).read_text())


def _data_store_mode() -> str:
    explicit_mode = os.getenv("DATASET_STORE_MODE", "").strip().lower()
    if explicit_mode:
        return explicit_mode
    return "s3" if os.getenv("KFP_POD_NAME") else "filesystem"


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


def _dataset_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=_dataset_store_endpoint(),
        aws_access_key_id=_dataset_store_access_key(),
        aws_secret_access_key=_dataset_store_secret_key(),
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )


def _ensure_dataset_bucket() -> None:
    client = _dataset_s3_client()
    bucket = _dataset_store_bucket()
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)


def _dataset_object_key(relative_path: str) -> str:
    normalized_relative = relative_path.lstrip("/")
    prefix = _dataset_store_prefix()
    return f"{prefix}/{normalized_relative}" if prefix else normalized_relative


def _s3_uri(bucket: str, key: str, is_directory: bool = False) -> str:
    normalized_key = key.rstrip("/")
    if is_directory:
        normalized_key = f"{normalized_key}/"
    return f"s3://{bucket}/{normalized_key}"


def _parse_s3_uri(uri: str) -> Tuple[str, str]:
    stripped = uri.removeprefix("s3://")
    bucket, _, key = stripped.partition("/")
    return bucket, key


def _write_json_reference(payload: Dict[str, Any] | List[Dict[str, Any]], relative_path: str, local_fallback: Path) -> str:
    if _data_store_mode() != "s3":
        return str(_json_dump(local_fallback, payload))

    _ensure_dataset_bucket()
    bucket = _dataset_store_bucket()
    key = _dataset_object_key(relative_path)
    _dataset_s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    return _s3_uri(bucket, key)


def _read_json_from_s3(uri: str) -> Any:
    bucket, key = _parse_s3_uri(uri)
    response = _dataset_s3_client().get_object(Bucket=bucket, Key=key)
    return json.loads(response["Body"].read().decode("utf-8"))


def _write_directory_reference(source_dir: Path, relative_prefix: str) -> str:
    if _data_store_mode() != "s3":
        return str(source_dir)

    _ensure_dataset_bucket()
    bucket = _dataset_store_bucket()
    prefix = _dataset_object_key(relative_prefix).rstrip("/")
    client = _dataset_s3_client()
    for file_path in source_dir.rglob("*"):
        if not file_path.is_file():
            continue
        relative_path = file_path.relative_to(source_dir).as_posix()
        client.upload_file(str(file_path), bucket, f"{prefix}/{relative_path}")
    return _s3_uri(bucket, prefix, is_directory=True)


def _download_file_reference(source: str | Path, target_path: Path) -> Path:
    source_text = str(source)
    if source_text.startswith("s3://"):
        bucket, key = _parse_s3_uri(source_text)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        _dataset_s3_client().download_file(bucket, key, str(target_path))
        return target_path

    source_path = Path(source_text)
    if source_path.resolve() == target_path.resolve():
        return source_path
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)
    return target_path


def _download_directory_reference(source: str | Path, target_dir: Path) -> Path:
    source_text = str(source)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    if source_text.startswith("s3://"):
        bucket, key = _parse_s3_uri(source_text)
        prefix = key.rstrip("/") + "/"
        paginator = _dataset_s3_client().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                object_key = item["Key"]
                if object_key.endswith("/"):
                    continue
                relative_path = object_key[len(prefix):]
                destination = target_dir / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                _dataset_s3_client().download_file(bucket, object_key, str(destination))
        return target_dir

    shutil.copytree(Path(source_text), target_dir, dirs_exist_ok=True)
    return target_dir


def _prepare_artifact_for_storage(artifact: Dict[str, Any], version: str) -> Dict[str, Any]:
    predictor_path = artifact.get("predictor_path")
    if predictor_path and _data_store_mode() == "s3":
        artifact = dict(artifact)
        artifact["predictor_uri"] = _write_directory_reference(Path(str(predictor_path)), f"artifacts/autogluon/{version}")
        artifact.pop("predictor_path", None)
    return artifact


def _workspace_root(path: str) -> Path:
    return Path(path)


def _raw_dataset_path(workspace_root: Path, dataset_version: str) -> Path:
    return workspace_root / "data" / "raw" / f"{dataset_version}.json"


def _feature_dataset_path(workspace_root: Path, dataset_version: str) -> Path:
    return workspace_root / "data" / "features" / f"{dataset_version}-{FEATURE_SCHEMA_VERSION}.json"


def _train_split_path(workspace_root: Path, dataset_version: str) -> Path:
    return workspace_root / "data" / "labeled" / f"{dataset_version}-train.json"


def _eval_split_path(workspace_root: Path, dataset_version: str) -> Path:
    return workspace_root / "data" / "labeled" / f"{dataset_version}-eval.json"


def _live_feature_window_prefix(dataset_version: str) -> str:
    return f"datasets/{dataset_version}/feature-windows"


def _coerce_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _percentile(values: List[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    sorted_values = sorted(float(value) for value in values)
    rank = (len(sorted_values) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = rank - lower
    return (sorted_values[lower] * (1.0 - weight)) + (sorted_values[upper] * weight)


def _normalize_anomaly_type(raw_value: Any, label: int) -> str:
    candidate = str(raw_value or "").strip()
    if not candidate:
        return NORMAL_ANOMALY_TYPE if label == 0 else "unknown"
    if candidate == "unknown" and label == 0:
        return NORMAL_ANOMALY_TYPE
    return canonical_anomaly_type(candidate)


def _derive_binary_label(anomaly_type: str) -> int:
    return 0 if canonical_anomaly_type(anomaly_type) == NORMAL_ANOMALY_TYPE else 1


def _canonical_record(features: Dict[str, float], anomaly_type: str) -> Dict[str, Any]:
    normalized_type = canonical_anomaly_type(anomaly_type)
    return {
        "features": {feature: float(features.get(feature, 0.0) or 0.0) for feature in FEATURES},
        "label": _derive_binary_label(normalized_type),
        "anomaly_type": normalized_type,
    }


def _count_by_anomaly_type(records: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {label: 0 for label in CANONICAL_LABELS}
    for record in records:
        counts[canonical_anomaly_type(record.get("anomaly_type"))] = counts.get(
            canonical_anomaly_type(record.get("anomaly_type")),
            0,
        ) + 1
    return counts


def _multiclass_training_guard(records: List[Dict[str, Any]], min_per_class: int) -> Dict[str, Any]:
    counts = _count_by_anomaly_type(records)
    missing = [label for label in CANONICAL_LABELS if counts.get(label, 0) < min_per_class]
    return {
        "class_counts": counts,
        "min_per_class": min_per_class,
        "all_classes_present": not missing,
        "missing_or_underfilled_classes": missing,
    }


def _softmax(logits: List[float]) -> List[float]:
    if not logits:
        return []
    max_logit = max(logits)
    exponents = [pow(2.718281828459045, logit - max_logit) for logit in logits]
    total = sum(exponents) or 1.0
    return [value / total for value in exponents]


def _top_class_predictions(probabilities: Dict[str, float], limit: int = 3) -> List[Dict[str, Any]]:
    ordered = sorted(
        ((canonical_anomaly_type(label), float(score)) for label, score in probabilities.items()),
        key=lambda item: (-item[1], item[0]),
    )
    return [
        {
            "anomaly_type": label,
            "probability": round(score, 6),
        }
        for label, score in ordered[:limit]
    ]


def _prediction_payload(probabilities: Dict[str, float]) -> Dict[str, Any]:
    normalized_probabilities = {
        label: round(float(probabilities.get(label, 0.0)), 6)
        for label in CANONICAL_LABELS
    }
    predicted_anomaly_type = max(
        normalized_probabilities.items(),
        key=lambda item: (item[1], -CANONICAL_LABELS.index(item[0])),
    )[0]
    predicted_confidence = float(normalized_probabilities.get(predicted_anomaly_type, 0.0))
    normal_probability = float(normalized_probabilities.get(NORMAL_ANOMALY_TYPE, 0.0))
    return {
        "predicted_anomaly_type": predicted_anomaly_type,
        "predicted_confidence": round(predicted_confidence, 6),
        "class_probabilities": normalized_probabilities,
        "top_classes": _top_class_predictions(normalized_probabilities),
        "anomaly_score": round(max(0.0, min(1.0, 1.0 - normal_probability)), 6),
        "is_anomaly": predicted_anomaly_type != NORMAL_ANOMALY_TYPE,
    }


def _linear_model_probabilities(sample: Dict[str, float], artifact: Dict[str, Any]) -> Dict[str, float]:
    mean_values = [float(value) for value in artifact.get("scaler_mean", [0.0 for _ in FEATURES])]
    scale_values = [max(float(value), 1e-9) for value in artifact.get("scaler_scale", [1.0 for _ in FEATURES])]
    coefficients = [
        [float(value) for value in row]
        for row in artifact.get("coefficients", [])
    ]
    intercepts = [float(value) for value in artifact.get("intercepts", [])]
    class_labels = [canonical_anomaly_type(label) for label in artifact.get("class_labels", CANONICAL_LABELS)]
    if not coefficients or not intercepts or len(class_labels) != len(coefficients):
        raise ValueError("Linear multiclass artifact is missing classifier weights")
    values = [float(sample.get(feature, 0.0) or 0.0) for feature in FEATURES]
    normalized = [(value - mean_value) / scale_value for value, mean_value, scale_value in zip(values, mean_values, scale_values)]
    logits = []
    for row, intercept in zip(coefficients, intercepts):
        logits.append(sum(weight * feature_value for weight, feature_value in zip(row, normalized)) + intercept)
    probabilities = _softmax(logits)
    return {label: probability for label, probability in zip(class_labels, probabilities)}


def _normalize_live_window(window: Dict[str, Any], dataset_version: str, index: int) -> Dict[str, Any]:
    features = window.get("features") or {}
    labels = window.get("labels") if isinstance(window.get("labels"), dict) else {}
    label = int(window.get("label", 1 if labels.get("anomaly") else 0))
    anomaly_type = _normalize_anomaly_type(
        window.get("anomaly_type") or labels.get("anomaly_type"),
        label,
    )
    return {
        "window_id": str(window.get("window_id") or f"{dataset_version}-{index}"),
        "schema_version": str(window.get("schema_version") or FEATURE_SCHEMA_VERSION),
        "features": {feature: _coerce_float(features.get(feature, 0.0)) for feature in FEATURES},
        "label": _derive_binary_label(anomaly_type),
        "anomaly_type": anomaly_type,
    }


def _load_live_feature_windows(dataset_version: str, workspace_root: str) -> List[Dict[str, Any]]:
    max_windows = max(int(os.getenv("IMS_MAX_REAL_WINDOWS", str(DEFAULT_MAX_REAL_WINDOWS))), 1)
    windows: List[Dict[str, Any]] = []

    try:
        if _data_store_mode() == "s3":
            bucket = _dataset_store_bucket()
            prefix = _dataset_object_key(_live_feature_window_prefix(dataset_version)).rstrip("/") + "/"
            paginator = _dataset_s3_client().get_paginator("list_objects_v2")
            discovered: List[Tuple[str, str]] = []
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for item in page.get("Contents", []):
                    key = item["Key"]
                    if key.endswith(".json"):
                        discovered.append((str(item.get("LastModified", "")), key))
            for _, key in sorted(discovered)[-max_windows:]:
                payload = _read_json_from_s3(_s3_uri(bucket, key))
                if isinstance(payload, dict):
                    windows.append(payload)
                elif isinstance(payload, list):
                    windows.extend(window for window in payload if isinstance(window, dict))
        else:
            local_dir = _workspace_root(workspace_root) / "data" / "feature-windows" / dataset_version
            if local_dir.exists():
                for path in sorted(local_dir.glob("*.json"))[-max_windows:]:
                    payload = json.loads(path.read_text())
                    if isinstance(payload, dict):
                        windows.append(payload)
                    elif isinstance(payload, list):
                        windows.extend(window for window in payload if isinstance(window, dict))
    except Exception:
        return []

    normalized = [
        _normalize_live_window(window, dataset_version=dataset_version, index=index)
        for index, window in enumerate(windows)
        if isinstance(window, dict) and isinstance(window.get("features"), dict)
    ]
    return normalized


TRITON_MODEL_TEMPLATE = """import json
from pathlib import Path

import numpy as np
import triton_python_backend_utils as pb_utils


class TritonPythonModel:
    def initialize(self, args):
        version_dir = Path(__file__).resolve().parent
        weights = json.loads((version_dir / "weights.json").read_text())
        self.mean = np.asarray(weights["scaler_mean"], dtype=np.float32)
        self.scale = np.asarray(weights["scaler_scale"], dtype=np.float32)
        self.coefficients = np.asarray(weights["coefficients"], dtype=np.float32)
        self.intercepts = np.asarray(weights["intercepts"], dtype=np.float32)
        self.class_labels = list(weights["class_labels"])
        self.normal_index = self.class_labels.index(weights["normal_class_label"])

    def execute(self, requests):
        responses = []
        safe_scale = np.where(self.scale == 0, 1.0, self.scale)
        for request in requests:
            values = pb_utils.get_input_tensor_by_name(request, "predict").as_numpy().astype(np.float32)
            if values.ndim == 1:
                values = values.reshape(1, -1)
            normalized = (values - self.mean) / safe_scale
            logits = normalized @ self.coefficients.T + self.intercepts
            logits = logits - np.max(logits, axis=1, keepdims=True)
            probabilities = np.exp(logits)
            probabilities = probabilities / np.sum(probabilities, axis=1, keepdims=True)
            anomaly_scores = 1.0 - probabilities[:, [self.normal_index]]
            responses.append(
                pb_utils.InferenceResponse(
                    output_tensors=[
                        pb_utils.Tensor("class_probabilities", probabilities.astype(np.float32)),
                        pb_utils.Tensor("anomaly_score", anomaly_scores.astype(np.float32)),
                    ]
                )
            )
        return responses
"""


TRITON_CONFIG_TEMPLATE = """name: "{model_name}"
backend: "python"
max_batch_size: 16
input [
  {{
    name: "predict"
    data_type: TYPE_FP32
    dims: [{feature_count}]
  }}
]
output [
  {{
    name: "class_probabilities"
    data_type: TYPE_FP32
    dims: [{class_count}]
  }},
  {{
    name: "anomaly_score"
    data_type: TYPE_FP32
    dims: [1]
  }}
]
instance_group [
  {{
    kind: KIND_CPU
    count: 1
  }}
]
version_policy: {{
  specific {{
    versions: [{model_version}]
  }}
}}
"""


def _jitter_feature(feature: str, value: float) -> float:
    spread = max(abs(value) * 0.12, 0.02)
    jittered = max(0.0, random.gauss(value, spread))
    if feature in {"error_4xx_ratio", "error_5xx_ratio"}:
        return round(max(0.0, min(jittered, 1.0)), 6)
    return round(jittered, 6)


def _synthetic_sample_for_label(anomaly_type: str) -> Dict[str, float]:
    definition = scenario_definition(anomaly_type)
    profiles = list(definition.get("event_profiles", []))
    method_counts = {"REGISTER": 0.0, "INVITE": 0.0, "BYE": 0.0}
    error_4xx_count = 0.0
    error_5xx_count = 0.0
    response_count = 0.0
    latency_samples: List[float] = []
    payload_extremes: List[float] = []
    retransmission_count = 0.0

    for profile in profiles:
        count = float(profile.get("count", 0.0) or 0.0)
        method = str(profile.get("method", "")).upper()
        if method in method_counts:
            method_counts[method] += count
        response_code = int(profile.get("response_code", 0) or 0)
        if not (response_code == 401 and method == "REGISTER"):
            response_count += count
            if 400 <= response_code < 500:
                error_4xx_count += count
            if response_code >= 500:
                error_5xx_count += count
        latency_ms = float(profile.get("latency_ms", 0.0) or 0.0)
        latency_step = float(profile.get("latency_step", 0.0) or 0.0)
        latency_samples.append(latency_ms + (latency_step * max(count * 0.95, 1.0)))
        payload_size = float(profile.get("payload_size", 0.0) or 0.0)
        payload_step = float(profile.get("payload_step", 0.0) or 0.0)
        payload_extremes.extend([payload_size, payload_size + (payload_step * max(count - 1.0, 0.0))])
        retransmission_every = float(profile.get("retransmission_every", 0.0) or 0.0)
        if retransmission_every > 0:
            retransmission_count += count / retransmission_every

    total_events = sum(method_counts.values()) or 1.0
    default_rate = max(float(definition.get("default_rate", 1.0) or 1.0), 0.25)
    duration_seconds = max(total_events / default_rate, 8.0)
    response_count = max(response_count, 1.0)
    payload_variance = max(payload_extremes) - min(payload_extremes) if payload_extremes else 0.0
    base_features = {
        "register_rate": method_counts["REGISTER"] / duration_seconds,
        "invite_rate": method_counts["INVITE"] / duration_seconds,
        "bye_rate": method_counts["BYE"] / duration_seconds,
        "error_4xx_ratio": error_4xx_count / response_count,
        "error_5xx_ratio": error_5xx_count / response_count,
        "latency_p95": max(latency_samples) if latency_samples else 0.0,
        "retransmission_count": retransmission_count,
        "inter_arrival_mean": duration_seconds / total_events,
        "payload_variance": payload_variance,
    }
    return {
        feature: _jitter_feature(feature, float(base_features.get(feature, 0.0)))
        for feature in FEATURES
    }


def generate_dataset(size_per_class: int = 120) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for _ in range(size_per_class):
        for anomaly_type in CANONICAL_LABELS:
            records.append(_canonical_record(_synthetic_sample_for_label(anomaly_type), anomaly_type))
    random.shuffle(records)
    return records


def split_dataset(records: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[str(record.get("anomaly_type", "unknown"))].append(record)

    train_records: List[Dict[str, Any]] = []
    eval_records: List[Dict[str, Any]] = []
    for group_records in grouped.values():
        shuffled = list(group_records)
        random.shuffle(shuffled)
        if len(shuffled) <= 1:
            train_records.extend(shuffled)
            continue
        cutoff = min(max(int(len(shuffled) * 0.7), 1), len(shuffled) - 1)
        train_records.extend(shuffled[:cutoff])
        eval_records.extend(shuffled[cutoff:])

    random.shuffle(train_records)
    random.shuffle(eval_records)
    return train_records, eval_records


def ingest_dataset(dataset_version: str, workspace_root: str, size_per_class: int = 120) -> Dict[str, Any]:
    workspace = _workspace_root(workspace_root)
    live_windows = _load_live_feature_windows(dataset_version, workspace_root)
    min_live_windows = max(int(os.getenv("IMS_MIN_REAL_WINDOWS", str(DEFAULT_MIN_REAL_WINDOWS))), 1)
    min_per_class = max(int(os.getenv("IMS_MIN_WINDOWS_PER_CLASS", str(DEFAULT_MIN_WINDOWS_PER_CLASS))), 1)
    allow_bootstrap = os.getenv("IMS_ALLOW_BOOTSTRAP_DATASET", "true").strip().lower() in {"1", "true", "yes", "on"}
    live_guard = _multiclass_training_guard(live_windows, min_per_class)
    minimum_live_records = max(min_live_windows, len(CANONICAL_LABELS) * min_per_class)

    if len(live_windows) >= minimum_live_records and live_guard["all_classes_present"]:
        records_path = _write_json_reference(
            live_windows,
            f"datasets/{dataset_version}/features/{dataset_version}-{FEATURE_SCHEMA_VERSION}.json",
            _feature_dataset_path(workspace, dataset_version),
        )
        return {
            "dataset_version": dataset_version,
            "dataset_path": records_path,
            "dataset_kind": "feature_windows",
            "record_count": len(live_windows),
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "created_at": _now(),
            "source": "openims-sipp-lab",
            "labels": sorted({canonical_anomaly_type(record["anomaly_type"]) for record in live_windows}),
            "class_counts": live_guard["class_counts"],
            "label_taxonomy_version": "ims_incident_taxonomy_v2",
        }

    if not allow_bootstrap:
        raise ValueError(
            "Live multiclass dataset does not meet minimum class coverage. "
            f"Required at least {min_per_class} record(s) for each class; "
            f"missing or underfilled classes: {live_guard['missing_or_underfilled_classes']}"
        )

    records = generate_dataset(size_per_class=size_per_class)
    records_path = _write_json_reference(records, f"datasets/{dataset_version}/raw/records.json", _raw_dataset_path(workspace, dataset_version))
    manifest = {
        "dataset_version": dataset_version,
        "dataset_path": records_path,
        "dataset_kind": "raw_records",
        "record_count": len(records),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "created_at": _now(),
        "source": "synthetic-ims-lab-multiclass",
        "labels": sorted({record["anomaly_type"] for record in records}),
        "live_record_count": len(live_windows),
        "live_class_counts": live_guard["class_counts"],
        "label_taxonomy_version": "ims_incident_taxonomy_v2",
    }
    return manifest


def materialize_feature_windows(dataset_manifest_path: str, workspace_root: str) -> Dict[str, Any]:
    dataset_manifest = _json_load(dataset_manifest_path)
    records = _json_load(dataset_manifest["dataset_path"])
    workspace = _workspace_root(workspace_root)
    windows = []
    if dataset_manifest.get("dataset_kind") == "feature_windows":
        for index, window in enumerate(records):
            windows.append(_normalize_live_window(window, dataset_version=dataset_manifest["dataset_version"], index=index))
    else:
        for index, record in enumerate(records):
            anomaly_type = canonical_anomaly_type(record["anomaly_type"])
            windows.append(
                {
                    "window_id": f"{dataset_manifest['dataset_version']}-{index}",
                    "schema_version": FEATURE_SCHEMA_VERSION,
                    "features": record["features"],
                    "label": _derive_binary_label(anomaly_type),
                    "anomaly_type": anomaly_type,
                }
            )

    features_path = _write_json_reference(
        windows,
        f"datasets/{dataset_manifest['dataset_version']}/features/{dataset_manifest['dataset_version']}-{FEATURE_SCHEMA_VERSION}.json",
        _feature_dataset_path(workspace, dataset_manifest["dataset_version"]),
    )
    return {
        "dataset_version": dataset_manifest["dataset_version"],
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_windows_path": features_path,
        "window_count": len(windows),
        "created_at": _now(),
    }


def generate_labels(feature_manifest_path: str, workspace_root: str) -> Dict[str, Any]:
    feature_manifest = _json_load(feature_manifest_path)
    windows = _json_load(feature_manifest["feature_windows_path"])
    records = [
        {
            "features": window["features"],
            "label": window["label"],
            "anomaly_type": window["anomaly_type"],
        }
        for window in windows
    ]
    train_records, eval_records = split_dataset(records)
    workspace = _workspace_root(workspace_root)
    train_path = _write_json_reference(
        train_records,
        f"datasets/{feature_manifest['dataset_version']}/labeled/train.json",
        _train_split_path(workspace, feature_manifest["dataset_version"]),
    )
    eval_path = _write_json_reference(
        eval_records,
        f"datasets/{feature_manifest['dataset_version']}/labeled/eval.json",
        _eval_split_path(workspace, feature_manifest["dataset_version"]),
    )
    return {
        "dataset_version": feature_manifest["dataset_version"],
        "feature_schema_version": feature_manifest["feature_schema_version"],
        "label_taxonomy_version": "ims_incident_taxonomy_v2",
        "train_path": train_path,
        "eval_path": eval_path,
        "train_count": len(train_records),
        "eval_count": len(eval_records),
        "train_class_counts": _count_by_anomaly_type(train_records),
        "eval_class_counts": _count_by_anomaly_type(eval_records),
        "created_at": _now(),
    }


def load_records(path: str) -> List[Dict[str, Any]]:
    return _json_load(path)


def _linear_artifact_from_model(model: Pipeline, model_type: str) -> Dict[str, Any]:
    scaler = model.named_steps["scaler"]
    classifier = model.named_steps["classifier"]
    return {
        "model_type": model_type,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_names": FEATURES,
        "class_labels": [canonical_anomaly_type(label) for label in classifier.classes_.tolist()],
        "normal_class_label": NORMAL_ANOMALY_TYPE,
        "scaler_mean": [round(float(value), 10) for value in scaler.mean_.tolist()],
        "scaler_scale": [round(float(value), 10) for value in scaler.scale_.tolist()],
        "coefficients": [
            [round(float(weight), 10) for weight in row]
            for row in classifier.coef_.tolist()
        ],
        "intercepts": [round(float(value), 10) for value in classifier.intercept_.tolist()],
        "anomaly_score_strategy": "1-minus-normal_probability",
    }


def train_baseline(train_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    features, labels = vectorize(train_records)
    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(
                    max_iter=2_000,
                    multi_class="multinomial",
                    class_weight="balanced",
                    random_state=7,
                ),
            ),
        ]
    )
    model.fit(features, labels)
    artifact = _linear_artifact_from_model(model, "baseline_multiclass_logistic_regression")
    artifact["baseline_family"] = "multinomial_logistic_regression"
    return artifact


def score_baseline(sample: Dict[str, float], artifact: Dict[str, Any]) -> Dict[str, Any]:
    return _prediction_payload(_linear_model_probabilities(sample, artifact))


def train_autogluon_candidate(
    train_records: List[Dict[str, Any]],
    workspace_root: str,
    version: str,
    automl_engine: str = "autogluon",
) -> Dict[str, Any]:
    if automl_engine != "autogluon":
        raise ValueError(f"Unsupported automl engine {automl_engine}; expected autogluon")

    import pandas as pd
    from autogluon.tabular import TabularPredictor

    workspace = _workspace_root(workspace_root)
    # AutoGluon setup_outputdir uses makedirs(..., exist_ok=False): path must not exist.
    # Clear any leftover dir from retries / shared workspace before TabularPredictor.
    ag_root = workspace / "models" / "autogluon" / version
    ag_root.mkdir(parents=True, exist_ok=True)
    predictor_dir: Path | None = None
    for _ in range(8):
        candidate = ag_root / uuid.uuid4().hex
        if candidate.exists():
            shutil.rmtree(candidate, ignore_errors=True)
        if not candidate.exists():
            predictor_dir = candidate
            break
        time.sleep(0.05)
    if predictor_dir is None:
        raise RuntimeError("Could not allocate an empty AutoGluon output directory")
    preset = os.environ.get("IMS_AUTOGLUON_PRESET", "medium_quality").strip() or "medium_quality"
    time_limit = int(os.environ.get("IMS_AUTOGLUON_TIME_LIMIT", "180"))
    for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(k, "1")
    rows = []
    for record in train_records:
        row = {feature: float(record["features"][feature]) for feature in FEATURES}
        row["anomaly_type"] = canonical_anomaly_type(record["anomaly_type"])
        rows.append(row)
    train_frame = pd.DataFrame(rows)
    predictor = TabularPredictor(
        label="anomaly_type",
        path=str(predictor_dir),
        problem_type="multiclass",
        eval_metric="f1_macro",
    ).fit(
        train_data=train_frame,
        presets=preset,
        time_limit=time_limit,
        verbosity=0,
    )
    leaderboard = predictor.leaderboard(train_frame, silent=True).to_dict("records")
    class_labels = list(getattr(predictor, "class_labels", []) or [])
    if not class_labels:
        class_labels = list(
            predictor.predict_proba(train_frame[FEATURES].iloc[:1], as_multiclass=True).columns
        )
    return {
        "model_type": "autogluon_tabular_multiclass",
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "automl_engine": "autogluon",
        "predictor_path": str(predictor_dir),
        "best_model": predictor.model_best,
        "leaderboard": leaderboard[:5],
        "class_labels": [canonical_anomaly_type(label) for label in class_labels],
        "normal_class_label": NORMAL_ANOMALY_TYPE,
        "anomaly_score_strategy": "1-minus-normal_probability",
    }


def score_autogluon(sample: Dict[str, float], artifact: Dict[str, Any]) -> Dict[str, Any]:
    import pandas as pd
    from autogluon.tabular import TabularPredictor

    predictor_uri = str(artifact.get("predictor_uri") or "").strip()
    predictor_path = str(artifact.get("predictor_path") or "").strip()
    predictor_source = predictor_uri or predictor_path
    if not predictor_source:
        raise ValueError("AutoGluon artifact is missing predictor_path or predictor_uri")
    predictor = _AUTOGLUON_PREDICTOR_CACHE.get(predictor_source)
    if predictor is None:
        if predictor_uri:
            predictor_dir = _download_directory_reference(
                predictor_source,
                Path(tempfile.gettempdir()) / "ims-autogluon-cache" / artifact.get("best_model", "predictor"),
            )
        else:
            predictor_dir = Path(predictor_source)
        predictor = TabularPredictor.load(str(predictor_dir))
        _AUTOGLUON_PREDICTOR_CACHE[predictor_source] = predictor
    frame = pd.DataFrame([{feature: float(sample[feature]) for feature in FEATURES}])
    probabilities = predictor.predict_proba(frame, as_multiclass=True)
    probability_map = {
        canonical_anomaly_type(str(label)): float(probabilities.iloc[0][label])
        for label in probabilities.columns
    }
    return _prediction_payload(probability_map)


def evaluate(
    records: List[Dict[str, Any]],
    artifact: Dict[str, Any],
    scorer: Callable[[Dict[str, float], Dict[str, Any]], Dict[str, Any]],
) -> Dict[str, Any]:
    labels = list(artifact.get("class_labels") or CANONICAL_LABELS)
    y_true: List[str] = []
    y_pred: List[str] = []
    probability_vectors: List[List[float]] = []
    confidences: List[float] = []
    latency_samples_ms: List[float] = []

    for record in records:
        started = time.perf_counter()
        prediction = scorer(record["features"], artifact)
        latency_samples_ms.append((time.perf_counter() - started) * 1000.0)
        actual = canonical_anomaly_type(record["anomaly_type"])
        predicted = canonical_anomaly_type(prediction["predicted_anomaly_type"])
        vector = [float(prediction["class_probabilities"].get(label, 0.0)) for label in labels]
        total_probability = sum(vector) or 1.0
        y_true.append(actual)
        y_pred.append(predicted)
        probability_vectors.append([value / total_probability for value in vector])
        confidences.append(float(prediction.get("predicted_confidence", 0.0)))

    macro_precision, macro_recall, macro_f1, _ = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=labels,
        average="macro",
        zero_division=0,
    )
    _, _, weighted_f1, _ = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=labels,
        average="weighted",
        zero_division=0,
    )
    per_class_precision, per_class_recall, _, support = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=labels,
        average=None,
        zero_division=0,
    )
    matrix = confusion_matrix(y_true, y_pred, labels=labels)
    normal_total = sum(1 for label in y_true if label == NORMAL_ANOMALY_TYPE)
    normal_false_alarm_rate = (
        sum(1 for actual, predicted in zip(y_true, y_pred) if actual == NORMAL_ANOMALY_TYPE and predicted != NORMAL_ANOMALY_TYPE)
        / max(normal_total, 1)
    )
    class_recall_values = [float(value) for value in per_class_recall.tolist()] if len(per_class_recall) else [0.0]
    stability_score = max(0.0, 1.0 - (max(class_recall_values) - min(class_recall_values)))
    calibration_summary = {
        "multiclass_log_loss": round(float(log_loss(y_true, probability_vectors, labels=labels)), 4),
        "average_predicted_confidence": round(sum(confidences) / max(len(confidences), 1), 4),
    }
    return {
        "macro_precision": round(float(macro_precision), 4),
        "macro_recall": round(float(macro_recall), 4),
        "macro_f1": round(float(macro_f1), 4),
        "weighted_f1": round(float(weighted_f1), 4),
        "balanced_accuracy": round(float(balanced_accuracy_score(y_true, y_pred)), 4),
        "normal_false_alarm_rate": round(float(normal_false_alarm_rate), 4),
        "per_class_precision": {
            label: round(float(value), 4)
            for label, value in zip(labels, per_class_precision.tolist())
        },
        "per_class_recall": {
            label: round(float(value), 4)
            for label, value in zip(labels, per_class_recall.tolist())
        },
        "per_class_support": {
            label: int(value)
            for label, value in zip(labels, support.tolist())
        },
        "confusion_matrix": {
            "labels": labels,
            "matrix": matrix.tolist(),
        },
        "calibration": calibration_summary,
        "latency_p95_ms": round(float(_percentile(latency_samples_ms, 0.95)), 4),
        "stability_score": round(float(stability_score), 4),
    }


def gate_metrics(metrics: Dict[str, Any], gate: Dict[str, Any] | None = None) -> Dict[str, Any]:
    active_gate = gate or PROMOTION_GATE
    per_class_recall = {
        canonical_anomaly_type(label): float(value)
        for label, value in dict(metrics.get("per_class_recall", {})).items()
    }
    min_class_recall = min(per_class_recall.values()) if per_class_recall else 0.0
    macro_f1_ok = float(metrics.get("macro_f1", 0.0)) >= float(active_gate["min_macro_f1"])
    weighted_f1_ok = float(metrics.get("weighted_f1", 0.0)) >= float(active_gate["min_weighted_f1"])
    balanced_accuracy_ok = float(metrics.get("balanced_accuracy", 0.0)) >= float(active_gate["min_balanced_accuracy"])
    class_recall_ok = min_class_recall >= float(active_gate["min_class_recall"])
    normal_fpr_ok = float(metrics.get("normal_false_alarm_rate", 1.0)) <= float(active_gate["max_normal_false_alarm_rate"])
    log_loss_ok = float(dict(metrics.get("calibration", {})).get("multiclass_log_loss", 10_000.0)) <= float(
        active_gate["max_multiclass_log_loss"]
    )
    latency_ok = float(metrics.get("latency_p95_ms", 10_000.0)) <= float(active_gate["max_latency_p95_ms"])
    stability_ok = float(metrics.get("stability_score", 0.0)) >= float(active_gate["min_stability_score"])
    status = "passed" if all([macro_f1_ok, weighted_f1_ok, balanced_accuracy_ok, class_recall_ok, normal_fpr_ok, log_loss_ok, latency_ok, stability_ok]) else "failed"
    return {
        "status": status,
        "macro_f1_ok": macro_f1_ok,
        "weighted_f1_ok": weighted_f1_ok,
        "balanced_accuracy_ok": balanced_accuracy_ok,
        "class_recall_ok": class_recall_ok,
        "normal_false_alarm_rate_ok": normal_fpr_ok,
        "multiclass_log_loss_ok": log_loss_ok,
        "latency_ok": latency_ok,
        "stability_ok": stability_ok,
        "minimum_observed_class_recall": round(float(min_class_recall), 4),
        "gate": active_gate,
    }


def vectorize(records: List[Dict[str, Any]]) -> Tuple[List[List[float]], List[str]]:
    features = [[record["features"][feature] for feature in FEATURES] for record in records]
    labels = [canonical_anomaly_type(record["anomaly_type"]) for record in records]
    return features, labels


def train_serving_model(train_records: List[Dict[str, Any]]) -> Pipeline:
    features, labels = vectorize(train_records)
    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(
                    max_iter=2_000,
                    multi_class="multinomial",
                    class_weight="balanced",
                    random_state=7,
                ),
            ),
        ]
    )
    model.fit(features, labels)
    return model


def export_triton_repository(serving_root: Path, model: Pipeline, source_model_version: str) -> Dict[str, Path]:
    legacy_artifact = serving_root / "model.joblib"
    if legacy_artifact.exists():
        legacy_artifact.unlink()
    repository_root = serving_root / TRITON_MODEL_NAME
    if repository_root.exists():
        shutil.rmtree(repository_root)

    version_root = repository_root / TRITON_MODEL_VERSION
    version_root.mkdir(parents=True, exist_ok=True)

    scaler = model.named_steps["scaler"]
    classifier = model.named_steps["classifier"]
    weights_path = version_root / "weights.json"
    _json_dump(
        weights_path,
        {
            "model_type": "triton_python_multiclass_logistic_regression",
            "source_model_version": source_model_version,
            **_linear_artifact_from_model(model, "triton_python_multiclass_logistic_regression"),
        },
    )
    (version_root / "model.py").write_text(TRITON_MODEL_TEMPLATE)
    (repository_root / "config.pbtxt").write_text(
        TRITON_CONFIG_TEMPLATE.format(
            model_name=TRITON_MODEL_NAME,
            feature_count=len(FEATURES),
            class_count=len(classifier.classes_),
            model_version=TRITON_MODEL_VERSION,
        )
    )
    return {
        "repository_root": repository_root,
        "version_root": version_root,
        "weights_path": weights_path,
        "model_script_path": version_root / "model.py",
        "config_path": repository_root / "config.pbtxt",
    }


def evaluate_serving_model(records: List[Dict[str, Any]], model: Pipeline) -> Dict[str, Any]:
    return evaluate(
        records,
        _linear_artifact_from_model(model, "triton_python_multiclass_logistic_regression"),
        score_baseline,
    )


def scorer_for_artifact(artifact: Dict[str, Any]) -> Callable[[Dict[str, float], Dict[str, Any]], Dict[str, Any]]:
    model_type = artifact.get("model_type")
    if model_type in {
        "baseline_multiclass_logistic_regression",
        "triton_python_multiclass_logistic_regression",
    }:
        return score_baseline
    if model_type == "autogluon_tabular_multiclass":
        return score_autogluon
    raise ValueError(f"Unsupported model type {model_type}")


def persist_model_artifact(artifact_dir: str, version: str, artifact: Dict[str, Any]) -> str:
    path = Path(artifact_dir) / f"{version}.json"
    return _write_json_reference(artifact, f"artifacts/models/{version}.json", path)


def select_best_model(evaluation: Dict[str, Any]) -> Dict[str, Any]:
    baseline = evaluation["baseline"]
    candidate = evaluation["candidate"]
    candidate_gate = gate_metrics(candidate["metrics"], evaluation.get("promotion_gate"))
    selected = baseline
    reason = "candidate failed evaluation gate"

    if candidate_gate["status"] == "passed" and (
        float(candidate["metrics"]["macro_f1"]) > float(baseline["metrics"]["macro_f1"])
        or (
            float(candidate["metrics"]["macro_f1"]) == float(baseline["metrics"]["macro_f1"])
            and float(candidate["metrics"]["weighted_f1"]) >= float(baseline["metrics"]["weighted_f1"])
        )
    ):
        selected = candidate
        reason = "candidate satisfied gate and outperformed baseline"
    elif float(baseline["metrics"]["macro_f1"]) >= float(candidate["metrics"]["macro_f1"]):
        reason = "baseline retained due to better or equal macro-F1 score"

    return {
        "dataset_version": evaluation["dataset_version"],
        "feature_schema_version": evaluation["feature_schema_version"],
        "label_manifest": evaluation["label_manifest"],
        "label_taxonomy_version": "ims_incident_taxonomy_v2",
        "promotion_gate": evaluation["promotion_gate"],
        "candidate_gate_result": candidate_gate,
        "baseline": baseline,
        "candidate": candidate,
        "selected_model_version": selected["version"],
        "selected_model_type": selected["model_type"],
        "selected_artifact_path": selected["artifact_path"],
        "selection_reason": reason,
        "selected_training_mode": "multiclass_supervised",
        "candidate_deployment_ready": candidate_gate["status"] == "passed",
    }


def build_registry(
    dataset_version: str,
    baseline_version: str,
    candidate_version: str,
    baseline_artifact: Dict[str, Any],
    candidate_artifact: Dict[str, Any],
    baseline_metrics: Dict[str, Any],
    candidate_metrics: Dict[str, Any],
    serving_metrics: Dict[str, Any],
    selected_version: str,
) -> Dict[str, Any]:
    gate = gate_metrics(candidate_metrics, PROMOTION_GATE)
    deployed_runtime_version = "predictive-serving-v1"
    return {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "label_taxonomy_version": "ims_incident_taxonomy_v2",
        "class_labels": CANONICAL_LABELS,
        "normal_class_label": NORMAL_ANOMALY_TYPE,
        "feature_schemas": [
            {
                "version": FEATURE_SCHEMA_VERSION,
                "status": "active",
                "created_at": _now(),
            }
        ],
        "dataset_version": dataset_version,
        "selected_model_version": selected_version,
        "deployment_source_model_version": selected_version,
        "deployed_model_version": deployed_runtime_version,
        "datasets": [
            {
                "version": dataset_version,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "record_source": "synthetic-ims-lab-multiclass",
                "status": "registered",
                "created_at": _now(),
            }
        ],
        "promotion_gate": {
            **PROMOTION_GATE,
            "status": gate["status"],
        },
        "promotion_history": [
            {
                "version": selected_version,
                "deployment_version": deployed_runtime_version,
                "stage": "prod",
                "promoted_by": "pipeline",
                "promoted_at": _now(),
            }
        ],
        "serving_artifact": f"models/serving/predictive/{TRITON_MODEL_NAME}/{TRITON_MODEL_VERSION}/weights.json",
        "serving_repository": "models/serving/predictive",
        "serving_runtime": "nvidia-triton-runtime",
        "serving_model_name": TRITON_MODEL_NAME,
        "models": [
            {
                "version": baseline_version,
                "kind": baseline_artifact["model_type"],
                "artifact": f"models/artifacts/{baseline_version}.json",
                "dataset_version": dataset_version,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "label_taxonomy_version": "ims_incident_taxonomy_v2",
                "training_mode": "multiclass_supervised",
                "class_labels": baseline_artifact.get("class_labels", CANONICAL_LABELS),
                "normal_class_label": baseline_artifact.get("normal_class_label", NORMAL_ANOMALY_TYPE),
                "metrics": baseline_metrics,
            },
            {
                "version": candidate_version,
                "kind": candidate_artifact["model_type"],
                "artifact": f"models/artifacts/{candidate_version}.json",
                "dataset_version": dataset_version,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "label_taxonomy_version": "ims_incident_taxonomy_v2",
                "training_mode": "multiclass_supervised",
                "class_labels": candidate_artifact.get("class_labels", CANONICAL_LABELS),
                "normal_class_label": candidate_artifact.get("normal_class_label", NORMAL_ANOMALY_TYPE),
                "metrics": candidate_metrics,
                "automl_engine": candidate_artifact.get("automl_engine", "autogluon"),
                "best_model": candidate_artifact.get("best_model"),
            },
            {
                "version": deployed_runtime_version,
                "kind": "triton_python_multiclass_logistic_regression",
                "artifact": f"models/serving/predictive/{TRITON_MODEL_NAME}/{TRITON_MODEL_VERSION}/weights.json",
                "serving_repository": "models/serving/predictive",
                "triton_model_name": TRITON_MODEL_NAME,
                "dataset_version": dataset_version,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "label_taxonomy_version": "ims_incident_taxonomy_v2",
                "training_mode": "multiclass_supervised",
                "class_labels": CANONICAL_LABELS,
                "normal_class_label": NORMAL_ANOMALY_TYPE,
                "source_model_version": selected_version,
                "metrics": serving_metrics,
            },
        ],
    }


def upload_to_minio(
    registry: Dict[str, Any],
    registry_path: Path,
    selected_artifact_path: str | Path,
    baseline_artifact_path: str | Path,
    candidate_artifact_path: str | Path,
    serving_repository_root: Path,
) -> Dict[str, Any]:
    endpoint = os.getenv("MINIO_ENDPOINT", "http://model-storage-minio.ims-data.svc.cluster.local:9000")
    access_key = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    secret_key = os.getenv("MINIO_SECRET_KEY", "minioadmin")
    bucket = os.getenv("MINIO_BUCKET", "ims-models")
    predictive_prefix = os.getenv("MINIO_PREDICTIVE_PREFIX", "predictive")
    registry_key = f"{predictive_prefix}/model_registry.json"

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )

    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)

    uploads = [
        (baseline_artifact_path, f"{predictive_prefix}/{Path(str(baseline_artifact_path)).name}"),
        (candidate_artifact_path, f"{predictive_prefix}/{Path(str(candidate_artifact_path)).name}"),
        (selected_artifact_path, f"{predictive_prefix}/model.json"),
        (registry_path, registry_key),
    ]
    upload_staging_root = Path(tempfile.mkdtemp(prefix="ims-model-upload-"))
    for source_path, object_key in uploads:
        materialized_source = (
            _download_file_reference(source_path, upload_staging_root / Path(str(source_path)).name)
            if str(source_path).startswith("s3://")
            else Path(str(source_path))
        )
        client.upload_file(str(materialized_source), bucket, object_key)

    for file_path in serving_repository_root.rglob("*"):
        if not file_path.is_file():
            continue
        relative_path = file_path.relative_to(serving_repository_root)
        object_key = f"{predictive_prefix}/{relative_path.as_posix()}"
        client.upload_file(str(file_path), bucket, object_key)

    registry["minio_upload"] = {
        "bucket": bucket,
        "endpoint": endpoint,
        "predictive_prefix": predictive_prefix,
        "registry_key": registry_key,
        "selected_model_key": f"{predictive_prefix}/model.json",
        "serving_repository_prefix": predictive_prefix,
        "serving_model_key": f"{predictive_prefix}/{TRITON_MODEL_NAME}/{TRITON_MODEL_VERSION}/weights.json",
    }
    registry_path.write_text(json.dumps(registry, indent=2))
    client.upload_file(str(registry_path), bucket, registry_key)
    return registry["minio_upload"]


def full_run(
    dataset_version: str,
    workspace_root: str,
    artifact_dir: str,
    registry_path: str,
    baseline_version: str,
    candidate_version: str,
    automl_engine: str,
    skip_minio_upload: bool,
) -> Dict[str, Any]:
    dataset_manifest_path = _json_dump(Path("/tmp") / f"{dataset_version}-dataset-manifest.json", ingest_dataset(dataset_version, workspace_root))
    dataset_manifest = _json_load(dataset_manifest_path)
    feature_manifest_path = _json_dump(
        Path("/tmp") / f"{dataset_version}-feature-manifest.json",
        materialize_feature_windows(dataset_manifest_path, workspace_root),
    )
    feature_manifest = _json_load(feature_manifest_path)
    label_manifest_path = _json_dump(
        Path("/tmp") / f"{dataset_version}-label-manifest.json",
        generate_labels(feature_manifest_path, workspace_root),
    )
    label_manifest = _json_load(label_manifest_path)
    train_records = load_records(label_manifest["train_path"])
    eval_records = load_records(label_manifest["eval_path"])

    baseline_artifact = train_baseline(train_records)
    candidate_artifact = _prepare_artifact_for_storage(
        train_autogluon_candidate(train_records, workspace_root, candidate_version, automl_engine=automl_engine),
        candidate_version,
    )
    baseline_metrics = evaluate(eval_records, baseline_artifact, score_baseline)
    candidate_metrics = evaluate(eval_records, candidate_artifact, scorer_for_artifact(candidate_artifact))

    artifact_dir_path = Path(artifact_dir)
    artifact_dir_path.mkdir(parents=True, exist_ok=True)
    baseline_artifact_path = persist_model_artifact(artifact_dir, baseline_version, baseline_artifact)
    candidate_artifact_path = persist_model_artifact(artifact_dir, candidate_version, candidate_artifact)

    serving_dir = artifact_dir_path.parent / "serving" / "predictive"
    serving_dir.mkdir(parents=True, exist_ok=True)
    serving_model = train_serving_model(train_records)
    serving_metrics = evaluate_serving_model(eval_records, serving_model)

    evaluation_manifest = {
        "dataset_version": label_manifest["dataset_version"],
        "feature_schema_version": label_manifest["feature_schema_version"],
        "label_manifest": str(label_manifest_path),
        "baseline": {
            "version": baseline_version,
            "artifact_path": baseline_artifact_path,
            "artifact": baseline_artifact,
            "metrics": baseline_metrics,
        },
        "candidate": {
            "version": candidate_version,
            "artifact_path": candidate_artifact_path,
            "artifact": candidate_artifact,
            "metrics": candidate_metrics,
        },
        "promotion_gate": {**PROMOTION_GATE},
    }
    selection = select_best_model(evaluation_manifest)
    selected_version = selection["selected_model_version"]
    selected_artifact_path = baseline_artifact_path if selected_version == baseline_version else candidate_artifact_path
    triton_export = export_triton_repository(serving_dir, serving_model, selected_version)

    registry = build_registry(
        dataset_version=dataset_version,
        baseline_version=baseline_version,
        candidate_version=candidate_version,
        baseline_artifact=baseline_artifact,
        candidate_artifact=candidate_artifact,
        baseline_metrics=baseline_metrics,
        candidate_metrics=candidate_metrics,
        serving_metrics=serving_metrics,
        selected_version=selected_version,
    )
    registry["serving_artifact_path"] = str(triton_export["weights_path"])
    registry["serving_repository_path"] = str(serving_dir)
    registry["selected_artifact_path"] = selected_artifact_path
    registry["baseline_artifact_path"] = baseline_artifact_path
    registry["candidate_artifact_path"] = candidate_artifact_path
    registry_path_obj = Path(registry_path)
    registry_path_obj.parent.mkdir(parents=True, exist_ok=True)
    registry_path_obj.write_text(json.dumps(registry, indent=2))

    if not skip_minio_upload:
        upload_to_minio(
            registry=registry,
            registry_path=registry_path_obj,
            selected_artifact_path=selected_artifact_path,
            baseline_artifact_path=baseline_artifact_path,
            candidate_artifact_path=candidate_artifact_path,
            serving_repository_root=serving_dir,
        )
    return registry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", default="full-run")
    parser.add_argument("--dataset-version", default=DEFAULT_DATASET_VERSION)
    # Must match ims_anomaly_pipeline.WORKSPACE_ROOT. Default "ai" breaks KFP: that path is
    # root-owned image content; OpenShift runs as random UID and cannot mkdir under ai/.
    parser.add_argument("--workspace-root", default="/tmp/ims-pipeline")
    parser.add_argument("--artifact-dir", default="/tmp/ims-pipeline/models/artifacts")
    parser.add_argument("--registry-path", default="/tmp/ims-pipeline/registry/model_registry.json")
    parser.add_argument("--baseline-version", default="baseline-v1")
    parser.add_argument("--candidate-version", default="candidate-v1")
    parser.add_argument("--automl-engine", default="autogluon")
    parser.add_argument("--size-per-class", type=int, default=120)
    parser.add_argument("--dataset-manifest")
    parser.add_argument("--feature-manifest")
    parser.add_argument("--label-manifest")
    parser.add_argument("--baseline-manifest")
    parser.add_argument("--candidate-manifest")
    parser.add_argument("--evaluation-manifest")
    parser.add_argument("--selection-manifest")
    parser.add_argument("--output")
    parser.add_argument("--skip-minio-upload", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    random.seed(7)

    if args.step == "ingest-data":
        manifest = ingest_dataset(args.dataset_version, args.workspace_root, size_per_class=args.size_per_class)
        target = Path(args.output) if args.output else Path("/tmp") / f"{args.dataset_version}-dataset-manifest.json"
        _json_dump(target, manifest)
        print(target.read_text())
        return

    if args.step == "feature-engineering":
        if not args.dataset_manifest:
            raise ValueError("--dataset-manifest is required for feature-engineering")
        manifest = materialize_feature_windows(args.dataset_manifest, args.workspace_root)
        target = Path(args.output) if args.output else Path("/tmp") / f"{args.dataset_version}-feature-manifest.json"
        _json_dump(target, manifest)
        print(target.read_text())
        return

    if args.step == "label-generation":
        if not args.feature_manifest:
            raise ValueError("--feature-manifest is required for label-generation")
        manifest = generate_labels(args.feature_manifest, args.workspace_root)
        target = Path(args.output) if args.output else Path("/tmp") / f"{args.dataset_version}-label-manifest.json"
        _json_dump(target, manifest)
        print(target.read_text())
        return

    if args.step == "train-baseline":
        if not args.label_manifest:
            raise ValueError("--label-manifest is required for train-baseline")
        label_manifest = _json_load(args.label_manifest)
        artifact = train_baseline(load_records(label_manifest["train_path"]))
        artifact_path = persist_model_artifact(args.artifact_dir, args.baseline_version, artifact)
        manifest = {
            "version": args.baseline_version,
            "model_type": artifact["model_type"],
            "artifact_path": str(artifact_path),
            "label_manifest": args.label_manifest,
        }
        target = Path(args.output) if args.output else Path("/tmp") / f"{args.baseline_version}-manifest.json"
        _json_dump(target, manifest)
        print(target.read_text())
        return

    if args.step == "train-automl":
        if not args.label_manifest:
            raise ValueError("--label-manifest is required for train-automl")
        label_manifest = _json_load(args.label_manifest)
        artifact = _prepare_artifact_for_storage(
            train_autogluon_candidate(
                load_records(label_manifest["train_path"]),
                args.workspace_root,
                args.candidate_version,
                automl_engine=args.automl_engine,
            ),
            args.candidate_version,
        )
        artifact_path = persist_model_artifact(args.artifact_dir, args.candidate_version, artifact)
        manifest = {
            "version": args.candidate_version,
            "model_type": artifact["model_type"],
            "automl_engine": artifact.get("automl_engine", args.automl_engine),
            "artifact_path": str(artifact_path),
            "label_manifest": args.label_manifest,
        }
        target = Path(args.output) if args.output else Path("/tmp") / f"{args.candidate_version}-manifest.json"
        _json_dump(target, manifest)
        print(target.read_text())
        return

    if args.step == "evaluate":
        if not all([args.label_manifest, args.baseline_manifest, args.candidate_manifest]):
            raise ValueError("--label-manifest, --baseline-manifest, and --candidate-manifest are required for evaluate")
        label_manifest = _json_load(args.label_manifest)
        eval_records = load_records(label_manifest["eval_path"])
        baseline_manifest = _json_load(args.baseline_manifest)
        candidate_manifest = _json_load(args.candidate_manifest)
        baseline_artifact = _json_load(baseline_manifest["artifact_path"])
        candidate_artifact = _json_load(candidate_manifest["artifact_path"])
        baseline_metrics = evaluate(eval_records, baseline_artifact, score_baseline)
        candidate_metrics = evaluate(eval_records, candidate_artifact, scorer_for_artifact(candidate_artifact))
        manifest = {
            "dataset_version": label_manifest["dataset_version"],
            "feature_schema_version": label_manifest["feature_schema_version"],
            "label_manifest": args.label_manifest,
            "baseline": {
                "version": baseline_manifest["version"],
                "artifact_path": baseline_manifest["artifact_path"],
                "model_type": baseline_artifact["model_type"],
                "metrics": baseline_metrics,
            },
            "candidate": {
                "version": candidate_manifest["version"],
                "artifact_path": candidate_manifest["artifact_path"],
                "model_type": candidate_artifact["model_type"],
                "metrics": candidate_metrics,
            },
            "promotion_gate": {
                **PROMOTION_GATE,
                "status": gate_metrics(candidate_metrics, PROMOTION_GATE)["status"],
            },
        }
        target = Path(args.output) if args.output else Path("/tmp") / f"{args.dataset_version}-evaluation-manifest.json"
        _json_dump(target, manifest)
        print(target.read_text())
        return

    if args.step == "select-best":
        if not args.evaluation_manifest:
            raise ValueError("--evaluation-manifest is required for select-best")
        evaluation = _json_load(args.evaluation_manifest)
        manifest = select_best_model(evaluation)
        target = Path(args.output) if args.output else Path("/tmp") / f"{args.dataset_version}-selection-manifest.json"
        _json_dump(target, manifest)
        print(target.read_text())
        return

    if args.step == "register-model":
        if args.selection_manifest:
            selection = _json_load(args.selection_manifest)
        elif args.evaluation_manifest:
            selection = select_best_model(_json_load(args.evaluation_manifest))
        else:
            raise ValueError("--selection-manifest or --evaluation-manifest is required for register-model")

        label_manifest_path = args.label_manifest or selection.get("label_manifest")
        if not label_manifest_path:
            raise ValueError("--label-manifest is required for register-model")

        label_manifest = _json_load(label_manifest_path)
        train_records = load_records(label_manifest["train_path"])
        eval_records = load_records(label_manifest["eval_path"])

        serving_dir = Path(args.artifact_dir).parent / "serving" / "predictive"
        serving_dir.mkdir(parents=True, exist_ok=True)
        serving_model = train_serving_model(train_records)
        serving_metrics = evaluate_serving_model(eval_records, serving_model)

        baseline_artifact_path = selection["baseline"]["artifact_path"]
        candidate_artifact_path = selection["candidate"]["artifact_path"]
        baseline_artifact = _json_load(baseline_artifact_path)
        candidate_artifact = _json_load(candidate_artifact_path)
        selected_version = selection["selected_model_version"]

        registry = build_registry(
            dataset_version=selection["dataset_version"],
            baseline_version=selection["baseline"]["version"],
            candidate_version=selection["candidate"]["version"],
            baseline_artifact=baseline_artifact,
            candidate_artifact=candidate_artifact,
            baseline_metrics=selection["baseline"]["metrics"],
            candidate_metrics=selection["candidate"]["metrics"],
            serving_metrics=serving_metrics,
            selected_version=selected_version,
        )
        triton_export = export_triton_repository(serving_dir, serving_model, selected_version)
        registry_path = Path(args.registry_path)
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(json.dumps(registry, indent=2))
        serving_repository_reference = _write_directory_reference(serving_dir, "artifacts/serving/predictive")
        registry["serving_artifact_path"] = (
            f"{serving_repository_reference.rstrip('/')}/{TRITON_MODEL_NAME}/{TRITON_MODEL_VERSION}/weights.json"
            if serving_repository_reference.startswith("s3://")
            else str(triton_export["weights_path"])
        )
        registry["serving_repository_path"] = serving_repository_reference
        registry["selected_artifact_path"] = str(
            baseline_artifact_path if selected_version == selection["baseline"]["version"] else candidate_artifact_path
        )
        registry["baseline_artifact_path"] = str(baseline_artifact_path)
        registry["candidate_artifact_path"] = str(candidate_artifact_path)
        registry_path.write_text(json.dumps(registry, indent=2))

        target = Path(args.output) if args.output else registry_path
        if args.output:
            if _data_store_mode() == "s3":
                registry_uri = _write_json_reference(
                    registry,
                    "registry/pipeline_model_registry.json",
                    registry_path,
                )
            else:
                registry_uri = str(registry_path.resolve())
            _json_dump(target, {"registry_uri": registry_uri})
        elif target != registry_path:
            _json_dump(target, registry)
        print(target.read_text())
        return

    if args.step == "deploy-model":
        if not args.registry_path:
            raise ValueError("--registry-path is required for deploy-model")
        raw_registry = _json_load(args.registry_path)
        if isinstance(raw_registry, dict) and "registry_uri" in raw_registry:
            registry = _json_load(raw_registry["registry_uri"])
        else:
            registry = raw_registry
        staging_root = Path(tempfile.mkdtemp(prefix="ims-deploy-stage-"))
        selected_artifact_path = _download_file_reference(
            registry["selected_artifact_path"],
            staging_root / Path(str(registry["selected_artifact_path"])).name,
        )
        baseline_artifact_path = _download_file_reference(
            registry["baseline_artifact_path"],
            staging_root / Path(str(registry["baseline_artifact_path"])).name,
        )
        candidate_artifact_path = _download_file_reference(
            registry["candidate_artifact_path"],
            staging_root / Path(str(registry["candidate_artifact_path"])).name,
        )
        serving_repository_path = _download_directory_reference(
            registry.get("serving_repository_path", str(Path(args.artifact_dir).parent / "serving" / "predictive")),
            staging_root / "serving-repository",
        )
        registry_path = _json_dump(staging_root / "model_registry.json", registry)

        if not args.skip_minio_upload:
            upload_to_minio(
                registry=registry,
                registry_path=registry_path,
                selected_artifact_path=selected_artifact_path,
                baseline_artifact_path=baseline_artifact_path,
                candidate_artifact_path=candidate_artifact_path,
                serving_repository_root=serving_repository_path,
            )
        target = Path(args.output) if args.output else registry_path
        if target != registry_path:
            _json_dump(target, registry)
        print(target.read_text())
        return

    registry = full_run(
        dataset_version=args.dataset_version,
        workspace_root=args.workspace_root,
        artifact_dir=args.artifact_dir,
        registry_path=args.registry_path,
        baseline_version=args.baseline_version,
        candidate_version=args.candidate_version,
        automl_engine=args.automl_engine,
        skip_minio_upload=args.skip_minio_upload,
    )
    print(json.dumps(registry, indent=2))


if __name__ == "__main__":
    main()
