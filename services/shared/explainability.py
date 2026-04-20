from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Sequence
from urllib.parse import urlparse

import requests
import urllib3

from shared.classifier_profiles import (
    DEFAULT_ACTIVE_CLASSIFIER_PROFILE,
    classifier_profile_catalog,
    normalize_classifier_profile,
    resolve_active_classifier_profile,
)
from shared.incident_taxonomy import NORMAL_ANOMALY_TYPE, canonical_anomaly_type, metric_weights, scenario_definition


DEFAULT_EXPLAINABILITY_SCHEMA_VERSION = "ani.explainability.v1"
DEFAULT_EXPLAINABILITY_TIMEOUT_SECONDS = 8.0
DEFAULT_EXPLAINABILITY_TOP_FEATURES = 5

_TRUSTYAI_PROVIDER = {
    "key": "trustyai",
    "label": "TrustyAI Explainability",
    "family": "Explainability",
}

_LOCAL_PROVIDER = {
    "key": "local_heuristic",
    "label": "Incident profile heuristic",
    "family": "Explainability",
}

_TONE_SEQUENCE = ("rose", "amber", "sky", "emerald", "violet")
_NUMERIC_FEATURE_ORDER = (
    "register_rate",
    "invite_rate",
    "bye_rate",
    "error_4xx_ratio",
    "error_5xx_ratio",
    "latency_p95",
    "retransmission_count",
    "inter_arrival_mean",
    "payload_variance",
)


def explainability_schema_version() -> str:
    return (
        str(os.getenv("ANI_EXPLAINABILITY_SCHEMA_VERSION", DEFAULT_EXPLAINABILITY_SCHEMA_VERSION)).strip()
        or DEFAULT_EXPLAINABILITY_SCHEMA_VERSION
    )


