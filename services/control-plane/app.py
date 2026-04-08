import logging
import html
import json
import hashlib
import hmac
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
import requests

from shared.aap import (
    AAPAutomationError,
    action_supported as aap_action_supported,
    bootstrap_resources as aap_bootstrap_resources,
    launch_action as aap_launch_action,
    launch_runner_job as aap_launch_runner_job,
    wait_for_job as aap_wait_for_job,
    wait_for_runner_job as aap_wait_for_runner_job,
)
from shared.cluster_env import (
    anomaly_service_url,
    control_plane_url,
    console_cluster_name,
    feature_gateway_url,
    predictive_service_url,
    rca_service_url,
)
from shared.cors import install_cors
from shared.debug_trace import interaction_trace_packets, make_trace_packet
from shared.eda import (
    EDAAutomationError,
    bootstrap_resources as eda_bootstrap_resources,
    publish_event as eda_publish_event,
)
from shared.db import (
    attach_rca,
    create_ticket_resolution_extract,
    create_incident,
    get_incident,
    get_incident_action,
    get_incident_remediation,
    get_incident_ticket,
    get_incident_verification,
    get_ticket_by_provider_external_id,
    list_approvals,
    init_db,
    list_audit_events,
    list_incident_actions,
    list_incident_rca,
    list_incident_remediations,
    list_incident_tickets,
    list_incident_verifications,
    list_incidents,
    list_ticket_comments,
    list_ticket_resolution_extracts,
    list_ticket_sync_events,
    record_approval,
    record_audit,
    record_incident_action,
    record_ticket_sync_event,
    record_verification,
    remediation_success_rates,
    replace_remediations,
    set_incident_remediation_status,
    transition_incident_state,
    upsert_incident_ticket,
    upsert_ticket_comment,
    update_incident_remediation,
    update_approval,
    update_incident_action,
    update_incident_status,
)
from shared.incident_taxonomy import (
    NORMAL_ANOMALY_TYPE,
    canonical_anomaly_type,
    console_scenario_catalog,
    console_scenario_names,
    metric_weights,
    normalize_scenario_name,
    scenario_definition,
)
from shared.integrations import (
    clear_integration_status_cache,
    create_jira_issue,
    integration_status,
    send_slack_notification,
)
from shared.metrics import (
    install_metrics,
    record_automation,
    record_incident,
    record_integration,
    record_model_promotion,
    record_ticket_sync,
    record_verification as record_verification_metric,
    record_workflow_transition,
    set_active_incidents,
)
from shared.model_registry import get_model, list_datasets, list_feature_schemas, load_registry, promote_model
from shared.rag import (
    DEFAULT_MILVUS_COLLECTIONS,
    RUNBOOK_COLLECTION,
    get_document_by_reference,
    publish_semantic_record,
    retrieve_context,
    retrieve_knowledge_articles,
)
from shared.security import AuthContext, ensure_project_access, ensure_role, outbound_headers, require_api_key
from shared.tickets import TicketProviderError, get_ticket_provider
from shared.workflow import (
    APPROVED,
    AWAITING_APPROVAL,
    CLOSED,
    ESCALATED,
    EXECUTING,
    EXECUTED,
    EXECUTION_FAILED,
    FALSE_POSITIVE,
    NEW,
    RCA_GENERATED,
    RCA_REJECTED,
    AI_PLAYBOOK_GENERATION_ACTION,
    REMEDIATION_SUGGESTED,
    VERIFIED,
    VERIFICATION_FAILED,
    can_transition,
    generate_remediation_suggestions,
    is_active_state,
    normalize_workflow_state,
    plane_state_for_workflow,
    resolution_quality,
    severity_from_prediction,
    severity_from_score,
)


logger = logging.getLogger(__name__)
RELATED_CONTEXT_COLLECTIONS = [name for name in DEFAULT_MILVUS_COLLECTIONS if name != RUNBOOK_COLLECTION]
_AUTOMATION_BOOTSTRAP_LOCK = threading.Lock()
_AUTOMATION_BOOTSTRAP_STARTED = False
DEBUG_TRACE_EVENT_TYPE = "debug_trace_packet"
DEFAULT_INCIDENT_AUTO_RCA_SAMPLE_RATE = 1.0


class IncidentCreate(BaseModel):
    incident_id: str
    project: str = "ims-demo"
    anomaly_score: float
    anomaly_type: str
    predicted_confidence: float = 0.0
    class_probabilities: Dict[str, float] = Field(default_factory=dict)
    top_classes: List[Dict[str, object]] = Field(default_factory=list)
    is_anomaly: bool = True
    model_version: str
    feature_window_id: Optional[str] = None
    feature_snapshot: Dict[str, object] = Field(default_factory=dict)
    created_at: Optional[str] = None
    status: str = NEW
    severity: Optional[str] = None
    source_system: str = "anomaly-service"
    auto_generate_rca: Optional[bool] = None
    debug_trace: List[Dict[str, object]] = Field(default_factory=list)


class RCAAttach(BaseModel):
    root_cause: str
    explanation: Optional[str] = None
    confidence: float
    evidence: List[Dict[str, object]]
    recommendation: str
    generation_mode: Optional[str] = None
    generation_source_label: Optional[str] = None
    llm_used: Optional[bool] = None
    llm_configured: Optional[bool] = None
    llm_model: Optional[str] = None
    llm_runtime: Optional[str] = None
    retrieved_documents: List[Dict[str, object]] = Field(default_factory=list)
    debug_trace: List[Dict[str, object]] = Field(default_factory=list)


def _incident_auto_rca_sample_rate() -> float:
    raw_value = str(os.getenv("INCIDENT_AUTO_RCA_SAMPLE_RATE", str(DEFAULT_INCIDENT_AUTO_RCA_SAMPLE_RATE))).strip()
    if not raw_value:
        return DEFAULT_INCIDENT_AUTO_RCA_SAMPLE_RATE
    try:
        return max(0.0, min(float(raw_value), 1.0))
    except ValueError:
        logger.warning(
            "Invalid INCIDENT_AUTO_RCA_SAMPLE_RATE=%r, falling back to %.2f",
            raw_value,
            DEFAULT_INCIDENT_AUTO_RCA_SAMPLE_RATE,
        )
        return DEFAULT_INCIDENT_AUTO_RCA_SAMPLE_RATE


def _stable_sample_ratio(value: str) -> float:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    numerator = int.from_bytes(digest[:8], "big")
    return numerator / float((1 << 64) - 1)


def _auto_rca_policy(auto_generate_rca: Optional[bool], incident_id: str) -> Dict[str, object]:
    if auto_generate_rca is not None:
        return {
            "enabled": bool(auto_generate_rca),
            "mode": "explicit",
            "sample_rate": None,
            "sample_value": None,
        }

    sample_rate = _incident_auto_rca_sample_rate()
    if sample_rate <= 0.0:
        return {
            "enabled": False,
            "mode": "sampled",
            "sample_rate": sample_rate,
            "sample_value": 1.0,
        }
    if sample_rate >= 1.0:
        return {
            "enabled": True,
            "mode": "sampled",
            "sample_rate": sample_rate,
            "sample_value": 0.0,
        }

    sample_value = _stable_sample_ratio(str(incident_id or "incident"))
    return {
        "enabled": sample_value < sample_rate,
        "mode": "sampled",
        "sample_rate": sample_rate,
        "sample_value": round(sample_value, 6),
    }


class ModelPromotionRequest(BaseModel):
    version: str
    approved_by: str
    stage: str = "prod"


class ConsoleScenarioRequest(BaseModel):
    scenario: str
    project: str = "ims-demo"


class IncidentTransitionRequest(BaseModel):
    target_state: str
    notes: str = ""
    source_url: str = ""


class RemediationActionRequest(BaseModel):
    remediation_id: Optional[int] = None
    action: Optional[str] = None
    approved_by: str
    notes: str = ""
    execute: bool = False
    source_of_action: str = "platform_ui"
    source_url: str = ""


class VerificationRequest(BaseModel):
    action_id: Optional[int] = None
    verified_by: str
    verification_status: str
    notes: str = ""
    custom_resolution: str = ""
    metric_based: bool = False
    close_after_verify: bool = False


class TicketRequest(BaseModel):
    provider: str = "plane"
    note: str = ""
    force: bool = False
    source_url: str = ""


class TicketSyncRequest(BaseModel):
    note: str = ""
    source_url: str = ""


class ResolutionExtractRequest(BaseModel):
    summary: Optional[str] = None
    source_comment_id: Optional[str] = None
    verified: bool = True


class RemediationDecisionRequest(BaseModel):
    approved_by: str
    notes: str = ""
    source_url: str = ""


class PlaybookGenerationRequest(BaseModel):
    requested_by: str
    notes: str = ""
    source_url: str = ""


class PlaybookGenerationCallbackRequest(BaseModel):
    correlation_id: str
    status: str = "generated"
    title: str = ""
    description: str = ""
    summary: str = ""
    expected_outcome: str = ""
    preconditions: List[str] = Field(default_factory=list)
    playbook_yaml: str = ""
    playbook_ref: str = ""
    action_ref: str = ""
    provider_name: str = ""
    provider_run_id: str = ""
    error: str = ""
    metadata: Dict[str, object] = Field(default_factory=dict)


class AutomationActionTriggerRequest(BaseModel):
    approved_by: str
    notes: str = ""
    source_of_action: str = "event_driven_policy"


class RemediationRejectRequest(BaseModel):
    rejected_by: str
    notes: str = ""


class RelatedRecordsRequest(BaseModel):
    limit: int = Field(default=6, ge=1, le=24)
    knowledge_limit: int = Field(default=10, ge=1, le=20)


app = FastAPI(title="control-plane", version="0.1.0")
install_cors(app)
install_metrics(app, "control-plane")


PLAYBOOKS = {
    "scale_scscf": "/app/automation/ansible/playbooks/scale-scscf.yaml",
    "rate_limit_pcscf": "/app/automation/ansible/playbooks/rate-limit-pcscf.yaml",
    "quarantine_imsi": "/app/automation/ansible/playbooks/quarantine-imsi.yaml",
}
AI_PLAYBOOK_GENERATION_TOPIC = "aiops-ansible-playbook-generate-instruction"
AI_PLAYBOOK_GENERATION_PROVIDER = os.getenv("AI_PLAYBOOK_GENERATION_PROVIDER", "external generator").strip() or "external generator"
AI_PLAYBOOK_GENERATION_PREVIEW_CORRELATION_ID = "generated-at-publish-time"

WORKFLOW_STATE_OPTIONS = [
    NEW,
    RCA_GENERATED,
    REMEDIATION_SUGGESTED,
    AWAITING_APPROVAL,
    APPROVED,
    EXECUTING,
    EXECUTED,
    VERIFIED,
    CLOSED,
    RCA_REJECTED,
    EXECUTION_FAILED,
    VERIFICATION_FAILED,
    FALSE_POSITIVE,
    ESCALATED,
]


def _positive_int_from_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return value if value > 0 else default


def _non_negative_int_from_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return value if value >= 0 else default


def _bool_from_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name, "true" if default else "false").strip().lower()
    return raw_value in {"1", "true", "yes", "on"}


def _string_from_env(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


def _safe_imsi_for_automation(incident: Dict[str, object]) -> str:
    feature_snapshot = incident.get("feature_snapshot")
    if isinstance(feature_snapshot, dict):
        for key in ("imsi", "subscriber_imsi", "subscriber_id", "source_id"):
            candidate = str(feature_snapshot.get(key) or "").strip()
            if candidate:
                return candidate
    return _string_from_env("AAP_QUARANTINE_DEFAULT_IMSI", "001010000000001")


CONSOLE_SCENARIOS = set(console_scenario_names())
CONSOLE_CLUSTER_NAME = console_cluster_name()
CONSOLE_AUTO_REFRESH_SECONDS = _positive_int_from_env("CONSOLE_AUTO_REFRESH_SECONDS", 5)
CONSOLE_RECENT_INCIDENT_LIMIT = _positive_int_from_env("CONSOLE_RECENT_INCIDENT_LIMIT", 24)
UPSTREAM_TIMEOUT_SECONDS = float(os.getenv("CONSOLE_UPSTREAM_TIMEOUT_SECONDS", "20"))
HEALTH_PROBE_TIMEOUT_SECONDS = float(os.getenv("CONSOLE_HEALTH_PROBE_TIMEOUT_SECONDS", "5"))
SERVICE_SNAPSHOT_CACHE_SECONDS = float(os.getenv("CONSOLE_SERVICE_SNAPSHOT_CACHE_SECONDS", "10"))
AAP_JOB_TIMEOUT_SECONDS = _positive_int_from_env("AAP_JOB_TIMEOUT_SECONDS", 300)
AAP_JOB_POLL_SECONDS = _positive_int_from_env("AAP_JOB_POLL_SECONDS", 5)
FEATURE_GATEWAY_URL = feature_gateway_url().rstrip("/")
ANOMALY_SERVICE_URL = anomaly_service_url().rstrip("/")
RCA_SERVICE_URL = rca_service_url().rstrip("/")
PREDICTIVE_SERVICE_URL = (
    os.getenv("PREDICTIVE_FS_SERVICE_URL", "").strip()
    or predictive_service_url()
).rstrip("/")
_SERVICE_SNAPSHOT_CACHE_LOCK = threading.Lock()
_SERVICE_SNAPSHOT_CACHE: List[Dict[str, object]] | None = None
_SERVICE_SNAPSHOT_CACHE_EXPIRES_AT = 0.0


@app.on_event("startup")
def startup() -> None:
    init_db()
    _start_automation_bootstrap_worker()


def _automation_bootstrap_on_startup() -> bool:
    return _bool_from_env("AUTOMATION_BOOTSTRAP_ON_STARTUP", True)


def _automation_bootstrap_retry_seconds() -> int:
    return _positive_int_from_env("AUTOMATION_BOOTSTRAP_RETRY_SECONDS", 15)


def _automation_bootstrap_max_attempts() -> int:
    return _non_negative_int_from_env("AUTOMATION_BOOTSTRAP_MAX_ATTEMPTS", 0)


def _bootstrap_automation_once() -> bool:
    component_errors: List[str] = []
    for component_name, bootstrap in (
        ("aap", aap_bootstrap_resources),
        ("eda", eda_bootstrap_resources),
    ):
        try:
            bootstrap()
            record_integration(component_name, "bootstrapped")
        except (AAPAutomationError, EDAAutomationError) as exc:
            component_errors.append(f"{component_name}: {exc}")
            record_integration(component_name, "bootstrap_failed")
            logger.warning("Automatic %s bootstrap attempt failed: %s", component_name.upper(), exc)
        except Exception as exc:  # noqa: BLE001
            component_errors.append(f"{component_name}: {exc}")
            record_integration(component_name, "bootstrap_failed")
            logger.exception("Unexpected automatic %s bootstrap failure", component_name.upper())
    if component_errors:
        logger.warning("Automatic automation bootstrap incomplete: %s", " | ".join(component_errors))
        return False
    logger.info("Automatic automation bootstrap completed successfully.")
    return True


def _automation_bootstrap_worker() -> None:
    max_attempts = _automation_bootstrap_max_attempts()
    retry_seconds = _automation_bootstrap_retry_seconds()
    attempt = 0
    while True:
        attempt += 1
        if _bootstrap_automation_once():
            return
        if max_attempts > 0 and attempt >= max_attempts:
            logger.warning(
                "Automatic automation bootstrap exhausted %s attempts without full success.",
                max_attempts,
            )
            return
        time.sleep(retry_seconds)


def _start_automation_bootstrap_worker() -> None:
    global _AUTOMATION_BOOTSTRAP_STARTED
    if not _automation_bootstrap_on_startup():
        return
    with _AUTOMATION_BOOTSTRAP_LOCK:
        if _AUTOMATION_BOOTSTRAP_STARTED:
            return
        _AUTOMATION_BOOTSTRAP_STARTED = True
    thread = threading.Thread(target=_automation_bootstrap_worker, name="automation-bootstrap", daemon=True)
    thread.start()


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _titleize(value: str | None) -> str:
    return str(value or "unknown").replace("_", " ").strip().title()


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _plane_webhook_secret() -> str:
    return os.getenv("PLANE_WEBHOOK_SECRET", "").strip()


def _available_transition_targets(current_state: str) -> List[str]:
    current = normalize_workflow_state(current_state)
    return [state for state in WORKFLOW_STATE_OPTIONS if can_transition(current, state) and state != current]


def _transition_incident_with_audit(
    incident: Dict[str, object],
    target_state: str,
    actor: str,
    detail: str = "",
) -> Dict[str, object]:
    current_state = normalize_workflow_state(str(incident.get("status") or incident.get("workflow_state") or NEW))
    normalized_target = normalize_workflow_state(target_state)
    if not can_transition(current_state, normalized_target):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid workflow transition from {current_state} to {normalized_target}",
        )
    updated = transition_incident_state(str(incident.get("id")), normalized_target)
    if not updated:
        raise HTTPException(status_code=404, detail="Incident not found")
    if current_state != normalized_target:
        record_workflow_transition(current_state, normalized_target)
    record_audit(
        "workflow_transition",
        actor,
        {
            "from_state": current_state,
            "to_state": normalized_target,
            "detail": detail,
        },
        incident_id=str(incident.get("id")),
    )
    return updated


def _workflow_state_counts(incidents: List[Dict[str, object]]) -> List[Dict[str, object]]:
    counts: Dict[str, int] = {}
    for incident in incidents:
        state = normalize_workflow_state(str(incident.get("status") or incident.get("workflow_state") or NEW))
        counts[state] = counts.get(state, 0) + 1
    items = [{"state": state, "count": count, "plane_state": plane_state_for_workflow(state)} for state, count in counts.items()]
    items.sort(key=lambda item: (-int(item["count"]), str(item["state"])))
    return items


def _current_remediation_items(remediations: List[Dict[str, object]]) -> List[Dict[str, object]]:
    return [
        item
        for item in remediations
        if str(item.get("status") or "").lower() in {"available", "approved", "executing", "executed"}
    ]


def _workflow_payload(incident: Dict[str, object]) -> Dict[str, object]:
    incident_id = str(incident.get("id") or "")
    project = str(incident.get("project") or "ims-demo")
    all_incidents = list_incidents(project=project)
    audit_events = list_audit_events(limit=200, incident_id=incident_id)
    enriched_incident = _enrich_incident(incident, audit_events, all_incidents)
    rca_history = list_incident_rca(incident_id)
    remediations = list_incident_remediations(incident_id)
    actions = list_incident_actions(incident_id)
    verifications = list_incident_verifications(incident_id)
    tickets = list_incident_tickets(incident_id)
    resolution_extracts = list_ticket_resolution_extracts(incident_id)
    detailed_tickets = []
    for ticket in tickets:
        detailed_tickets.append(
            ticket
            | {
                "sync_events": list_ticket_sync_events(int(ticket["id"]))[:10],
                "comments": list_ticket_comments(int(ticket["id"]))[:10],
            }
        )
    current_ticket = next(
        (ticket for ticket in detailed_tickets if ticket.get("id") == incident.get("current_ticket_id")),
        detailed_tickets[0] if detailed_tickets else None,
    )
    return {
        "incident": enriched_incident,
        "rca_history": rca_history,
        "remediations": remediations,
        "current_remediations": _current_remediation_items(remediations),
        "actions": actions,
        "verifications": verifications,
        "tickets": detailed_tickets,
        "current_ticket": current_ticket,
        "resolution_extracts": resolution_extracts,
        "available_transitions": _available_transition_targets(str(enriched_incident.get("status") or NEW)),
        "plane_workflow_state": plane_state_for_workflow(str(enriched_incident.get("status") or NEW)),
    }


