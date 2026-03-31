import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field
import requests

from shared.cors import install_cors
from shared.db import (
    attach_rca,
    create_incident,
    get_incident,
    list_approvals,
    init_db,
    list_audit_events,
    list_incidents,
    record_approval,
    record_audit,
    update_incident_status,
)
from shared.integrations import create_jira_issue, integration_status, send_slack_notification
from shared.metrics import install_metrics, record_automation, record_incident, record_integration, record_model_promotion
from shared.model_registry import get_model, list_datasets, list_feature_schemas, load_registry, promote_model
from shared.security import AuthContext, ensure_project_access, ensure_role, outbound_headers, require_api_key


class IncidentCreate(BaseModel):
    incident_id: str
    project: str = "ims-demo"
    anomaly_score: float
    anomaly_type: str
    model_version: str
    feature_window_id: Optional[str] = None
    feature_snapshot: Dict[str, object] = Field(default_factory=dict)
    created_at: Optional[str] = None
    status: str = "open"


class RCAAttach(BaseModel):
    root_cause: str
    confidence: float
    evidence: List[Dict[str, object]]
    recommendation: str
    generation_mode: Optional[str] = None
    retrieved_documents: List[Dict[str, object]] = Field(default_factory=list)


class ApprovalRequest(BaseModel):
    action: str
    approved_by: str
    notes: str = ""
    execute: bool = False


class ModelPromotionRequest(BaseModel):
    version: str
    approved_by: str
    stage: str = "prod"


class ConsoleScenarioRequest(BaseModel):
    scenario: str
    project: str = "ims-demo"


app = FastAPI(title="control-plane", version="0.1.0")
install_cors(app)
install_metrics(app, "control-plane")


PLAYBOOKS = {
    "scale_scscf": "/app/automation/ansible/playbooks/scale-scscf.yaml",
    "rate_limit_pcscf": "/app/automation/ansible/playbooks/rate-limit-pcscf.yaml",
    "quarantine_imsi": "/app/automation/ansible/playbooks/quarantine-imsi.yaml",
}

CONSOLE_SCENARIOS = {"normal", "registration_storm", "malformed_invite"}
CONSOLE_CLUSTER_NAME = os.getenv("CONSOLE_CLUSTER_NAME", "ims-demo-lab")
UPSTREAM_TIMEOUT_SECONDS = float(os.getenv("CONSOLE_UPSTREAM_TIMEOUT_SECONDS", "20"))
FEATURE_GATEWAY_URL = os.getenv("FEATURE_GATEWAY_URL", "http://feature-gateway.ims-demo-lab.svc.cluster.local:8080").rstrip("/")
ANOMALY_SERVICE_URL = os.getenv("ANOMALY_SERVICE_URL", "http://anomaly-service.ims-demo-lab.svc.cluster.local:8080").rstrip("/")
RCA_SERVICE_URL = os.getenv("RCA_SERVICE_URL", "http://rca-service.ims-demo-lab.svc.cluster.local:8080").rstrip("/")
PREDICTIVE_SERVICE_URL = os.getenv(
    "PREDICTIVE_SERVICE_URL",
    "http://ims-predictive-predictor.ims-demo-lab.svc.cluster.local:8080",
).rstrip("/")


@app.on_event("startup")
def startup() -> None:
    init_db()


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _titleize(value: str | None) -> str:
    return str(value or "unknown").replace("_", " ").strip().title()


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _list_automation_actions() -> List[Dict[str, object]]:
    mode = _automation_mode()
    actions = []
    for name, playbook in PLAYBOOKS.items():
        actions.append(
            {
                "action": name,
                "playbook": playbook,
                "exists": os.path.exists(playbook),
                "automation_mode": mode,
                "automation_enabled": mode in {"simulate", "execute"},
            }
        )
    return actions


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


def _probe_service(name: str, url: str, path: str = "/healthz") -> Dict[str, object]:
    endpoint = f"{url}{path}" if url else path
    try:
        response = requests.get(endpoint, headers=outbound_headers(), timeout=UPSTREAM_TIMEOUT_SECONDS)
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


def _service_snapshot() -> List[Dict[str, object]]:
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
    return services