def trustyai_explainability_enabled() -> bool:
    raw = str(os.getenv("ANI_INCIDENT_EXPLAINABILITY_TRUSTYAI_ENABLED", "true")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def trustyai_explainability_timeout_seconds() -> float:
    raw = str(
        os.getenv("ANI_INCIDENT_EXPLAINABILITY_TIMEOUT_SECONDS", str(DEFAULT_EXPLAINABILITY_TIMEOUT_SECONDS))
    ).strip()
    try:
        return max(1.0, min(float(raw), 20.0))
    except ValueError:
        return DEFAULT_EXPLAINABILITY_TIMEOUT_SECONDS


def trustyai_explainability_verify_tls() -> bool:
    raw = str(os.getenv("TRUSTYAI_EXPLAINABILITY_VERIFY_TLS", "")).strip().lower()
    if raw:
        return raw not in {"0", "false", "no", "off"}
    endpoint = str(os.getenv("TRUSTYAI_EXPLAINABILITY_ENDPOINT", "")).strip()
    hostname = str(urlparse(endpoint).hostname or "").strip().lower()
    if hostname.endswith(".svc.cluster.local"):
        return False
    return True


def trustyai_explainability_endpoint(model_context: Mapping[str, Any] | None = None) -> str:
    explicit = str(os.getenv("TRUSTYAI_EXPLAINABILITY_ENDPOINT", "")).strip()
    if explicit:
        normalized = explicit.rstrip("/")
        if normalized.endswith(":explain") or normalized.endswith("/explain"):
            return normalized
        return normalized

    context = model_context or {}
    endpoint = str(context.get("explainability_endpoint") or context.get("endpoint") or "").strip().rstrip("/")
    model_name = str(context.get("model_name") or "").strip()
    if not endpoint or not model_name:
        requested = normalize_classifier_profile(os.getenv("PREDICTIVE_ACTIVE_PROFILE", DEFAULT_ACTIVE_CLASSIFIER_PROFILE))
        active_key, profile = resolve_active_classifier_profile(requested, classifier_profile_catalog())
        context = profile or {}
        endpoint = str(context.get("explainability_endpoint") or context.get("endpoint") or "").strip().rstrip("/")
        model_name = str(context.get("model_name") or active_key or "").strip()
    if not endpoint or not model_name:
        return ""
    if endpoint.endswith(":explain") or endpoint.endswith("/explain"):
        return endpoint
    return f"{endpoint}/v1/models/{model_name}:explain"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _titleize(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _coerce_float(value: object) -> float:
    try:
        if value is None:
            return 0.0
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return number


def _top_feature_limit() -> int:
    raw = str(os.getenv("ANI_INCIDENT_EXPLAINABILITY_TOP_FEATURES", str(DEFAULT_EXPLAINABILITY_TOP_FEATURES))).strip()
    try:
        return max(3, min(int(raw), 8))
    except ValueError:
        return DEFAULT_EXPLAINABILITY_TOP_FEATURES


def _normalize_feature_map(features: Mapping[str, object] | None) -> Dict[str, object]:
    if not isinstance(features, Mapping):
        return {}
    return {str(key): value for key, value in features.items()}


def _ordered_numeric_feature_names(feature_values: Mapping[str, object]) -> List[str]:
    ordered = list(_NUMERIC_FEATURE_ORDER)
    for name, value in feature_values.items():
        if name in ordered:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            ordered.append(name)
    return ordered


def _feature_display_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if abs(number) >= 100:
            return f"{number:.0f}"
        if abs(number) >= 10:
            return f"{number:.1f}"
        return f"{number:.2f}"
    text = str(value or "").strip()
    return text[:48] if text else "n/a"


def _feature_item(
    feature: str,
    raw_impact: float,
    *,
    value: object,
    index: int,
) -> Dict[str, object]:
    direction = "increase" if raw_impact >= 0 else "decrease"
    return {
        "feature": feature,
        "label": _titleize(feature),
        "impact": round(abs(raw_impact), 6),
        "raw_impact": round(raw_impact, 6),
        "direction": direction,
        "value": value,
        "display_value": _feature_display_value(value),
        "tone": _TONE_SEQUENCE[index % len(_TONE_SEQUENCE)],
    }


def _explanation_confidence(predicted_confidence: float, top_features: Sequence[Mapping[str, object]]) -> str:
    if not top_features:
        return "low"
    total = sum(abs(_coerce_float(item.get("raw_impact") or item.get("impact"))) for item in top_features)
    head = abs(_coerce_float(top_features[0].get("raw_impact") or top_features[0].get("impact")))
    concentration = head / total if total else 0.0
    if predicted_confidence >= 0.8 and concentration >= 0.35:
        return "high"
    if predicted_confidence >= 0.6:
        return "medium"
    return "low"


def _pattern_insight(anomaly_type: str, top_features: Sequence[Mapping[str, object]]) -> str:
    normalized_type = canonical_anomaly_type(anomaly_type)
    feature_labels = [str(item.get("label") or _titleize(str(item.get("feature") or ""))).strip() for item in top_features[:2]]
    definition = scenario_definition(normalized_type) or {}
    summary = str(definition.get("summary") or "").strip()
    if len(feature_labels) >= 2:
        prefix = f"{feature_labels[0]} and {feature_labels[1]} are the dominant signals behind the {_titleize(normalized_type)} prediction."
    elif feature_labels:
        prefix = f"{feature_labels[0]} is the dominant signal behind the {_titleize(normalized_type)} prediction."
    else:
        prefix = f"The model explanation is aligned to the {_titleize(normalized_type)} prediction."
    if summary and summary.lower() not in prefix.lower():
        return f"{prefix} {summary}"
    return prefix


def _heuristic_attributions(features: Mapping[str, object], anomaly_type: str) -> List[Dict[str, object]]:
    normalized_type = canonical_anomaly_type(anomaly_type or NORMAL_ANOMALY_TYPE)
    weights = metric_weights(normalized_type)
    if not weights:
        weights = {
            "latency_p95": 0.35,
            "error_5xx_ratio": 0.25,
            "retransmission_count": 0.2,
            "register_rate": 0.2,
        }

    numeric_values = {name: abs(_coerce_float(features.get(name))) for name in weights}
    max_value = max(numeric_values.values()) if numeric_values else 0.0
    items: List[Dict[str, object]] = []
    for index, (feature, weight) in enumerate(
        sorted(weights.items(), key=lambda item: (-float(item[1]), str(item[0])))
    ):
        magnitude_ratio = numeric_values.get(feature, 0.0) / max_value if max_value > 0 else 1.0
        adjusted_impact = float(weight) * (0.65 + (0.35 * magnitude_ratio))
        items.append(_feature_item(feature, adjusted_impact, value=features.get(feature), index=index))
    return items[: _top_feature_limit()]


def _normalize_named_items(raw_items: Iterable[object], feature_values: Mapping[str, object]) -> List[Dict[str, object]]:
    items: List[Dict[str, object]] = []
    feature_names = {str(name).strip(): name for name in feature_values.keys()}
    for index, item in enumerate(raw_items):
        if not isinstance(item, Mapping):
            continue
        feature = str(item.get("feature") or item.get("name") or item.get("label") or "").strip()
        if not feature:
            continue
        normalized_feature = feature if feature in feature_names else feature.lower()
        raw_impact = _coerce_float(
            item.get("impact")
            if item.get("impact") is not None
            else item.get("saliency")
            if item.get("saliency") is not None
            else item.get("score")
            if item.get("score") is not None
            else item.get("importance")
            if item.get("importance") is not None
            else item.get("value")
        )
        items.append(
            _feature_item(
                normalized_feature,
                raw_impact,
                value=feature_values.get(normalized_feature),
                index=index,
            )
        )
    return items


def _normalize_mapping_items(raw_mapping: Mapping[str, object], feature_values: Mapping[str, object]) -> List[Dict[str, object]]:
    items: List[Dict[str, object]] = []
    for index, (feature, raw_value) in enumerate(raw_mapping.items()):
        if str(feature) not in feature_values:
            continue
        items.append(
            _feature_item(
                str(feature),
                _coerce_float(raw_value),
                value=feature_values.get(str(feature)),
                index=index,
            )
        )
    return items


def _normalize_numeric_items(raw_items: Sequence[object], feature_values: Mapping[str, object]) -> List[Dict[str, object]]:
    items: List[Dict[str, object]] = []
    feature_names = _ordered_numeric_feature_names(feature_values)
    if len(raw_items) != len(feature_names):
        return []
    for index, feature in enumerate(feature_names):
        items.append(
            _feature_item(
                feature,
                _coerce_float(raw_items[index]),
                value=feature_values.get(feature),
                index=index,
            )
        )
    return items


def _saliency_feature_name(name: str, feature_values: Mapping[str, object]) -> str:
    raw_name = str(name or "").strip()
    if not raw_name:
        return ""
    if raw_name in feature_values:
        return raw_name

    lowered = raw_name.lower()
    if lowered in feature_values:
        return lowered

    for prefix in ("inputs-", "input-", "feature-", "features-"):
        if lowered.startswith(prefix):
            suffix = lowered[len(prefix) :]
            try:
                index = int(suffix)
            except ValueError:
                continue
            feature_names = _ordered_numeric_feature_names(feature_values)
            if 0 <= index < len(feature_names):
                return feature_names[index]

    return raw_name


def _normalize_saliency_group(raw_items: Iterable[object], feature_values: Mapping[str, object]) -> List[Dict[str, object]]:
    items: List[Dict[str, object]] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, Mapping):
            continue
        feature_candidate = item.get("feature")
        if isinstance(feature_candidate, Mapping):
            feature_candidate = feature_candidate.get("name") or feature_candidate.get("label")
        if feature_candidate is None:
            feature_candidate = item.get("name") or item.get("label")
        feature = _saliency_feature_name(str(feature_candidate or ""), feature_values)
        if not feature:
            continue
        raw_impact = _coerce_float(
            item.get("score")
            if item.get("score") is not None
            else item.get("impact")
            if item.get("impact") is not None
            else item.get("saliency")
            if item.get("saliency") is not None
            else item.get("value")
        )
        items.append(
            _feature_item(
                feature,
                raw_impact,
                value=feature_values.get(feature),
                index=index,
            )
        )
    return items


def _extract_trustyai_items(candidate: object, feature_values: Mapping[str, object]) -> List[Dict[str, object]]:
    if isinstance(candidate, list):
        items = _normalize_saliency_group(candidate, feature_values)
        if items:
            return items
        items = _normalize_named_items(candidate, feature_values)
        if items:
            return items
        numeric_items = _normalize_numeric_items(candidate, feature_values)
        if numeric_items:
            return numeric_items
        for nested in candidate:
            items = _extract_trustyai_items(nested, feature_values)
            if items:
                return items
        return []

    if not isinstance(candidate, Mapping):
        return []

    for key in (
        "perFeatureImportance",
        "featureImportances",
        "feature_importances",
        "saliencies",
        "attributions",
        "explanations",
        "items",
        "features",
        "data",
    ):
        nested = candidate.get(key)
        if nested is None:
            continue
        items = _extract_trustyai_items(nested, feature_values)
        if items:
            return items

    items = _normalize_mapping_items(candidate, feature_values)
    if items:
        return items

    for nested in candidate.values():
        items = _extract_trustyai_items(nested, feature_values)
        if items:
            return items
    return []


def _trustyai_response_items(payload: Mapping[str, object], feature_values: Mapping[str, object]) -> List[Dict[str, object]]:
    outputs = payload.get("outputs")
    if isinstance(outputs, list):
        for output in outputs:
            if not isinstance(output, Mapping):
                continue
            name = str(output.get("name") or "").strip().lower()
            if name in {"saliency", "saliencies", "attributions", "shap", "explanation"}:
                data = output.get("data")
                if isinstance(data, list):
                    items = _normalize_numeric_items(data, feature_values)
                    if items:
                        return items

    for key in ("explanations", "saliencies", "attributions", "explanation"):
        candidate = payload.get(key)
        items = _extract_trustyai_items(candidate, feature_values)
        if items:
            return items

    result = payload.get("result")
    if isinstance(result, Mapping):
        return _trustyai_response_items(result, feature_values)

    return []


def _trustyai_payload_variants(features: Mapping[str, object]) -> List[Dict[str, object]]:
    feature_names = _ordered_numeric_feature_names(features)
    numeric_items = {
        name: _coerce_float(features.get(name)) for name in feature_names if name in _NUMERIC_FEATURE_ORDER
    }
    for name, value in features.items():
        if name in numeric_items:
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric_items[name] = _coerce_float(value)
    feature_names = list(numeric_items.keys())
    numeric_values = [[numeric_items[name] for name in feature_names]]
    return [{"instances": numeric_values}] if feature_names else []


def _trustyai_explanation(
    features: Mapping[str, object],
    *,
    model_context: Mapping[str, object] | None = None,
) -> tuple[List[Dict[str, object]], str]:
    if not trustyai_explainability_enabled():
        return [], ""

    endpoint = trustyai_explainability_endpoint(model_context)
    if not endpoint:
        return [], ""

    verify_tls = trustyai_explainability_verify_tls()
    if not verify_tls:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    errors: List[str] = []
    for payload in _trustyai_payload_variants(features):
        try:
            response = requests.post(
                endpoint,
                json=payload,
                timeout=trustyai_explainability_timeout_seconds(),
                verify=verify_tls,
            )
            body = response.text.strip()
            try:
                response_payload = response.json() if body else {}
            except ValueError:
                response_payload = {"detail": body} if body else {}
            if not response.ok:
                errors.append(f"{response.status_code}: {body[:160]}")
                continue
            items = _trustyai_response_items(response_payload if isinstance(response_payload, Mapping) else {}, features)
            if items:
                return items, ""
            errors.append("usable attributions missing from TrustyAI response")
        except Exception as exc:
            errors.append(str(exc))
    return [], "; ".join(error for error in errors if error)


def _normalize_feature_items(items: Iterable[Mapping[str, object]]) -> List[Dict[str, object]]:
    normalized = [
        {
            "feature": str(item.get("feature") or "").strip(),
            "label": str(item.get("label") or _titleize(str(item.get("feature") or ""))).strip(),
            "impact": round(abs(_coerce_float(item.get("impact"))), 6),
            "raw_impact": round(
                _coerce_float(
                    item.get("raw_impact")
                    if item.get("raw_impact") is not None
                    else item.get("impact")
                ),
                6,
            ),
            "direction": str(item.get("direction") or ("increase" if _coerce_float(item.get("raw_impact") or item.get("impact")) >= 0 else "decrease")),
            "value": item.get("value"),
            "display_value": str(item.get("display_value") or _feature_display_value(item.get("value"))),
            "tone": str(item.get("tone") or "sky"),
        }
        for item in items
        if str(item.get("feature") or "").strip()
    ]
    normalized.sort(key=lambda item: (-float(item["impact"]), str(item["feature"])))
    return normalized[: _top_feature_limit()]


def _model_metadata(model_version: str, model_context: Mapping[str, object] | None = None) -> Dict[str, object]:
    context = dict(model_context or {})
    return {
        "version": str(model_version or context.get("model_version_label") or context.get("model_name") or "").strip(),
        "profile_key": str(context.get("profile_key") or "").strip(),
        "profile_label": str(context.get("profile_label") or "").strip(),
        "name": str(context.get("model_name") or "").strip(),
        "endpoint": str(context.get("endpoint") or "").strip(),
        "explainability_endpoint": str(context.get("explainability_endpoint") or "").strip(),
    }


def build_model_explanation(
    features: Mapping[str, object] | None,
    *,
    anomaly_type: str,
    predicted_confidence: float,
    model_version: str,
    model_context: Mapping[str, object] | None = None,
    prefer_trustyai: bool = True,
) -> Dict[str, object]:
    feature_values = _normalize_feature_map(features)
    trustyai_items, trustyai_error = (
        _trustyai_explanation(feature_values, model_context=model_context) if prefer_trustyai else ([], "")
    )
    if trustyai_items:
        provider = dict(_TRUSTYAI_PROVIDER)
        status = "available"
        message = "TrustyAI feature attributions were attached at scoring time."
        top_features = _normalize_feature_items(trustyai_items)
    else:
        provider = dict(_LOCAL_PROVIDER)
        status = "fallback"
        message = (
            "TrustyAI explainability was unavailable for this scoring request; the incident profile heuristic is shown instead."
            if trustyai_error
            else "The incident profile heuristic is shown because TrustyAI explainability is not configured for this scoring path."
        )
        top_features = _normalize_feature_items(_heuristic_attributions(feature_values, anomaly_type))

    return {
        "provider": provider,
        "schema_version": explainability_schema_version(),
        "status": status,
        "message": message,
        "prediction": {
            "anomaly_type": canonical_anomaly_type(anomaly_type),
            "confidence": round(_coerce_float(predicted_confidence), 6),
        },
        "model": _model_metadata(model_version, model_context=model_context),
        "pattern_insight": _pattern_insight(anomaly_type, top_features),
        "explanation_confidence": _explanation_confidence(_coerce_float(predicted_confidence), top_features),
        "top_features": top_features,
        "generated_at": _now_iso(),
    }


def resolve_incident_model_explanation(incident: Mapping[str, object] | None) -> Dict[str, object]:
    if not isinstance(incident, Mapping):
        return {}
    existing = incident.get("model_explanation")
    if isinstance(existing, Mapping):
        top_features = existing.get("top_features")
        if isinstance(top_features, list) and top_features:
            payload = dict(existing)
            payload["top_features"] = _normalize_feature_items(
                [item for item in top_features if isinstance(item, Mapping)]
            )
            payload.setdefault("provider", dict(_LOCAL_PROVIDER))
            payload.setdefault("schema_version", explainability_schema_version())
            payload.setdefault("status", "available")
            return payload

    return build_model_explanation(
        incident.get("feature_snapshot") if isinstance(incident.get("feature_snapshot"), Mapping) else {},
        anomaly_type=str(incident.get("anomaly_type") or NORMAL_ANOMALY_TYPE),
        predicted_confidence=_coerce_float(incident.get("predicted_confidence")),
        model_version=str(incident.get("model_version") or ""),
        prefer_trustyai=False,
    )


def legacy_explainability_items(model_explanation: Mapping[str, object] | None) -> List[Dict[str, object]]:
    if not isinstance(model_explanation, Mapping):
        return []
    top_features = model_explanation.get("top_features")
    if not isinstance(top_features, list):
        return []
    items: List[Dict[str, object]] = []
    for item in top_features:
        if not isinstance(item, Mapping):
            continue
        items.append(
            {
                "feature": str(item.get("feature") or "").strip(),
                "weight": round(abs(_coerce_float(item.get("impact") or item.get("raw_impact"))), 6),
                "label": str(item.get("label") or _titleize(str(item.get("feature") or ""))).strip(),
                "tone": str(item.get("tone") or "sky"),
            }
        )
    return [item for item in items if item["feature"]]