def _generate_and_store_remediations(incident_id: str, actor: str = "control-plane") -> List[Dict[str, object]]:
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    rca_payload = incident.get("rca_payload") or {}
    if not isinstance(rca_payload, dict) or not rca_payload:
        return []
    previous_state = normalize_workflow_state(str(incident.get("status") or NEW))
    suggestions = generate_remediation_suggestions(incident, rca_payload, remediation_success_rates())
    remediations = replace_remediations(incident_id, incident.get("current_rca_id"), suggestions)
    _publish_remediation_reasoning_records(get_incident(incident_id) or incident, remediations)
    if suggestions:
        record_audit(
            "remediations_generated",
            actor,
            {
                "count": len(suggestions),
                "top_suggestion": suggestions[0].get("title"),
            },
            incident_id=incident_id,
        )
        _publish_eda_event_best_effort(
            "remediations_generated",
            get_incident(incident_id) or incident,
            extra={
                "remediation_count": len(remediations),
                "top_remediation_title": str(suggestions[0].get("title") or ""),
            },
            remediations=remediations,
        )
        if previous_state != REMEDIATION_SUGGESTED:
            record_workflow_transition(previous_state, REMEDIATION_SUGGESTED)
            record_audit(
                "workflow_transition",
                actor,
                {
                    "from_state": previous_state,
                    "to_state": REMEDIATION_SUGGESTED,
                    "detail": "Deterministic remediation suggestions ranked from the RCA context.",
                },
                incident_id=incident_id,
            )
        updated = get_incident(incident_id)
        if updated:
            _transition_incident_with_audit(
                updated,
                AWAITING_APPROVAL,
                actor,
                "Remediation suggestions generated and awaiting operator approval.",
            )
    return remediations


def _request_ai_playbook_generation(
    incident: Dict[str, object],
    remediation: Dict[str, object],
    requested_by: str,
    notes: str,
    source_url: str,
) -> Dict[str, object]:
    if not _ai_playbook_generation_enabled():
        raise HTTPException(status_code=400, detail="AI playbook generation is disabled")
    if not _is_ai_playbook_generation_request(remediation):
        raise HTTPException(status_code=400, detail="Selected remediation is not an AI playbook generation request")
    if not isinstance(incident.get("rca_payload"), dict) or not incident.get("rca_payload"):
        raise HTTPException(status_code=400, detail="RCA must exist before requesting AI playbook generation")

    correlation_id = uuid.uuid4().hex
    instruction = _build_playbook_generation_instruction(incident, remediation, correlation_id, notes, source_url)
    try:
        publish_result = _publish_playbook_generation_instruction(correlation_id, instruction)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Failed to publish AI playbook generation instruction: {exc}") from exc

    metadata = _merge_remediation_metadata(
        remediation,
        {
            "ai_generated": True,
            "generation_kind": "request",
            "generation_status": "requested",
            "generation_provider": AI_PLAYBOOK_GENERATION_PROVIDER,
            "generation_correlation_id": correlation_id,
            "generation_error": "",
            "generation_requested_at": _now_iso(),
            "generation_requested_by": requested_by,
            "generation_notes": notes,
            "generation_source_url": source_url,
            "generation_topic": publish_result["topic"],
            "generation_instruction": instruction,
        },
    )
    updated_remediation = update_incident_remediation(
        str(incident.get("id") or ""),
        int(remediation.get("id") or 0),
        status="available",
        metadata=metadata,
    )
    if not updated_remediation:
        raise HTTPException(status_code=500, detail="Failed to persist AI playbook generation request")
    record_audit(
        "ai_playbook_generation_requested",
        requested_by,
        {
            "remediation_id": updated_remediation.get("id"),
            "correlation_id": correlation_id,
            "topic": publish_result["topic"],
            "source_url": source_url,
            "notes": notes,
        },
        incident_id=str(incident.get("id") or ""),
    )
    return {
        "remediation": updated_remediation,
        "publish": publish_result,
    }


def _preview_ai_playbook_generation_instruction(
    incident: Dict[str, object],
    remediation: Dict[str, object],
    notes: str,
    source_url: str,
) -> Dict[str, object]:
    if not _is_ai_playbook_generation_request(remediation):
        raise HTTPException(status_code=400, detail="Selected remediation is not an AI playbook generation request")
    if not isinstance(incident.get("rca_payload"), dict) or not incident.get("rca_payload"):
        raise HTTPException(status_code=400, detail="RCA must exist before previewing AI playbook generation")

    metadata = _remediation_metadata(remediation)
    correlation_id = str(metadata.get("generation_correlation_id") or "").strip() or AI_PLAYBOOK_GENERATION_PREVIEW_CORRELATION_ID
    return {
        "instruction": _build_playbook_generation_instruction(incident, remediation, correlation_id, notes, source_url),
        "correlation_id": correlation_id,
        "draft": correlation_id == AI_PLAYBOOK_GENERATION_PREVIEW_CORRELATION_ID,
    }