def _severity_from_score(score: float) -> Dict[str, str]:
    if score >= 0.95:
        return {"label": "Critical", "tone": "rose"}
    if score >= 0.8:
        return {"label": "Warning", "tone": "amber"}
    return {"label": "Medium", "tone": "sky"}


def _incident_subtitle(anomaly_type: str) -> str:
    mapping = {
        "registration_storm": "P-CSCF registration saturation causing retransmission amplification.",
        "malformed_sip": "Malformed SIP payloads are failing validation on the ingress path.",
        "malformed_invite": "Malformed INVITE payloads are failing validation on the ingress path.",
        "service_degradation": "Service latency is degrading IMS control-plane responsiveness.",
    }
    return mapping.get(anomaly_type, "Unexpected IMS behavior detected by the predictive workflow.")


def _blast_radius(anomaly_type: str) -> str:
    mapping = {
        "registration_storm": "P-CSCF, S-CSCF, anomaly-service, feature-gateway",
        "malformed_sip": "Ingress parser, validation path, anomaly-service",
        "malformed_invite": "Ingress parser, validation path, anomaly-service",
        "service_degradation": "HSS, registration flow, downstream scoring path",
    }
    return mapping.get(anomaly_type, "Feature extraction, scoring pipeline, operator workflow")


def _topology_for(anomaly_type: str) -> List[str]:
    mapping = {
        "registration_storm": ["UE", "P-CSCF", "S-CSCF", "HSS"],
        "malformed_sip": ["UE", "P-CSCF", "Validation", "S-CSCF"],
        "malformed_invite": ["UE", "P-CSCF", "Validation", "S-CSCF"],
        "service_degradation": ["UE", "P-CSCF", "S-CSCF", "HSS"],
    }
    return mapping.get(anomaly_type, ["UE", "P-CSCF", "S-CSCF", "HSS"])


def _default_recommendation(anomaly_type: str) -> str:
    mapping = {
        "registration_storm": "Scale the relevant IMS function and review the active traffic profile before approving remediation.",
        "malformed_sip": "Quarantine the malformed traffic source and inspect the SIP generator profile.",
        "malformed_invite": "Quarantine the malformed traffic source and inspect the SIP generator profile.",
        "service_degradation": "Inspect infrastructure pressure and clear the bottleneck before closing the incident.",
    }
    return mapping.get(anomaly_type, "Review the feature window, inspect RCA evidence, and approve the safest remediation action.")


def _incident_impact(incident: Dict[str, object]) -> str:
    anomaly_type = str(incident.get("anomaly_type", "service_degradation"))
    features = incident.get("feature_snapshot") or {}
    if not isinstance(features, dict):
        features = {}

    register_rate = _coerce_float(features.get("register_rate"))
    invite_rate = _coerce_float(features.get("invite_rate"))
    latency_p95 = _coerce_float(features.get("latency_p95") or features.get("latency_p95_ms"))
    retransmissions = _coerce_float(features.get("retransmission_count"))
    error_4xx = _coerce_float(features.get("error_4xx_ratio"))

    if anomaly_type == "registration_storm":
        return (
            f"Registration rate reached {register_rate:.2f}/s, retransmissions are {retransmissions:.0f}, "
            f"and latency p95 is {latency_p95:.0f} ms."
        )
    if anomaly_type in {"malformed_sip", "malformed_invite"}:
        return (
            f"INVITE rate is {invite_rate:.2f}/s and the 4xx ratio is {error_4xx:.2f}, "
            "showing ingress validation rejects for malformed traffic."
        )
    return (
        f"Latency p95 is {latency_p95:.0f} ms with register rate at {register_rate:.2f}/s, "
        "indicating service pressure on the registration path."
    )


