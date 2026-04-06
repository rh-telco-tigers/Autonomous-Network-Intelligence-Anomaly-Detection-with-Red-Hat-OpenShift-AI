import json
import math
import os
from pathlib import Path
from typing import Dict, Tuple

from joblib import load as joblib_load
import requests

from shared.incident_taxonomy import NORMAL_ANOMALY_TYPE, canonical_anomaly_type, canonical_anomaly_types
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


def _canonical_class_labels(metadata: Dict[str, object], artifact: Dict[str, object] | object) -> list[str]:
    if isinstance(artifact, dict):
        raw_labels = artifact.get("class_labels") or metadata.get("class_labels") or canonical_anomaly_types()
    else:
        raw_labels = metadata.get("class_labels") or canonical_anomaly_types()
    labels: list[str] = []
    for label in raw_labels:
        normalized = canonical_anomaly_type(str(label))
        if normalized not in labels:
            labels.append(normalized)
    return labels or canonical_anomaly_types()


def _prediction_from_probabilities(probabilities: Dict[str, float]) -> Dict[str, object]:
    labels = canonical_anomaly_types()
    normalized = {label: max(0.0, float(probabilities.get(label, 0.0))) for label in labels}
    total = sum(normalized.values()) or 1.0
    normalized = {label: value / total for label, value in normalized.items()}
    predicted_anomaly_type = max(normalized.items(), key=lambda item: (item[1], -labels.index(item[0])))[0]
    predicted_confidence = float(normalized[predicted_anomaly_type])
    anomaly_score = 1.0 - float(normalized.get(NORMAL_ANOMALY_TYPE, 0.0))
    top_classes = sorted(
        (
            {"anomaly_type": label, "probability": round(score, 6)}
            for label, score in normalized.items()
        ),
        key=lambda item: (-float(item["probability"]), str(item["anomaly_type"])),
    )[:3]
    return {
        "anomaly_type": predicted_anomaly_type,
        "predicted_anomaly_type": predicted_anomaly_type,
        "predicted_confidence": round(predicted_confidence, 6),
        "class_probabilities": {
            label: round(score, 6)
            for label, score in normalized.items()
        },
        "top_classes": top_classes,
        "anomaly_score": round(max(0.0, min(anomaly_score, 1.0)), 6),
        "is_anomaly": predicted_anomaly_type != NORMAL_ANOMALY_TYPE,
        "prediction_source": "model",
    }


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
        return "network_degradation"
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
    model_type = artifact.get("model_type") if isinstance(artifact, dict) else None
    class_labels = _canonical_class_labels(metadata, artifact)
    remote_prediction = _remote_score(features, class_labels, anomaly_type_hint=anomaly_type_hint)
    scoring_mode = "remote-kserve"
    if remote_prediction is not None:
        prediction = remote_prediction
        reported_version = str(_reported_remote_model_version(reported_version) or reported_version)
    elif metadata.get("kind") == "triton_python_multiclass_logistic_regression":
        prediction = _score_triton_export(features, artifact)
        scoring_mode = "triton-artifact"
    elif metadata.get("kind") in {"sklearn_multiclass_logistic_regression", "sklearn_logistic_regression"}:
        prediction = _score_serving_model(features, artifact, metadata)
        scoring_mode = "local-artifact"
    elif model_type == "baseline_multiclass_logistic_regression":
        prediction = _score_baseline(features, artifact)
        scoring_mode = "baseline-artifact"
    elif model_type == "autogluon_tabular_multiclass":
        prediction = _score_autogluon(features, artifact, metadata)
        scoring_mode = "autogluon-artifact"
    else:
        prediction = _score_legacy_runtime(features, artifact, metadata, anomaly_type_hint)
        scoring_mode = "legacy-fallback"
    return {
        **prediction,
        "model_version": reported_version,
        "scoring_mode": scoring_mode,
        "provided_anomaly_type_hint": canonical_anomaly_type(anomaly_type_hint) if anomaly_type_hint else None,
    }


def _flatten_numbers(value: object) -> list[float]:
    if isinstance(value, list):
        flattened: list[float] = []
        for item in value:
            flattened.extend(_flatten_numbers(item))
        return flattened
    try:
        return [float(value)]
    except (TypeError, ValueError):
        return []