def _apply_ai_playbook_generation_callback(
    incident_id: str,
    payload: PlaybookGenerationCallbackRequest,
) -> Dict[str, object]:
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    remediation = _find_ai_playbook_generation_remediation(incident_id, payload.correlation_id)
    if remediation is None:
        raise HTTPException(status_code=404, detail="No AI playbook generation request matches this correlation id")

    provider_name = str(payload.provider_name or AI_PLAYBOOK_GENERATION_PROVIDER).strip() or AI_PLAYBOOK_GENERATION_PROVIDER
    normalized_status = str(payload.status or "generated").strip().lower() or "generated"
    current_revision = int((get_incident(incident_id) or incident).get("workflow_revision") or remediation.get("based_on_revision") or 1)
    metadata = _merge_remediation_metadata(
        remediation,
        {
            "ai_generated": True,
            "generation_provider": provider_name,
            "generation_correlation_id": payload.correlation_id,
            "generation_updated_at": _now_iso(),
            "provider_run_id": str(payload.provider_run_id or "").strip(),
        },
    )

    if normalized_status == "failed":
        updated = update_incident_remediation(
            incident_id,
            int(remediation.get("id") or 0),
            based_on_revision=current_revision,
            metadata=metadata
            | {
                "generation_kind": "request",
                "generation_status": "failed",
                "generation_error": str(payload.error or "External playbook generation failed").strip(),
            },
            playbook_yaml="",
            status="available",
        )
        record_audit(
            "ai_playbook_generation_failed",
            provider_name,
            {
                "remediation_id": remediation.get("id"),
                "correlation_id": payload.correlation_id,
                "error": str(payload.error or "External playbook generation failed").strip(),
            },
            incident_id=incident_id,
        )
        if not updated:
            raise HTTPException(status_code=500, detail="Failed to persist AI playbook generation failure")
        return updated

    if normalized_status not in {"generated", "ready", "completed", "success"}:
        raise HTTPException(status_code=400, detail=f"Unsupported playbook generation status '{payload.status}'")

    playbook_yaml = str(payload.playbook_yaml or "").strip()
    if not playbook_yaml:
        raise HTTPException(status_code=400, detail="playbook_yaml is required when status=generated")

    action_ref = str(payload.action_ref or "").strip() or _generated_playbook_action_ref(payload.correlation_id)
    playbook_ref = str(payload.playbook_ref or "").strip() or action_ref
    updated = update_incident_remediation(
        incident_id,
        int(remediation.get("id") or 0),
        based_on_revision=current_revision,
        title=str(payload.title or remediation.get("title") or "AI generated Ansible playbook").strip(),
        suggestion_type="ansible_playbook",
        description=str(
            payload.description
            or payload.summary
            or remediation.get("description")
            or "AI-generated playbook returned from the external generator workflow."
        ).strip(),
        risk_level=str(remediation.get("risk_level") or "medium"),
        confidence=max(float(remediation.get("confidence") or 0.0), 0.55),
        automation_level="human_approved",
        requires_approval=True,
        playbook_ref=playbook_ref,
        action_ref=action_ref,
        preconditions=_string_list(payload.preconditions) or _string_list(remediation.get("preconditions")),
        expected_outcome=str(payload.expected_outcome or remediation.get("expected_outcome") or "").strip(),
        status="available",
        metadata=metadata
        | {
            "generation_kind": "generated",
            "generation_status": "generated",
            "generation_error": "",
            "generated_action_ref": action_ref,
            "generated_playbook_ref": playbook_ref,
        },
        playbook_yaml=playbook_yaml,
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to persist AI-generated playbook")
    record_audit(
        "ai_playbook_generated",
        provider_name,
        {
            "remediation_id": updated.get("id"),
            "correlation_id": payload.correlation_id,
            "action_ref": action_ref,
            "playbook_ref": playbook_ref,
            "provider_run_id": str(payload.provider_run_id or "").strip(),
        },
        incident_id=incident_id,
    )
    return updated


def _find_matching_remediation(incident_id: str, action_ref: str) -> Dict[str, object] | None:
    normalized_action = str(action_ref or "").strip()
    if not normalized_action:
        return None
    for remediation in list_incident_remediations(incident_id):
        if str(remediation.get("action_ref") or "") == normalized_action:
            return remediation
        if str(remediation.get("playbook_ref") or "") == normalized_action:
            return remediation
    return None


def _latest_action_for_ref(incident_id: str, action_ref: str) -> Dict[str, object] | None:
    normalized_action = str(action_ref or "").strip()
    if not normalized_action:
        return None
    for action in list_incident_actions(incident_id):
        result_json = action.get("result_json") if isinstance(action.get("result_json"), dict) else {}
        result_action = str(result_json.get("action_ref") or "")
        if result_action == normalized_action:
            return action
    return None


def _text_from_rich_comment(value: object) -> str:
    if isinstance(value, str):
        text = html.unescape(value)
        if "<" in text and ">" in text:
            text = (
                text.replace("<br/>", "\n")
                .replace("<br>", "\n")
                .replace("</p>", "\n")
                .replace("</div>", "\n")
                .replace("</li>", "\n")
                .replace("<li>", "- ")
            )
            text = re.sub(r"<[^>]+>", "", text)
        return "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()
    if isinstance(value, dict):
        fragments = []
        text = value.get("text")
        if text:
            fragments.append(str(text))
        for item in value.get("content") or []:
            fragments.append(_text_from_rich_comment(item))
        return " ".join(fragment for fragment in fragments if fragment).strip()
    if isinstance(value, list):
        return " ".join(_text_from_rich_comment(item) for item in value if item).strip()
    return ""


def _plane_actor_name(payload: Dict[str, Any], data: Dict[str, Any]) -> str:
    actor = data.get("actor")
    if isinstance(actor, dict):
        for field in ("display_name", "first_name", "email", "id"):
            value = str(actor.get(field) or "").strip()
            if value:
                return value

    activity = payload.get("activity") or {}
    if isinstance(activity, dict):
        activity_actor = activity.get("actor")
        if isinstance(activity_actor, dict):
            for field in ("display_name", "first_name", "email", "id"):
                value = str(activity_actor.get(field) or "").strip()
                if value:
                    return value
        elif activity_actor:
            value = str(activity_actor).strip()
            if value:
                return value

    if actor:
        value = str(actor).strip()
        if value:
            return value

    for fallback in ("created_by", "updated_by"):
        value = str(data.get(fallback) or "").strip()
        if value:
            return value
    return "plane-user"


def _string_list(value: object) -> List[str]:
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        text = str(item or "").strip()
        if text:
            items.append(text)
    return items


def _feature_signal_summary(features: Dict[str, object]) -> str:
    keys = (
        "register_rate",
        "invite_rate",
        "bye_rate",
        "error_4xx_ratio",
        "error_5xx_ratio",
        "latency_p95",
        "retransmission_count",
        "payload_variance",
    )
    fragments = []
    for key in keys:
        if key not in features:
            continue
        value = features.get(key)
        if value in {None, ""}:
            continue
        fragments.append(f"{key}={value}")
    return ", ".join(fragments[:8]) or "no summarized feature signals"


def _ai_playbook_generation_enabled() -> bool:
    return _bool_from_env("AI_PLAYBOOK_GENERATION_ENABLED", True)


def _ai_playbook_generation_topic() -> str:
    return _string_from_env("AI_PLAYBOOK_GENERATION_KAFKA_TOPIC", AI_PLAYBOOK_GENERATION_TOPIC)


def _ai_playbook_generation_bootstrap_servers() -> List[str]:
    raw = (
        os.getenv("AI_PLAYBOOK_GENERATION_KAFKA_BOOTSTRAP_SERVERS", "").strip()
        or os.getenv("KAFKA_BOOTSTRAP_SERVERS", "").strip()
        or "ims-release-kafka-kafka-bootstrap.ims-demo-lab.svc.cluster.local:9092"
    )
    return [item.strip() for item in raw.split(",") if item.strip()]


def _ai_playbook_generation_client_id() -> str:
    return _string_from_env("AI_PLAYBOOK_GENERATION_KAFKA_CLIENT_ID", "ims-control-plane-playbook-generator")


def _ai_playbook_generation_security_protocol() -> str:
    return _string_from_env("AI_PLAYBOOK_GENERATION_KAFKA_SECURITY_PROTOCOL", "PLAINTEXT")


def _public_control_plane_base_url() -> str:
    return (
        os.getenv("PLAYBOOK_GENERATION_CALLBACK_BASE_URL", "").strip()
        or os.getenv("CONTROL_PLANE_PUBLIC_URL", "").strip()
        or control_plane_url()
    ).rstrip("/")


def _playbook_generation_callback_url(incident_id: str) -> str:
    return f"{_public_control_plane_base_url()}/incidents/{incident_id}/playbook-generation/callback"


def _remediation_metadata(remediation: Dict[str, object] | None) -> Dict[str, object]:
    if not remediation:
        return {}
    value = remediation.get("metadata")
    return dict(value) if isinstance(value, dict) else {}


def _merge_remediation_metadata(remediation: Dict[str, object] | None, extra: Dict[str, object]) -> Dict[str, object]:
    metadata = _remediation_metadata(remediation)
    metadata.update(extra)
    return metadata


def _is_ai_playbook_generation_request(remediation: Dict[str, object] | None) -> bool:
    if not remediation:
        return False
    metadata = _remediation_metadata(remediation)
    return (
        str(remediation.get("action_ref") or "") == AI_PLAYBOOK_GENERATION_ACTION
        or str(metadata.get("generation_kind") or "") == "request"
    )


def _generated_playbook_yaml(remediation: Dict[str, object] | None) -> str:
    if not remediation:
        return ""
    direct_yaml = str(remediation.get("playbook_yaml") or "").strip()
    if direct_yaml:
        return direct_yaml
    metadata = _remediation_metadata(remediation)
    return str(metadata.get("playbook_yaml") or "").strip()


def _candidate_remediation_titles(incident_id: str, ignored_id: int | None = None) -> List[str]:
    items: List[str] = []
    for remediation in list_incident_remediations(incident_id):
        if ignored_id is not None and int(remediation.get("id") or 0) == ignored_id:
            continue
        if _is_ai_playbook_generation_request(remediation):
            continue
        title = str(remediation.get("title") or "").strip()
        if title:
            items.append(title)
    return items[:5]


def _build_playbook_generation_instruction(
    incident: Dict[str, object],
    remediation: Dict[str, object],
    correlation_id: str,
    notes: str,
    source_url: str,
) -> str:
    incident_id = str(incident.get("id") or "")
    rca_payload = incident.get("rca_payload") if isinstance(incident.get("rca_payload"), dict) else {}
    feature_snapshot = incident.get("feature_snapshot") if isinstance(incident.get("feature_snapshot"), dict) else {}
    retrieved_documents = rca_payload.get("retrieved_documents") if isinstance(rca_payload.get("retrieved_documents"), list) else []
    evidence_refs = [
        str(item.get("reference") or item.get("title") or "").strip()
        for item in retrieved_documents
        if isinstance(item, dict)
    ]
    candidate_titles = _candidate_remediation_titles(incident_id, ignored_id=int(remediation.get("id") or 0))
    callback_url = _playbook_generation_callback_url(incident_id)
    lines = [
        f"Generate a reviewable Ansible playbook for IMS incident {incident_id}.",
        "",
        "Incident context:",
        f"- project: {incident.get('project') or 'ims-demo'}",
        f"- anomaly_type: {canonical_anomaly_type(str(incident.get('anomaly_type') or NORMAL_ANOMALY_TYPE))}",
        f"- severity: {incident.get('severity') or 'Unknown'}",
        f"- predicted_confidence: {_incident_confidence(incident):.2f}",
        f"- workflow_revision: {int(incident.get('workflow_revision') or 1)}",
        f"- feature_signals: {_feature_signal_summary(feature_snapshot)}",
        "",
        "RCA:",
        f"- root_cause: {str(rca_payload.get('root_cause') or incident.get('recommendation') or 'Not available').strip()}",
        f"- explanation: {str(rca_payload.get('explanation') or rca_payload.get('recommendation') or 'Not available').strip()}",
        f"- recommended_response: {str(rca_payload.get('recommendation') or incident.get('recommendation') or 'Not available').strip()}",
    ]
    if candidate_titles:
        lines.extend(
            [
                "",
                "Existing remediation context:",
                f"- current ranked options: {'; '.join(candidate_titles)}",
            ]
        )
    if evidence_refs:
        lines.extend(
            [
                "",
                "Evidence references:",
                f"- supporting_documents: {'; '.join(evidence_refs[:4])}",
            ]
        )
    if notes.strip():
        lines.extend(["", "Operator note:", f"- {notes.strip()}"])
    if source_url.strip():
        lines.extend(["", "Operator context link:", f"- {source_url.strip()}"])
    lines.extend(
        [
            "",
            "Generation requirements:",
            "- return one safe, idempotent Ansible playbook in YAML",
            "- prefer explicit OpenShift or Kubernetes object changes with clear guardrails",
            "- include concise title, summary, preconditions, and expected outcome",
            "- avoid destructive, irreversible, or environment-wide changes",
            "",
            "Callback contract:",
            f"- callback_url: {callback_url}",
            f"- correlation_id: {correlation_id}",
            "- authenticate using the control-plane API key already provisioned for your service",
            "- POST JSON with fields: correlation_id, status, title, description, summary, expected_outcome, preconditions, playbook_yaml, playbook_ref, action_ref, provider_name, provider_run_id, error, metadata",
            "- use status=generated on success or status=failed with error details on failure",
        ]
    )
    return "\n".join(lines).strip()


def _publish_playbook_generation_instruction(correlation_id: str, instruction: str) -> Dict[str, object]:
    from kafka import KafkaProducer  # pyright: ignore[reportMissingImports]

    producer = KafkaProducer(
        bootstrap_servers=_ai_playbook_generation_bootstrap_servers(),
        client_id=_ai_playbook_generation_client_id(),
        security_protocol=_ai_playbook_generation_security_protocol(),
        acks=os.getenv("AI_PLAYBOOK_GENERATION_KAFKA_ACKS", "all"),
        retries=max(int(os.getenv("AI_PLAYBOOK_GENERATION_KAFKA_RETRIES", "3")), 0),
        linger_ms=max(int(os.getenv("AI_PLAYBOOK_GENERATION_KAFKA_LINGER_MS", "0")), 0),
        request_timeout_ms=max(int(os.getenv("AI_PLAYBOOK_GENERATION_KAFKA_REQUEST_TIMEOUT_MS", "20000")), 1_000),
        max_block_ms=max(int(os.getenv("AI_PLAYBOOK_GENERATION_KAFKA_MAX_BLOCK_MS", "20000")), 1_000),
        value_serializer=lambda value: str(value).encode("utf-8"),
        key_serializer=lambda value: str(value).encode("utf-8"),
    )
    try:
        send_timeout = float(os.getenv("AI_PLAYBOOK_GENERATION_KAFKA_SEND_TIMEOUT_SECONDS", "20"))
        flush_timeout = float(os.getenv("AI_PLAYBOOK_GENERATION_KAFKA_FLUSH_TIMEOUT_SECONDS", "20"))
        topic = _ai_playbook_generation_topic()
        producer.send(topic, key=correlation_id, value=instruction).get(timeout=send_timeout)
        producer.flush(timeout=flush_timeout)
    finally:
        producer.close()
    return {
        "topic": _ai_playbook_generation_topic(),
        "correlation_id": correlation_id,
        "bootstrap_servers": _ai_playbook_generation_bootstrap_servers(),
        "instruction": instruction,
        "instruction_preview": instruction[:400],
    }


def _generated_playbook_action_ref(correlation_id: str) -> str:
    return f"ai_generated_playbook_{correlation_id[:12]}"


def _find_ai_playbook_generation_remediation(incident_id: str, correlation_id: str) -> Dict[str, object] | None:
    for remediation in list_incident_remediations(incident_id):
        metadata = _remediation_metadata(remediation)
        if str(metadata.get("generation_correlation_id") or "") == correlation_id:
            return remediation
    return None


def _incident_category(incident: Dict[str, object]) -> str:
    anomaly_type = canonical_anomaly_type(str(incident.get("anomaly_type") or NORMAL_ANOMALY_TYPE))
    definition = scenario_definition(anomaly_type)
    return str(definition.get("category") or "").strip().lower()


def _related_context_query(incident: Dict[str, object]) -> str:
    features = incident.get("feature_snapshot") or {}
    if not isinstance(features, dict):
        features = {}
    anomaly_type = canonical_anomaly_type(str(incident.get("anomaly_type") or NORMAL_ANOMALY_TYPE))
    scenario_name = str(features.get("scenario_name") or "")
    conditions = ", ".join(_string_list(features.get("contributing_conditions")))
    signal_summary = _feature_signal_summary(features)
    recommendation = str(incident.get("recommendation") or "")
    return " | ".join(
        part
        for part in [
            f"incident_id={incident.get('id')}",
            f"anomaly_type={anomaly_type}",
            f"scenario_name={scenario_name}",
            f"signals={signal_summary}",
            f"conditions={conditions}",
            f"recommendation={recommendation}",
        ]
        if part
    )


def _publish_incident_evidence_record(incident: Dict[str, object]) -> None:
    incident_id = str(incident.get("id") or "")
    if not incident_id:
        return
    features = incident.get("feature_snapshot") or {}
    if not isinstance(features, dict):
        features = {}
    anomaly_type = canonical_anomaly_type(str(incident.get("anomaly_type") or NORMAL_ANOMALY_TYPE))
    severity = _incident_severity_label(incident)
    scenario_name = str(features.get("scenario_name") or "")
    contributing_conditions = _string_list(features.get("contributing_conditions"))
    signal_summary = _feature_signal_summary(features)
    content = {
        "incident_id": incident_id,
        "stage": "evidence",
        "project": str(incident.get("project") or "ims-demo"),
        "anomaly_type": anomaly_type,
        "severity": severity,
        "status": str(incident.get("status") or NEW),
        "scenario_name": scenario_name,
        "contributing_conditions": contributing_conditions,
        "feature_window_id": incident.get("feature_window_id"),
        "feature_snapshot": features,
        "predicted_confidence": _incident_confidence(incident),
        "top_classes": incident.get("top_classes") or [],
    }
    embedding_text = (
        f"Evidence incident {incident_id}. "
        f"Anomaly type {anomaly_type}. "
        f"Scenario {scenario_name or 'unknown'}. "
        f"Contributing conditions: {'; '.join(contributing_conditions) or 'none'}. "
        f"Summarized feature signals: {signal_summary}."
    )
    publish_semantic_record(
        collection_name="incident_evidence",
        reference=f"evidence/{incident_id}.json",
        title=f"Incident evidence {incident_id}",
        content=content,
        doc_type="incident_evidence",
        embedding_text=embedding_text,
        metadata={
            "stage": "evidence",
            "incident_id": incident_id,
            "project": incident.get("project"),
            "created_at": incident.get("created_at"),
            "status": incident.get("status"),
            "knowledge_weight": 0.55,
        },
    )


def _publish_rca_reasoning_record(incident: Dict[str, object], rca_payload: Dict[str, object]) -> None:
    incident_id = str(incident.get("id") or "")
    if not incident_id:
        return
    history = list_incident_rca(incident_id)
    current_rca_id = incident.get("current_rca_id")
    current_rca = next((item for item in history if current_rca_id and item.get("id") == current_rca_id), history[0] if history else None)
    evidence_refs = [str(item.get("reference") or "") for item in rca_payload.get("evidence") or [] if isinstance(item, dict)]
    retrieved_refs = [
        str(item.get("reference") or "")
        for item in rca_payload.get("retrieved_documents") or []
        if isinstance(item, dict)
    ]
    content = {
        "incident_id": incident_id,
        "parent_id": str(current_rca.get("id") if current_rca else current_rca_id or incident_id),
        "stage": "rca",
        "record_status": "active",
        "root_cause": str(rca_payload.get("root_cause") or ""),
        "recommendation": str(rca_payload.get("recommendation") or ""),
        "confidence": float(rca_payload.get("confidence") or 0.0),
        "evidence_references": evidence_refs,
        "retrieved_documents": retrieved_refs,
    }
    embedding_text = (
        f"RCA incident {incident_id}. "
        f"Root cause summary: {rca_payload.get('root_cause') or 'unknown'}. "
        f"Causal reasoning recommendation: {rca_payload.get('recommendation') or 'none'}. "
        f"Evidence references: {'; '.join(evidence_refs) or 'none'}."
    )
    publish_semantic_record(
        collection_name="incident_reasoning",
        reference=f"reasoning/{incident_id}-rca-{current_rca.get('id') if current_rca else 'current'}.json",
        title=f"RCA reasoning {incident_id}",
        content=content,
        doc_type="incident_reasoning",
        embedding_text=embedding_text,
        metadata={
            "stage": "rca",
            "incident_id": incident_id,
            "parent_id": incident_id,
            "project": incident.get("project"),
            "created_at": current_rca.get("created_at") if current_rca else incident.get("updated_at"),
            "status": "active",
            "category": canonical_anomaly_type(str(incident.get("anomaly_type") or "")),
            "knowledge_weight": max(0.6, min(float(rca_payload.get("confidence") or 0.0), 1.0)),
        },
    )


def _publish_remediation_reasoning_records(incident: Dict[str, object], remediations: List[Dict[str, object]]) -> None:
    incident_id = str(incident.get("id") or "")
    if not incident_id:
        return
    for remediation in remediations:
        remediation_id = remediation.get("id")
        if remediation_id is None:
            continue
        preconditions = _string_list(remediation.get("preconditions"))
        embedding_text = (
            f"Remediation incident {incident_id}. "
            f"Action title: {remediation.get('title') or 'unknown'}. "
            f"Action type: {remediation.get('suggestion_type') or remediation.get('action_mode') or 'manual'}. "
            f"Preconditions: {'; '.join(preconditions) or 'none'}. "
            f"Expected outcome: {remediation.get('expected_outcome') or 'none'}."
        )
        content = {
            "incident_id": incident_id,
            "parent_id": str(remediation.get("rca_id") or incident.get("current_rca_id") or incident_id),
            "stage": "remediation",
            "remediation_id": remediation_id,
            "title": remediation.get("title"),
            "description": remediation.get("description"),
            "suggestion_type": remediation.get("suggestion_type"),
            "action_ref": remediation.get("action_ref"),
            "playbook_ref": remediation.get("playbook_ref"),
            "preconditions": preconditions,
            "expected_outcome": remediation.get("expected_outcome"),
            "risk_level": remediation.get("risk_level"),
            "confidence": remediation.get("confidence"),
            "rank_score": remediation.get("rank_score"),
            "status": remediation.get("status"),
        }
        publish_semantic_record(
            collection_name="incident_reasoning",
            reference=f"reasoning/{incident_id}-remediation-{remediation_id}.json",
            title=f"Remediation {remediation_id} for {incident_id}",
            content=content,
            doc_type="incident_remediation",
            embedding_text=embedding_text,
            metadata={
                "stage": "remediation",
                "incident_id": incident_id,
                "parent_id": remediation.get("rca_id") or incident.get("current_rca_id"),
                "project": incident.get("project"),
                "created_at": remediation.get("created_at"),
                "status": remediation.get("status"),
                "category": canonical_anomaly_type(str(incident.get("anomaly_type") or "")),
                "suggestion_type": remediation.get("suggestion_type"),
                "knowledge_weight": max(0.45, min(float(remediation.get("confidence") or 0.0), 1.0)),
            },
        )


def _publish_resolution_record(
    incident: Dict[str, object],
    verification: Dict[str, object],
    extract: Dict[str, object],
    action: Dict[str, object] | None = None,
    ticket: Dict[str, object] | None = None,
) -> None:
    incident_id = str(incident.get("id") or "")
    if not incident_id:
        return
    summary = str(extract.get("summary") or verification.get("custom_resolution") or verification.get("notes") or "").strip()
    if not summary:
        return
    resolution_type = "verified_resolution" if bool(extract.get("verified")) else "resolution_candidate"
    operator_notes = str(verification.get("notes") or "")
    action_summary = str((action or {}).get("result_summary") or "")
    content = {
        "incident_id": incident_id,
        "parent_id": str((action or {}).get("id") or incident.get("current_rca_id") or incident_id),
        "stage": "resolution",
        "verified": bool(extract.get("verified")),
        "verified_by": verification.get("verified_by"),
        "resolution_type": resolution_type,
        "resolution_summary": summary,
        "operator_notes": operator_notes,
        "action_summary": action_summary,
        "ticket_id": (ticket or {}).get("id"),
        "ticket_provider": (ticket or {}).get("provider"),
    }
    embedding_text = (
        f"Resolution incident {incident_id}. "
        f"Actual fix applied: {summary}. "
        f"Validation outcome: {verification.get('verification_status') or 'unknown'}. "
        f"Why it worked: {operator_notes or action_summary or 'not recorded'}."
    )
    publish_semantic_record(
        collection_name="incident_resolution",
        reference=f"resolution/{incident_id}-{extract.get('id')}.json",
        title=f"Resolution {incident_id}",
        content=content,
        doc_type=resolution_type,
        embedding_text=embedding_text,
        metadata={
            "stage": "resolution",
            "incident_id": incident_id,
            "parent_id": (action or {}).get("id") or incident.get("current_rca_id"),
            "project": incident.get("project"),
            "created_at": extract.get("created_at") or _now_iso(),
            "status": "verified" if bool(extract.get("verified")) else "candidate",
            "verified": bool(extract.get("verified")),
            "verified_by": verification.get("verified_by"),
            "resolution_type": resolution_type,
            "knowledge_weight": extract.get("knowledge_weight"),
            "success_score": extract.get("success_rate"),
        },
    )


def _categorize_related_documents(documents: List[Dict[str, object]]) -> Dict[str, List[Dict[str, object]]]:
    related = {
        "evidence": [],
        "reasoning": [],
        "resolution": [],
        "knowledge": [],
    }
    for document in documents:
        collection = str(document.get("collection") or "")
        if collection == "incident_evidence":
            related["evidence"].append(document)
        elif collection == "incident_reasoning":
            related["reasoning"].append(document)
        elif collection == "incident_resolution":
            related["resolution"].append(document)
        elif collection == RUNBOOK_COLLECTION:
            related["knowledge"].append(document)
    return related


def _maybe_create_resolution_extract(
    incident: Dict[str, object],
    verification: Dict[str, object],
    action: Dict[str, object] | None = None,
    ticket: Dict[str, object] | None = None,
    source_comment_id: str | None = None,
    summary_override: str | None = None,
) -> Dict[str, object] | None:
    status = str(verification.get("verification_status") or "").strip().lower()
    if status != "verified":
        return None
    action_summary = str((action or {}).get("result_summary") or "").strip()
    summary = str(
        summary_override
        or verification.get("custom_resolution")
        or verification.get("notes")
        or action_summary
    ).strip()
    if not summary:
        return None
    quality = resolution_quality(
        bool(verification.get("metric_based")),
        str(verification.get("notes") or ""),
        str(verification.get("custom_resolution") or summary),
    )
    knowledge_weight = 1.0 if quality == "high" else 0.75 if quality == "medium" else 0.55
    extract = create_ticket_resolution_extract(
        incident_id=str(incident.get("id")),
        ticket_id=int(ticket["id"]) if ticket and ticket.get("id") else None,
        source_comment_id=source_comment_id,
        summary=summary,
        verified=True,
        verification_quality=quality,
        knowledge_weight=knowledge_weight,
        success_rate=1.0,
        last_validated_at=_now_iso(),
    )
    _publish_resolution_record(incident, verification, extract, action=action, ticket=ticket)
    return extract


def _sync_ticket_provider(
    incident: Dict[str, object],
    provider_name: str,
    note: str = "",
    force: bool = False,
    source_url: str = "",
) -> Dict[str, object]:
    workflow = _workflow_payload(incident)
    provider = get_ticket_provider(provider_name)
    existing_ticket = next((ticket for ticket in workflow["tickets"] if str(ticket.get("provider")) == provider_name), None)
    existing_metadata = (existing_ticket or {}).get("metadata") if isinstance(existing_ticket, dict) else {}
    if not isinstance(existing_metadata, dict):
        existing_metadata = {}
    reference_url = str(source_url or existing_metadata.get("source_url") or "").strip()
    if existing_ticket:
        result = provider.sync_ticket(incident, workflow, existing_ticket, note=note, source_url=reference_url)
    else:
        result = provider.create_ticket(incident, workflow, note=note, force=force, source_url=reference_url)
    status = str(result.get("status") or "unknown")
    record_ticket_sync(provider_name, "outbound", status)
    if status in {"created", "synced"}:
        ticket = upsert_incident_ticket(
            incident_id=str(incident.get("id")),
            provider=provider_name,
            external_key=str(result.get("external_key") or result.get("external_id") or ""),
            external_id=str(result.get("external_id") or ""),
            workspace_id=str(result.get("workspace_id") or ""),
            project_id=str(result.get("project_id") or ""),
            status=str(result.get("ticket_status") or plane_state_for_workflow(str(incident.get("status") or NEW))),
            url=str(result.get("url") or ""),
            title=str(result.get("title") or ""),
            sync_state=status,
            last_synced_revision=int(incident.get("workflow_revision") or 1),
            metadata={
                "mode": result.get("mode"),
                "raw": result.get("raw", {}),
                "source_url": reference_url,
            },
        )
        payload_hash = hashlib.sha256(json.dumps(result, sort_keys=True).encode("utf-8")).hexdigest()
        sync_event = record_ticket_sync_event(
            int(ticket["id"]),
            "outbound",
            f"{provider_name}_sync",
            None,
            payload_hash,
            status,
            result,
        )
        comment_payload = result.get("comment")
        if not isinstance(comment_payload, dict) and str(result.get("note") or note).strip():
            comment_payload = {
                "body": str(result.get("note") or note).strip(),
                "author": "IMS Platform",
                "comment_type": "operator_update",
            }
        if isinstance(comment_payload, dict):
            comment_body = str(comment_payload.get("body") or result.get("note") or note).strip()
            if comment_body:
                comment_external_id = str(comment_payload.get("external_comment_id") or "").strip()
                if not comment_external_id:
                    comment_external_id = f"{provider_name}-outbound-{sync_event.get('id') or uuid.uuid4().hex}"
                upsert_ticket_comment(
                    ticket_id=int(ticket["id"]),
                    external_comment_id=comment_external_id,
                    author=str(comment_payload.get("author") or "IMS Platform"),
                    body=comment_body,
                    comment_type=str(comment_payload.get("comment_type") or "operator_update"),
                )
        record_audit(
            "ticket_synced" if existing_ticket else "ticket_created",
            "operator",
            {
                "provider": provider_name,
                "ticket_id": ticket.get("id"),
                "external_key": ticket.get("external_key"),
                "status": status,
                "note": note.strip() or None,
                "sync_event_id": sync_event.get("id") if sync_event else None,
            },
            incident_id=str(incident.get("id")),
        )
        return ticket | {"operation": result}
    return result


def _ticket_note(title: str, fields: List[tuple[str, object]]) -> str:
    lines = [title]
    for label, value in fields:
        rendered = str(value or "").strip()
        if rendered:
            lines.append(f"{label}: {rendered}")
    return "\n".join(lines)


def _current_ticket_from_workflow(workflow: Dict[str, object]) -> Dict[str, object] | None:
    current_ticket = workflow.get("current_ticket")
    if isinstance(current_ticket, dict) and current_ticket:
        return current_ticket
    tickets = workflow.get("tickets") or []
    for ticket in tickets:
        if isinstance(ticket, dict) and ticket:
            return ticket
    return None


def _sync_current_ticket_best_effort(
    incident: Dict[str, object],
    note: str,
    actor: str,
    reason: str,
) -> Dict[str, object] | None:
    incident_id = str(incident.get("id") or "")
    if not incident_id:
        return None
    workflow = _workflow_payload(incident)
    current_ticket = _current_ticket_from_workflow(workflow)
    provider_name = str((current_ticket or {}).get("provider") or "").strip().lower()
    if not provider_name:
        return None
    try:
        return _sync_ticket_provider(incident, provider_name, note=note, force=True)
    except Exception as exc:
        logger.warning("Ticket sync failed for incident %s during %s: %s", incident_id, reason, exc)
        record_ticket_sync(provider_name, "outbound", "failed")
        record_audit(
            "ticket_sync_failed",
            actor,
            {"provider": provider_name, "reason": reason, "detail": str(exc)},
            incident_id=incident_id,
        )
        return None


def _list_automation_actions() -> List[Dict[str, object]]:
    mode = _automation_mode()
    actions = []
    for name, playbook in PLAYBOOKS.items():
        uses_aap = aap_action_supported(name)
        trigger_modes = ["manual_ui"]
        if name == "rate_limit_pcscf":
            trigger_modes.append("event_driven")
        actions.append(
            {
                "action": name,
                "playbook": playbook,
                "exists": os.path.exists(playbook),
                "automation_mode": "aap" if uses_aap else mode,
                "automation_enabled": uses_aap or mode in {"simulate", "execute"},
                "trigger_modes": trigger_modes,
            }
        )
    return actions


def _aap_extra_vars_for_action(
    action_ref: str,
    incident: Dict[str, object],
    remediation: Dict[str, object] | None,
    approved_by: str,
    notes: str,
) -> Dict[str, object]:
    base = {
        "incident_id": str(incident.get("id") or ""),
        "approved_by": approved_by,
        "approval_notes": notes,
        "workflow_revision": int(incident.get("workflow_revision") or 1),
        "remediation_id": int(remediation["id"]) if remediation and remediation.get("id") else None,
        "remediation_title": str((remediation or {}).get("title") or ""),
    }
    if action_ref == "scale_scscf":
        return base | {
            "target_namespace": os.getenv("AAP_SCALE_SCSCF_NAMESPACE", "ims-demo-lab"),
            "target_deployment": os.getenv("AAP_SCALE_SCSCF_DEPLOYMENT", "ims-scscf"),
            "target_replicas": _positive_int_from_env("AAP_SCALE_SCSCF_REPLICAS", 2),
        }
    if action_ref == "rate_limit_pcscf":
        return base | {
            "target_namespace": _string_from_env("AAP_RATE_LIMIT_PCSCF_NAMESPACE", "ims-demo-lab"),
            "target_deployment": _string_from_env("AAP_RATE_LIMIT_PCSCF_DEPLOYMENT", "ims-pcscf"),
            "annotation_key": _string_from_env("AAP_RATE_LIMIT_PCSCF_ANNOTATION_KEY", "ims.demo/rate-limit-review"),
            "annotation_value": _string_from_env("AAP_RATE_LIMIT_PCSCF_ANNOTATION_VALUE", "approved"),
        }
    if action_ref == "quarantine_imsi":
        return base | {
            "target_namespace": _string_from_env("AAP_QUARANTINE_NAMESPACE", "ims-demo-lab"),
            "target_configmap": _string_from_env("AAP_QUARANTINE_CONFIGMAP", "ims-remediation-state"),
            "quarantine_key": _string_from_env("AAP_QUARANTINE_KEY", "quarantined_imsi"),
            "quarantine_reason": canonical_anomaly_type(str(incident.get("anomaly_type") or NORMAL_ANOMALY_TYPE)),
            "imsi": _safe_imsi_for_automation(incident),
        }
    return base


def _extract_aap_execution_summary(stdout: str, fallback: str) -> str:
    match = re.search(r"Scaled [^\n]+ replicas\.", stdout, flags=re.IGNORECASE)
    if match:
        return match.group(0).strip()
    for line in reversed([item.strip() for item in stdout.splitlines() if item.strip()]):
        if line.startswith("msg:"):
            return line.replace("msg:", "", 1).strip(" '\"")
    return fallback


def _launch_aap_automation(
    action_ref: str,
    incident: Dict[str, object],
    remediation: Dict[str, object] | None,
    approved_by: str,
    notes: str,
) -> Dict[str, object]:
    extra_vars = _aap_extra_vars_for_action(action_ref, incident, remediation, approved_by, notes)
    namespace = str(extra_vars.get("target_namespace") or "")
    deployment = str(extra_vars.get("target_deployment") or "")
    replicas = extra_vars.get("target_replicas")
    try:
        launch = aap_launch_action(action_ref, extra_vars)
        launch_summary = f"Launched AAP job {launch['job_id']} for {action_ref}."
        if namespace and deployment and replicas:
            launch_summary = f"Launched AAP job {launch['job_id']} to scale {namespace}/{deployment} to {replicas} replicas."
        return {
            "backend": "aap-controller",
            "job_id": launch["job_id"],
            "job_template_id": launch["job_template_id"],
            "job_template_name": launch["job_template_name"],
            "job_api_url": launch["job_api_url"],
            "job_stdout_url": launch["job_stdout_url"],
            "controller_app_url": launch.get("controller_app_url"),
            "playbook": PLAYBOOKS.get(action_ref, ""),
            "requested_vars": extra_vars,
            "launch_summary": launch_summary,
        }
    except AAPAutomationError as exc:
        if "License is missing" not in str(exc):
            raise
        launch = aap_launch_runner_job(action_ref, extra_vars)
        launch_summary = (
            f"AAP controller writes are blocked by the current license, so the platform launched "
            f"runner job {launch['job_name']} in namespace {launch['job_namespace']}."
        )
        if namespace and deployment and replicas:
            launch_summary = (
                f"AAP controller writes are blocked by the current license, so the platform launched "
                f"runner job {launch['job_name']} to scale {namespace}/{deployment} to {replicas} replicas."
            )
        return {
            "backend": "aap-runner-job",
            "job_name": launch["job_name"],
            "job_namespace": launch["job_namespace"],
            "controller_app_url": launch.get("controller_app_url"),
            "playbook": PLAYBOOKS.get(action_ref, ""),
            "requested_vars": extra_vars,
            "launch_summary": launch_summary,
        }


def _eda_event_payload(
    event_type: str,
    incident: Dict[str, object],
    extra: Dict[str, object] | None = None,
    remediations: List[Dict[str, object]] | None = None,
) -> Dict[str, object]:
    remediation_items = remediations or []
    action_refs = {str(item.get("action_ref") or "") for item in remediation_items}
    payload = {
        "event_type": event_type,
        "timestamp": _now_iso(),
        "incident_id": str(incident.get("id") or ""),
        "project": str(incident.get("project") or "ims-demo"),
        "anomaly_type": canonical_anomaly_type(str(incident.get("anomaly_type") or NORMAL_ANOMALY_TYPE)),
        "severity": _incident_severity_label(incident),
        "status": normalize_workflow_state(str(incident.get("status") or NEW)),
        "workflow_revision": int(incident.get("workflow_revision") or 1),
        "anomaly_score": _coerce_float(incident.get("anomaly_score")),
        "predicted_confidence": _incident_confidence(incident),
        "top_classes": incident.get("top_classes") or [],
        "recommendation": str(incident.get("recommendation") or ""),
        "top_action_ref": str((remediation_items[0] or {}).get("action_ref") or "") if remediation_items else "",
        "available_actions": [item for item in sorted(action_refs) if item],
        "rate_limit_pcscf_available": "rate_limit_pcscf" in action_refs,
        "scale_scscf_available": "scale_scscf" in action_refs,
        "quarantine_imsi_available": "quarantine_imsi" in action_refs,
    }
    if extra:
        payload.update(extra)
    return payload


def _publish_eda_event_best_effort(
    event_type: str,
    incident: Dict[str, object],
    *,
    extra: Dict[str, object] | None = None,
    remediations: List[Dict[str, object]] | None = None,
) -> None:
    try:
        deliveries = eda_publish_event(_eda_event_payload(event_type, incident, extra=extra, remediations=remediations))
        if deliveries:
            record_integration("eda", "published")
    except Exception as exc:
        logger.warning("EDA event publish failed for %s on incident %s: %s", event_type, incident.get("id"), exc)
        record_integration("eda", "failed")


def _finalize_aap_automation(
    incident_id: str,
    action_id: int,
    approval_id: int,
    action_ref: str,
    approved_by: str,
    notes: str,
) -> None:
    finished_at = _now_iso()
    raw_status = "failed"
    summary = f"AAP automation failed for {action_ref}."
    merged_result: Dict[str, object] = {}
    job_id = 0
    try:
        action_record = get_incident_action(incident_id, action_id)
        if not action_record:
            raise AAPAutomationError(f"Incident action {action_id} for {incident_id} could not be reloaded.")

        result_json = action_record.get("result_json") if isinstance(action_record.get("result_json"), dict) else {}
        merged_result = dict(result_json)
        backend = str(result_json.get("backend") or "aap-controller")
        if backend == "aap-runner-job":
            job_name = str(result_json.get("job_name") or "")
            job_namespace = str(result_json.get("job_namespace") or os.getenv("AAP_RUNNER_NAMESPACE", "aap"))
            if not job_name:
                raise AAPAutomationError(f"Incident action {action_id} does not contain an AAP runner job name.")
            job = aap_wait_for_runner_job(
                job_name,
                job_namespace,
                timeout_seconds=AAP_JOB_TIMEOUT_SECONDS,
                poll_interval_seconds=AAP_JOB_POLL_SECONDS,
            )
            raw_status = str(job.get("status") or "failed").strip().lower()
            stdout = str(job.get("stdout") or "")
            summary = _extract_aap_execution_summary(
                stdout,
                f"AAP runner job {job_name} finished with status {raw_status}.",
            )
            merged_result |= {
                "job_status": raw_status,
                "job_name": job_name,
                "job_namespace": job_namespace,
                "stdout_excerpt": stdout[-4000:] if stdout else "",
            }
        else:
            job_id = int(result_json.get("job_id") or 0)
            if job_id <= 0:
                raise AAPAutomationError(f"Incident action {action_id} does not contain an AAP job id.")

            job = aap_wait_for_job(job_id, timeout_seconds=AAP_JOB_TIMEOUT_SECONDS, poll_interval_seconds=AAP_JOB_POLL_SECONDS)
            raw_status = str(job.get("status") or "failed").strip().lower()
            stdout = str(job.get("stdout") or "")
            summary = _extract_aap_execution_summary(stdout, f"AAP job {job_id} finished with status {raw_status}.")
            finished_at = str(job.get("finished") or finished_at)
            merged_result |= {
                "job_status": raw_status,
                "job_name": job.get("name"),
                "job_finished_at": job.get("finished"),
                "stdout_excerpt": stdout[-4000:] if stdout else "",
            }
    except Exception as exc:  # noqa: BLE001
        logger.exception("AAP execution monitoring failed for incident %s action %s", incident_id, action_ref)
        raw_status = "failed"
        summary = f"AAP automation failed for {action_ref}: {exc}"
        merged_result |= {"job_status": raw_status, "error": str(exc)}

    final_status = "executed" if raw_status == "successful" else "failed"
    update_incident_action(
        incident_id,
        action_id,
        final_status,
        finished_at=finished_at,
        result_summary=summary,
        result_json=merged_result,
    )
    update_approval(approval_id, final_status, summary)

    latest_incident = get_incident(incident_id)
    if latest_incident:
        current_state = normalize_workflow_state(str(latest_incident.get("status") or NEW))
        if final_status == "executed" and current_state == EXECUTING:
            latest_incident = _transition_incident_with_audit(
                latest_incident,
                EXECUTED,
                approved_by,
                f"AAP automation completed for {action_ref}.",
            )
        elif final_status == "failed" and current_state in {APPROVED, EXECUTING}:
            latest_incident = _transition_incident_with_audit(
                latest_incident,
                EXECUTION_FAILED,
                approved_by,
                f"AAP automation failed for {action_ref}.",
            )

    record_audit(
        "action_execution_completed",
        approved_by,
        {
            "action_ref": action_ref,
            "execution_status": final_status,
            "job_id": job_id,
            "result_summary": summary,
            "notes": notes,
        },
        incident_id=incident_id,
    )
    record_automation(action_ref, final_status)

    refreshed_incident = get_incident(incident_id)
    if refreshed_incident:
        _sync_current_ticket_best_effort(
            refreshed_incident,
            _ticket_note(
                "Incident action update",
                [
                    ("Incident", incident_id),
                    ("Workflow state", refreshed_incident.get("status")),
                    ("Operator", approved_by),
                    ("Action", action_ref),
                    ("Execution status", final_status),
                    ("Result", summary),
                    ("Comment", notes or "AAP execution completed from the incident workflow."),
                ],
            ),
            approved_by,
            "incident_action",
        )
        set_active_incidents(list_incidents(project=str(refreshed_incident.get("project") or "ims-demo")))


def _request_json(method: str, url: str, payload: Dict[str, object] | None = None) -> Dict[str, object]:
    try:
        response = requests.request(
            method=method,
            url=url,
            json=payload,
            headers={"Content-Type": "application/json", **outbound_headers()},
            timeout=UPSTREAM_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Failed to reach upstream service {url}: {exc}") from exc

    body = response.text.strip()
    try:
        parsed: object = response.json() if body else {}
    except ValueError:
        parsed = {"detail": body} if body else {}

    if not response.ok:
        detail = parsed if isinstance(parsed, dict) else {"detail": str(parsed)}
        raise HTTPException(
            status_code=502,
            detail={
                "message": f"Upstream request failed for {url}",
                "status_code": response.status_code,
                "body": detail,
            },
        )

    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _record_debug_trace_packets(incident_id: str, actor: str, packets: object) -> None:
    if not incident_id or not isinstance(packets, list):
        return
    for packet in packets:
        if not isinstance(packet, dict):
            continue
        trace_payload = dict(packet)
        trace_payload.setdefault("timestamp", _now_iso())
        trace_payload.setdefault("category", "workflow")
        trace_payload.setdefault("phase", "event")
        trace_payload.setdefault("title", "Trace event")
        trace_payload.setdefault("service", "control-plane")
        trace_payload.setdefault("payload", {})
        trace_payload.setdefault("metadata", {})
        record_audit(DEBUG_TRACE_EVENT_TYPE, actor, trace_payload, incident_id=incident_id)


def _trace_event_timestamp(payload: object, fallback: str) -> str:
    if isinstance(payload, dict):
        timestamp = str(payload.get("timestamp") or "").strip()
        if timestamp:
            return timestamp
    return fallback


def _trace_sort_key(packet: Dict[str, object], fallback_index: int) -> tuple[datetime, int, int]:
    raw_timestamp = str(packet.get("timestamp") or "").strip()
    try:
        parsed_timestamp = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    except ValueError:
        parsed_timestamp = datetime.min.replace(tzinfo=timezone.utc)
    phase_priority = {"request": 0, "response": 1, "event": 2}.get(str(packet.get("phase") or "event"), 3)
    return parsed_timestamp, phase_priority, fallback_index


def _audit_trace_category(event_type: str) -> str:
    if event_type.startswith("ticket_") or event_type == "plane_webhook_processed":
        return "ticket"
    if event_type in {"scenario_executed", "incident_created", "rca_attached"}:
        return "api"
    if event_type in {"incident_approved", "action_executed", "verification_recorded", "workflow_transition"}:
        return "workflow"
    return "workflow"


def _debug_trace_packets_for_incident(incident: Dict[str, object]) -> List[Dict[str, object]]:
    incident_id = str(incident.get("id") or "")
    workflow = _workflow_payload(incident)
    audit_events = list_audit_events(limit=500, incident_id=incident_id)
    packets: List[Dict[str, object]] = []

    for audit_event in audit_events:
        payload = audit_event.get("payload")
        if str(audit_event.get("event_type") or "") == DEBUG_TRACE_EVENT_TYPE and isinstance(payload, dict):
            packets.append(
                dict(payload)
                | {
                    "timestamp": _trace_event_timestamp(payload, str(audit_event.get("created_at") or _now_iso())),
                    "metadata": {
                        **(payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}),
                        "audit_event_id": audit_event.get("id"),
                        "actor": audit_event.get("actor"),
                        "event_type": audit_event.get("event_type"),
                    },
                }
            )
            continue

        packets.append(
            make_trace_packet(
                _audit_trace_category(str(audit_event.get("event_type") or "")),
                "event",
                title=_timeline_title(str(audit_event.get("event_type") or "")),
                service="control-plane",
                timestamp=str(audit_event.get("created_at") or _now_iso()),
                payload=payload if isinstance(payload, dict) else {"value": payload},
                metadata={
                    "actor": audit_event.get("actor"),
                    "event_type": audit_event.get("event_type"),
                    "audit_event_id": audit_event.get("id"),
                },
            )
        )

    for action in workflow.get("actions") or []:
        if not isinstance(action, dict):
            continue
        packets.append(
            make_trace_packet(
                "action",
                "event",
                title=f"Action {action.get('id')} result",
                service="control-plane",
                timestamp=str(action.get("finished_at") or action.get("started_at") or _now_iso()),
                payload=action.get("result_json") if isinstance(action.get("result_json"), dict) else {"value": action.get("result_json")},
                metadata={
                    "action_id": action.get("id"),
                    "action_mode": action.get("action_mode"),
                    "execution_status": action.get("execution_status"),
                    "triggered_by": action.get("triggered_by"),
                    "remediation_id": action.get("remediation_id"),
                },
            )
        )

    for ticket in workflow.get("tickets") or []:
        if not isinstance(ticket, dict):
            continue
        provider = str(ticket.get("provider") or "ticket")
        for sync_event in ticket.get("sync_events") or []:
            if not isinstance(sync_event, dict):
                continue
            packets.append(
                make_trace_packet(
                    "ticket",
                    "event",
                    title=f"{provider.upper()} {sync_event.get('event_type') or 'sync'}",
                    service="control-plane",
                    target=provider,
                    timestamp=str(sync_event.get("created_at") or _now_iso()),
                    payload=sync_event.get("payload") if isinstance(sync_event.get("payload"), dict) else {"value": sync_event.get("payload")},
                    metadata={
                        "ticket_id": ticket.get("id"),
                        "provider": provider,
                        "direction": sync_event.get("direction"),
                        "status": sync_event.get("status"),
                        "sync_event_id": sync_event.get("id"),
                    },
                )
            )

    indexed_packets = list(enumerate(packets))
    sorted_packets = sorted(
        indexed_packets,
        key=lambda item_with_index: _trace_sort_key(item_with_index[1], item_with_index[0]),
    )
    ordered_packets: List[Dict[str, object]] = []
    for sequence, (_, packet) in enumerate(sorted_packets, start=1):
        ordered_packets.append(packet | {"sequence": sequence})
    return ordered_packets


def _incident_feature_context(incident: Dict[str, object]) -> Dict[str, object]:
    features = incident.get("feature_snapshot") or {}
    return features if isinstance(features, dict) else {}


def _incident_rca_request_payload(incident: Dict[str, object]) -> Dict[str, object]:
    features = _incident_feature_context(incident)
    return {
        "incident_id": str(incident.get("id") or incident.get("incident_id") or ""),
        "context": {
            "project": incident.get("project"),
            "scenario_name": features.get("scenario_name"),
            "anomaly_type": incident.get("anomaly_type"),
            "feature_window_id": incident.get("feature_window_id"),
            "features": features,
        },
    }


def _request_incident_rca(incident: Dict[str, object]) -> Dict[str, object]:
    incident_id = str(incident.get("id") or incident.get("incident_id") or "")
    request_payload = _incident_rca_request_payload(incident)
    request_timestamp = _now_iso()
    try:
        response_payload = _request_json("POST", f"{RCA_SERVICE_URL}/rca", request_payload)
    except HTTPException as exc:
        _record_debug_trace_packets(
            incident_id,
            "control-plane",
            interaction_trace_packets(
                category="api",
                service="control-plane",
                target="rca-service",
                method="POST",
                endpoint=f"{RCA_SERVICE_URL}/rca",
                request_payload=request_payload,
                response_payload={"error": exc.detail},
                request_timestamp=request_timestamp,
                response_timestamp=_now_iso(),
                metadata={"incident_id": incident_id},
            ),
        )
        raise
    _record_debug_trace_packets(
        incident_id,
        "control-plane",
        interaction_trace_packets(
            category="api",
            service="control-plane",
            target="rca-service",
            method="POST",
            endpoint=f"{RCA_SERVICE_URL}/rca",
            request_payload=request_payload,
            response_payload=response_payload,
            request_timestamp=request_timestamp,
            response_timestamp=_now_iso(),
            metadata={"incident_id": incident_id},
        ),
    )
    return response_payload


def _auto_generate_incident_rca(incident_id: str, actor: str = "control-plane:auto-rca") -> None:
    incident = get_incident(incident_id)
    if not incident:
        return

    existing_rca = incident.get("rca_payload") or {}
    if isinstance(existing_rca, dict) and existing_rca:
        return

    try:
        _request_incident_rca(incident)
    except HTTPException as exc:
        logger.warning("Auto RCA generation failed for incident %s: %s", incident_id, exc.detail)
        record_audit(
            "rca_auto_generation_failed",
            actor,
            {"detail": exc.detail},
            incident_id=incident_id,
        )
    except Exception as exc:
        logger.exception("Unexpected auto RCA generation failure for incident %s", incident_id)
        record_audit(
            "rca_auto_generation_failed",
            actor,
            {"detail": str(exc)},
            incident_id=incident_id,
        )


def _wait_for_incident_rca(incident_id: str, timeout_seconds: float = 8.0, poll_interval_seconds: float = 0.5) -> Dict[str, object] | None:
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    latest_incident = get_incident(incident_id)
    while time.monotonic() < deadline:
        rca_payload = (latest_incident or {}).get("rca_payload") or {}
        if isinstance(rca_payload, dict) and rca_payload:
            return latest_incident
        time.sleep(max(poll_interval_seconds, 0.1))
        latest_incident = get_incident(incident_id)
    return latest_incident


def _probe_service(name: str, url: str, path: str = "/healthz") -> Dict[str, object]:
    endpoint = f"{url}{path}" if url else path
    try:
        response = requests.get(endpoint, headers=outbound_headers(), timeout=HEALTH_PROBE_TIMEOUT_SECONDS)
        payload: Dict[str, object]
        raw = response.text.strip()
        try:
            payload = response.json() if raw else {}
        except ValueError:
            payload = {"detail": raw} if raw else {}

        is_ok = response.ok and str(payload.get("status", "ok")).lower() not in {"degraded", "error", "not-ready"}
        if name == "Predictive Service":
            payload = {
                "status": "ready" if response.ok else "not-ready",
                "endpoint": endpoint,
            }
            is_ok = response.ok
        return {
            "name": name,
            "ok": is_ok,
            "status": str(payload.get("status", "ok" if is_ok else "error")),
            "endpoint": endpoint,
            "payload": payload,
        }
    except requests.RequestException as exc:
        return {
            "name": name,
            "ok": False,
            "status": "error",
            "endpoint": endpoint,
            "payload": {"detail": str(exc)},
        }


def _clear_service_snapshot_cache() -> None:
    global _SERVICE_SNAPSHOT_CACHE, _SERVICE_SNAPSHOT_CACHE_EXPIRES_AT
    with _SERVICE_SNAPSHOT_CACHE_LOCK:
        _SERVICE_SNAPSHOT_CACHE = None
        _SERVICE_SNAPSHOT_CACHE_EXPIRES_AT = 0.0


def _service_snapshot() -> List[Dict[str, object]]:
    global _SERVICE_SNAPSHOT_CACHE, _SERVICE_SNAPSHOT_CACHE_EXPIRES_AT
    now = time.time()
    if SERVICE_SNAPSHOT_CACHE_SECONDS > 0:
        with _SERVICE_SNAPSHOT_CACHE_LOCK:
            if _SERVICE_SNAPSHOT_CACHE is not None and now < _SERVICE_SNAPSHOT_CACHE_EXPIRES_AT:
                return _SERVICE_SNAPSHOT_CACHE
    local_health = healthz()
    services = [
        {
            "name": "Control Plane",
            "ok": str(local_health.get("status", "ok")).lower() == "ok",
            "status": str(local_health.get("status", "ok")),
            "endpoint": "local:/healthz",
            "payload": local_health,
        },
        _probe_service("Feature Gateway", FEATURE_GATEWAY_URL),
        _probe_service("Anomaly Service", ANOMALY_SERVICE_URL),
        _probe_service("RCA Service", RCA_SERVICE_URL),
    ]
    if PREDICTIVE_SERVICE_URL:
        services.append(_probe_service("Predictive Service", PREDICTIVE_SERVICE_URL, path="/v2/health/ready"))
    if SERVICE_SNAPSHOT_CACHE_SECONDS > 0:
        with _SERVICE_SNAPSHOT_CACHE_LOCK:
            _SERVICE_SNAPSHOT_CACHE = services
            _SERVICE_SNAPSHOT_CACHE_EXPIRES_AT = now + SERVICE_SNAPSHOT_CACHE_SECONDS
    return services


def _severity_tone(severity: str) -> Dict[str, str]:
    normalized = str(severity or "Medium")
    if normalized == "Critical":
        return {"label": "Critical", "tone": "rose"}
    if normalized == "Warning":
        return {"label": "Warning", "tone": "amber"}
    if normalized == "Low":
        return {"label": "Low", "tone": "emerald"}
    return {"label": "Medium", "tone": "sky"}


def _incident_confidence(incident: Dict[str, object]) -> float:
    return _coerce_float(incident.get("predicted_confidence"))


def _incident_severity_label(incident: Dict[str, object]) -> str:
    anomaly_type = canonical_anomaly_type(str(incident.get("anomaly_type") or NORMAL_ANOMALY_TYPE))
    return str(
        incident.get("severity")
        or severity_from_prediction(anomaly_type, _incident_confidence(incident))
        or severity_from_score(_coerce_float(incident.get("anomaly_score")))
    )


def _incident_subtitle(anomaly_type: str) -> str:
    definition = scenario_definition(anomaly_type)
    return str(definition.get("summary") or "Unexpected IMS behavior detected by the predictive workflow.")


def _blast_radius(anomaly_type: str) -> str:
    definition = scenario_definition(anomaly_type)
    return str(definition.get("blast_radius") or "Feature extraction, scoring pipeline, operator workflow")


def _topology_for(anomaly_type: str) -> List[str]:
    definition = scenario_definition(anomaly_type)
    return [str(node) for node in definition.get("topology", ["UE", "P-CSCF", "S-CSCF", "HSS"])]


def _default_recommendation(anomaly_type: str) -> str:
    definition = scenario_definition(anomaly_type)
    return str(
        definition.get("recommendation")
        or "Review the feature window, inspect RCA evidence, and approve the safest remediation action."
    )


def _incident_impact(incident: Dict[str, object]) -> str:
    anomaly_type = canonical_anomaly_type(str(incident.get("anomaly_type", NORMAL_ANOMALY_TYPE)))
    features = incident.get("feature_snapshot") or {}
    if not isinstance(features, dict):
        features = {}

    register_rate = _coerce_float(features.get("register_rate"))
    invite_rate = _coerce_float(features.get("invite_rate"))
    bye_rate = _coerce_float(features.get("bye_rate"))
    latency_p95 = _coerce_float(features.get("latency_p95") or features.get("latency_p95_ms"))
    retransmissions = _coerce_float(features.get("retransmission_count"))
    error_4xx = _coerce_float(features.get("error_4xx_ratio"))
    error_5xx = _coerce_float(features.get("error_5xx_ratio"))
    payload_variance = _coerce_float(features.get("payload_variance"))

    if anomaly_type == NORMAL_ANOMALY_TYPE:
        return (
            f"Nominal traffic is steady with register rate at {register_rate:.2f}/s, invite rate at {invite_rate:.2f}/s, "
            f"and latency p95 at {latency_p95:.0f} ms."
        )
    if anomaly_type == "registration_storm":
        return (
            f"Registration rate reached {register_rate:.2f}/s, retransmissions are {retransmissions:.0f}, "
            f"and latency p95 is {latency_p95:.0f} ms."
        )
    if anomaly_type == "registration_failure":
        return (
            f"Registration requests are failing with a 4xx ratio of {error_4xx:.2f}, register rate at {register_rate:.2f}/s, "
            f"and retransmissions at {retransmissions:.0f}."
        )
    if anomaly_type == "authentication_failure":
        return (
            f"Authentication challenges are looping with a 4xx ratio of {error_4xx:.2f}, register rate at {register_rate:.2f}/s, "
            f"and retransmissions at {retransmissions:.0f}."
        )
    if anomaly_type == "malformed_sip":
        return (
            f"INVITE rate is {invite_rate:.2f}/s and the 4xx ratio is {error_4xx:.2f}, "
            "showing ingress validation rejects for malformed traffic."
        )
    if anomaly_type == "routing_error":
        return (
            f"INVITE rate is {invite_rate:.2f}/s with a 4xx ratio of {error_4xx:.2f}, "
            "indicating route lookup failures on the session setup path."
        )
    if anomaly_type == "busy_destination":
        return (
            f"INVITE rate is {invite_rate:.2f}/s while the destination is returning busy responses, "
            f"driving a 4xx ratio of {error_4xx:.2f}."
        )
    if anomaly_type == "call_setup_timeout":
        return (
            f"Session setup latency reached {latency_p95:.0f} ms with retransmissions at {retransmissions:.0f}, "
            f"causing INVITE timeouts at {invite_rate:.2f}/s."
        )
    if anomaly_type == "call_drop_mid_session":
        return (
            f"Mid-session traffic is unstable with BYE rate at {bye_rate:.2f}/s, retransmissions at {retransmissions:.0f}, "
            f"and latency p95 at {latency_p95:.0f} ms."
        )
    if anomaly_type == "server_internal_error":
        return (
            f"Server-side errors pushed the 5xx ratio to {error_5xx:.2f} while latency p95 reached {latency_p95:.0f} ms "
            f"across register and invite traffic."
        )
    if anomaly_type == "network_degradation":
        return (
            f"Network instability pushed latency p95 to {latency_p95:.0f} ms with retransmissions at {retransmissions:.0f}, "
            f"affecting both register rate {register_rate:.2f}/s and invite rate {invite_rate:.2f}/s."
        )
    if anomaly_type == "retransmission_spike":
        return (
            f"Retransmissions spiked to {retransmissions:.0f} while register rate is {register_rate:.2f}/s "
            f"and payload variance is {payload_variance:.0f} bytes."
        )
    return (
        f"Latency p95 is {latency_p95:.0f} ms with register rate at {register_rate:.2f}/s, "
        "indicating service pressure on the registration path."
    )


def _explainability_for(incident: Dict[str, object]) -> List[Dict[str, object]]:
    anomaly_type = canonical_anomaly_type(str(incident.get("anomaly_type", NORMAL_ANOMALY_TYPE)))
    palettes = ["sky", "amber", "rose", "emerald"]
    weights = metric_weights(anomaly_type)
    if not weights:
        weights = {
            "latency_p95": 0.35,
            "error_5xx_ratio": 0.25,
            "retransmission_count": 0.2,
            "register_rate": 0.2,
        }

    result = []
    for index, (feature, weight) in enumerate(weights.items()):
        result.append(
            {
                "feature": feature,
                "weight": weight,
                "label": _titleize(feature),
                "tone": palettes[index % len(palettes)],
            }
        )
    return result


def _timeline_title(event_type: str) -> str:
    mapping = {
        "scenario_executed": "Scenario executed",
        "incident_created": "Incident created",
        "rca_attached": "RCA attached",
        "remediations_generated": "Remediations generated",
        "workflow_transition": "Workflow transitioned",
        "incident_approved": "Action approved",
        "action_executed": "Action executed",
        "eda_policy_triggered": "EDA policy triggered",
        "verification_recorded": "Verification recorded",
        "slack_notified": "Slack notified",
        "jira_created": "Jira ticket created",
        "ticket_created": "Ticket created",
        "ticket_synced": "Ticket synced",
        "ticket_sync_failed": "Ticket sync failed",
        "plane_webhook_processed": "Plane webhook processed",
        "resolution_extract_created": "Resolution extract created",
        "model_promoted": "Model promoted",
    }
    return mapping.get(event_type, _titleize(event_type))


def _timeline_detail(event: Dict[str, object]) -> str:
    event_type = str(event.get("event_type", "event"))
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {"detail": str(payload)}

    if event_type == "scenario_executed":
        scenario = payload.get("scenario", "unknown")
        source = payload.get("feature_source", "unknown")
        return f"{_titleize(str(scenario))} window generated from {source}."
    if event_type == "incident_created":
        anomaly_type = payload.get("anomaly_type", "unknown")
        confidence = _coerce_float(payload.get("predicted_confidence"))
        return f"{_titleize(str(anomaly_type))} predicted with confidence {confidence:.2f}."
    if event_type == "rca_attached":
        confidence = _coerce_float(payload.get("confidence"))
        return f"RCA attached with confidence {confidence:.2f}."
    if event_type == "remediations_generated":
        return f"{int(payload.get('count', 0))} remediation suggestions ranked for approval."
    if event_type == "workflow_transition":
        from_state = payload.get("from_state", "unknown")
        to_state = payload.get("to_state", "unknown")
        detail = str(payload.get("detail") or "").strip()
        summary = f"{_titleize(str(from_state))} -> {_titleize(str(to_state))}."
        return f"{summary} {detail}".strip()
    if event_type == "incident_approved":
        action = payload.get("action", "unknown_action")
        execute = bool(payload.get("execute"))
        return f"{_titleize(str(action))} approved ({'execute' if execute else 'record only'})."
    if event_type == "action_executed":
        action = payload.get("action", payload.get("action_ref", "action"))
        status = payload.get("execution_status", "unknown")
        return f"{_titleize(str(action))} recorded with status {status}."
    if event_type == "eda_policy_triggered":
        action = payload.get("action_ref", "action")
        status = payload.get("execution_status", "unknown")
        return f"Event-driven policy launched {_titleize(str(action))} with status {status}."
    if event_type == "verification_recorded":
        status = payload.get("verification_status", "unknown")
        return f"Verification outcome recorded as {_titleize(str(status))}."
    if event_type == "slack_notified":
        return f"Slack notification status: {payload.get('status', 'unknown')}."
    if event_type == "jira_created":
        issue_key = payload.get("issue_key", "pending")
        return f"Jira issue {issue_key} created."
    if event_type in {"ticket_created", "ticket_synced"}:
        provider = payload.get("provider", "ticket")
        external_key = payload.get("external_key", payload.get("ticket_id", "pending"))
        return f"{_titleize(str(provider))} ticket {external_key} {event_type.removeprefix('ticket_')}."
    if event_type == "ticket_sync_failed":
        provider = payload.get("provider", "ticket")
        reason = payload.get("reason", "unknown")
        return f"{_titleize(str(provider))} ticket sync failed during {reason}."
    if event_type == "plane_webhook_processed":
        return f"Plane webhook {payload.get('event', 'unknown')}::{payload.get('action', 'unknown')} processed."
    if event_type == "resolution_extract_created":
        quality = payload.get("verification_quality", "unknown")
        return f"Resolution extract captured with {quality} verification quality."
    if event_type == "model_promoted":
        version = payload.get("version", "unknown")
        stage = payload.get("stage", "prod")
        return f"Model {version} promoted to {stage}."
    detail = payload.get("detail")
    if detail:
        return str(detail)
    return json.dumps(payload, sort_keys=True)[:180]


def _timeline_for_incident(incident: Dict[str, object], audit_events: List[Dict[str, object]]) -> List[Dict[str, object]]:
    incident_id = str(incident.get("id", ""))
    relevant = [event for event in audit_events if str(event.get("incident_id") or "") == incident_id]
    if not relevant:
        return [
            {
                "time": str(incident.get("created_at", "")),
                "title": "Incident created",
                "detail": _incident_subtitle(str(incident.get("anomaly_type", NORMAL_ANOMALY_TYPE))),
            }
        ]

    return [
        {
            "time": str(event.get("created_at", "")),
            "title": _timeline_title(str(event.get("event_type", ""))),
            "detail": _timeline_detail(event),
        }
        for event in relevant[:8]
    ]


def _evidence_sources(incident: Dict[str, object]) -> List[Dict[str, object]]:
    rca_payload = incident.get("rca_payload") or {}
    if not isinstance(rca_payload, dict):
        rca_payload = {}

    documents = rca_payload.get("retrieved_documents") or []
    if isinstance(documents, list) and documents:
        evidence = []
        for document in documents[:3]:
            if not isinstance(document, dict):
                continue
            score = _coerce_float(document.get("score"))
            evidence.append(
                {
                    "title": str(document.get("title") or document.get("reference") or "retrieved-document"),
                    "detail": (
                        f"{document.get('doc_type', 'document')} "
                        f"· score {score:.2f}"
                    ),
                    "reference": str(document.get("reference") or ""),
                    "collection": str(document.get("collection") or ""),
                    "doc_type": str(document.get("doc_type") or ""),
                    "score": score,
                    "excerpt": str(document.get("excerpt") or ""),
                }
            )
        if evidence:
            return evidence

    fallback = rca_payload.get("evidence") or []
    evidence = []
    if isinstance(fallback, list):
        for item in fallback[:3]:
            if not isinstance(item, dict):
                continue
            weight = _coerce_float(item.get("weight"))
            evidence.append(
                {
                    "title": str(item.get("reference") or item.get("type") or "evidence"),
                    "detail": f"weight {weight:.2f}",
                    "reference": str(item.get("reference") or ""),
                }
            )
    return evidence


def _similar_incidents(incident: Dict[str, object], incidents: List[Dict[str, object]]) -> List[Dict[str, object]]:
    current_id = str(incident.get("id", ""))
    current_type = str(incident.get("anomaly_type", ""))
    matches = []
    for candidate in incidents:
        if str(candidate.get("id", "")) == current_id:
            continue
        if str(candidate.get("anomaly_type", "")) != current_type and len(matches) >= 2:
            continue
        matches.append(
            {
                "title": f"incident/{str(candidate.get('id', 'unknown'))[:12]}...",
                "detail": (
                    f"confidence {_coerce_float(candidate.get('predicted_confidence')):.2f} "
                    f"· {_titleize(str(candidate.get('anomaly_type', 'unknown')))}"
                ),
            }
        )
        if len(matches) >= 3:
            break
    return matches


def _ticket_search_fragments(ticket: Dict[str, object]) -> List[str]:
    fragments = [
        str(ticket.get(field) or "")
        for field in ("provider", "external_key", "external_id", "title", "status", "sync_state", "url")
    ]
    metadata = ticket.get("metadata")
    if isinstance(metadata, dict) and metadata:
        fragments.extend(str(metadata.get(field) or "") for field in ("mode", "source_url"))
        raw = metadata.get("raw")
        if isinstance(raw, dict) and raw:
            fragments.extend(str(raw.get(field) or "") for field in ("name", "priority", "external_source"))
    return [fragment for fragment in fragments if fragment]


def _ticket_context(
    incident: Dict[str, object],
    *,
    include_search_text: bool = False,
) -> tuple[Dict[str, object] | None, str, int]:
    incident_id = str(incident.get("id") or "")
    if not incident_id:
        return None, "", 0

    tickets = list_incident_tickets(incident_id)
    if not tickets:
        return None, "", 0

    current_ticket_id = incident.get("current_ticket_id")
    current_ticket = next(
        (ticket for ticket in tickets if current_ticket_id and ticket.get("id") == current_ticket_id),
        tickets[0],
    )
    search_fragments: List[str] = []
    if include_search_text:
        for ticket in tickets:
            search_fragments.extend(_ticket_search_fragments(ticket))
            ticket_id = ticket.get("id")
            if ticket_id:
                for comment in list_ticket_comments(int(ticket_id)):
                    search_fragments.extend(
                        [
                            str(comment.get("author") or ""),
                            str(comment.get("body") or ""),
                            str(comment.get("comment_type") or ""),
                        ]
                    )
                for event in list_ticket_sync_events(int(ticket_id)):
                    search_fragments.extend(
                        [
                            str(event.get("direction") or ""),
                            str(event.get("event_type") or ""),
                            str(event.get("status") or ""),
                        ]
                    )

    return (
        {
            "provider": str(current_ticket.get("provider") or ""),
            "external_key": str(current_ticket.get("external_key") or ""),
            "external_id": str(current_ticket.get("external_id") or ""),
            "title": str(current_ticket.get("title") or ""),
            "url": str(current_ticket.get("url") or ""),
            "sync_state": str(current_ticket.get("sync_state") or ""),
            "status": str(current_ticket.get("status") or ""),
        },
        " ".join(fragment for fragment in search_fragments if fragment),
        len(tickets),
    )


def _incident_summary_view(
    incident: Dict[str, object],
    *,
    include_ticket_context: bool = False,
    include_ticket_search: bool = False,
) -> Dict[str, object]:
    score = _coerce_float(incident.get("anomaly_score"))
    anomaly_type = canonical_anomaly_type(str(incident.get("anomaly_type", NORMAL_ANOMALY_TYPE)))
    predicted_confidence = _incident_confidence(incident)
    severity_label = _incident_severity_label(incident)
    severity = _severity_tone(severity_label)
    rca_payload = incident.get("rca_payload") or {}
    if not isinstance(rca_payload, dict):
        rca_payload = {}

    recommendation = str(
        incident.get("recommendation")
        or rca_payload.get("recommendation")
        or _default_recommendation(anomaly_type)
    )
    current_ticket_summary = None
    ticket_search_text = ""
    ticket_count = 0
    if include_ticket_context:
        current_ticket_summary, ticket_search_text, ticket_count = _ticket_context(
            incident,
            include_search_text=include_ticket_search,
        )
    workflow_state = normalize_workflow_state(str(incident.get("status") or incident.get("workflow_state") or NEW))

    return {
        "id": str(incident.get("id") or ""),
        "project": str(incident.get("project") or "ims-demo"),
        "status": workflow_state,
        "workflow_state": workflow_state,
        "workflow_revision": int(incident.get("workflow_revision") or 1),
        "severity": severity["label"],
        "severity_tone": severity["tone"],
        "anomaly_score": score,
        "anomaly_type": anomaly_type,
        "predicted_confidence": predicted_confidence,
        "top_classes": incident.get("top_classes") or [],
        "class_probabilities": incident.get("class_probabilities") or {},
        "model_version": str(incident.get("model_version") or ""),
        "recommendation": recommendation,
        "created_at": str(incident.get("created_at") or ""),
        "updated_at": str(incident.get("updated_at") or incident.get("created_at") or ""),
        "subtitle": _incident_subtitle(anomaly_type),
        "impact": _incident_impact(incident),
        "plane_workflow_state": plane_state_for_workflow(workflow_state),
        "is_active": is_active_state(workflow_state),
        "current_ticket_summary": current_ticket_summary,
        "ticket_search_text": ticket_search_text,
        "ticket_count": ticket_count,
    }


def _payload_view(incident: Dict[str, object]) -> str:
    payload = {
        "incident": incident.get("id"),
        "type": incident.get("anomaly_type"),
        "status": incident.get("status"),
        "model_version": incident.get("model_version"),
        "feature_window_id": incident.get("feature_window_id"),
        "features": incident.get("feature_snapshot", {}),
        "rca": incident.get("rca_payload", {}),
    }
    return json.dumps(payload, indent=2)


def _enrich_incident(
    incident: Dict[str, object],
    audit_events: List[Dict[str, object]],
    incidents: List[Dict[str, object]],
    include_ticket_context: bool = False,
) -> Dict[str, object]:
    summary = _incident_summary_view(incident, include_ticket_context=include_ticket_context)
    anomaly_type = str(summary.get("anomaly_type") or NORMAL_ANOMALY_TYPE)
    rca_payload = incident.get("rca_payload") or {}
    if not isinstance(rca_payload, dict):
        rca_payload = {}
    feature_snapshot = incident.get("feature_snapshot")
    if not isinstance(feature_snapshot, dict):
        feature_snapshot = {}
    return summary | {
        "feature_window_id": incident.get("feature_window_id"),
        "feature_snapshot": feature_snapshot,
        "blast_radius": _blast_radius(anomaly_type),
        "narrative": str(rca_payload.get("explanation") or rca_payload.get("root_cause") or _incident_subtitle(anomaly_type)),
        "timeline": _timeline_for_incident(incident, audit_events),
        "evidence_sources": _evidence_sources(incident),
        "similar_incidents": _similar_incidents(incident, incidents),
        "explainability": _explainability_for(incident),
        "payload_pretty": _payload_view(incident),
        "topology": _topology_for(anomaly_type),
    }


def _matches_incident_filters(
    summary: Dict[str, object],
    *,
    status_filter: str | None = None,
    severity_filter: str | None = None,
    query: str | None = None,
) -> bool:
    if status_filter and str(summary.get("status") or "") != status_filter:
        return False
    if severity_filter and str(summary.get("severity") or "") != severity_filter:
        return False
    normalized_query = str(query or "").strip().lower()
    if not normalized_query:
        return True
    current_ticket_summary = summary.get("current_ticket_summary")
    ticket = current_ticket_summary if isinstance(current_ticket_summary, dict) else {}
    fragments = [
        str(summary.get("id") or ""),
        str(summary.get("anomaly_type") or ""),
        str(summary.get("severity") or ""),
        str(summary.get("status") or ""),
        str(summary.get("subtitle") or ""),
        str(summary.get("impact") or ""),
        str(summary.get("recommendation") or ""),
        str(ticket.get("provider") or ""),
        str(ticket.get("external_key") or ""),
        str(ticket.get("external_id") or ""),
        str(ticket.get("title") or ""),
        str(summary.get("ticket_search_text") or ""),
    ]
    haystack = " ".join(fragment for fragment in fragments if fragment).lower()
    return normalized_query in haystack


def _active_incident_summary(open_incidents: List[Dict[str, object]]) -> Dict[str, List[Dict[str, object]]]:
    categories: Dict[str, Dict[str, object]] = {}
    model_versions: Dict[str, Dict[str, object]] = {}
    for incident in open_incidents:
        anomaly_type = canonical_anomaly_type(str(incident.get("anomaly_type") or "unknown"))
        definition = scenario_definition(anomaly_type)
        model_version = str(incident.get("model_version") or "unknown")

        category = categories.setdefault(
            anomaly_type,
            {
                "anomaly_type": anomaly_type,
                "label": str(definition.get("display_name") or _titleize(anomaly_type)),
                "count": 0,
                "model_versions": set(),
            },
        )
        category["count"] = int(category["count"]) + 1
        category["model_versions"].add(model_version)

        model = model_versions.setdefault(
            model_version,
            {
                "model_version": model_version,
                "count": 0,
                "anomaly_types": set(),
            },
        )
        model["count"] = int(model["count"]) + 1
        model["anomaly_types"].add(anomaly_type)

    category_items = [
        {
            "anomaly_type": str(item["anomaly_type"]),
            "label": str(item["label"]),
            "count": int(item["count"]),
            "model_versions": sorted(str(version) for version in item["model_versions"]),
        }
        for item in categories.values()
    ]
    category_items.sort(key=lambda item: (-int(item["count"]), str(item["anomaly_type"])))

    model_items = [
        {
            "model_version": str(item["model_version"]),
            "count": int(item["count"]),
            "anomaly_types": sorted(str(anomaly_type) for anomaly_type in item["anomaly_types"]),
        }
        for item in model_versions.values()
    ]
    model_items.sort(key=lambda item: (-int(item["count"]), str(item["model_version"])))

    return {
        "categories": category_items,
        "models": model_items,
    }


def _build_console_state(project: str) -> Dict[str, object]:
    incidents = list_incidents(project=project)
    services = _service_snapshot()
    incident_summaries = [_incident_summary_view(incident) for incident in incidents]
    recent_incident_summaries = incident_summaries[:CONSOLE_RECENT_INCIDENT_LIMIT]
    latest_incident = incidents[0] if incidents else None
    latest_incident_summary = incident_summaries[0] if incident_summaries else None
    active_incidents = [incident for incident in incident_summaries if is_active_state(str(incident.get("status") or NEW))]
    active_summary = _active_incident_summary(active_incidents)
    set_active_incidents(active_incidents)
    healthy_services = sum(1 for service in services if bool(service.get("ok")))
    integrations = integration_status()
    return {
        "generated_at": _now_iso(),
        "cluster": {
            "name": CONSOLE_CLUSTER_NAME,
            "status": "degraded" if active_incidents or healthy_services < len(services) else "healthy",
            "active_incident_id": latest_incident_summary.get("id") if latest_incident_summary else None,
            "rca_status": "attached" if latest_incident and latest_incident.get("rca_payload") else "none",
            "auto_refresh_seconds": CONSOLE_AUTO_REFRESH_SECONDS,
        },
        "summary": {
            "incident_count": len(incident_summaries),
            "active_incident_count": len(active_incidents),
            "open_incidents": len(active_incidents),
            "critical_incidents": sum(1 for incident in active_incidents if incident.get("severity") == "Critical"),
            "active_incident_categories": active_summary["categories"],
            "active_incidents_by_model": active_summary["models"],
            "workflow_state_distribution": _workflow_state_counts(incident_summaries),
            "latest_score": _coerce_float(latest_incident_summary.get("anomaly_score")) if latest_incident_summary else 0.0,
            "latest_confidence": _coerce_float(latest_incident_summary.get("predicted_confidence")) if latest_incident_summary else 0.0,
            "healthy_services": healthy_services,
            "service_count": len(services),
        },
        "incidents": recent_incident_summaries,
        "services": services,
        "integrations": integrations,
        "scenarios": console_scenario_catalog(),
    }


@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "db_path": os.getenv("CONTROL_PLANE_DB_PATH", "/tmp/ims-demo-control-plane.db"),
        "ansible_available": shutil.which("ansible-playbook") is not None,
        "automation_mode": _automation_mode(),
        "registry_loaded": bool(load_registry().get("models")),
    }


