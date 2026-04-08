import json
import os
from typing import Dict, List

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from shared.control_plane_client import attach_rca
from shared.cors import install_cors
from shared.debug_trace import make_trace_packet, trace_now
from shared.incident_taxonomy import NORMAL_ANOMALY_TYPE, canonical_anomaly_type, metric_weights, scenario_definition
from shared.metrics import install_metrics, record_rca
from shared.rag import DEFAULT_MILVUS_COLLECTIONS, RUNBOOK_COLLECTION, build_prompt, generate_with_llm_trace, retrieve_context, retrieve_knowledge_articles
from shared.security import require_api_key


class RCARequest(BaseModel):
    incident_id: str
    context: Dict[str, object] = Field(default_factory=dict)


app = FastAPI(title="rca-service", version="0.1.0")
install_cors(app)
install_metrics(app, "rca-service")
RCA_SUPPORT_COLLECTIONS = [collection for collection in DEFAULT_MILVUS_COLLECTIONS if collection != RUNBOOK_COLLECTION]


def _structured_runbook_payload(document: Dict[str, object]) -> Dict[str, object] | None:
    raw_content = document.get("content")
    if isinstance(raw_content, dict):
        return raw_content
    text = str(raw_content or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _structured_runbook_guidance(documents: List[Dict[str, object]]) -> Dict[str, object]:
    for document in documents:
        payload = _structured_runbook_payload(document)
        if not payload:
            continue
        recommended_rca = payload.get("recommended_rca")
        if isinstance(recommended_rca, dict) and recommended_rca:
            return recommended_rca
    return {}


def _prioritize_rca_documents(documents: List[Dict[str, object]], anomaly_type: str) -> List[Dict[str, object]]:
    def _sort_key(document: Dict[str, object]) -> tuple[int, int, int, float]:
        guidance = _structured_runbook_guidance([document])
        anomaly_types = document.get("anomaly_types") or []
        exact_anomaly_match = 1 if isinstance(anomaly_types, list) and anomaly_type in anomaly_types else 0
        return (
            exact_anomaly_match,
            1 if str(document.get("collection") or "") == RUNBOOK_COLLECTION else 0,
            1 if guidance else 0,
            float(document.get("score") or 0.0),
        )

    return sorted(documents, key=_sort_key, reverse=True)


def _incident_category(anomaly_type: str) -> str:
    definition = scenario_definition(anomaly_type)
    return str(definition.get("category") or "").strip().lower()


def _flatten_context(value: object) -> List[str]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, dict):
        fragments: List[str] = []
        for key, nested in value.items():
            if key in {"debug_trace", "retrieved_documents"}:
                continue
            nested_fragments = _flatten_context(nested)
            if nested_fragments:
                fragments.append(f"{key}={' ; '.join(nested_fragments)}")
        return fragments
    if isinstance(value, list):
        fragments: List[str] = []
        for item in value:
            fragments.extend(_flatten_context(item))
        return fragments
    return [str(value).strip()]


def _retrieval_query(incident_id: str, anomaly_type: str, context: Dict[str, object]) -> str:
    category = _incident_category(anomaly_type)
    fragments = [
        f"incident_id={incident_id}",
        f"anomaly_type={anomaly_type}",
        f"category={category}",
    ]
    for key in (
        "scenario_name",
        "recommendation",
        "root_cause",
        "severity",
        "source_system",
    ):
        value = str(context.get(key) or "").strip()
        if value:
            fragments.append(f"{key}={value}")
    for fragment in _flatten_context(context.get("feature_snapshot"))[:8]:
        fragments.append(f"feature={fragment}")
    for fragment in _flatten_context(context.get("evidence"))[:6]:
        fragments.append(f"evidence={fragment}")
    for fragment in _flatten_context(context)[:10]:
        if fragment not in fragments:
            fragments.append(f"context={fragment}")
    return " | ".join(fragment for fragment in fragments if fragment)