def _remote_score(
    features: Dict[str, object],
    class_labels: list[str],
    anomaly_type_hint: str | None = None,
) -> Dict[str, object] | None:
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
        outputs_by_name = {
            str(output.get("name", "")).lower(): output
            for output in outputs
            if isinstance(output, dict)
        }
        probability_output = outputs_by_name.get("class_probabilities") or outputs_by_name.get("predict_proba")
        if probability_output:
            raw_probabilities = _flatten_numbers(probability_output.get("data", []))
            if len(raw_probabilities) >= len(class_labels):
                probability_map = {
                    canonical_anomaly_type(label): float(raw_probabilities[index])
                    for index, label in enumerate(class_labels)
                }
                prediction = _prediction_from_probabilities(probability_map)
                score_output = outputs_by_name.get("anomaly_score")
                if score_output:
                    score_values = _flatten_numbers(score_output.get("data", []))
                    if score_values:
                        prediction["anomaly_score"] = round(float(score_values[0]), 6)
                return prediction
        score_output = outputs_by_name.get("anomaly_score")
        if score_output:
            score_values = _flatten_numbers(score_output.get("data", []))
            if score_values:
                return _legacy_prediction_from_score(float(score_values[0]), features, anomaly_type_hint=anomaly_type_hint)
    except Exception:
        return None
    return None


def _load_artifact(artifact_path: Path, kind: str) -> Dict[str, object] | object:
    if kind in {"sklearn_logistic_regression", "sklearn_multiclass_logistic_regression"} or artifact_path.suffix == ".joblib":
        return joblib_load(artifact_path)
    return json.loads(artifact_path.read_text())


def _score_triton_export(features: Dict[str, object], artifact: Dict[str, object]) -> Dict[str, object]:
    return _prediction_from_probabilities(_linear_multiclass_probabilities(features, artifact))


def _linear_multiclass_probabilities(features: Dict[str, object], artifact: Dict[str, object]) -> Dict[str, float]:
    values = [float(features.get(feature, 0.0)) for feature in NUMERIC_FEATURES]
    means = [float(value) for value in artifact.get("scaler_mean", [0.0 for _ in NUMERIC_FEATURES])]
    scales = [max(float(value), 1e-6) for value in artifact.get("scaler_scale", [1.0 for _ in NUMERIC_FEATURES])]
    coefficients = [
        [float(weight) for weight in row]
        for row in artifact.get("coefficients", [])
    ]
    intercepts = [float(value) for value in artifact.get("intercepts", [])]
    class_labels = [canonical_anomaly_type(label) for label in artifact.get("class_labels", canonical_anomaly_types())]
    logits = []
    for weights, intercept in zip(coefficients, intercepts):
        logit = intercept
        for value, mean, scale, coefficient in zip(values, means, scales, weights):
            logit += ((value - mean) / scale) * coefficient
        logits.append(logit)
    if not logits:
        raise ModelUnavailableError("Multiclass linear artifact is missing logits")
    max_logit = max(logits)
    exponentials = [math.exp(logit - max_logit) for logit in logits]
    total = sum(exponentials) or 1.0
    probabilities = [value / total for value in exponentials]
    return {label: score for label, score in zip(class_labels, probabilities)}


def _score_baseline(features: Dict[str, object], artifact: Dict[str, object]) -> Dict[str, object]:
    return _prediction_from_probabilities(_linear_multiclass_probabilities(features, artifact))


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


def _score_autogluon(features: Dict[str, object], artifact: Dict[str, object], metadata: Dict[str, object]) -> Dict[str, object]:
    predictor_path = str(artifact.get("predictor_path") or artifact.get("predictor_uri") or "").strip()
    if not predictor_path:
        raise RuntimeError("AutoGluon predictor_path is missing from the artifact")

    import pandas as pd
    from autogluon.tabular import TabularPredictor

    predictor = TabularPredictor.load(predictor_path)
    frame = pd.DataFrame([{feature: float(features.get(feature, 0.0)) for feature in NUMERIC_FEATURES}])
    probabilities = predictor.predict_proba(frame, as_multiclass=True)
    probability_map = {
        canonical_anomaly_type(str(label)): float(probabilities.iloc[0][label])
        for label in probabilities.columns
    }
    for label in _canonical_class_labels(metadata, artifact):
        probability_map.setdefault(label, 0.0)
    return _prediction_from_probabilities(probability_map)