@app.post("/incidents")
def post_incident(
    payload: IncidentCreate,
    background_tasks: BackgroundTasks,
    auth: AuthContext | None = Depends(require_api_key),
):
    ensure_project_access(auth, payload.project)
    incident_request_payload = payload.model_dump(exclude_none=True)
    inbound_debug_trace = list(incident_request_payload.pop("debug_trace", []))
    request_timestamp = _now_iso()
    incident = create_incident(incident_request_payload)
    response_timestamp = _now_iso()
    _publish_incident_evidence_record(incident)
    record_audit("incident_created", "anomaly-service", incident, incident_id=incident["id"])
    _record_debug_trace_packets(
        str(incident.get("id") or ""),
        str(payload.source_system or "anomaly-service"),
        interaction_trace_packets(
            category="api",
            service=str(payload.source_system or "anomaly-service"),
            target="control-plane",
            method="POST",
            endpoint="/incidents",
            request_payload=incident_request_payload,
            response_payload=incident,
            request_timestamp=request_timestamp,
            response_timestamp=response_timestamp,
            metadata={"source_system": payload.source_system},
        )
        + inbound_debug_trace,
    )
    record_incident(incident["project"], incident["anomaly_type"], incident["status"])
    set_active_incidents(list_incidents(project=incident["project"]))
    background_tasks.add_task(_publish_eda_event_best_effort, "incident_created", incident)
    auto_rca_policy = _auto_rca_policy(payload.auto_generate_rca, str(incident.get("id") or payload.incident_id))
    if bool(auto_rca_policy["enabled"]):
        background_tasks.add_task(
            _auto_generate_incident_rca,
            incident["id"],
            f"{incident.get('source_system') or payload.source_system}:auto-rca",
        )
    else:
        record_audit(
            "rca_auto_generation_deferred",
            "control-plane:auto-rca-policy",
            {
                "mode": auto_rca_policy["mode"],
                "sample_rate": auto_rca_policy["sample_rate"],
                "sample_value": auto_rca_policy["sample_value"],
                "status": incident.get("status"),
            },
            incident_id=incident["id"],
        )
    return incident


