import os
import shutil
import subprocess
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from shared.db import attach_rca, create_incident, get_incident, init_db, list_audit_events, list_incidents, record_approval, record_audit
from shared.integrations import create_jira_issue, send_slack_notification
from shared.metrics import install_metrics
from shared.security import require_api_key


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


class ApprovalRequest(BaseModel):
    action: str
    approved_by: str
    notes: str = ""
    execute: bool = False


app = FastAPI(title="control-plane", version="0.1.0")
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
    }


@app.post("/incidents", dependencies=[Depends(require_api_key)])
def post_incident(payload: IncidentCreate):
    incident = create_incident(payload.model_dump())
    record_audit("incident_created", "anomaly-service", incident, incident_id=incident["id"])
    return incident


@app.get("/incidents")
def get_incidents():
    return list_incidents()


@app.get("/incidents/{incident_id}")
def get_incident_by_id(incident_id: str):
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident


@app.post("/incidents/{incident_id}/rca", dependencies=[Depends(require_api_key)])
def post_rca(incident_id: str, payload: RCAAttach):
    incident = attach_rca(incident_id, payload.model_dump())
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    record_audit("rca_attached", "rca-service", payload.model_dump(), incident_id=incident_id)
    return incident


@app.post("/incidents/{incident_id}/approve", dependencies=[Depends(require_api_key)])
def approve_incident(incident_id: str, payload: ApprovalRequest):
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

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
    record_audit("incident_approved", payload.approved_by, payload.model_dump(), incident_id=incident_id)
    return approval


@app.post("/incidents/{incident_id}/notify/slack", dependencies=[Depends(require_api_key)])
def notify_slack(incident_id: str):
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    result = send_slack_notification(
        f"IMS incident {incident_id}: {incident['anomaly_type']} score={incident['anomaly_score']} status={incident['status']}"
    )
    record_audit("slack_notified", "operator", result, incident_id=incident_id)
    return result


@app.post("/incidents/{incident_id}/notify/jira", dependencies=[Depends(require_api_key)])
def notify_jira(incident_id: str):
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    result = create_jira_issue(
        summary=f"IMS incident {incident_id}",
        description=f"Anomaly type: {incident['anomaly_type']}\nScore: {incident['anomaly_score']}",
    )
    record_audit("jira_created", "operator", result, incident_id=incident_id)
    return result


@app.get("/audit")
def audit(limit: int = 100):
    return list_audit_events(limit=limit)


@app.get("/models")
def models():
    registry_path = "/app/ai/registry/model_registry.json"
    if not os.path.exists(registry_path):
        return {"deployed_model_version": None, "models": []}
    with open(registry_path, "r", encoding="utf-8") as handle:
        return __import__("json").load(handle)


def _execute_playbook(action: str) -> tuple[str, str]:
    playbook = PLAYBOOKS.get(action)
    if not playbook:
        return "unknown action", "rejected"
    if not os.getenv("ENABLE_AUTOMATION", "false").lower() == "true":
        return f"automation gated; playbook {playbook} not executed", "pending_execution"
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

