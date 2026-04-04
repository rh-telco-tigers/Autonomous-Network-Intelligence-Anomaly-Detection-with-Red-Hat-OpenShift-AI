import os
from typing import Dict, List

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from shared.control_plane_client import attach_rca
from shared.cors import install_cors
from shared.incident_taxonomy import NORMAL_ANOMALY_TYPE, canonical_anomaly_type, metric_weights, scenario_definition
from shared.metrics import install_metrics, record_rca
from shared.rag import build_prompt, generate_with_llm, retrieve_context
from shared.security import require_api_key


class RCARequest(BaseModel):
    incident_id: str
    context: Dict[str, object] = Field(default_factory=dict)


app = FastAPI(title="rca-service", version="0.1.0")
install_cors(app)
install_metrics(app, "rca-service")


def infer_root_cause(anomaly_type: str) -> str:
    definition = scenario_definition(anomaly_type)
    return str(definition.get("root_cause") or "Unexpected IMS behavior detected on the control plane.")


def _human_join(values: List[str]) -> str:
    items = [value for value in values if value]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def infer_explanation(anomaly_type: str, root_cause: str, documents: List[Dict[str, object]]) -> str:
    definition = scenario_definition(anomaly_type)
    summary = str(root_cause or "").strip()
    if len(summary.split()) <= 2:
        summary = str(definition.get("root_cause") or summary or "Unexpected IMS behavior detected on the control plane.")
    if summary and summary[-1] not in ".!?":
        summary = f"{summary}."

    evidence_refs: List[str] = []
    for doc in documents[:2]:
        label = str(doc.get("title") or doc.get("reference") or "").strip()
        collection = str(doc.get("collection") or "").strip()
        if label and collection:
            evidence_refs.append(f"{label} ({collection})")
        elif label:
            evidence_refs.append(label)

    explanation = f"{summary} This matches the observed {anomaly_type.replace('_', ' ')} incident pattern."
    if evidence_refs:
        explanation += f" Supporting context was retrieved from {_human_join(evidence_refs)}, which aligns with the current platform signals."
    return explanation


def build_evidence(anomaly_type: str, documents: List[Dict[str, object]]) -> List[Dict[str, object]]:
    evidence: List[Dict[str, object]] = [
        {
            "type": "doc",
            "reference": str(doc["reference"]),
            "weight": round(0.6 / max(len(documents), 1), 2),
        }
        for doc in documents[:3]
    ]
    weights = metric_weights(anomaly_type)
    primary_metric = max(weights.items(), key=lambda item: item[1])[0] if weights else "latency_p95"
    evidence.insert(0, {"type": "metric", "reference": primary_metric, "weight": 0.4})
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


def normalize_evidence_items(evidence: object) -> List[Dict[str, object]]:
    if not isinstance(evidence, list):
        return []
    normalized_items: List[Dict[str, object]] = []
    for item in evidence:
        if isinstance(item, dict):
            entry = dict(item)
            reference = str(entry.get("reference") or entry.get("document") or entry.get("title") or "").strip()
            if reference:
                entry.setdefault("reference", reference)
            if not str(entry.get("type") or "").strip():
                entry["type"] = "doc"
            weight = entry.get("weight")
            if weight not in (None, ""):
                try:
                    entry["weight"] = float(weight)
                except (TypeError, ValueError):
                    entry.pop("weight", None)
            normalized_items.append(entry)
            continue

        reference = str(item or "").strip()
        if reference:
            normalized_items.append(
                {
                    "type": "doc",
                    "reference": reference,
                    "weight": 0.2,
                }
            )
    return normalized_items


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
    definition = scenario_definition(anomaly_type)
    normalized["incident_id"] = incident_id
    normalized.setdefault("root_cause", infer_root_cause(anomaly_type))
    normalized.setdefault(
        "recommendation",
        str(
            definition.get("recommendation")
            or "Scale the relevant IMS function and review the active SIP traffic scenario before approving remediation."
        ),
    )
    explanation = str(normalized.get("explanation") or "").strip()
    root_cause = str(normalized.get("root_cause") or "").strip()
    if len(explanation.split()) < 8 or explanation == root_cause:
        normalized["explanation"] = infer_explanation(anomaly_type, root_cause, documents)
    else:
        normalized["explanation"] = explanation
    evidence = normalize_evidence_items(normalized.get("evidence"))
    if len(evidence) < 2:
        evidence = build_evidence(anomaly_type, documents)
    document_refs = {str(doc.get("reference", "")) for doc in documents}
    if documents and not any(str(item.get("reference") or item.get("document") or "") in document_refs for item in evidence):
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