@app.get("/incidents")
def get_incidents(
    project: str | None = None,
    active_only: bool = False,
    include_details: bool = False,
    status: str | None = None,
    severity: str | None = None,
    q: str | None = None,
    auth: AuthContext | None = Depends(require_api_key),
):
    if project:
        ensure_project_access(auth, project)
        incidents = list_incidents(project=project)
    elif auth is None or "*" in auth.projects:
        incidents = list_incidents()
    else:
        incidents = []
        for allowed_project in auth.projects:
            incidents.extend(list_incidents(project=allowed_project))
        incidents.sort(key=lambda item: item["created_at"], reverse=True)
    audit_events = list_audit_events(limit=200) if include_details else []
    normalized_query = str(q or "").strip()
    enriched: List[Dict[str, object]] = []
    for incident in incidents:
        summary = _incident_summary_view(
            incident,
            include_ticket_context=True,
            include_ticket_search=bool(normalized_query),
        )
        if active_only and not bool(summary.get("is_active")):
            continue
        if not _matches_incident_filters(
            summary,
            status_filter=status,
            severity_filter=severity,
            query=normalized_query,
        ):
            continue
        if include_details:
            enriched.append(_enrich_incident(incident, audit_events, incidents, include_ticket_context=True))
        else:
            enriched.append(summary)
    return enriched


