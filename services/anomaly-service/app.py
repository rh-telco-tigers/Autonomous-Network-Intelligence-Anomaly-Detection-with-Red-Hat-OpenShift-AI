import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from shared.control_plane_client import create_incident
from shared.metrics import install_metrics
from shared.model_store import score_features
from shared.security import require_api_key


class ScoreRequest(BaseModel):
    features: Dict[str, object] = Field(default_factory=dict)
    project: str = "ims-demo"
    feature_window_id: Optional[str] = None


app = FastAPI(title="anomaly-service", version="0.1.0")
install_metrics(app, "anomaly-service")


@app.get("/healthz")
def healthz():
    _, _, _, model_version = score_features({})
    return {"status": "ok", "model_version": model_version}


@app.post("/score", dependencies=[Depends(require_api_key)])
def score(request: ScoreRequest):
    anomaly_score, is_anomaly, anomaly_type, model_version = score_features(request.features)
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
        "created_at": created_at,
    }
