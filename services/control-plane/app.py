import os
import shutil
import subprocess
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

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
from shared.security import AuthContext, ensure_project_access, ensure_role, require_api_key


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


app = FastAPI(title="control-plane", version="0.1.0")
install_cors(app)
install_metrics(app, "control-plane")


PLAYBOOKS = {
    "scale_scscf": "/app/automation/ansible/playbooks/scale-scscf.yaml",
    "rate_limit_pcscf": "/app/automation/ansible/playbooks/rate-limit-pcscf.yaml",
    "quarantine_imsi": "/app/automation/ansible/playbooks/quarantine-imsi.yaml",
}


@app.on_event("startup")
def startup() -> None:
    init_db()


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
        "automation_actions": automation_actions(auth),
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