@app.get("/incidents/{incident_id}")
def get_incident_by_id(incident_id: str, auth: AuthContext | None = Depends(require_api_key)):
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    return _workflow_payload(incident)


@app.get("/incidents/{incident_id}/debug-trace")
def get_incident_debug_trace(incident_id: str, auth: AuthContext | None = Depends(require_api_key)):
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    return {
        "incident": _enrich_incident(incident, list_audit_events(limit=500, incident_id=incident_id), list_incidents(project=str(incident.get("project") or "ims-demo"))),
        "trace_packets": _debug_trace_packets_for_incident(incident),
    }


@app.get("/incidents/{incident_id}/rca")
def get_incident_rca(incident_id: str, auth: AuthContext | None = Depends(require_api_key)):
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    history = list_incident_rca(incident_id)
    current_rca_id = incident.get("current_rca_id")
    current = next((item for item in history if current_rca_id and item.get("id") == current_rca_id), history[0] if history else None)
    return {
        "current_rca": current,
        "history": history,
    }


@app.post("/incidents/{incident_id}/rca/generate")
def generate_incident_rca(incident_id: str, auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "operator")
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    rca_payload = _request_incident_rca(incident)
    updated = get_incident(incident_id) or incident
    return {
        "rca": rca_payload,
        "workflow": _workflow_payload(updated),
    }


@app.post("/incidents/{incident_id}/rca")
def post_rca(incident_id: str, payload: RCAAttach, auth: AuthContext | None = Depends(require_api_key)):
    request_payload = payload.model_dump()
    inbound_debug_trace = list(request_payload.get("debug_trace") or [])
    audit_payload = {key: value for key, value in request_payload.items() if key != "debug_trace"}
    request_timestamp = _now_iso()
    incident = attach_rca(incident_id, request_payload)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    record_audit("rca_attached", "rca-service", audit_payload, incident_id=incident_id)
    _record_debug_trace_packets(
        incident_id,
        "rca-service",
        interaction_trace_packets(
            category="api",
            service="rca-service",
            target="control-plane",
            method="POST",
            endpoint=f"/incidents/{incident_id}/rca",
            request_payload=request_payload,
            response_payload=_workflow_payload(get_incident(incident_id) or incident),
            request_timestamp=request_timestamp,
            response_timestamp=_now_iso(),
            metadata={"incident_id": incident_id},
        )
        + inbound_debug_trace,
    )
    refreshed = get_incident(incident_id) or incident
    _publish_rca_reasoning_record(refreshed, payload.model_dump())
    _publish_eda_event_best_effort("rca_attached", refreshed)
    _generate_and_store_remediations(incident_id, actor="rca-service")
    refreshed = get_incident(incident_id)
    set_active_incidents(list_incidents(project=incident["project"]))
    return _workflow_payload(refreshed or incident)


@app.post("/incidents/{incident_id}/transition")
def transition_incident(
    incident_id: str,
    payload: IncidentTransitionRequest,
    auth: AuthContext | None = Depends(require_api_key),
):
    ensure_role(auth, "operator")
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    updated = _transition_incident_with_audit(
        incident,
        payload.target_state,
        auth.subject if auth else "operator",
        payload.notes,
    )
    updated = get_incident(incident_id) or updated
    transition_note = _ticket_note(
        "Workflow update",
        [
            ("Incident", incident_id),
            ("Workflow state", updated.get("status")),
            ("Operator", auth.subject if auth else "operator"),
            ("Comment", payload.notes or f"Transitioned incident to {payload.target_state}."),
        ],
    )
    normalized_target = normalize_workflow_state(payload.target_state)
    if normalized_target == ESCALATED:
        try:
            _sync_ticket_provider(
                updated,
                "plane",
                note=transition_note,
                force=True,
                source_url=payload.source_url,
            )
        except Exception as exc:
            logger.warning("Plane ticket sync failed for escalated incident %s: %s", incident_id, exc)
            record_ticket_sync("plane", "outbound", "failed")
            record_audit(
                "ticket_sync_failed",
                auth.subject if auth else "operator",
                {"provider": "plane", "reason": "workflow_transition_escalated", "detail": str(exc)},
                incident_id=incident_id,
            )
    else:
        _sync_current_ticket_best_effort(
            updated,
            transition_note,
            auth.subject if auth else "operator",
            "workflow_transition",
        )
    updated = get_incident(incident_id) or updated
    set_active_incidents(list_incidents(project=incident["project"]))
    return _workflow_payload(updated)


@app.post("/incidents/{incident_id}/remediation/generate")
def generate_incident_remediations(incident_id: str, auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "operator")
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    rca_payload = incident.get("rca_payload") or {}
    if not isinstance(rca_payload, dict) or not rca_payload:
        raise HTTPException(status_code=400, detail="RCA must exist before generating remediations")
    remediations = _generate_and_store_remediations(incident_id, actor=auth.subject if auth else "operator")
    updated = get_incident(incident_id) or incident
    return {
        "remediations": remediations,
        "workflow": _workflow_payload(updated),
    }


@app.post("/incidents/{incident_id}/remediation/{remediation_id}/generate-playbook")
def generate_incident_ai_playbook(
    incident_id: str,
    remediation_id: int,
    payload: PlaybookGenerationRequest,
    auth: AuthContext | None = Depends(require_api_key),
):
    ensure_role(auth, "operator")
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    remediation = get_incident_remediation(incident_id, remediation_id)
    if not remediation:
        raise HTTPException(status_code=404, detail="Remediation not found")
    result = _request_ai_playbook_generation(
        incident,
        remediation,
        payload.requested_by,
        payload.notes,
        payload.source_url,
    )
    updated = get_incident(incident_id) or incident
    return {
        "remediation": result["remediation"],
        "generation": result["publish"],
        "workflow": _workflow_payload(updated),
    }


@app.post("/incidents/{incident_id}/remediation/{remediation_id}/playbook-instruction-preview")
def preview_incident_ai_playbook_instruction(
    incident_id: str,
    remediation_id: int,
    payload: PlaybookGenerationRequest,
    auth: AuthContext | None = Depends(require_api_key),
):
    ensure_role(auth, "operator")
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    remediation = get_incident_remediation(incident_id, remediation_id)
    if not remediation:
        raise HTTPException(status_code=404, detail="Remediation not found")
    return _preview_ai_playbook_generation_instruction(
        incident,
        remediation,
        payload.notes,
        payload.source_url,
    )