def _dedupe_documents(documents: List[Dict[str, object]]) -> List[Dict[str, object]]:
    deduped: Dict[tuple[str, str], Dict[str, object]] = {}
    for document in documents:
        key = (str(document.get("collection") or ""), str(document.get("reference") or ""))
        existing = deduped.get(key)
        if not existing or float(document.get("score") or 0.0) > float(existing.get("score") or 0.0):
            deduped[key] = document
    return list(deduped.values())


def _retrieve_rca_documents(query: str, anomaly_type: str) -> List[Dict[str, object]]:
    category = _incident_category(anomaly_type)
    knowledge_documents = retrieve_knowledge_articles(
        query,
        category=category,
        anomaly_type=anomaly_type,
        limit=4,
    )
    support_documents = retrieve_context(
        query,
        limit=4,
        collections=RCA_SUPPORT_COLLECTIONS,
        anomaly_type=anomaly_type,
    )
    return _prioritize_rca_documents(_dedupe_documents([*knowledge_documents, *support_documents]), anomaly_type)[:4]


def infer_root_cause(anomaly_type: str, documents: List[Dict[str, object]] | None = None) -> str:
    guidance = _structured_runbook_guidance(documents or [])
    structured_root_cause = str(guidance.get("root_cause") or "").strip()
    if structured_root_cause:
        return structured_root_cause
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


def _sentence(text: str) -> str:
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized:
        return ""
    return normalized if normalized[-1] in ".!?" else f"{normalized}."


def _runbook_signal_fragments(documents: List[Dict[str, object]], limit: int = 2) -> List[str]:
    fragments: List[str] = []
    seen: set[str] = set()
    for document in documents:
        payload = _structured_runbook_payload(document) or {}
        symptom_profile = payload.get("symptom_profile") or {}
        for field in ("primary_signals", "supporting_signals"):
            for item in symptom_profile.get(field) or []:
                fragment = " ".join(str(item or "").split()).strip().rstrip(".")
                key = fragment.lower()
                if fragment and key not in seen:
                    seen.add(key)
                    fragments.append(fragment)
                    if len(fragments) >= limit:
                        return fragments
    return fragments


def _signal_summary_sentence(signals: List[str]) -> str:
    if not signals:
        return ""
    if len(signals) == 1:
        return f"The strongest supporting signal is {signals[0]}."
    return f"The strongest supporting signals are {'; '.join(signals)}."


