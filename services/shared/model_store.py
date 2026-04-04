import json
import math
import os
from pathlib import Path
from typing import Dict, Tuple

from joblib import load as joblib_load
import requests

from shared.incident_taxonomy import NORMAL_ANOMALY_TYPE, canonical_anomaly_type
from shared.model_registry import load_registry as load_registry_document


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


class ModelUnavailableError(RuntimeError):
    pass


def _predictive_endpoint() -> str:
    return os.getenv("PREDICTIVE_ENDPOINT", "").rstrip("/")


def _predictive_model_name() -> str:
    explicit = os.getenv("PREDICTIVE_MODEL_NAME", "ims-predictive-fs").strip()
    return explicit or "ims-predictive-fs"


def _reported_remote_model_version(default: str | None = None) -> str | None:
    explicit = os.getenv("PREDICTIVE_MODEL_VERSION_LABEL", "").strip()
    if explicit:
        return explicit
    if _predictive_endpoint():
        return _predictive_model_name()
    return default


def _registry_path() -> Path:
    return Path(os.getenv("MODEL_REGISTRY_PATH", "/app/ai/registry/model_registry.json"))


def load_registry() -> Dict[str, object] | None:
    path = _registry_path()
    if not path.exists():
        return None
    return load_registry_document()


def current_model_status() -> Dict[str, object]:
    registry = load_registry()
    endpoint = _predictive_endpoint()
    registry_deployed = registry.get("deployed_model_version") if registry else None
    deployed = _reported_remote_model_version(registry_deployed)
    artifact_path = None
    if registry and registry_deployed:
        for model in registry.get("models", []):
            if model.get("version") == registry_deployed:
                artifact = Path(model["artifact"])
                if not artifact.is_absolute():
                    artifact = _registry_path().parent.parent / artifact
                artifact_path = artifact
                break
    return {
        "registry_loaded": bool(registry and registry.get("models")),
        "deployed_model_version": deployed,
        "predictive_model_name": _predictive_model_name() if endpoint else None,
        "predictive_endpoint": endpoint or None,
        "artifact_present": bool(artifact_path and artifact_path.exists()),
        "scoring_modes": ["remote-kserve-triton", "local-artifact"] if endpoint else ["local-artifact"],
    }


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
            artifact = _load_artifact(artifact_path, str(model.get("kind", "")))
            return {
                "metadata": model,
                "artifact": artifact,
            }
    return None


def classify_anomaly_type(features: Dict[str, object], anomaly_type_hint: str | None = None) -> str:
    hinted_type = canonical_anomaly_type(anomaly_type_hint)
    if hinted_type and hinted_type != NORMAL_ANOMALY_TYPE:
        return hinted_type

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
    return NORMAL_ANOMALY_TYPE


def score_features(features: Dict[str, object], anomaly_type_hint: str | None = None) -> Tuple[float, bool, str, str]:
    result = score_features_detailed(features, anomaly_type_hint=anomaly_type_hint)
    return (
        float(result["anomaly_score"]),
        bool(result["is_anomaly"]),
        str(result["anomaly_type"]),
        str(result["model_version"]),
    )


def score_features_detailed(features: Dict[str, object], anomaly_type_hint: str | None = None) -> Dict[str, object]:
    deployed = load_deployed_model()
    if not deployed:
        raise ModelUnavailableError("No deployed model is registered for anomaly scoring")

    artifact = deployed["artifact"]
    metadata = deployed["metadata"]
    reported_version = str(metadata["version"])
    threshold = float(metadata.get("threshold", artifact.get("threshold", 0.6) if isinstance(artifact, dict) else 0.6))
    model_type = artifact.get("model_type") if isinstance(artifact, dict) else None
    remote_score = _remote_score(features, threshold)
    scoring_mode = "remote-kserve"
    if remote_score is not None:
        score = remote_score
        reported_version = str(_reported_remote_model_version(reported_version) or reported_version)
    elif metadata.get("kind") == "triton_python_logistic_regression":
        score = _score_triton_export(features, artifact)
        scoring_mode = "triton-artifact"
    elif metadata.get("kind") == "sklearn_logistic_regression":
        score = _score_serving_model(features, artifact)
        scoring_mode = "local-artifact"
    elif model_type == "baseline_threshold":
        score = _score_baseline(features, artifact)
        scoring_mode = "baseline-artifact"
    elif model_type == "autogluon_tabular":
        score = _score_autogluon(features, artifact)
        scoring_mode = "autogluon-artifact"
    else:
        score = _score_weighted(features, artifact)
        scoring_mode = "weighted-artifact"
    anomaly_type = classify_anomaly_type(features, anomaly_type_hint=anomaly_type_hint)
    rounded = round(score, 2)
    return {
        "anomaly_score": rounded,
        "is_anomaly": rounded >= threshold,
        "anomaly_type": anomaly_type,
        "model_version": reported_version,
        "scoring_mode": scoring_mode,
    }