@app.post("/incidents/{incident_id}/playbook-generation/callback")
def ai_playbook_generation_callback(
    incident_id: str,
    payload: PlaybookGenerationCallbackRequest,
    auth: AuthContext | None = Depends(require_api_key),
):
    ensure_role(auth, "automation")
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    updated_remediation = _apply_ai_playbook_generation_callback(incident_id, payload)
    updated_incident = get_incident(incident_id) or incident
    actor = str(payload.provider_name or AI_PLAYBOOK_GENERATION_PROVIDER).strip() or AI_PLAYBOOK_GENERATION_PROVIDER
    _sync_current_ticket_best_effort(
        updated_incident,
        _ticket_note(
            "AI playbook generation update",
            [
                ("Incident", incident_id),
                ("Provider", actor),
                ("Status", payload.status),
                ("Correlation", payload.correlation_id),
                ("Remediation", updated_remediation.get("title")),
                ("Comment", payload.error or payload.summary or payload.description or "AI playbook generation callback received."),
            ],
        ),
        actor,
        "ai_playbook_generation",
    )
    set_active_incidents(list_incidents(project=incident["project"]))
    return {
        "remediation": updated_remediation,
        "workflow": _workflow_payload(updated_incident),
    }


def _execute_incident_action(
    incident_id: str,
    payload: RemediationActionRequest,
    auth: AuthContext | None = Depends(require_api_key),
    background_tasks: BackgroundTasks | None = None,
):
    ensure_role(auth, "operator")
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])

    remediation = None
    if payload.remediation_id is not None:
        remediation = get_incident_remediation(incident_id, payload.remediation_id)
        if not remediation:
            raise HTTPException(status_code=404, detail="Remediation not found")
    elif payload.action:
        remediation = _find_matching_remediation(incident_id, payload.action)

    if remediation and _is_ai_playbook_generation_request(remediation):
        raise HTTPException(
            status_code=400,
            detail="Use the AI playbook generation endpoint for this remediation before approving or executing it",
        )

    action_ref = str(
        payload.action
        or (remediation or {}).get("action_ref")
        or (remediation or {}).get("playbook_ref")
        or ""
    ).strip()
    if not action_ref:
        raise HTTPException(status_code=400, detail="Action reference is required")
    dynamic_playbook_yaml = _generated_playbook_yaml(remediation)

    actor = auth.subject if auth else payload.approved_by
    started_at = _now_iso() if payload.execute else None
    finished_at = None
    action_result_json: Dict[str, object] = {"execute": payload.execute, "action_ref": action_ref}
    current_state = normalize_workflow_state(str(incident.get("status") or NEW))
    remediation_status = str((remediation or {}).get("status") or "").strip().lower()
    reuse_existing_approval = current_state == APPROVED and payload.execute and remediation_status == "approved"

    if current_state in {EXECUTED, EXECUTION_FAILED, VERIFICATION_FAILED} or (
        current_state == APPROVED and not reuse_existing_approval
    ):
        incident = _transition_incident_with_audit(
            incident,
            REMEDIATION_SUGGESTED,
            actor,
            payload.notes
            or (
                f"Reopened remediation review after the previous result for {action_ref}."
                if current_state in {EXECUTED, EXECUTION_FAILED, VERIFICATION_FAILED}
                else f"Reopened remediation review to choose a different approved action instead of {action_ref}."
            ),
        )
        incident = _transition_incident_with_audit(
            incident,
            AWAITING_APPROVAL,
            actor,
            "Operator is reviewing another remediation option.",
        )
    elif current_state == REMEDIATION_SUGGESTED:
        incident = _transition_incident_with_audit(
            incident,
            AWAITING_APPROVAL,
            actor,
            "Operator opened remediation workflow.",
        )

    if not reuse_existing_approval:
        incident = _transition_incident_with_audit(
            incident,
            APPROVED,
            actor,
            payload.notes or f"Approved remediation action {action_ref}.",
        )
    else:
        action_result_json["approval_reused"] = True
    if payload.execute:
        ensure_role(auth, "automation")

    execution_status = "approved"
    output = "Approval recorded."
    skip_ticket_update = False
    if payload.execute and (action_ref in PLAYBOOKS or dynamic_playbook_yaml):
        incident = _transition_incident_with_audit(
            incident,
            EXECUTING,
            actor,
            f"Executing automation for {action_ref}.",
        )
        if dynamic_playbook_yaml:
            output, raw_status = _execute_playbook(
                action_ref,
                playbook_content=dynamic_playbook_yaml,
                playbook_label=str((remediation or {}).get("playbook_ref") or action_ref),
            )
            finished_at = _now_iso()
            action_result_json |= {
                "backend": "dynamic-playbook",
                "playbook": str((remediation or {}).get("playbook_ref") or action_ref),
                "raw_status": raw_status,
                "ai_generated": True,
            }
            if raw_status in {"executed", "simulated"}:
                execution_status = "executed"
                incident = _transition_incident_with_audit(
                    get_incident(incident_id) or incident,
                    EXECUTED,
                    actor,
                    f"Automation completed for {action_ref}.",
                )
            elif raw_status in {"failed", "rejected"}:
                execution_status = "failed"
                incident = _transition_incident_with_audit(
                    get_incident(incident_id) or incident,
                    EXECUTION_FAILED,
                    actor,
                    f"Automation failed for {action_ref}.",
                )
            else:
                execution_status = "approved"
                incident = _transition_incident_with_audit(
                    get_incident(incident_id) or incident,
                    APPROVED,
                    actor,
                    f"Automation for {action_ref} was gated before execution.",
                )
        elif aap_action_supported(action_ref):
            try:
                launch = _launch_aap_automation(action_ref, incident, remediation, payload.approved_by, payload.notes)
                execution_status = "executing"
                output = str(launch.get("launch_summary") or f"Launched AAP automation for {action_ref}.")
                action_result_json |= launch | {"raw_status": "launched"}
            except AAPAutomationError as exc:
                execution_status = "failed"
                finished_at = _now_iso()
                output = str(exc)
                action_result_json |= {"backend": "aap", "raw_status": "launch_failed", "error": str(exc)}
                incident = _transition_incident_with_audit(
                    get_incident(incident_id) or incident,
                    EXECUTION_FAILED,
                    actor,
                    f"AAP automation failed to launch for {action_ref}.",
                )
        else:
            output, raw_status = _execute_playbook(action_ref)
            finished_at = _now_iso()
            action_result_json |= {"backend": "local", "playbook": PLAYBOOKS.get(action_ref, ""), "raw_status": raw_status}
            if raw_status in {"executed", "simulated"}:
                execution_status = "executed"
                incident = _transition_incident_with_audit(
                    get_incident(incident_id) or incident,
                    EXECUTED,
                    actor,
                    f"Automation completed for {action_ref}.",
                )
            elif raw_status in {"failed", "rejected"}:
                execution_status = "failed"
                incident = _transition_incident_with_audit(
                    get_incident(incident_id) or incident,
                    EXECUTION_FAILED,
                    actor,
                    f"Automation failed for {action_ref}.",
                )
            else:
                execution_status = "approved"
                incident = _transition_incident_with_audit(
                    get_incident(incident_id) or incident,
                    APPROVED,
                    actor,
                    f"Automation for {action_ref} was gated before execution.",
                )
    elif payload.execute and action_ref == "open_plane_escalation":
        execution_status = "executed"
        finished_at = _now_iso()
        skip_ticket_update = True
        action_result_json |= {"backend": "ticket", "raw_status": "escalated"}
        incident = _transition_incident_with_audit(
            get_incident(incident_id) or incident,
            ESCALATED,
            actor,
            payload.notes or "Escalated incident for human coordination in Plane.",
        )
        ticket_note = _ticket_note(
            "Incident escalation",
            [
                ("Incident", incident_id),
                ("Workflow state", incident.get("status")),
                ("Operator", payload.approved_by),
                ("Action", action_ref),
                ("Remediation", (remediation or {}).get("title")),
                ("Comment", payload.notes or "Escalated from the remediation workflow."),
            ],
        )
        try:
            ticket_result = _sync_ticket_provider(
                incident,
                "plane",
                note=ticket_note,
                force=True,
                source_url=payload.source_url,
            )
            action_result_json["ticket"] = ticket_result
            operation = ticket_result.get("operation") if isinstance(ticket_result, dict) else None
            status_payload = operation if isinstance(operation, dict) else ticket_result if isinstance(ticket_result, dict) else {}
            ticket_status = str(status_payload.get("status") or "").strip().lower()
            ticket_key = str(
                (ticket_result if isinstance(ticket_result, dict) else {}).get("external_key")
                or (ticket_result if isinstance(ticket_result, dict) else {}).get("external_id")
                or ""
            ).strip()
            if ticket_status == "created":
                output = f"Incident escalated and Plane ticket {ticket_key or 'created'} is ready for coordination."
            elif ticket_status == "synced":
                output = f"Incident escalated and Plane ticket {ticket_key or 'updated'} is ready for coordination."
            elif ticket_status == "skipped":
                output = str(
                    status_payload.get("reason")
                    or "Incident escalated. Open the ticket workflow to create or sync the Plane ticket."
                )
            else:
                output = "Incident escalated. Open the ticket workflow to review Plane synchronization."
        except Exception as exc:
            execution_status = "failed"
            output = f"Incident escalated but Plane ticket synchronization failed: {exc}"
            action_result_json |= {"raw_status": "failed", "error": str(exc)}
            logger.warning("Plane ticket sync failed for escalated remediation on incident %s: %s", incident_id, exc)
            record_ticket_sync("plane", "outbound", "failed")
            record_audit(
                "ticket_sync_failed",
                actor,
                {"provider": "plane", "reason": "remediation_open_plane_escalation", "detail": str(exc)},
                incident_id=incident_id,
            )
    elif payload.execute:
        execution_status = "executed"
        output = payload.notes or f"Manual or notify action {action_ref} recorded as executed."
        finished_at = _now_iso()
        action_result_json |= {"backend": "manual", "raw_status": "executed"}
        incident = _transition_incident_with_audit(
            get_incident(incident_id) or incident,
            EXECUTED,
            actor,
            f"Manual or notify action {action_ref} recorded as executed.",
        )

    approval = record_approval(
        incident_id=incident_id,
        action=action_ref,
        approved_by=payload.approved_by,
        execute=payload.execute,
        status=execution_status,
        output=output,
    )
    action_result_json["approval_id"] = approval.get("id")
    action_record = record_incident_action(
        incident_id=incident_id,
        remediation_id=int(remediation["id"]) if remediation and remediation.get("id") else None,
        action_mode=str((remediation or {}).get("action_mode") or ("ansible" if action_ref in PLAYBOOKS or dynamic_playbook_yaml else "manual")),
        source_of_action=payload.source_of_action,
        approved_revision=int((get_incident(incident_id) or incident).get("workflow_revision") or 1),
        triggered_by=payload.approved_by,
        execution_status=execution_status,
        notes=payload.notes,
        started_at=started_at,
        finished_at=finished_at,
        result_summary=output,
        result_json=action_result_json,
    )
    record_audit(
        "incident_approved",
        payload.approved_by,
        payload.model_dump(),
        incident_id=incident_id,
    )
    record_audit(
        "action_executed",
        payload.approved_by,
        {
            "action_ref": action_ref,
            "execution_status": execution_status,
            "notes": payload.notes,
            "result_summary": output,
        },
        incident_id=incident_id,
    )
    if (action_ref in PLAYBOOKS or dynamic_playbook_yaml) and execution_status in {"executed", "failed"}:
        record_automation(action_ref, execution_status)
    if execution_status == "executing":
        if background_tasks is not None:
            background_tasks.add_task(
                _finalize_aap_automation,
                incident_id,
                int(action_record["id"]),
                int(approval["id"]),
                action_ref,
                payload.approved_by,
                payload.notes,
            )
        else:
            _finalize_aap_automation(
                incident_id,
                int(action_record["id"]),
                int(approval["id"]),
                action_ref,
                payload.approved_by,
                payload.notes,
            )
    refreshed_incident = get_incident(incident_id) or incident
    if not skip_ticket_update:
        _sync_current_ticket_best_effort(
            refreshed_incident,
            _ticket_note(
                "Incident action update",
                [
                    ("Incident", incident_id),
                    ("Workflow state", refreshed_incident.get("status")),
                    ("Operator", payload.approved_by),
                    ("Action", action_ref),
                    ("Remediation", (remediation or {}).get("title")),
                    ("Execution status", execution_status),
                    ("Result", output),
                    ("Comment", payload.notes or "Action recorded from the incident workflow."),
                ],
            ),
            payload.approved_by,
            "incident_action",
        )
    set_active_incidents(list_incidents(project=incident["project"]))
    return {
        "approval": approval,
        "action": action_record,
        "workflow": _workflow_payload(refreshed_incident),
    }


@app.post("/incidents/{incident_id}/remediation/{remediation_id}/approve")
def approve_remediation(
    incident_id: str,
    remediation_id: int,
    payload: RemediationDecisionRequest,
    auth: AuthContext | None = Depends(require_api_key),
):
    return _execute_incident_action(
        incident_id,
        RemediationActionRequest(
            remediation_id=remediation_id,
            approved_by=payload.approved_by,
            notes=payload.notes,
            execute=False,
            source_url=payload.source_url,
        ),
        auth,
    )


@app.post("/incidents/{incident_id}/remediation/{remediation_id}/execute")
def execute_remediation(
    incident_id: str,
    remediation_id: int,
    payload: RemediationDecisionRequest,
    background_tasks: BackgroundTasks,
    auth: AuthContext | None = Depends(require_api_key),
):
    return _execute_incident_action(
        incident_id,
        RemediationActionRequest(
            remediation_id=remediation_id,
            approved_by=payload.approved_by,
            notes=payload.notes,
            execute=True,
            source_url=payload.source_url,
        ),
        auth,
        background_tasks,
    )


@app.post("/incidents/{incident_id}/automation/actions/{action_ref}/execute")
def execute_automation_action(
    incident_id: str,
    action_ref: str,
    payload: AutomationActionTriggerRequest,
    background_tasks: BackgroundTasks,
    auth: AuthContext | None = Depends(require_api_key),
):
    ensure_role(auth, "automation")
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])

    remediation = _find_matching_remediation(incident_id, action_ref)
    if not remediation and isinstance(incident.get("rca_payload"), dict) and incident.get("rca_payload"):
        _generate_and_store_remediations(incident_id, actor=auth.subject if auth else payload.approved_by)
        remediation = _find_matching_remediation(incident_id, action_ref)
    if remediation is None and action_ref not in PLAYBOOKS:
        raise HTTPException(status_code=404, detail=f"Automation action '{action_ref}' is not available for this incident")

    latest_action = _latest_action_for_ref(incident_id, action_ref)
    if payload.source_of_action == "event_driven_policy" and latest_action:
        if str(latest_action.get("execution_status") or "").lower() in {"executing", "executed"}:
            return {
                "skipped": True,
                "reason": "Action already launched for this incident.",
                "action": latest_action,
                "workflow": _workflow_payload(get_incident(incident_id) or incident),
            }

    response = _execute_incident_action(
        incident_id,
        RemediationActionRequest(
            remediation_id=int(remediation["id"]) if remediation and remediation.get("id") else None,
            action=action_ref,
            approved_by=payload.approved_by,
            notes=payload.notes,
            execute=True,
            source_of_action=payload.source_of_action,
        ),
        auth,
        background_tasks,
    )
    if payload.source_of_action == "event_driven_policy":
        record_audit(
            "eda_policy_triggered",
            payload.approved_by,
            {
                "action_ref": action_ref,
                "notes": payload.notes,
                "execution_status": str(((response.get("action") or {}).get("execution_status")) or "unknown"),
            },
            incident_id=incident_id,
        )
    return response


@app.post("/incidents/{incident_id}/remediation/{remediation_id}/reject")
def reject_remediation(
    incident_id: str,
    remediation_id: int,
    payload: RemediationRejectRequest,
    auth: AuthContext | None = Depends(require_api_key),
):
    ensure_role(auth, "operator")
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    remediation = get_incident_remediation(incident_id, remediation_id)
    if not remediation:
        raise HTTPException(status_code=404, detail="Remediation not found")
    updated_remediation = set_incident_remediation_status(incident_id, remediation_id, "rejected")
    record_audit(
        "remediation_rejected",
        payload.rejected_by,
        {
            "remediation_id": remediation_id,
            "title": remediation.get("title"),
            "notes": payload.notes,
        },
        incident_id=incident_id,
    )
    updated_incident = get_incident(incident_id) or incident
    active_items = _current_remediation_items(list_incident_remediations(incident_id))
    if not active_items and can_transition(str(updated_incident.get("status") or NEW), RCA_REJECTED):
        updated_incident = _transition_incident_with_audit(
            updated_incident,
            RCA_REJECTED,
            payload.rejected_by,
            payload.notes or "All current remediation suggestions were rejected.",
        )
    updated_incident = get_incident(incident_id) or updated_incident
    _sync_current_ticket_best_effort(
        updated_incident,
        _ticket_note(
            "Remediation rejection update",
            [
                ("Incident", incident_id),
                ("Workflow state", updated_incident.get("status")),
                ("Operator", payload.rejected_by),
                ("Remediation", remediation.get("title")),
                ("Comment", payload.notes or "Selected remediation was rejected."),
            ],
        ),
        payload.rejected_by,
        "remediation_rejected",
    )
    set_active_incidents(list_incidents(project=incident["project"]))
    return {
        "remediation": updated_remediation,
        "workflow": _workflow_payload(updated_incident),
    }


@app.post("/incidents/{incident_id}/notify/slack")
def notify_slack(incident_id: str, auth: AuthContext | None = Depends(require_api_key)):
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    result = send_slack_notification(
        f"IMS incident {incident_id}: {incident['anomaly_type']} confidence={_incident_confidence(incident):.2f} status={incident['status']}"
    )
    record_audit("slack_notified", "operator", result, incident_id=incident_id)
    record_integration("slack", result.get("status", "unknown"))
    return result


@app.post("/incidents/{incident_id}/notify/jira")
def notify_jira(incident_id: str, auth: AuthContext | None = Depends(require_api_key)):
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    result = create_jira_issue(
        summary=f"IMS incident {incident_id}",
        description=(
            f"Anomaly type: {incident['anomaly_type']}\n"
            f"Predicted confidence: {_incident_confidence(incident):.2f}\n"
            f"Anomaly score: {incident['anomaly_score']}"
        ),
    )
    record_audit("jira_created", "operator", result, incident_id=incident_id)
    record_integration("jira", result.get("status", "unknown"))
    return result


