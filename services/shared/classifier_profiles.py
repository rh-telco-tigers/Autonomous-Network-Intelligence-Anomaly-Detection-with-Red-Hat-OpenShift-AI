from __future__ import annotations

import os
from typing import Any, Dict


DEFAULT_ACTIVE_CLASSIFIER_PROFILE = "live"


def normalize_classifier_profile(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized or DEFAULT_ACTIVE_CLASSIFIER_PROFILE


def classifier_profile_catalog() -> Dict[str, Dict[str, Any]]:
    live_endpoint = (
        os.getenv("PREDICTIVE_ENDPOINT_LIVE", "").strip()
        or os.getenv("PREDICTIVE_ENDPOINT", "").strip()
        or os.getenv("PREDICTIVE_SERVICE_URL", "").strip()
    ).rstrip("/")
    live_model_name = (
        os.getenv("PREDICTIVE_MODEL_NAME_LIVE", "").strip()
        or os.getenv("PREDICTIVE_MODEL_NAME", "").strip()
        or "ani-predictive-fs"
    )
    live_version_label = (
        os.getenv("PREDICTIVE_MODEL_VERSION_LABEL_LIVE", "").strip()
        or os.getenv("PREDICTIVE_MODEL_VERSION_LABEL", "").strip()
        or live_model_name
    )

    backfill_endpoint = (
        os.getenv("PREDICTIVE_ENDPOINT_BACKFILL", "").strip()
        or os.getenv("PREDICTIVE_BACKFILL_SERVICE_URL", "").strip()
    ).rstrip("/")
    backfill_model_name = os.getenv("PREDICTIVE_MODEL_NAME_BACKFILL", "").strip() or "ani-predictive-backfill"
    backfill_version_label = (
        os.getenv("PREDICTIVE_MODEL_VERSION_LABEL_BACKFILL", "").strip()
        or backfill_model_name
    )

    return {
        "live": {
            "key": "live",
            "label": "Live model",
            "description": "Incident-linked predictive model used by the live release workflow.",
            "endpoint": live_endpoint,
            "model_name": live_model_name,
            "model_version_label": live_version_label,
            "configured": bool(live_endpoint and live_model_name),
            "allow_local_fallback": True,
        },
        "backfill": {
            "key": "backfill",
            "label": "Backfill model",
            "description": "AutoGluon model trained from the large backfill dataset path.",
            "endpoint": backfill_endpoint,
            "model_name": backfill_model_name,
            "model_version_label": backfill_version_label,
            "configured": bool(backfill_endpoint and backfill_model_name),
            "allow_local_fallback": False,
        },
    }


def first_configured_classifier_profile(profiles: Dict[str, Dict[str, Any]]) -> str | None:
    for key in ("live", "backfill"):
        profile = profiles.get(key) or {}
        if bool(profile.get("configured")):
            return key
    for key, profile in profiles.items():
        if bool(profile.get("configured")):
            return key
    return None


def resolve_active_classifier_profile(
    requested_profile: str | None,
    profiles: Dict[str, Dict[str, Any]] | None = None,
) -> tuple[str | None, Dict[str, Any] | None]:
    catalog = profiles or classifier_profile_catalog()
    normalized = normalize_classifier_profile(requested_profile)
    selected = catalog.get(normalized)
    if selected and bool(selected.get("configured")):
        return normalized, selected
    fallback_key = first_configured_classifier_profile(catalog)
    if not fallback_key:
        return None, None
    return fallback_key, catalog.get(fallback_key)


def classifier_profile_payloads(
    requested_profile: str | None,
    *,
    active_profile: str | None = None,
    profiles: Dict[str, Dict[str, Any]] | None = None,
) -> list[Dict[str, Any]]:
    catalog = profiles or classifier_profile_catalog()
    resolved_active, _ = resolve_active_classifier_profile(requested_profile, catalog)
    active_key = active_profile or resolved_active
    normalized_requested = normalize_classifier_profile(requested_profile)
    items: list[Dict[str, Any]] = []
    for key in ("live", "backfill"):
        profile = catalog.get(key)
        if not profile:
            continue
        items.append(
            {
                "key": key,
                "label": str(profile.get("label") or key.title()),
                "description": str(profile.get("description") or ""),
                "endpoint": str(profile.get("endpoint") or ""),
                "model_name": str(profile.get("model_name") or ""),
                "model_version_label": str(profile.get("model_version_label") or ""),
                "configured": bool(profile.get("configured")),
                "reachable": bool(profile.get("reachable", profile.get("configured"))),
                "status": str(profile.get("status") or ("ready" if profile.get("configured") else "not_configured")),
                "active": key == active_key,
                "requested": key == normalized_requested,
            }
        )
    return items