def _explainability_for(incident: Dict[str, object]) -> List[Dict[str, object]]:
    anomaly_type = str(incident.get("anomaly_type", "service_degradation"))
    palettes = ["sky", "amber", "rose", "emerald"]
    if anomaly_type == "registration_storm":
        weights = {
            "register_rate": 0.4,
            "retransmission_count": 0.3,
            "latency_p95": 0.2,
            "error_4xx_ratio": 0.1,
        }
    elif anomaly_type in {"malformed_sip", "malformed_invite"}:
        weights = {
            "error_4xx_ratio": 0.4,
            "payload_variance": 0.25,
            "invite_rate": 0.2,
            "retransmission_count": 0.15,
        }
    else:
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
        "incident_approved": "Action approved",
        "slack_notified": "Slack notified",
        "jira_created": "Jira ticket created",
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
        score = _coerce_float(payload.get("anomaly_score"))
        return f"{_titleize(str(anomaly_type))} raised with score {score:.2f}."
    if event_type == "rca_attached":
        confidence = _coerce_float(payload.get("confidence"))
        return f"RCA attached with confidence {confidence:.2f}."
    if event_type == "incident_approved":
        action = payload.get("action", "unknown_action")
        execute = bool(payload.get("execute"))
        return f"{_titleize(str(action))} approved ({'execute' if execute else 'record only'})."
    if event_type == "slack_notified":
        return f"Slack notification status: {payload.get('status', 'unknown')}."
    if event_type == "jira_created":
        issue_key = payload.get("issue_key", "pending")
        return f"Jira issue {issue_key} created."
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
                "detail": _incident_subtitle(str(incident.get("anomaly_type", "service_degradation"))),
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
            evidence.append(
                {
                    "title": str(document.get("reference") or document.get("title") or "retrieved-document"),
                    "detail": (
                        f"{document.get('doc_type', 'document')} "
                        f"· score {float(document.get('score', 0.0)):.2f}"
                    ),
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
            evidence.append(
                {
                    "title": str(item.get("reference") or item.get("type") or "evidence"),
                    "detail": f"weight {float(item.get('weight', 0.0)):.2f}",
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
                    f"score {_coerce_float(candidate.get('anomaly_score')):.2f} "
                    f"· {_titleize(str(candidate.get('anomaly_type', 'unknown')))}"
                ),
            }
        )
        if len(matches) >= 3:
            break
    return matches


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
) -> Dict[str, object]:
    score = _coerce_float(incident.get("anomaly_score"))
    anomaly_type = str(incident.get("anomaly_type", "service_degradation"))
    severity = _severity_from_score(score)
    rca_payload = incident.get("rca_payload") or {}
    if not isinstance(rca_payload, dict):
        rca_payload = {}

    recommendation = str(
        incident.get("recommendation")
        or rca_payload.get("recommendation")
        or _default_recommendation(anomaly_type)
    )
    return incident | {
        "severity": severity["label"],
        "severity_tone": severity["tone"],
        "subtitle": _incident_subtitle(anomaly_type),
        "impact": _incident_impact(incident),
        "blast_radius": _blast_radius(anomaly_type),
        "recommendation": recommendation,
        "narrative": str(rca_payload.get("root_cause") or _incident_subtitle(anomaly_type)),
        "timeline": _timeline_for_incident(incident, audit_events),
        "evidence_sources": _evidence_sources(incident),
        "similar_incidents": _similar_incidents(incident, incidents),
        "explainability": _explainability_for(incident),
        "payload_pretty": _payload_view(incident),
        "topology": _topology_for(anomaly_type),
    }