@app.post("/incidents/{incident_id}/verify")
def verify_incident(
    incident_id: str,
    payload: VerificationRequest,
    auth: AuthContext | None = Depends(require_api_key),
):
    ensure_role(auth, "operator")
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    action = get_incident_action(incident_id, payload.action_id) if payload.action_id is not None else None
    verification = record_verification(
        incident_id=incident_id,
        action_id=payload.action_id,
        verified_by=payload.verified_by,
        verification_status=payload.verification_status,
        notes=payload.notes,
        custom_resolution=payload.custom_resolution,
        metric_based=payload.metric_based,
    )
    normalized_status = str(payload.verification_status or "").strip().lower()
    if normalized_status == "verified":
        updated = _transition_incident_with_audit(
            incident,
            VERIFIED,
            auth.subject if auth else payload.verified_by,
            payload.notes or "Verification passed.",
        )
        if payload.close_after_verify:
            updated = _transition_incident_with_audit(
                updated,
                CLOSED,
                auth.subject if auth else payload.verified_by,
                "Incident closed after successful verification.",
            )
    elif normalized_status == "false_positive":
        updated = _transition_incident_with_audit(
            incident,
            FALSE_POSITIVE,
            auth.subject if auth else payload.verified_by,
            payload.notes or "Marked as false positive.",
        )
        if payload.close_after_verify:
            updated = _transition_incident_with_audit(
                updated,
                CLOSED,
                auth.subject if auth else payload.verified_by,
                "False positive closed.",
            )
    else:
        updated = _transition_incident_with_audit(
            incident,
            VERIFICATION_FAILED,
            auth.subject if auth else payload.verified_by,
            payload.notes or "Verification failed or requires more work.",
        )

    current_ticket = None
    tickets = list_incident_tickets(incident_id)
    if tickets:
        current_ticket = next((ticket for ticket in tickets if ticket.get("id") == updated.get("current_ticket_id")), tickets[0])
    extract = _maybe_create_resolution_extract(updated, verification, action=action, ticket=current_ticket)
    if extract:
        record_audit(
            "resolution_extract_created",
            payload.verified_by,
            extract,
            incident_id=incident_id,
        )
    record_audit(
        "verification_recorded",
        payload.verified_by,
        verification,
        incident_id=incident_id,
    )
    updated = get_incident(incident_id) or updated
    _sync_current_ticket_best_effort(
        updated,
        _ticket_note(
            "Verification update",
            [
                ("Incident", incident_id),
                ("Workflow state", updated.get("status")),
                ("Operator", payload.verified_by),
                ("Outcome", normalized_status or payload.verification_status),
                ("Related action", (action or {}).get("result_summary") or (action or {}).get("execution_status")),
                ("Actual fix", payload.custom_resolution),
                ("Comment", payload.notes or "Verification recorded from the incident workflow."),
            ],
        ),
        payload.verified_by,
        "verification",
    )
    record_verification_metric(normalized_status or "unknown")
    set_active_incidents(list_incidents(project=incident["project"]))
    return {
        "verification": verification,
        "resolution_extract": extract,
        "workflow": _workflow_payload(updated),
    }


@app.post("/incidents/{incident_id}/related")
def related_incident_records(
    incident_id: str,
    payload: RelatedRecordsRequest,
    auth: AuthContext | None = Depends(require_api_key),
):
    ensure_role(auth, "operator")
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    query = _related_context_query(incident)
    anomaly_type = canonical_anomaly_type(str(incident.get("anomaly_type") or NORMAL_ANOMALY_TYPE))
    documents = retrieve_context(
        query,
        limit=payload.limit,
        collections=RELATED_CONTEXT_COLLECTIONS,
        anomaly_type=anomaly_type,
    )
    knowledge_articles = retrieve_knowledge_articles(
        query,
        category=_incident_category(incident),
        anomaly_type=anomaly_type,
        limit=payload.knowledge_limit,
    )
    categorized = _categorize_related_documents(documents)
    if knowledge_articles:
        categorized["knowledge"] = knowledge_articles
    return {
        "incident_id": incident_id,
        "documents": documents,
        **categorized,
    }


@app.get("/knowledge/articles/{reference:path}")
def knowledge_article(reference: str, auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "operator")
    article = get_document_by_reference(reference, collection_name=RUNBOOK_COLLECTION)
    if not article:
        raise HTTPException(status_code=404, detail="Knowledge article not found")
    return {"article": article}


@app.get("/documents/{collection}/{reference:path}")
def document_detail(collection: str, reference: str, auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "operator")
    if collection not in DEFAULT_MILVUS_COLLECTIONS:
        raise HTTPException(status_code=404, detail="Document collection not found")
    document = get_document_by_reference(reference, collection_name=collection)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"document": document}


@app.get("/incidents/{incident_id}/tickets")
def list_incident_ticket_references(incident_id: str, auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "operator")
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    workflow = _workflow_payload(incident)
    return {
        "tickets": workflow["tickets"],
        "current_ticket": workflow["current_ticket"],
    }


@app.post("/incidents/{incident_id}/tickets/{provider}")
def create_or_update_incident_ticket(
    incident_id: str,
    provider: str,
    payload: TicketRequest,
    auth: AuthContext | None = Depends(require_api_key),
):
    ensure_role(auth, "operator")
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    try:
        result = _sync_ticket_provider(
            incident,
            provider,
            note=payload.note,
            force=payload.force,
            source_url=payload.source_url,
        )
    except TicketProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ticket": result,
        "workflow": _workflow_payload(get_incident(incident_id) or incident),
    }


@app.post("/incidents/{incident_id}/tickets/{ticket_id}/sync")
def sync_incident_ticket(
    incident_id: str,
    ticket_id: int,
    payload: TicketSyncRequest,
    auth: AuthContext | None = Depends(require_api_key),
):
    ensure_role(auth, "operator")
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    ticket = get_incident_ticket(incident_id, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    try:
        result = _sync_ticket_provider(
            incident,
            str(ticket.get("provider") or "plane"),
            note=payload.note,
            force=True,
            source_url=payload.source_url,
        )
    except TicketProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ticket": result,
        "workflow": _workflow_payload(get_incident(incident_id) or incident),
    }


@app.get("/tickets/{provider}/{external_id}")
def get_ticket_reference(
    provider: str,
    external_id: str,
    auth: AuthContext | None = Depends(require_api_key),
):
    ensure_role(auth, "operator")
    ticket = get_ticket_by_provider_external_id(provider, external_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    incident_id = str(ticket.get("incident_id") or "")
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])

    detailed_ticket = ticket | {
        "sync_events": list_ticket_sync_events(int(ticket["id"]))[:20],
        "comments": list_ticket_comments(int(ticket["id"]))[:20],
    }
    return {
        "ticket": detailed_ticket,
        "workflow": _workflow_payload(incident),
    }


@app.post("/incidents/{incident_id}/tickets/{ticket_id}/extract-resolution")
def extract_ticket_resolution(
    incident_id: str,
    ticket_id: int,
    payload: ResolutionExtractRequest,
    auth: AuthContext | None = Depends(require_api_key),
):
    ensure_role(auth, "operator")
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    ticket = get_incident_ticket(incident_id, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    summary = str(payload.summary or "").strip()
    if not summary:
        comments = list_ticket_comments(ticket_id)
        if payload.source_comment_id:
            selected = next((comment for comment in comments if comment.get("external_comment_id") == payload.source_comment_id), None)
            summary = str((selected or {}).get("body") or "").strip()
        elif comments:
            summary = str(comments[0].get("body") or "").strip()
    if not summary:
        raise HTTPException(status_code=400, detail="No resolution summary available to extract")
    quality = resolution_quality(True, summary, summary)
    extract = create_ticket_resolution_extract(
        incident_id=incident_id,
        ticket_id=ticket_id,
        source_comment_id=payload.source_comment_id,
        summary=summary,
        verified=payload.verified,
        verification_quality=quality,
        knowledge_weight=1.0 if quality == "high" else 0.75 if quality == "medium" else 0.55,
        success_rate=1.0 if payload.verified else 0.0,
        last_validated_at=_now_iso(),
    )
    _publish_resolution_record(
        incident,
        {
            "verified_by": auth.subject if auth else "operator",
            "verification_status": "verified" if payload.verified else "candidate",
            "notes": summary,
            "custom_resolution": summary,
        },
        extract,
        ticket=ticket,
    )
    record_audit("resolution_extract_created", auth.subject if auth else "operator", extract, incident_id=incident_id)
    return extract


@app.post("/integrations/plane/webhooks")
async def plane_webhook(request: Request):
    raw_body = await request.body()
    secret = _plane_webhook_secret()
    signature = request.headers.get("X-Plane-Signature", "")
    if secret:
        expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=403, detail="Invalid Plane webhook signature")
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid webhook payload: {exc}") from exc

    event = str(request.headers.get("X-Plane-Event") or payload.get("event") or "unknown")
    delivery_id = str(request.headers.get("X-Plane-Delivery") or "")
    action = str(payload.get("action") or "unknown")
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        data = {}

    external_id = ""
    if event == "issue":
        external_id = str(data.get("id") or "")
    elif event == "issue_comment":
        external_id = str(
            data.get("work_item")
            or data.get("work_item_id")
            or data.get("issue")
            or data.get("issue_id")
            or (data.get("issue_detail") or {}).get("id")
            or ""
        )

    ticket = get_ticket_by_provider_external_id("plane", external_id) if external_id else None
    payload_hash = hashlib.sha256(raw_body).hexdigest()
    if not ticket:
        record_ticket_sync("plane", "inbound", "unmapped")
        return {"status": "ignored", "reason": "No matching Plane ticket", "event": event, "action": action}

    sync_event = record_ticket_sync_event(
        int(ticket["id"]),
        "inbound",
        f"plane_{event}",
        delivery_id or None,
        payload_hash,
        "received",
        payload if isinstance(payload, dict) else {"value": payload},
    )
    if sync_event is None:
        return {"status": "duplicate", "delivery_id": delivery_id}

    if event == "issue":
        ticket_metadata = ticket.get("metadata") if isinstance(ticket, dict) else {}
        if not isinstance(ticket_metadata, dict):
            ticket_metadata = {}
        updated_ticket = upsert_incident_ticket(
            incident_id=str(ticket["incident_id"]),
            provider="plane",
            external_key=str(data.get("sequence_id") or ticket.get("external_key") or ""),
            external_id=str(ticket.get("external_id") or external_id),
            workspace_id=str(ticket.get("workspace_id") or payload.get("workspace_id") or ""),
            project_id=str(ticket.get("project_id") or ""),
            status=str((data.get("state_detail") or {}).get("name") or data.get("state") or action),
            url=str(ticket.get("url") or ""),
            title=str(data.get("name") or ticket.get("title") or ""),
            sync_state="received",
            last_synced_revision=ticket.get("last_synced_revision"),
            metadata=ticket_metadata | {"webhook": payload},
        )
        record_audit(
            "plane_webhook_processed",
            "plane-webhook",
            {"event": event, "action": action, "ticket_id": updated_ticket.get("id")},
            incident_id=str(ticket["incident_id"]),
        )
    elif event == "issue_comment":
        body = _text_from_rich_comment(data.get("comment_json") or data.get("comment_html") or "")
        comment = upsert_ticket_comment(
            int(ticket["id"]),
            str(data.get("id") or delivery_id or payload_hash[:12]),
            _plane_actor_name(payload, data),
            body,
            "plane_comment",
            created_at=str(data.get("created_at") or _now_iso()),
        )
        record_audit(
            "plane_webhook_processed",
            "plane-webhook",
            {"event": event, "action": action, "ticket_id": ticket.get("id"), "comment_id": comment.get("id")},
            incident_id=str(ticket["incident_id"]),
        )
    record_ticket_sync("plane", "inbound", "received")
    return {"status": "processed", "event": event, "action": action}


@app.get("/audit")
def audit(limit: int = 100, incident_id: str | None = None, auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "operator")
    if incident_id:
        incident = get_incident(incident_id)
        if not incident:
            raise HTTPException(status_code=404, detail="Incident not found")
        ensure_project_access(auth, incident["project"])
    return list_audit_events(limit=limit, incident_id=incident_id)


@app.get("/approvals")
def approvals(limit: int = 100, incident_id: str | None = None, auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "operator")
    if incident_id:
        incident = get_incident(incident_id)
        if not incident:
            raise HTTPException(status_code=404, detail="Incident not found")
        ensure_project_access(auth, incident["project"])
    return list_approvals(limit=limit, incident_id=incident_id)


@app.get("/models")
def models(auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "operator")
    return load_registry()


@app.get("/models/{version}")
def model_details(version: str, auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "operator")
    model = get_model(version)
    if not model:
        raise HTTPException(status_code=404, detail="Model version not found")
    return model


@app.post("/models/promote")
def promote_registry_model(payload: ModelPromotionRequest, auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "admin")
    try:
        registry = promote_model(payload.version, payload.approved_by, payload.stage)
    except ValueError as exc:
        record_model_promotion(payload.stage, "failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record_audit(
        "model_promoted",
        payload.approved_by,
        payload.model_dump(),
    )
    record_model_promotion(payload.stage, "passed")
    return registry


@app.get("/datasets")
def datasets(auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "operator")
    return list_datasets()


@app.get("/feature-schemas")
def feature_schemas(auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "operator")
    return list_feature_schemas()


@app.get("/integrations/status")
def integrations_status(auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "operator")
    return integration_status()


@app.get("/automation/actions")
def automation_actions(auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "operator")
    return _list_automation_actions()


@app.post("/automation/bootstrap")
def bootstrap_automation(auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "admin")
    try:
        result = {
            "aap": aap_bootstrap_resources(),
            "eda": eda_bootstrap_resources(),
        }
        clear_integration_status_cache()
        _clear_service_snapshot_cache()
        return result
    except (AAPAutomationError, EDAAutomationError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/platform/status")
def platform_status(auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "operator")
    incidents = list_incidents()
    return {
        "incident_count": len(incidents),
        "open_incidents": sum(1 for incident in incidents if is_active_state(str(incident.get("status") or NEW))),
        "approval_count": len(list_approvals(limit=100)),
        "model_registry": load_registry(),
        "integrations": integration_status(),
        "automation_actions": _list_automation_actions(),
    }


@app.get("/console/state")
def console_state(project: str = "ims-demo", auth: AuthContext | None = Depends(require_api_key)):
    ensure_project_access(auth, project)
    return _build_console_state(project)


@app.post("/console/run-scenario")
def console_run_scenario(payload: ConsoleScenarioRequest, auth: AuthContext | None = Depends(require_api_key)):
    ensure_project_access(auth, payload.project)
    scenario_name = normalize_scenario_name(payload.scenario)
    if scenario_name not in CONSOLE_SCENARIOS:
        raise HTTPException(status_code=400, detail=f"Unsupported scenario {payload.scenario}")

    trace_actor = auth.subject if auth else "console-ui"
    feature_window_url = f"{FEATURE_GATEWAY_URL}/live-window/{scenario_name}"
    feature_window_request_payload = {
        "path_params": {"scenario": scenario_name},
        "project": payload.project,
    }
    feature_window_request_timestamp = _now_iso()
    feature_window = _request_json("GET", feature_window_url)
    feature_window_response_timestamp = _now_iso()
    features = feature_window.get("features")
    if not isinstance(features, dict):
        raise HTTPException(status_code=502, detail="Feature gateway returned an invalid feature window payload")

    labels = feature_window.get("labels")
    if not isinstance(labels, dict):
        labels = {}
    anomaly_type_hint = canonical_anomaly_type(
        str(feature_window.get("anomaly_type") or labels.get("anomaly_type") or scenario_name)
    )
    scoring_features = dict(features)
    scoring_features["scenario_name"] = str(feature_window.get("scenario_name") or scenario_name)
    scoring_features["feature_source"] = str(feature_window.get("feature_source") or "feature-gateway-console")
    scoring_features["source"] = str(feature_window.get("source") or "feature-gateway-console")
    scoring_features["transport"] = str(feature_window.get("transport") or "udp")
    scoring_features["call_limit"] = feature_window.get("call_limit")
    scoring_features["rate"] = feature_window.get("rate")
    scoring_features["contributing_conditions"] = list(feature_window.get("contributing_conditions") or [])

    score = _request_json(
        "POST",
        f"{ANOMALY_SERVICE_URL}/score",
        {
            "features": scoring_features,
            "project": payload.project,
            "feature_window_id": feature_window.get("window_id"),
            "scenario_name": scenario_name,
            "anomaly_type_hint": anomaly_type_hint,
        },
    )

    incident_id = str(score.get("incident_id") or "") or None
    if incident_id:
        _record_debug_trace_packets(
            incident_id,
            trace_actor,
            interaction_trace_packets(
                category="api",
                service="control-plane",
                target="feature-gateway",
                method="GET",
                endpoint=feature_window_url,
                request_payload=feature_window_request_payload,
                response_payload=feature_window,
                request_timestamp=feature_window_request_timestamp,
                response_timestamp=feature_window_response_timestamp,
                metadata={
                    "project": payload.project,
                    "scenario_name": scenario_name,
                    "feature_window_id": feature_window.get("window_id"),
                },
            ),
        )
    record_audit(
        "scenario_executed",
        trace_actor,
        {
            "project": payload.project,
            "scenario": scenario_name,
            "feature_source": feature_window.get("feature_source"),
            "feature_window_id": feature_window.get("window_id"),
            "window_start": feature_window.get("window_start") or feature_window.get("start_time"),
            "window_end": feature_window.get("window_end") or feature_window.get("end_time"),
            "scoring_mode": score.get("scoring_mode"),
            "is_anomaly": score.get("is_anomaly"),
            "anomaly_type": score.get("anomaly_type"),
            "anomaly_score": score.get("anomaly_score"),
            "incident_id": incident_id,
            "features": scoring_features,
            "executed_at": _now_iso(),
        },
        incident_id=incident_id,
    )

    rca_payload: Dict[str, object] | None = None
    rca_error: Dict[str, object] | None = None
    incident: Dict[str, object] | None = None
    if incident_id:
        incident = _wait_for_incident_rca(incident_id, timeout_seconds=8.0, poll_interval_seconds=0.5)
        if not incident:
            incident = get_incident(incident_id)
        current_rca = (incident or {}).get("rca_payload") or {}
        if isinstance(current_rca, dict) and current_rca:
            rca_payload = current_rca

    state = _build_console_state(payload.project)
    enriched_incident = None
    if incident:
        enriched_incident = next((item for item in state["incidents"] if item.get("id") == incident["id"]), None)

    return {
        "scenario": scenario_name,
        "feature_window": feature_window,
        "score": score,
        "rca": rca_payload,
        "rca_error": rca_error,
        "incident": enriched_incident,
        "state": state,
    }


def _automation_mode() -> str:
    explicit = os.getenv("AUTOMATION_MODE", "").strip().lower()
    if explicit in {"disabled", "simulate", "execute"}:
        return explicit
    if os.getenv("ENABLE_AUTOMATION", "false").lower() == "true":
        return "execute"
    return "simulate"


def _execute_playbook(
    action: str,
    *,
    playbook_content: str | None = None,
    playbook_label: str | None = None,
) -> tuple[str, str]:
    playbook = playbook_label or PLAYBOOKS.get(action)
    if not playbook and not playbook_content:
        return "unknown action", "rejected"
    mode = _automation_mode()
    if mode == "disabled":
        return f"automation gated; playbook {playbook or action} not executed", "pending_execution"
    if mode == "simulate":
        return f"demo automation simulated for playbook {playbook or action}", "simulated"
    binary = shutil.which("ansible-playbook")
    if not binary:
        return "ansible-playbook not installed in runtime", "failed"

    temp_path = ""
    target_playbook = playbook or action
    try:
        if playbook_content is not None:
            with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as handle:
                handle.write(playbook_content)
                temp_path = handle.name
            target_playbook = temp_path
        result = subprocess.run(
            [binary, target_playbook, "-i", "localhost,", "-c", "local"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        return output, "executed" if result.returncode == 0 else "failed"
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
