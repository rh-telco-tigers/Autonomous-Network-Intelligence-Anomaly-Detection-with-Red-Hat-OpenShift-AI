import json
import math
import os
import threading
import time
from pathlib import Path
from typing import Dict, Tuple

from joblib import load as joblib_load
import requests

from shared.classifier_profiles import (
    DEFAULT_ACTIVE_CLASSIFIER_PROFILE,
    classifier_profile_catalog,
    classifier_profile_payloads,
    normalize_classifier_profile,
    resolve_active_classifier_profile,
)
from shared.debug_trace import interaction_trace_packets, trace_now
from shared.incident_taxonomy import NORMAL_ANOMALY_TYPE, canonical_anomaly_type, canonical_anomaly_types
from shared.model_registry import load_registry as load_registry_document
from shared.security import outbound_headers


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


_CLASSIFIER_PROFILE_CACHE_LOCK = threading.Lock()
_CLASSIFIER_PROFILE_CACHE: Dict[str, object] | None = None
_CLASSIFIER_PROFILE_CACHE_EXPIRES_AT = 0.0


def _classifier_profile_cache_seconds() -> float:
    raw_value = str(os.getenv("CLASSIFIER_PROFILE_CACHE_SECONDS", "5")).strip()
    try:
        value = float(raw_value)
    except ValueError:
        return 5.0
    return max(0.0, value)


def _classifier_profile_selection() -> str:
    global _CLASSIFIER_PROFILE_CACHE, _CLASSIFIER_PROFILE_CACHE_EXPIRES_AT
    base_url = os.getenv("CONTROL_PLANE_URL", "").rstrip("/")
    fallback = normalize_classifier_profile(os.getenv("PREDICTIVE_ACTIVE_PROFILE", DEFAULT_ACTIVE_CLASSIFIER_PROFILE))
    if not base_url:
        return fallback

    cache_seconds = _classifier_profile_cache_seconds()
    now = time.time()
    if cache_seconds > 0:
        with _CLASSIFIER_PROFILE_CACHE_LOCK:
            if _CLASSIFIER_PROFILE_CACHE is not None and now < _CLASSIFIER_PROFILE_CACHE_EXPIRES_AT:
                return str(
                    _CLASSIFIER_PROFILE_CACHE.get("active_profile")
                    or _CLASSIFIER_PROFILE_CACHE.get("requested_profile")
                    or fallback
                )

    try:
        response = requests.get(
            f"{base_url}/models/classifier-profile",
            headers=outbound_headers(),
            timeout=5,
        )
        response.raise_for_status()
        payload = response.json()
        requested = normalize_classifier_profile(
            str((payload or {}).get("active_profile") or (payload or {}).get("requested_profile") or fallback)
        )
        if cache_seconds > 0:
            with _CLASSIFIER_PROFILE_CACHE_LOCK:
                _CLASSIFIER_PROFILE_CACHE = payload if isinstance(payload, dict) else {"requested_profile": requested}
                _CLASSIFIER_PROFILE_CACHE_EXPIRES_AT = now + cache_seconds
        return requested
    except Exception:
        return fallback


def _active_predictive_profile() -> tuple[str | None, Dict[str, object] | None, Dict[str, Dict[str, object]], str]:
    catalog = classifier_profile_catalog()
    requested = _classifier_profile_selection()
    active_key, profile = resolve_active_classifier_profile(requested, catalog)
    return active_key, profile, catalog, requested


def _predictive_endpoint() -> str:
    _active_key, profile, _catalog, _requested = _active_predictive_profile()
    return str((profile or {}).get("endpoint") or "").rstrip("/")


def _predictive_model_name() -> str:
    _active_key, profile, _catalog, _requested = _active_predictive_profile()
    return str((profile or {}).get("model_name") or "ani-predictive-fs")


def _reported_remote_model_version(default: str | None = None) -> str | None:
    _active_key, profile, _catalog, _requested = _active_predictive_profile()
    explicit = str((profile or {}).get("model_version_label") or "").strip()
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
    active_profile, profile, catalog, requested_profile = _active_predictive_profile()
    endpoint = str((profile or {}).get("endpoint") or "").rstrip("/")
    explainability_endpoint = str((profile or {}).get("explainability_endpoint") or "").rstrip("/")
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
        "predictive_model_name": str((profile or {}).get("model_name") or "") if endpoint else None,
        "predictive_endpoint": endpoint or None,
        "predictive_explainability_endpoint": explainability_endpoint or None,
        "artifact_present": bool(artifact_path and artifact_path.exists()),
        "scoring_modes": (
            ["remote-kserve-v2", "local-artifact"]
            if endpoint and bool((profile or {}).get("allow_local_fallback"))
            else (["remote-kserve-v2"] if endpoint else ["local-artifact"])
        ),
        "active_classifier_profile": active_profile,
        "requested_classifier_profile": requested_profile,
        "classifier_profiles": classifier_profile_payloads(
            requested_profile,
            active_profile=active_profile,
            profiles=catalog,
        ),
    }


