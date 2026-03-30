from typing import Dict, List

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from shared.control_plane_client import attach_rca
from shared.metrics import install_metrics
from shared.rag import build_prompt, generate_with_llm, retrieve_context
from shared.security import require_api_key


class RCARequest(BaseModel):
    incident_id: str
    context: Dict[str, object] = Field(default_factory=dict)


app = FastAPI(title="rca-service", version="0.1.0")
install_metrics(app, "rca-service")


def infer_root_cause(anomaly_type: str) -> str:
    if anomaly_type == "registration_storm":
        return "P-CSCF registration saturation causing retransmission amplification"
    if anomaly_type == "malformed_sip":
        return "Malformed INVITE traffic rejected by S-CSCF validation path"
    return "HSS latency impacting downstream IMS registration and call setup flows"


@app.get("/healthz")
def healthz():
    import os

    return {
        "status": "ok",
        "llm_endpoint_configured": bool(os.getenv("LLM_ENDPOINT", "")),
        "milvus_endpoint_configured": bool(os.getenv("MILVUS_URI", "")),
    }


@app.post("/rca", dependencies=[Depends(require_api_key)])
def rca(request: RCARequest):
    anomaly_type = str(request.context.get("anomaly_type", "service_degradation"))
    query = f"incident={request.incident_id} anomaly_type={anomaly_type} context={request.context}"
    documents = retrieve_context(query, limit=3)
    evidence: List[Dict[str, object]] = [
        {"type": "doc", "reference": doc["reference"], "weight": round(0.6 / max(len(documents), 1), 2)}
        for doc in documents[:3]
    ]
    if anomaly_type == "registration_storm":
        evidence.insert(0, {"type": "metric", "reference": "register_rate", "weight": 0.4})
    elif anomaly_type == "malformed_sip":
        evidence.insert(0, {"type": "metric", "reference": "error_4xx_ratio", "weight": 0.4})
    else:
        evidence.insert(0, {"type": "metric", "reference": "latency_p95", "weight": 0.4})
    if len(evidence) < 2:
        evidence.append({"type": "log", "reference": "fallback-log-evidence", "weight": 0.2})

    response = {
        "incident_id": request.incident_id,
        "root_cause": infer_root_cause(anomaly_type),
        "confidence": 0.83,
        "evidence": evidence,
        "recommendation": "Scale the relevant IMS function and review the active SIP traffic scenario before approving remediation.",
        "generation_mode": "local-rag",
    }
    generated = generate_with_llm(build_prompt({"incident_id": request.incident_id, **request.context}, documents))
    if generated:
        generated.setdefault("incident_id", request.incident_id)
        generated.setdefault("evidence", evidence)
        response = generated
        response["generation_mode"] = "llm-rag"

    if len(response.get("evidence", [])) < 2:
        raise ValueError("RCA output must include at least two evidence sources")

    attach_rca(
        request.incident_id,
        {
            "root_cause": response["root_cause"],
            "confidence": response["confidence"],
            "evidence": response["evidence"],
            "recommendation": response["recommendation"],
        },
    )
    return response
