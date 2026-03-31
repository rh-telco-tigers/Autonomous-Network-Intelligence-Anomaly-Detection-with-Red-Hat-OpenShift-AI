import json
from typing import Dict, List

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from shared.control_plane_client import attach_rca
from shared.cors import install_cors
from shared.metrics import install_metrics, record_rca
from shared.rag import build_prompt, generate_with_llm, publish_document, retrieve_context
from shared.security import require_api_key


class RCARequest(BaseModel):
    incident_id: str
    context: Dict[str, object] = Field(default_factory=dict)


app = FastAPI(title="rca-service", version="0.1.0")
install_cors(app)
install_metrics(app, "rca-service")


def infer_root_cause(anomaly_type: str) -> str:
    if anomaly_type == "registration_storm":
        return "P-CSCF registration saturation causing retransmission amplification"
    if anomaly_type == "malformed_sip":
        return "Malformed INVITE traffic rejected by S-CSCF validation path"
    return "HSS latency impacting downstream IMS registration and call setup flows"


def build_evidence(anomaly_type: str, documents: List[Dict[str, object]]) -> List[Dict[str, object]]:
    evidence: List[Dict[str, object]] = [
        {
            "type": "doc",
            "reference": str(doc["reference"]),
            "weight": round(0.6 / max(len(documents), 1), 2),
        }
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
    return evidence


def compute_confidence(evidence: List[Dict[str, object]], documents: List[Dict[str, object]]) -> float:
    evidence_weight = sum(float(item.get("weight", 0.0)) for item in evidence)
    document_score = 0.0
    if documents:
        document_score = sum(float(doc.get("score", 0.0)) for doc in documents[:3]) / len(documents[:3])
    confidence = 0.45 + min(evidence_weight, 1.0) * 0.35 + max(min(document_score, 1.0), 0.0) * 0.2
    return round(max(0.35, min(confidence, 0.98)), 2)


def summarize_documents(documents: List[Dict[str, object]]) -> List[Dict[str, object]]:
    summaries = []
    for doc in documents:
        summaries.append(
            {
                "title": str(doc.get("title", "")),
                "reference": str(doc.get("reference", "")),
                "doc_type": str(doc.get("doc_type", "")),
                "collection": str(doc.get("collection", "")),
                "score": float(doc.get("score", 0.0)),
                "excerpt": str(doc.get("content", ""))[:220],
            }
        )
    return summaries


def normalize_response(response: Dict[str, object], documents: List[Dict[str, object]], anomaly_type: str, incident_id: str) -> Dict[str, object]:
    normalized = dict(response)
    normalized["incident_id"] = incident_id
    normalized.setdefault("root_cause", infer_root_cause(anomaly_type))
    normalized.setdefault(
        "recommendation",
        "Scale the relevant IMS function and review the active SIP traffic scenario before approving remediation.",
    )
    evidence = normalized.get("evidence")
    if not isinstance(evidence, list) or len(evidence) < 2:
        evidence = build_evidence(anomaly_type, documents)
    document_refs = {str(doc.get("reference", "")) for doc in documents}
    if documents and not any(str(item.get("reference", "")) in document_refs for item in evidence):
        evidence = list(evidence) + [
            {
                "type": "doc",
                "reference": str(documents[0].get("reference", "retrieved-doc")),
                "weight": 0.2,
            }
        ]
    normalized["evidence"] = evidence
    normalized["confidence"] = float(normalized.get("confidence") or compute_confidence(evidence, documents))
    normalized["retrieved_documents"] = summarize_documents(documents)
    return normalized


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
    evidence = build_evidence(anomaly_type, documents)
    confidence = compute_confidence(evidence, documents)

    response = {
        "incident_id": request.incident_id,
        "root_cause": infer_root_cause(anomaly_type),
        "confidence": confidence,
        "evidence": evidence,
        "recommendation": "Scale the relevant IMS function and review the active SIP traffic scenario before approving remediation.",
        "generation_mode": "local-rag",
        "retrieved_documents": summarize_documents(documents),
    }
    generated = generate_with_llm(build_prompt({"incident_id": request.incident_id, **request.context}, documents))
    if generated:
        response = normalize_response(generated, documents, anomaly_type, request.incident_id)
        response["generation_mode"] = "llm-rag"
    else:
        response = normalize_response(response, documents, anomaly_type, request.incident_id)

    if len(response.get("evidence", [])) < 2:
        raise ValueError("RCA output must include at least two evidence sources")

    record_rca(
        str(request.context.get("project", "ims-demo")),
        str(response.get("generation_mode", "unknown")),
        float(response["confidence"]),
    )
    attach_rca(
        request.incident_id,
        {
            "root_cause": response["root_cause"],
            "confidence": response["confidence"],
            "evidence": response["evidence"],
            "recommendation": response["recommendation"],
            "generation_mode": response.get("generation_mode"),
            "retrieved_documents": response.get("retrieved_documents", []),
        },
    )
    publish_document(
        collection_name="ims_incidents",
        reference=f"incidents/{request.incident_id}.json",
        title=f"Incident {request.incident_id}",
        content=json.dumps(
            {
                "incident_id": request.incident_id,
                "context": request.context,
                "rca": {
                    "root_cause": response["root_cause"],
                    "confidence": response["confidence"],
                    "recommendation": response["recommendation"],
                    "evidence": response["evidence"],
                    "retrieved_documents": response.get("retrieved_documents", []),
                },
            },
            indent=2,
        ),
        doc_type="incident",
    )
    return response