def current_predictive_profile() -> Dict[str, object]:
    active_key, profile, catalog, requested_profile = _active_predictive_profile()
    selected = profile or {}
    return {
        "profile_key": active_key,
        "requested_profile": requested_profile,
        "profile_label": str(selected.get("label") or active_key or ""),
        "endpoint": str(selected.get("endpoint") or "").rstrip("/"),
        "explainability_endpoint": str(selected.get("explainability_endpoint") or "").rstrip("/"),
        "model_name": str(selected.get("model_name") or ""),
        "model_version_label": str(selected.get("model_version_label") or ""),
        "configured": bool(selected.get("configured")),
        "profiles": classifier_profile_payloads(
            requested_profile,
            active_profile=active_key,
            profiles=catalog,
        ),
    }


def _resolve_artifact_path(model: Dict[str, object]) -> Path:
    artifact_path = Path(str(model.get("artifact") or ""))
    if not artifact_path.is_absolute():
        artifact_path = _registry_path().parent.parent / artifact_path
    return artifact_path


def load_deployed_model() -> Dict[str, object] | None:
    registry = load_registry()
    if not registry:
        return None
    deployed = registry.get("deployed_model_version")
    for model in registry.get("models", []):
        if model.get("version") == deployed:
            return {
                "metadata": model,
                "artifact_path": _resolve_artifact_path(model),
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


def score_features_detailed(
    features: Dict[str, object],
    anomaly_type_hint: str | None = None,
    include_debug_trace: bool = False,
) -> Dict[str, object]:
    deployed = load_deployed_model()
    if not deployed:
        raise ModelUnavailableError("No deployed model is registered for anomaly scoring")

    metadata = deployed["metadata"]
    reported_version = str(metadata["version"])
    class_labels = _canonical_class_labels(metadata, {})
    debug_trace_packets: list[Dict[str, object]] = []
    active_profile_key, active_profile, _catalog, requested_profile = _active_predictive_profile()
    remote_prediction, remote_trace_packets = _remote_score(
        features,
        class_labels,
        predictive_profile=active_profile,
        anomaly_type_hint=anomaly_type_hint,
        include_debug_trace=include_debug_trace,
    )
    if include_debug_trace:
        debug_trace_packets.extend(remote_trace_packets)
    scoring_mode = "remote-kserve"
    if remote_prediction is not None:
        prediction = remote_prediction
        reported_version = str(_reported_remote_model_version(reported_version) or reported_version)
        if active_profile_key:
            scoring_mode = f"remote-kserve:{active_profile_key}"
    else:
        if active_profile and not bool(active_profile.get("allow_local_fallback")) and str(active_profile.get("endpoint") or "").strip():
            raise ModelUnavailableError(
                f"Remote predictive endpoint {str(active_profile.get('endpoint') or '').rstrip('/')} "
                f"for classifier profile {active_profile_key or requested_profile} did not return a usable prediction"
            )
        artifact = _load_local_artifact(deployed)
        model_type = artifact.get("model_type") if isinstance(artifact, dict) else None
        class_labels = _canonical_class_labels(metadata, artifact)
        if metadata.get("kind") == "triton_python_multiclass_logistic_regression":
            prediction = _score_triton_export(features, artifact)
            scoring_mode = "triton-artifact"
            if include_debug_trace:
                debug_trace_packets.extend(
                    _local_model_trace_packets(features, prediction, "triton-artifact", reported_version, str(metadata.get("kind") or ""))
                )
        elif metadata.get("kind") in {"sklearn_multiclass_logistic_regression", "sklearn_logistic_regression"}:
            prediction = _score_serving_model(features, artifact, metadata)
            scoring_mode = "local-artifact"
            if include_debug_trace:
                debug_trace_packets.extend(
                    _local_model_trace_packets(features, prediction, "local-artifact", reported_version, str(metadata.get("kind") or ""))
                )
        elif model_type == "baseline_multiclass_logistic_regression":
            prediction = _score_baseline(features, artifact)
            scoring_mode = "baseline-artifact"
            if include_debug_trace:
                debug_trace_packets.extend(
                    _local_model_trace_packets(features, prediction, "baseline-artifact", reported_version, str(model_type or "baseline"))
                )
        elif model_type == "autogluon_tabular_multiclass":
            prediction = _score_autogluon(features, artifact, metadata)
            scoring_mode = "autogluon-artifact"
            if include_debug_trace:
                debug_trace_packets.extend(
                    _local_model_trace_packets(features, prediction, "autogluon-artifact", reported_version, str(model_type or "autogluon"))
                )
        else:
            prediction = _score_legacy_runtime(features, artifact, metadata, anomaly_type_hint)
            scoring_mode = "legacy-fallback"
            if include_debug_trace:
                debug_trace_packets.extend(
                    _local_model_trace_packets(features, prediction, "legacy-fallback", reported_version, str(model_type or metadata.get("kind") or "legacy"))
                )
    result = {
        **prediction,
        "model_version": reported_version,
        "scoring_mode": scoring_mode,
        "classifier_profile": active_profile_key,
        "provided_anomaly_type_hint": canonical_anomaly_type(anomaly_type_hint) if anomaly_type_hint else None,
    }
    if include_debug_trace:
        result["debug_trace"] = debug_trace_packets
    return result


def _load_local_artifact(deployed: Dict[str, object]) -> Dict[str, object] | object:
    metadata = deployed["metadata"]
    artifact_path = deployed.get("artifact_path")
    if not isinstance(artifact_path, Path) or not artifact_path.exists():
        version = str(metadata.get("version") or "unknown")
        path = str(artifact_path or "")
        endpoint = _predictive_endpoint()
        if endpoint:
            raise ModelUnavailableError(
                f"Remote predictive endpoint {endpoint} did not return a usable prediction and the local artifact "
                f"for model {version} is missing at {path or '<unknown>'}"
            )
        raise ModelUnavailableError(
            f"Local scoring artifact for model {version} is missing at {path or '<unknown>'}"
        )
    return _load_artifact(artifact_path, str(metadata.get("kind", "")))


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


def _numeric_feature_payload(features: Dict[str, object]) -> Dict[str, float]:
    return {feature: float(features.get(feature, 0.0)) for feature in NUMERIC_FEATURES}


def _local_model_trace_packets(
    features: Dict[str, object],
    prediction: Dict[str, object],
    scoring_mode: str,
    model_version: str,
    model_kind: str,
) -> list[Dict[str, object]]:
    request_timestamp = trace_now()
    response_timestamp = trace_now()
    return interaction_trace_packets(
        category="model",
        service="anomaly-service",
        target="local-artifact",
        method="LOCAL",
        endpoint=f"local://{scoring_mode}",
        request_payload={
            "features": _numeric_feature_payload(features),
            "numeric_vector": list(_numeric_feature_payload(features).values()),
        },
        response_payload=prediction,
        request_timestamp=request_timestamp,
        response_timestamp=response_timestamp,
        metadata={
            "model_version": model_version,
            "scoring_mode": scoring_mode,
            "model_kind": model_kind,
        },
    )


def _remote_score(
    features: Dict[str, object],
    class_labels: list[str],
    predictive_profile: Dict[str, object] | None = None,
    anomaly_type_hint: str | None = None,
    include_debug_trace: bool = False,
) -> tuple[Dict[str, object] | None, list[Dict[str, object]]]:
    endpoint = str((predictive_profile or {}).get("endpoint") or "").rstrip("/")
    model_name = str((predictive_profile or {}).get("model_name") or "")
    if not endpoint:
        return None, []

    values = [[float(features.get(feature, 0.0)) for feature in NUMERIC_FEATURES]]
    request_payload = {
        "inputs": [
            {
                "name": "predict",
                "shape": [1, len(NUMERIC_FEATURES)],
                "datatype": "FP32",
                "data": values,
            }
        ]
    }
    request_endpoint = f"{endpoint}/v2/models/{model_name}/infer"
    request_timestamp = trace_now() if include_debug_trace else None
    try:
        response = requests.post(
            request_endpoint,
            json=request_payload,
            timeout=10,
        )
        response_timestamp = trace_now() if include_debug_trace else None
        body = response.text.strip()
        try:
            response_payload = response.json() if body else {}
        except ValueError:
            response_payload = {"detail": body} if body else {}
        response.raise_for_status()
        trace_packets = (
            interaction_trace_packets(
                category="model",
                service="anomaly-service",
                target="predictive-service",
                method="POST",
                endpoint=request_endpoint,
                request_payload=request_payload,
                response_payload={
                    "status_code": response.status_code,
                    "body": response_payload,
                },
                request_timestamp=request_timestamp,
                response_timestamp=response_timestamp,
                metadata={
                    "model_name": model_name,
                    "class_labels": class_labels,
                },
            )
            if include_debug_trace
            else []
        )
        outputs = response_payload.get("outputs", []) if isinstance(response_payload, dict) else []
        if not outputs:
            return None, trace_packets
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
                return prediction, trace_packets
        score_output = outputs_by_name.get("anomaly_score")
        if score_output:
            score_values = _flatten_numbers(score_output.get("data", []))
            if score_values:
                return (
                    _legacy_prediction_from_score(float(score_values[0]), features, anomaly_type_hint=anomaly_type_hint),
                    trace_packets,
                )
        return None, trace_packets
    except Exception as exc:
        if not include_debug_trace:
            return None, []
        return (
            None,
            interaction_trace_packets(
                category="model",
                service="anomaly-service",
                target="predictive-service",
                method="POST",
                endpoint=request_endpoint,
                request_payload=request_payload,
                response_payload={"error": str(exc)},
                request_timestamp=request_timestamp,
                response_timestamp=trace_now(),
                metadata={
                    "model_name": model_name,
                    "class_labels": class_labels,
                },
            ),
        )


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