def _traffic_preview(feature_window: Dict[str, object] | None) -> Dict[str, object]:
    if not feature_window:
        return {"rows": [], "stats": {"requests_per_second": 0.0, "retry_ratio": 0.0, "active_node": "pcscf-1"}, "packet_sample": ""}

    features = feature_window.get("features", feature_window)
    if not isinstance(features, dict):
        features = {}
    scenario_name = str(feature_window.get("scenario_name") or feature_window.get("anomaly_type") or "normal")
    latency = _coerce_float(features.get("latency_p95") or features.get("latency_p95_ms"), 80.0)
    register_rate = _coerce_float(features.get("register_rate"))
    invite_rate = _coerce_float(features.get("invite_rate"))
    bye_rate = _coerce_float(features.get("bye_rate"))
    retransmissions = _coerce_float(features.get("retransmission_count"))
    total_rate = max(register_rate + invite_rate + bye_rate, 0.0)
    retry_ratio = round(min(retransmissions / max(total_rate, 1.0), 1.0), 2)
    base = datetime.now(tz=timezone.utc)

    if scenario_name == "registration_storm":
        templates = [
            ("REGISTER", "UE → P-CSCF → S-CSCF", "401 retry", latency * 1.1),
            ("REGISTER", "UE → P-CSCF → S-CSCF", "202 accepted", latency),
            ("REGISTER", "UE → P-CSCF → S-CSCF", "200 OK", latency * 0.7),
            ("REGISTER", "UE → P-CSCF → S-CSCF", "401 retry", latency * 1.2),
            ("REGISTER", "UE → P-CSCF → S-CSCF", "200 OK", latency * 0.6),
            ("REGISTER", "UE → P-CSCF → S-CSCF", "202 accepted", latency * 0.9),
        ]
        packet_sample = (
            "REGISTER sip:ims.demo.lab SIP/2.0\n"
            "Via: SIP/2.0/UDP 10.0.8.12:5060\n"
            "From: <sip:user@ims.demo.lab>\n"
            "To: <sip:user@ims.demo.lab>\n"
            "Call-ID: registration-surge\n"
            "CSeq: 314159 REGISTER"
        )
    elif scenario_name in {"malformed_sip", "malformed_invite"}:
        templates = [
            ("INVITE", "UE → P-CSCF → Validation", "Malformed", 44),
            ("INVITE", "UE → P-CSCF → Validation", "400 reject", 38),
            ("REGISTER", "UE → P-CSCF → S-CSCF", "200 OK", 110),
            ("INVITE", "UE → P-CSCF → Validation", "Malformed", 42),
            ("INVITE", "UE → P-CSCF → Validation", "400 reject", 39),
            ("REGISTER", "UE → P-CSCF → S-CSCF", "200 OK", 105),
        ]
        packet_sample = (
            "INVITE sip:user@ims.demo.lab SIP/2.0\n"
            "Via: SIP/2.0/UDP 10.0.8.44:5060\n"
            "From malformed header\n"
            "To: <sip:user@ims.demo.lab>\n"
            "Call-ID: malformed-invite\n"
            "CSeq: 11 INVITE"
        )
    else:
        templates = [
            ("REGISTER", "UE → P-CSCF → S-CSCF", "200 OK", 90),
            ("INVITE", "UE → P-CSCF → S-CSCF", "200 OK", 70),
            ("BYE", "UE → P-CSCF → S-CSCF", "200 OK", 55),
            ("REGISTER", "UE → P-CSCF → S-CSCF", "200 OK", 85),
            ("INVITE", "UE → P-CSCF → S-CSCF", "200 OK", 68),
            ("BYE", "UE → P-CSCF → S-CSCF", "200 OK", 50),
        ]
        packet_sample = (
            "REGISTER sip:ims.demo.lab SIP/2.0\n"
            "Via: SIP/2.0/UDP 10.0.8.10:5060\n"
            "From: <sip:user@ims.demo.lab>\n"
            "To: <sip:user@ims.demo.lab>\n"
            "Call-ID: nominal-register\n"
            "CSeq: 1 REGISTER"
        )

    rows = []
    for index, (method, path, status, row_latency) in enumerate(templates):
        rows.append(
            {
                "time": (base.replace(microsecond=0)).strftime("%H:%M:%S"),
                "method": method,
                "path": path,
                "status": status,
                "latency_ms": round(float(row_latency), 1),
                "sequence": index,
            }
        )
        base = base.replace(microsecond=0)

    return {
        "scenario_name": scenario_name,
        "source": str(feature_window.get("feature_source", "derived")),
        "rows": rows,
        "stats": {
            "requests_per_second": round(total_rate, 2),
            "retry_ratio": retry_ratio,
            "active_node": "pcscf-1",
        },
        "packet_sample": packet_sample,
    }


