import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from shared.control_plane_client import create_incident
from shared.metrics import install_metrics
from shared.model_store import ModelUnavailableError, current_model_status, score_features_detailed
from shared.security import AuthContext, ensure_project_access, ensure_role, require_api_key


class ScoreRequest(BaseModel):
    features: Dict[str, object] = Field(default_factory=dict)
    project: str = "ims-demo"
    feature_window_id: Optional[str] = None


class BatchScoreRequest(BaseModel):
    items: List[ScoreRequest] = Field(default_factory=list)


app = FastAPI(title="anomaly-service", version="0.1.0")
install_metrics(app, "anomaly-service")


@app.get("/healthz")
def healthz():
    model_status = current_model_status()
    return {
        "status": "ok" if model_status["registry_loaded"] else "degraded",
        "model_version": model_status["deployed_model_version"],
        "scoring_modes": model_status["scoring_modes"],
        "predictive_endpoint": model_status["predictive_endpoint"],
        "artifact_present": model_status["artifact_present"],
    }


@app.get("/models/current")
def current_model(auth: AuthContext | None = Depends(require_api_key)):
    ensure_role(auth, "operator")
    return current_model_status()


@app.post("/score")
def score(request: ScoreRequest, auth: AuthContext | None = Depends(require_api_key)):
    ensure_project_access(auth, request.project)
    try:
        result = score_features_detailed(request.features)
    except ModelUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    anomaly_score = float(result["anomaly_score"])
    is_anomaly = bool(result["is_anomaly"])
    anomaly_type = str(result["anomaly_type"])
    model_version = str(result["model_version"])
    incident_id = str(uuid.uuid4()) if is_anomaly else None
    created_at = datetime.now(tz=timezone.utc).isoformat()
    if is_anomaly and incident_id:
        create_incident(
            {
                "incident_id": incident_id,
                "project": request.project,
                "feature_window_id": request.feature_window_id,
                "anomaly_score": anomaly_score,
                "anomaly_type": anomaly_type,
                "model_version": model_version,
                "feature_snapshot": request.features,
                "created_at": created_at,
            }
        )
    return {
        "anomaly_score": anomaly_score,
        "is_anomaly": is_anomaly,
        "incident_id": incident_id,
        "anomaly_type": anomaly_type,
        "model_version": model_version,
        "scoring_mode": result["scoring_mode"],
        "created_at": created_at,
    }


@app.post("/score/batch")
def score_batch(request: BatchScoreRequest, auth: AuthContext | None = Depends(require_api_key)):
    results = []
    for item in request.items:
        ensure_project_access(auth, item.project)
        results.append(score(item, auth))
    return {
        "count": len(results),
        "results": results,
    }