def _remote_score(features: Dict[str, object], threshold: float) -> float | None:
    endpoint = _predictive_endpoint()
    model_name = _predictive_model_name()
    if not endpoint:
        return None

    values = [[float(features.get(feature, 0.0)) for feature in NUMERIC_FEATURES]]
    try:
        response = requests.post(
            f"{endpoint}/v2/models/{model_name}/infer",
            json={
                "inputs": [
                    {
                        "name": "predict",
                        "shape": [1, len(NUMERIC_FEATURES)],
                        "datatype": "FP32",
                        "data": values,
                    }
                ]
            },
            timeout=10,
        )
        response.raise_for_status()
        outputs = response.json().get("outputs", [])
        if not outputs:
            return None
        output = outputs[0]
        raw = output.get("data", [0.0])
        output_name = str(output.get("name", "")).lower()
        value = raw[0] if isinstance(raw, list) else raw
        while isinstance(value, list):
            if not value:
                return None
            if output_name == "predict_proba" and len(value) >= 2 and all(not isinstance(item, list) for item in value):
                return float(value[1])
            value = value[0]
        value = float(value)
        datatype = str(output.get("datatype", "")).upper()
        if datatype.startswith("INT") or datatype.startswith("UINT"):
            return 1.0 if value >= 1.0 else 0.0
        return value
    except Exception:
        return None


def _load_artifact(artifact_path: Path, kind: str) -> Dict[str, object] | object:
    if kind == "sklearn_logistic_regression" or artifact_path.suffix == ".joblib":
        return joblib_load(artifact_path)
    return json.loads(artifact_path.read_text())


def _score_triton_export(features: Dict[str, object], artifact: Dict[str, object]) -> float:
    values = [float(features.get(feature, 0.0)) for feature in NUMERIC_FEATURES]
    means = [float(value) for value in artifact["scaler_mean"]]
    scales = [max(float(value), 1e-6) for value in artifact["scaler_scale"]]
    coefficients = [float(value) for value in artifact["coefficients"]]
    intercept = float(artifact["intercept"])

    logit = intercept
    for value, mean, scale, coefficient in zip(values, means, scales, coefficients):
        logit += ((value - mean) / scale) * coefficient
    return 1.0 / (1.0 + math.exp(-logit))


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


def _score_autogluon(features: Dict[str, object], artifact: Dict[str, object]) -> float:
    predictor_path = artifact.get("predictor_path")
    if not predictor_path:
        raise RuntimeError("AutoGluon predictor_path is missing from the artifact")

    import pandas as pd
    from autogluon.tabular import TabularPredictor

    predictor = TabularPredictor.load(predictor_path)
    frame = pd.DataFrame([{feature: float(features.get(feature, 0.0)) for feature in NUMERIC_FEATURES}])
    probabilities = predictor.predict_proba(frame, as_multiclass=True)
    if 1 in probabilities.columns:
        return float(probabilities[1].iloc[0])
    return float(probabilities.iloc[0].max())


def _score_serving_model(features: Dict[str, object], model: object) -> float:
    if not hasattr(model, "predict_proba"):
        raise ModelUnavailableError("Serving artifact does not expose predict_proba")
    feature_vector = [[float(features.get(feature, 0.0)) for feature in NUMERIC_FEATURES]]
    probabilities = model.predict_proba(feature_vector)
    return float(probabilities[0][1])
