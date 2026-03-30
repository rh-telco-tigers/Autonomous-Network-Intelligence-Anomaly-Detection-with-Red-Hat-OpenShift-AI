import json
import os
from pathlib import Path
from typing import Dict, Tuple


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


def _registry_path() -> Path:
    return Path(os.getenv("MODEL_REGISTRY_PATH", "/app/ai/registry/model_registry.json"))


def load_registry() -> Dict[str, object] | None:
    path = _registry_path()
    if not path.exists():
        return None
    return json.loads(path.read_text())


def load_deployed_model() -> Dict[str, object] | None:
    registry = load_registry()
    if not registry:
        return None
    deployed = registry.get("deployed_model_version")
    for model in registry.get("models", []):
        if model.get("version") == deployed:
            artifact_path = Path(model["artifact"])
            if not artifact_path.is_absolute():
                artifact_path = _registry_path().parent.parent / artifact_path
            artifact = json.loads(artifact_path.read_text())
            return {
                "metadata": model,
                "artifact": artifact,
            }
    return None


def classify_anomaly_type(features: Dict[str, object]) -> str:
    register_rate = float(features.get("register_rate", 0.0))
    error_4xx_ratio = float(features.get("error_4xx_ratio", 0.0))
    error_5xx_ratio = float(features.get("error_5xx_ratio", 0.0))
    latency_p95 = float(features.get("latency_p95", 0.0))
    retransmission_count = float(features.get("retransmission_count", 0.0))

    if register_rate > 3.0 and retransmission_count > 5:
        return "registration_storm"
    if error_4xx_ratio > 0.2:
        return "malformed_sip"
    if latency_p95 > 250 or error_5xx_ratio > 0.1:
        return "service_degradation"
    return "normal"


def score_features(features: Dict[str, object]) -> Tuple[float, bool, str, str]:
    deployed = load_deployed_model()
    if not deployed:
        return _heuristic_score(features)

    artifact = deployed["artifact"]
    metadata = deployed["metadata"]
    model_type = artifact.get("model_type")
    if model_type == "baseline_threshold":
        score = _score_baseline(features, artifact)
    else:
        score = _score_weighted(features, artifact)
    anomaly_type = classify_anomaly_type(features)
    return round(score, 2), score >= float(artifact.get("threshold", 0.6)), anomaly_type, metadata["version"]


def _score_baseline(features: Dict[str, object], artifact: Dict[str, object]) -> float:
    stats = artifact["feature_stats"]
    weights = artifact["feature_weights"]
    weighted_sum = 0.0
    weight_total = 0.0
    for feature, weight in weights.items():
        value = float(features.get(feature, 0.0))
        mean = float(stats[feature]["mean"])
        std = max(float(stats[feature]["std"]), 0.001)
        z_score = max(0.0, (value - mean) / (2.0 * std))
        weighted_sum += min(z_score, 1.0) * float(weight)
        weight_total += float(weight)
    return min(weighted_sum / max(weight_total, 0.001), 0.99)


def _score_weighted(features: Dict[str, object], artifact: Dict[str, object]) -> float:
    weights = artifact["weights"]
    bounds = artifact["bounds"]
    weighted_sum = 0.0
    weight_total = 0.0
    for feature, weight in weights.items():
        value = float(features.get(feature, 0.0))
        lower = float(bounds[feature]["min"])
        upper = float(bounds[feature]["max"])
        if upper <= lower:
            normalized = 0.0
        else:
            normalized = max(0.0, min((value - lower) / (upper - lower), 1.0))
        weighted_sum += normalized * float(weight)
        weight_total += float(weight)
    return min(weighted_sum / max(weight_total, 0.001), 0.99)


def _heuristic_score(features: Dict[str, object]) -> Tuple[float, bool, str, str]:
    register_rate = float(features.get("register_rate", 0.0))
    error_4xx_ratio = float(features.get("error_4xx_ratio", 0.0))
    error_5xx_ratio = float(features.get("error_5xx_ratio", 0.0))
    latency_p95 = float(features.get("latency_p95", 0.0))
    retransmission_count = float(features.get("retransmission_count", 0.0))

    score = 0.0
    if register_rate > 3.0:
        score += 0.35
    if error_4xx_ratio > 0.2:
        score += 0.25
    if error_5xx_ratio > 0.1:
        score += 0.25
    if latency_p95 > 250:
        score += 0.15
    if retransmission_count > 5:
        score += 0.2

    anomaly_type = classify_anomaly_type(features)
    score = min(score, 0.99)
    return round(score, 2), score >= 0.6, anomaly_type, "heuristic-fallback"