def _build_console_state(project: str) -> Dict[str, object]:
    incidents = list_incidents(project=project)
    audit_events = list_audit_events(limit=100)
    approvals = list_approvals(limit=100)
    services = _service_snapshot()
    registry = load_registry()
    enriched_incidents = [_enrich_incident(incident, audit_events, incidents) for incident in incidents]
    latest_incident = enriched_incidents[0] if enriched_incidents else None
    open_incidents = [incident for incident in enriched_incidents if str(incident.get("status", "open")) == "open"]
    healthy_services = sum(1 for service in services if bool(service.get("ok")))
    active_scenario = (
        str(latest_incident.get("anomaly_type")) if latest_incident else (registry.get("dataset_version") or "normal")
    )
    traffic_preview = _traffic_preview(
        {
            "scenario_name": latest_incident.get("anomaly_type") if latest_incident else "normal",
            "feature_source": "incident-feature-snapshot",
            "features": latest_incident.get("feature_snapshot", {}) if latest_incident else {},
        }
        if latest_incident
        else None
    )
    return {
        "generated_at": _now_iso(),
        "cluster": {
            "name": CONSOLE_CLUSTER_NAME,
            "status": "degraded" if open_incidents or healthy_services < len(services) else "healthy",
            "active_incident_id": latest_incident.get("id") if latest_incident else None,
            "rca_status": "attached" if latest_incident and latest_incident.get("rca_payload") else "none",
            "current_scenario": active_scenario,
            "auto_refresh_seconds": 15,
        },
        "summary": {
            "incident_count": len(enriched_incidents),
            "open_incidents": len(open_incidents),
            "critical_incidents": sum(1 for incident in enriched_incidents if incident.get("severity") == "Critical"),
            "latest_score": _coerce_float(latest_incident.get("anomaly_score")) if latest_incident else 0.0,
            "healthy_services": healthy_services,
            "service_count": len(services),
        },
        "incidents": enriched_incidents,
        "audit": audit_events,
        "approvals": approvals,
        "models": registry,
        "services": services,
        "integrations": integration_status(),
        "automation_actions": _list_automation_actions(),
        "traffic_preview": traffic_preview,
    }


@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "db_path": os.getenv("CONTROL_PLANE_DB_PATH", "/tmp/ims-demo-control-plane.db"),
        "ansible_available": shutil.which("ansible-playbook") is not None,
        "automation_mode": _automation_mode(),
        "integrations": integration_status(),
        "registry_loaded": bool(load_registry().get("models")),
    }


@app.post("/incidents")
def post_incident(payload: IncidentCreate, auth: AuthContext | None = Depends(require_api_key)):
    ensure_project_access(auth, payload.project)
    incident = create_incident(payload.model_dump())
    record_audit("incident_created", "anomaly-service", incident, incident_id=incident["id"])
    record_incident(incident["project"], incident["anomaly_type"], incident["status"])
    return incident


@app.get("/incidents")
def get_incidents(project: str | None = None, auth: AuthContext | None = Depends(require_api_key)):
    if project:
        ensure_project_access(auth, project)
        return list_incidents(project=project)
    if auth is None or "*" in auth.projects:
        return list_incidents()

    incidents = []
    for allowed_project in auth.projects:
        incidents.extend(list_incidents(project=allowed_project))
    incidents.sort(key=lambda item: item["created_at"], reverse=True)
    return incidents


@app.get("/incidents/{incident_id}")
def get_incident_by_id(incident_id: str, auth: AuthContext | None = Depends(require_api_key)):
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    return incident


@app.post("/incidents/{incident_id}/rca")
def post_rca(incident_id: str, payload: RCAAttach, auth: AuthContext | None = Depends(require_api_key)):
    incident = attach_rca(incident_id, payload.model_dump())
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    record_audit("rca_attached", "rca-service", payload.model_dump(), incident_id=incident_id)
    return incident


@app.post("/incidents/{incident_id}/approve")
def approve_incident(incident_id: str, payload: ApprovalRequest, auth: AuthContext | None = Depends(require_api_key)):
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    if payload.execute:
        ensure_role(auth, "automation")

    output = "execution skipped"
    status = "approved"
    if payload.execute:
        output, status = _execute_playbook(payload.action)

    approval = record_approval(
        incident_id=incident_id,
        action=payload.action,
        approved_by=payload.approved_by,
        execute=payload.execute,
        status=status,
        output=output,
    )
    next_status = "resolved" if status in {"executed", "simulated"} else "acknowledged" if status in {"approved", "pending_execution"} else "open"
    update_incident_status(incident_id, next_status)
    record_audit("incident_approved", payload.approved_by, payload.model_dump(), incident_id=incident_id)
    record_automation(payload.action, status)
    return approval