def _looks_like_meta_guidance(text: str, anomaly_type: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    anomaly_label = anomaly_type.lower()
    meta_patterns = (
        f"{anomaly_label} should",
        "the rca should",
        "should focus on",
        "should name",
        "should describe",
        "should identify",
        "should be anchored on",
        "should be treated as",
        "is best diagnosed by",
        "the important signal is",
        "the distinguishing signal is",
        "the defining characteristic",
        "this keeps remediation",
        "this prevents teams from",
        "that keeps investigation on",
    )
    return any(pattern in normalized for pattern in meta_patterns)


def _grounded_explanation(anomaly_type: str, root_cause: str, documents: List[Dict[str, object]]) -> str:
    signals = _runbook_signal_fragments(documents)
    if not signals:
        return ""
    tail = (
        "That points to the service tier, failing revision, or backend dependency actually emitting 5xx instead of a broad platform fault."
        if anomaly_type == "server_internal_error"
        else "That narrows the incident to the failing control-path boundary instead of a broad platform-wide fault."
    )
    return " ".join(
        part
        for part in (
            _sentence(root_cause),
            _signal_summary_sentence(signals),
            tail,
        )
        if part
    )


def infer_explanation(anomaly_type: str, root_cause: str, documents: List[Dict[str, object]]) -> str:
    definition = scenario_definition(anomaly_type)
    summary = str(root_cause or "").strip()
    if len(summary.split()) <= 2:
        summary = str(definition.get("root_cause") or summary or "Unexpected IMS behavior detected on the control plane.")
    summary = _sentence(summary)

    guidance = _structured_runbook_guidance(documents)
    structured_explanation = str(guidance.get("explanation") or "").strip()
    if structured_explanation:
        if _looks_like_meta_guidance(structured_explanation, anomaly_type):
            grounded = _grounded_explanation(anomaly_type, summary, documents)
            if grounded:
                return grounded
        return _sentence(structured_explanation)

    grounded = _grounded_explanation(anomaly_type, summary, documents)
    if grounded:
        return grounded
    return f"{summary} This matches the observed {anomaly_type.replace('_', ' ')} incident pattern."


def infer_recommendation(anomaly_type: str, documents: List[Dict[str, object]]) -> str:
    guidance = _structured_runbook_guidance(documents)
    structured_recommendation = str(guidance.get("recommendation") or "").strip()
    if structured_recommendation:
        return structured_recommendation
    definition = scenario_definition(anomaly_type)
    return str(
        definition.get("recommendation")
        or "Scale the relevant IMS function and review the active SIP traffic scenario before approving remediation."
    )


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
        content = _structured_runbook_payload(doc) or {}
        excerpt = str(content.get("summary") or doc.get("summary") or doc.get("content", ""))[:220]
        summaries.append(
            {
                "title": str(doc.get("title", "")),
                "reference": str(doc.get("reference", "")),
                "doc_type": str(doc.get("doc_type", "")),
                "collection": str(doc.get("collection", "")),
                "score": float(doc.get("score", 0.0)),
                "category": str(doc.get("category", "")),
                "anomaly_types": doc.get("anomaly_types") or content.get("anomaly_types") or [],
                "match_reasons": doc.get("match_reasons") or [],
                "excerpt": excerpt,
            }
        )
    return summaries


def normalize_response(response: Dict[str, object], documents: List[Dict[str, object]], anomaly_type: str, incident_id: str) -> Dict[str, object]:
    normalized = dict(response)
    normalized["incident_id"] = incident_id
    normalized.setdefault("root_cause", infer_root_cause(anomaly_type, documents))
    normalized.setdefault("recommendation", infer_recommendation(anomaly_type, documents))
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
    query = _retrieval_query(request.incident_id, anomaly_type, request.context)
    retrieval_started_at = trace_now()
    documents = _retrieve_rca_documents(query, anomaly_type)
    retrieval_finished_at = trace_now()
    evidence = build_evidence(anomaly_type, documents)
    confidence = compute_confidence(evidence, documents)
    prompt = build_prompt({"incident_id": request.incident_id, **request.context}, documents)
    trace_packets = [
        make_trace_packet(
            "llm",
            "event",
            title="RAG retrieval context",
            service="rca-service",
            timestamp=retrieval_finished_at,
            payload={
                "query": query,
                "incident_context": {"incident_id": request.incident_id, **request.context},
                "documents": documents,
            },
            metadata={
                "retrieved_document_count": len(documents),
                "retrieval_started_at": retrieval_started_at,
            },
        )
    ]

    response = {
        "incident_id": request.incident_id,
        "root_cause": infer_root_cause(anomaly_type, documents),
        "explanation": infer_explanation(anomaly_type, infer_root_cause(anomaly_type, documents), documents),
        "confidence": confidence,
        "evidence": evidence,
        "recommendation": infer_recommendation(anomaly_type, documents),
        "retrieved_documents": summarize_documents(documents),
        **_generation_metadata("local-rag"),
    }
    llm_trace = generate_with_llm_trace(prompt)
    generated = llm_trace.get("parsed") if isinstance(llm_trace, dict) else None
    if isinstance(llm_trace, dict):
        trace_packets.extend(llm_trace.get("trace_packets") or [])
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
            "debug_trace": trace_packets,
        },
    )
    return response