def _score_serving_model(features: Dict[str, object], model: object, metadata: Dict[str, object]) -> Dict[str, object]:
    if not hasattr(model, "predict_proba"):
        raise ModelUnavailableError("Serving artifact does not expose predict_proba")
    feature_vector = [[float(features.get(feature, 0.0)) for feature in NUMERIC_FEATURES]]
    probabilities = model.predict_proba(feature_vector)
    class_labels = getattr(model, "classes_", None)
    if class_labels is None:
        class_labels = _canonical_class_labels(metadata, {})
    probability_map = {
        canonical_anomaly_type(str(label)): float(probabilities[0][index])
        for index, label in enumerate(class_labels)
    }
    return _prediction_from_probabilities(probability_map)


def _legacy_prediction_from_score(
    score: float,
    features: Dict[str, object],
    anomaly_type_hint: str | None = None,
) -> Dict[str, object]:
    anomaly_type = classify_anomaly_type(features, anomaly_type_hint=anomaly_type_hint)
    normalized_score = round(max(0.0, min(float(score), 1.0)), 6)
    normal_probability = 1.0 - normalized_score
    probabilities = {
        label: 0.0
        for label in canonical_anomaly_types()
    }
    probabilities[NORMAL_ANOMALY_TYPE] = normal_probability
    probabilities[anomaly_type] = max(probabilities.get(anomaly_type, 0.0), normalized_score)
    prediction = _prediction_from_probabilities(probabilities)
    prediction["prediction_source"] = "heuristic-fallback"
    prediction["anomaly_type"] = anomaly_type
    prediction["predicted_anomaly_type"] = anomaly_type
    prediction["is_anomaly"] = normalized_score >= 0.6
    prediction["anomaly_score"] = normalized_score
    return prediction


def _score_legacy_runtime(
    features: Dict[str, object],
    artifact: Dict[str, object] | object,
    metadata: Dict[str, object],
    anomaly_type_hint: str | None,
) -> Dict[str, object]:
    model_type = artifact.get("model_type") if isinstance(artifact, dict) else None
    if metadata.get("kind") == "triton_python_logistic_regression":
        score = _score_triton_export_binary(features, artifact)
    elif metadata.get("kind") == "sklearn_logistic_regression":
        score = _score_serving_model_binary(features, artifact)
    elif model_type == "baseline_threshold":
        score = _score_baseline_binary(features, artifact)
    elif model_type == "autogluon_tabular":
        score = _score_autogluon_binary(features, artifact)
    else:
        score = _score_weighted(features, artifact)
    return _legacy_prediction_from_score(score, features, anomaly_type_hint=anomaly_type_hint)


def _score_triton_export_binary(features: Dict[str, object], artifact: Dict[str, object]) -> float:
    values = [float(features.get(feature, 0.0)) for feature in NUMERIC_FEATURES]
    means = [float(value) for value in artifact["scaler_mean"]]
    scales = [max(float(value), 1e-6) for value in artifact["scaler_scale"]]
    coefficients = [float(value) for value in artifact["coefficients"]]
    intercept = float(artifact["intercept"])
    logit = intercept
    for value, mean, scale, coefficient in zip(values, means, scales, coefficients):
        logit += ((value - mean) / scale) * coefficient
    return 1.0 / (1.0 + math.exp(-logit))


def _score_baseline_binary(features: Dict[str, object], artifact: Dict[str, object]) -> float:
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


def _score_autogluon_binary(features: Dict[str, object], artifact: Dict[str, object]) -> float:
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


def _score_serving_model_binary(features: Dict[str, object], model: object) -> float:
    if not hasattr(model, "predict_proba"):
        raise ModelUnavailableError("Serving artifact does not expose predict_proba")
    feature_vector = [[float(features.get(feature, 0.0)) for feature in NUMERIC_FEATURES]]
    probabilities = model.predict_proba(feature_vector)
    return float(probabilities[0][1])