@app.post("/incidents/{incident_id}/notify/slack")
def notify_slack(incident_id: str, auth: AuthContext | None = Depends(require_api_key)):
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    ensure_project_access(auth, incident["project"])
    result = send_slack_notification(
        f"IMS incident {incident_id}: {incident['anomaly_type']} score={incident['anomaly_score']} status={incident['status']}"
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
        description=f"Anomaly type: {incident['anomaly_type']}\nScore: {incident['anomaly_score']}",
    )
    record_audit("jira_created", "operator", result, incident_id=incident_id)
    record_integration("jira", result.get("status", "unknown"))
    return result


@app.get("/audit")
def audit(limit: int = 100, auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "operator")
    return list_audit_events(limit=limit)


@app.get("/approvals")
def approvals(limit: int = 100, auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "operator")
    return list_approvals(limit=limit)


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


@app.get("/platform/status")
def platform_status(auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "operator")
    incidents = list_incidents()
    return {
        "incident_count": len(incidents),
        "open_incidents": sum(1 for incident in incidents if incident["status"] == "open"),
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
    if payload.scenario not in CONSOLE_SCENARIOS:
        raise HTTPException(status_code=400, detail=f"Unsupported scenario {payload.scenario}")

    feature_window = _request_json("GET", f"{FEATURE_GATEWAY_URL}/live-window/{payload.scenario}")
    features = feature_window.get("features")
    if not isinstance(features, dict):
        raise HTTPException(status_code=502, detail="Feature gateway returned an invalid feature window payload")

    score = _request_json(
        "POST",
        f"{ANOMALY_SERVICE_URL}/score",
        {
            "features": features,
            "project": payload.project,
            "feature_window_id": feature_window.get("window_id"),
        },
    )

    incident_id = str(score.get("incident_id") or "") or None
    record_audit(
        "scenario_executed",
        auth.subject if auth else "console-ui",
        {
            "scenario": payload.scenario,
            "feature_source": feature_window.get("feature_source"),
            "feature_window_id": feature_window.get("window_id"),
            "window_start": feature_window.get("window_start"),
            "window_end": feature_window.get("window_end"),
            "scoring_mode": score.get("scoring_mode"),
            "is_anomaly": score.get("is_anomaly"),
        },
        incident_id=incident_id,
    )

    rca_payload: Dict[str, object] | None = None
    incident: Dict[str, object] | None = None
    if incident_id:
        rca_payload = _request_json(
            "POST",
            f"{RCA_SERVICE_URL}/rca",
            {
                "incident_id": incident_id,
                "context": {
                    "project": payload.project,
                    "scenario_name": payload.scenario,
                    "anomaly_type": score.get("anomaly_type"),
                    "feature_window_id": feature_window.get("window_id"),
                    "features": features,
                },
            },
        )
        incident = get_incident(incident_id)

    state = _build_console_state(payload.project)
    enriched_incident = None
    if incident:
        enriched_incident = next((item for item in state["incidents"] if item.get("id") == incident["id"]), None)

    return {
        "scenario": payload.scenario,
        "feature_window": feature_window,
        "score": score,
        "rca": rca_payload,
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


def _execute_playbook(action: str) -> tuple[str, str]:
    playbook = PLAYBOOKS.get(action)
    if not playbook:
        return "unknown action", "rejected"
    mode = _automation_mode()
    if mode == "disabled":
        return f"automation gated; playbook {playbook} not executed", "pending_execution"
    if mode == "simulate":
        return f"demo automation simulated for playbook {playbook}", "simulated"
    binary = shutil.which("ansible-playbook")
    if not binary:
        return "ansible-playbook not installed in runtime", "failed"

    result = subprocess.run(
        [binary, playbook, "-i", "localhost,", "-c", "local"],
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = (result.stdout + "\n" + result.stderr).strip()
    return output, "executed" if result.returncode == 0 else "failed"