def _llm_runtime_name() -> str | None:
    endpoint = os.getenv("LLM_ENDPOINT", "").strip().lower()
    if not endpoint:
        return None
    if "openai.com" in endpoint:
        return "OpenAI"
    if "ims-generative-proxy" in endpoint or ".svc.cluster.local" in endpoint or "predictor" in endpoint:
        return "vLLM"
    return "OpenAI-compatible"


def _generation_metadata(mode: str) -> Dict[str, object]:
    endpoint = os.getenv("LLM_ENDPOINT", "").strip()
    llm_configured = bool(endpoint)
    llm_used = mode == "llm-rag"
    model_name = os.getenv("LLM_MODEL", "").strip() if llm_configured else ""
    return {
        "generation_mode": mode,
        "generation_source_label": "LLM + RAG" if llm_used else "Local RAG fallback",
        "llm_used": llm_used,
        "llm_configured": llm_configured,
        "llm_model": model_name or None,
        "llm_runtime": _llm_runtime_name(),
    }


@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "llm_endpoint_configured": bool(os.getenv("LLM_ENDPOINT", "")),
        "llm_endpoint": os.getenv("LLM_ENDPOINT", ""),
        "llm_model": os.getenv("LLM_MODEL", ""),
        "milvus_endpoint_configured": bool(os.getenv("MILVUS_URI", "")),
    }


@app.post("/rca", dependencies=[Depends(require_api_key)])
def rca(request: RCARequest):
    anomaly_type = canonical_anomaly_type(str(request.context.get("anomaly_type", NORMAL_ANOMALY_TYPE)))
    definition = scenario_definition(anomaly_type)
    query = f"incident={request.incident_id} anomaly_type={anomaly_type} context={request.context}"
    documents = retrieve_context(query, limit=3)
    evidence = build_evidence(anomaly_type, documents)
    confidence = compute_confidence(evidence, documents)

    response = {
        "incident_id": request.incident_id,
        "root_cause": infer_root_cause(anomaly_type),
        "explanation": infer_explanation(anomaly_type, infer_root_cause(anomaly_type), documents),
        "confidence": confidence,
        "evidence": evidence,
        "recommendation": str(
            definition.get("recommendation")
            or "Scale the relevant IMS function and review the active SIP traffic scenario before approving remediation."
        ),
        "retrieved_documents": summarize_documents(documents),
        **_generation_metadata("local-rag"),
    }
    generated = generate_with_llm(build_prompt({"incident_id": request.incident_id, **request.context}, documents))
    if generated:
        response = normalize_response(generated, documents, anomaly_type, request.incident_id)
        response.update(_generation_metadata("llm-rag"))
    else:
        response = normalize_response(response, documents, anomaly_type, request.incident_id)
        response.update(_generation_metadata("local-rag"))

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
            "explanation": response.get("explanation"),
            "confidence": response["confidence"],
            "evidence": response["evidence"],
            "recommendation": response["recommendation"],
            "generation_mode": response.get("generation_mode"),
            "generation_source_label": response.get("generation_source_label"),
            "llm_used": response.get("llm_used"),
            "llm_configured": response.get("llm_configured"),
            "llm_model": response.get("llm_model"),
            "llm_runtime": response.get("llm_runtime"),
            "retrieved_documents": response.get("retrieved_documents", []),
        },
    )
    return response
