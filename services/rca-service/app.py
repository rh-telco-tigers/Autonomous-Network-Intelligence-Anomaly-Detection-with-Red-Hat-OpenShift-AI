import json
import json
import os
from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from shared.control_plane_client import attach_rca
from shared.cors import install_cors
from shared.debug_trace import make_trace_packet, trace_now
from shared.guardrails import (
    ALLOW,
    BLOCK,
    ERROR,
    REQUIRE_REVIEW,
    detector_result,
    guardrails_confidence_allow_threshold,
    guardrails_contract_version,
    guardrails_min_allow_evidence_items,
    guardrails_policy_version,
    lifecycle_state_for_status,
    new_rca_request_id,
    new_trace_id,
    rca_schema_version,
    recommendation_is_unsafe,
    sanitize_documents_for_prompt,
    sanitize_json_like,
    violation,
)
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
    if "guardrails" in endpoint or endpoint.endswith("/rca"):
        return "TrustyAI Guardrails Gateway"
    if "ani-generative-proxy" in endpoint or ".svc.cluster.local" in endpoint or "predictor" in endpoint:
        return "vLLM"
    return "OpenAI-compatible"


def _generation_metadata(mode: str) -> Dict[str, object]:
    endpoint = os.getenv("LLM_ENDPOINT", "").strip()
    llm_configured = bool(endpoint)
    llm_used = mode in {"llm-rag", "guardrails-blocked", "guardrails-error"}
    model_name = os.getenv("LLM_MODEL", "").strip() if llm_configured else ""
    source_labels = {
        "llm-rag": "LLM + RAG",
        "local-rag": "Local RAG fallback",
        "guardrails-blocked": "Guardrails blocked the RCA response",
        "guardrails-error": "Guardrails validation path unavailable",
    }
    return {
        "generation_mode": mode,
        "generation_source_label": source_labels.get(mode, "Local RAG fallback"),
        "llm_used": llm_used,
        "llm_configured": llm_configured,
        "llm_model": model_name or None,
        "llm_runtime": _llm_runtime_name(),
    }


def _source_workflow_revision(context: Dict[str, object]) -> int:
    raw_value = context.get("workflow_revision") or context.get("source_workflow_revision") or 1
    try:
        return max(int(raw_value), 1)
    except (TypeError, ValueError):
        return 1


def _llm_trace_payload_body(llm_trace: Dict[str, object] | None) -> Dict[str, object]:
    if not isinstance(llm_trace, dict):
        return {}
    response_payload = llm_trace.get("response_payload")
    if not isinstance(response_payload, dict):
        return {}
    body = response_payload.get("body")
    return body if isinstance(body, dict) else response_payload


def _uses_guardrails_gateway() -> bool:
    endpoint = os.getenv("LLM_ENDPOINT", "").strip().lower()
    if not endpoint:
        return False
    return "guardrails" in endpoint or endpoint.endswith("/rca")


def _guardrails_message(llm_trace: Dict[str, object] | None) -> str:
    if not isinstance(llm_trace, dict):
        return ""
    raw_content = str(llm_trace.get("raw_content") or "").strip()
    if raw_content:
        return raw_content
    response_payload = llm_trace.get("response_payload")
    if isinstance(response_payload, dict):
        for key in ("raw_text", "message", "error"):
            value = str(response_payload.get(key) or "").strip()
            if value:
                return value
    return ""


def _guardrails_block_reason(message: str) -> str | None:
    normalized = " ".join(str(message or "").lower().split())
    if not normalized:
        return None
    if "unsuitable input" in normalized or "input detections" in normalized:
        return "input_blocked"
    if "unsuitable output" in normalized or "output detections" in normalized:
        return "output_blocked"
    if "prompt injection" in normalized or "flagged the following text" in normalized or "detected entities" in normalized:
        return "policy_blocked"
    return None


def _strip_json_code_fence(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized.startswith("```"):
        return normalized
    lines = normalized.splitlines()
    if lines:
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_loose_json_dict(text: str) -> Dict[str, object] | None:
    normalized = _strip_json_code_fence(text)
    if not normalized:
        return None
    try:
        parsed = json.loads(normalized)
    except (TypeError, ValueError, json.JSONDecodeError):
        parsed = None
    if isinstance(parsed, dict):
        return parsed

    in_string = False
    escape = False
    depth = 0
    start: int | None = None
    for index, char in enumerate(normalized):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
            continue
        if char != "}":
            continue
        if depth == 0:
            continue
        depth -= 1
        if depth != 0 or start is None:
            continue
        candidate = normalized[start : index + 1]
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            start = None
            continue
        if isinstance(parsed, dict):
            return parsed
        start = None
    return None


def _looks_like_rca_payload(payload: Dict[str, object] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    keys = {str(key).strip() for key in payload.keys()}
    return len({"root_cause", "explanation", "confidence", "evidence", "recommendation"} & keys) >= 3


def _recover_guarded_payload(candidate: object) -> Dict[str, object] | None:
    if isinstance(candidate, dict):
        if _looks_like_rca_payload(candidate):
            return candidate
        choices = candidate.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict):
                recovered = _recover_guarded_payload(message.get("content"))
                if recovered:
                    return recovered
        for key in ("body", "raw_text", "raw_content", "message", "content"):
            if key in candidate:
                recovered = _recover_guarded_payload(candidate.get(key))
                if recovered:
                    return recovered
        return None
    if isinstance(candidate, list):
        for item in candidate:
            recovered = _recover_guarded_payload(item)
            if recovered:
                return recovered
        return None
    if isinstance(candidate, str):
        parsed = _extract_loose_json_dict(candidate)
        if not isinstance(parsed, dict):
            return None
        if _looks_like_rca_payload(parsed):
            return parsed
        return _recover_guarded_payload(parsed)
    return None


def _recover_guarded_generated_payload(llm_trace: Dict[str, object] | None) -> Dict[str, object] | None:
    if not isinstance(llm_trace, dict):
        return None
    parsed = llm_trace.get("parsed")
    if isinstance(parsed, dict) and _looks_like_rca_payload(parsed):
        return parsed

    response_payload = llm_trace.get("response_payload")
    for candidate in (
        _llm_trace_payload_body(llm_trace),
        response_payload,
        llm_trace.get("raw_content"),
        response_payload.get("raw_text") if isinstance(response_payload, dict) else None,
    ):
        recovered = _recover_guarded_payload(candidate)
        if recovered:
            return recovered
    return None


def _guardrails_gateway_findings(llm_trace: Dict[str, object] | None) -> Dict[str, object]:
    body = _llm_trace_payload_body(llm_trace)
    message = _guardrails_message(llm_trace)
    detector_results: List[Dict[str, str]] = []
    violations: List[Dict[str, str]] = []
    input_status = ALLOW
    output_status = ALLOW
    status = ALLOW

    warnings = body.get("warnings")
    warning_items = warnings if isinstance(warnings, list) else [warnings] if warnings not in (None, "") else []
    normalized_warnings = [str(item.get("type") or item.get("message") or item).strip() if isinstance(item, dict) else str(item).strip() for item in warning_items]
    normalized_warnings = [item for item in normalized_warnings if item]
    if any("unsuitable_input" in item.lower() or "unsuitable input" in item.lower() for item in normalized_warnings):
        input_status = BLOCK
        status = BLOCK
        violations.append(violation("unsuitable_input", "high", "Guardrails marked the RCA request as unsuitable input."))
    if any("unsuitable_output" in item.lower() or "unsuitable output" in item.lower() for item in normalized_warnings):
        output_status = BLOCK
        status = BLOCK
        violations.append(violation("unsuitable_output", "high", "Guardrails marked the RCA response as unsuitable output."))

    detections = body.get("detections")
    if isinstance(detections, dict):
        for phase in ("input", "output"):
            items = detections.get(phase)
            if not isinstance(items, list):
                items = [items] if items not in (None, "") else []
            for item in items:
                if isinstance(item, dict):
                    label = str(item.get("detector") or item.get("type") or item.get("label") or phase).strip()
                    score = item.get("score")
                    message_text = str(item.get("message") or item.get("text") or label).strip()
                    if score not in (None, ""):
                        message_text = f"{message_text} (score={score})"
                else:
                    label = phase
                    message_text = str(item).strip()
                detector_results.append(
                    detector_result(
                        label or phase,
                        "high" if phase == "input" else "medium",
                        "warn",
                        f"{phase.title()} detection: {message_text}",
                    )
                )

    block_reason = _guardrails_block_reason(message)
    if block_reason == "input_blocked":
        input_status = BLOCK
        status = BLOCK
    elif block_reason in {"output_blocked", "policy_blocked"}:
        output_status = BLOCK
        status = BLOCK

    if status == BLOCK and message:
        violations.append(violation(block_reason or "guardrails_blocked", "high", message))

    return {
        "status": status,
        "input_status": input_status,
        "output_status": output_status,
        "detector_results": detector_results,
        "violations": violations,
        "message": message,
    }


def _guardrails_envelope(
    *,
    status: str,
    input_status: str,
    output_status: str,
    reason: str,
    message: str,
    detector_results: List[Dict[str, str]],
    violations: List[Dict[str, str]],
    sanitization: Dict[str, object],
) -> Dict[str, object]:
    detectors = list(detector_results)
    return {
        "contract_version": guardrails_contract_version(),
        "policy_version": guardrails_policy_version(),
        "status": status,
        "input_status": input_status,
        "output_status": output_status,
        "reason": reason,
        "message": message,
        "detector_results": detectors,
        "detectors": detectors,
        "violations": list(violations),
        "sanitization": dict(sanitization),
    }


def _policy_blocked_response(
    *,
    anomaly_type: str,
    documents: List[Dict[str, object]],
    guardrails_payload: Dict[str, object],
) -> Dict[str, object]:
    return {
        "root_cause": "TrustyAI Guardrails blocked the generated RCA before it could be accepted.",
        "explanation": (
            "The guarded RCA response violated ANI policy and has been replaced with a safe blocked result. "
            "Keep the incident open for operator review, but do not unlock remediation until the flagged content is cleared."
        ),
        "confidence": 0.0,
        "evidence": build_evidence(anomaly_type, documents),
        "recommendation": "Review the guardrail findings and regenerate RCA only after the unsafe recommendation path is removed.",
        "guardrails": guardrails_payload,
        "generation_mode": "guardrails-blocked",
    }


def _finalize_guarded_response(
    response: Dict[str, object],
    *,
    llm_trace: Dict[str, object] | None,
    documents: List[Dict[str, object]],
    anomaly_type: str,
    rca_request_id: str,
    trace_id: str,
    source_workflow_revision: int,
    input_sanitization: Dict[str, object],
) -> Dict[str, object]:
    normalized = normalize_response(response, documents, anomaly_type, str(response.get("incident_id") or ""))
    gateway_findings = _guardrails_gateway_findings(llm_trace)
    detector_results = [*list(input_sanitization.get("detector_results") or []), *list(gateway_findings.get("detector_results") or [])]
    violations = [*list(input_sanitization.get("violations") or []), *list(gateway_findings.get("violations") or [])]

    status = str(gateway_findings.get("status") or ALLOW)
    input_status = "sanitize" if str(input_sanitization.get("status") or ALLOW) == "sanitize" else str(gateway_findings.get("input_status") or ALLOW)
    output_status = str(gateway_findings.get("output_status") or ALLOW)
    message = str(gateway_findings.get("message") or "").strip()

    evidence = normalized.get("evidence")
    evidence_items = evidence if isinstance(evidence, list) else []
    confidence = float(normalized.get("confidence") or 0.0)
    recommendation = str(normalized.get("recommendation") or "").strip()
    document_refs = {str(doc.get("reference") or "") for doc in documents}
    evidence_refs = {
        str(item.get("reference") or "")
        for item in evidence_items
        if isinstance(item, dict) and str(item.get("reference") or "").strip()
    }

    if recommendation_is_unsafe(recommendation):
        status = BLOCK
        output_status = BLOCK
        violations.append(
            violation(
                "unsafe_recommendation_language",
                "high",
                "The generated recommendation included unsupported or destructive action guidance.",
            )
        )
        detector_results.append(
            detector_result(
                "unsafe_recommendation_language",
                "high",
                "fail",
                "The generated recommendation violated the safe-action policy.",
            )
        )
    elif confidence < guardrails_confidence_allow_threshold():
        status = REQUIRE_REVIEW
        output_status = REQUIRE_REVIEW
        violations.append(
            violation(
                "confidence_below_threshold",
                "medium",
                "Model confidence is below the allow threshold for automatic remediation unlock.",
            )
        )
        detector_results.append(
            detector_result(
                "confidence_policy",
                "medium",
                "warn",
                "Confidence fell below the allow threshold and requires operator review.",
            )
        )

    if len(evidence_items) < guardrails_min_allow_evidence_items() and status != BLOCK:
        status = REQUIRE_REVIEW
        output_status = REQUIRE_REVIEW
        violations.append(
            violation(
                "insufficient_evidence",
                "medium",
                "The RCA did not include enough evidence items for automatic remediation unlock.",
            )
        )

    if documents and not (document_refs & evidence_refs) and status != BLOCK:
        status = REQUIRE_REVIEW
        output_status = REQUIRE_REVIEW
        violations.append(
            violation(
                "evidence_reference_gap",
                "medium",
                "The RCA evidence did not reference any retrieved support document directly.",
            )
        )

    decision_reason = "validated"
    if status == REQUIRE_REVIEW:
        decision_reason = str((violations[0] if violations else {}).get("type") or "policy_review_required")
    elif status == BLOCK:
        decision_reason = str((violations[0] if violations else {}).get("type") or "policy_blocked")

    guardrails_payload = _guardrails_envelope(
        status=status,
        input_status=input_status,
        output_status=output_status,
        reason=decision_reason,
        message=message,
        detector_results=detector_results,
        violations=violations,
        sanitization={
            "modified_documents": int(input_sanitization.get("modified_documents") or 0),
            "dropped_documents": int(input_sanitization.get("dropped_documents") or 0),
            "modified_context_paths": list(input_sanitization.get("modified_context_paths") or []),
        },
    )

    if status == BLOCK:
        normalized = normalize_response(
            _policy_blocked_response(anomaly_type=anomaly_type, documents=documents, guardrails_payload=guardrails_payload),
            documents,
            anomaly_type,
            str(normalized.get("incident_id") or ""),
        )
        normalized.update(_generation_metadata("guardrails-blocked"))
    else:
        normalized["guardrails"] = guardrails_payload

    normalized["rca_request_id"] = rca_request_id
    normalized["trace_id"] = trace_id
    normalized["rca_schema_version"] = rca_schema_version()
    normalized["source_workflow_revision"] = source_workflow_revision
    normalized["rca_state"] = lifecycle_state_for_status(status)
    return normalized


def _guardrails_response(
    llm_trace: Dict[str, object] | None,
    documents: List[Dict[str, object]],
    anomaly_type: str,
    *,
    rca_request_id: str,
    trace_id: str,
    source_workflow_revision: int,
    input_sanitization: Dict[str, object],
) -> Dict[str, object] | None:
    if not _uses_guardrails_gateway():
        return None

    findings = _guardrails_gateway_findings(llm_trace)
    message = str(findings.get("message") or "").strip()
    block_reason = _guardrails_block_reason(message)
    evidence = build_evidence(anomaly_type, documents)
    detector_results = [*list(input_sanitization.get("detector_results") or []), *list(findings.get("detector_results") or [])]
    violations = [*list(input_sanitization.get("violations") or []), *list(findings.get("violations") or [])]

    if block_reason:
        response = {
            "root_cause": "TrustyAI Guardrails blocked the RCA request before a model-authored diagnosis could be accepted.",
            "explanation": (
                "The guarded LLM endpoint flagged the RCA request or response and did not return a valid JSON RCA payload. "
                "Keep the incident open for review, but do not unlock remediation until the flagged content is reviewed."
            ),
            "confidence": 0.0,
            "evidence": evidence,
            "recommendation": "Review the evidence set and guardrail findings, then retry RCA generation only after the prompt path is safe.",
            "guardrails": _guardrails_envelope(
                status=BLOCK,
                input_status=str(findings.get("input_status") or ALLOW),
                output_status=str(findings.get("output_status") or ALLOW),
                reason=block_reason,
                message=message or "Guardrails blocked the RCA request.",
                detector_results=detector_results,
                violations=[*violations, violation(block_reason, "high", message or "Guardrails blocked the RCA request.")],
                sanitization={
                    "modified_documents": int(input_sanitization.get("modified_documents") or 0),
                    "dropped_documents": int(input_sanitization.get("dropped_documents") or 0),
                    "modified_context_paths": list(input_sanitization.get("modified_context_paths") or []),
                },
            ),
            "generation_mode": "guardrails-blocked",
        }
        response["rca_request_id"] = rca_request_id
        response["trace_id"] = trace_id
        response["rca_schema_version"] = rca_schema_version()
        response["source_workflow_revision"] = source_workflow_revision
        response["rca_state"] = lifecycle_state_for_status(BLOCK)
        return response

    response = {
        "root_cause": "TrustyAI Guardrails could not validate RCA output because the guarded generation path was unavailable.",
        "explanation": (
            "The RCA request reached the guarded LLM path, but Guardrails did not return a usable response. "
            "Treat this as a platform availability issue rather than a trusted RCA result."
        ),
        "confidence": 0.0,
        "evidence": evidence,
        "recommendation": "Investigate the Guardrails gateway and detector health, then retry RCA generation before enabling remediation.",
        "guardrails": _guardrails_envelope(
            status=ERROR,
            input_status=str(input_sanitization.get("status") or ALLOW),
            output_status=ERROR,
            reason="guardrails_unavailable",
            message=message or "Guardrails did not return a usable response.",
            detector_results=detector_results,
            violations=[*violations, violation("guardrails_unavailable", "high", message or "Guardrails did not return a usable response.")],
            sanitization={
                "modified_documents": int(input_sanitization.get("modified_documents") or 0),
                "dropped_documents": int(input_sanitization.get("dropped_documents") or 0),
                "modified_context_paths": list(input_sanitization.get("modified_context_paths") or []),
            },
        ),
        "generation_mode": "guardrails-error",
    }
    response["rca_request_id"] = rca_request_id
    response["trace_id"] = trace_id
    response["rca_schema_version"] = rca_schema_version()
    response["source_workflow_revision"] = source_workflow_revision
    response["rca_state"] = lifecycle_state_for_status(ERROR)
    return response


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
    source_workflow_revision = _source_workflow_revision(request.context)
    rca_request_id = str(request.context.get("rca_request_id") or new_rca_request_id()).strip() or new_rca_request_id()
    trace_id = str(request.context.get("trace_id") or new_trace_id()).strip() or new_trace_id()
    query = _retrieval_query(request.incident_id, anomaly_type, request.context)
    retrieval_started_at = trace_now()
    documents = _retrieve_rca_documents(query, anomaly_type)
    retrieval_finished_at = trace_now()
    evidence = build_evidence(anomaly_type, documents)
    confidence = compute_confidence(evidence, documents)
    prompt_documents, prompt_document_sanitization = sanitize_documents_for_prompt(documents)
    prompt_context, prompt_context_sanitization = sanitize_json_like(
        {"incident_id": request.incident_id, **request.context},
        path="incident_context",
    )
    input_sanitization = {
        "status": "sanitize"
        if str(prompt_document_sanitization.get("status") or ALLOW) == "sanitize" or str(prompt_context_sanitization.get("status") or ALLOW) == "sanitize"
        else ALLOW,
        "detector_results": [
            *list(prompt_document_sanitization.get("detector_results") or []),
            *list(prompt_context_sanitization.get("detector_results") or []),
        ],
        "violations": [
            *list(prompt_document_sanitization.get("violations") or []),
            *list(prompt_context_sanitization.get("violations") or []),
        ],
        "modified_documents": int(prompt_document_sanitization.get("modified_documents") or 0),
        "dropped_documents": int(prompt_document_sanitization.get("dropped_documents") or 0),
        "modified_context_paths": list(prompt_context_sanitization.get("modified_paths") or []),
    }
    prompt = build_prompt(prompt_context if isinstance(prompt_context, dict) else {"incident_id": request.incident_id}, prompt_documents)
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
    if (
        input_sanitization["modified_documents"]
        or input_sanitization["dropped_documents"]
        or input_sanitization["modified_context_paths"]
    ):
        trace_packets.append(
            make_trace_packet(
                "guardrails",
                "event",
                title="Prompt path sanitized before guarded generation",
                service="rca-service",
                timestamp=trace_now(),
                payload={
                    "modified_documents": input_sanitization["modified_documents"],
                    "dropped_documents": input_sanitization["dropped_documents"],
                    "modified_context_paths": input_sanitization["modified_context_paths"],
                },
            )
        )

    response = {
        "incident_id": request.incident_id,
        "root_cause": infer_root_cause(anomaly_type, documents),
        "explanation": infer_explanation(anomaly_type, infer_root_cause(anomaly_type, documents), documents),
        "confidence": confidence,
        "evidence": evidence,
        "recommendation": infer_recommendation(anomaly_type, documents),
        "retrieved_documents": summarize_documents(documents),
        "rca_request_id": rca_request_id,
        "trace_id": trace_id,
        "rca_schema_version": rca_schema_version(),
        "source_workflow_revision": source_workflow_revision,
        **_generation_metadata("local-rag"),
    }
    llm_trace = generate_with_llm_trace(prompt)
    generated = _recover_guarded_generated_payload(llm_trace)
    if isinstance(llm_trace, dict):
        trace_packets.extend(llm_trace.get("trace_packets") or [])
    guarded_response = (
        _guardrails_response(
            llm_trace,
            documents,
            anomaly_type,
            rca_request_id=rca_request_id,
            trace_id=trace_id,
            source_workflow_revision=source_workflow_revision,
            input_sanitization=input_sanitization,
        )
        if not generated
        else None
    )
    if generated:
        generated["incident_id"] = request.incident_id
        response = _finalize_guarded_response(
            generated,
            llm_trace=llm_trace,
            documents=documents,
            anomaly_type=anomaly_type,
            rca_request_id=rca_request_id,
            trace_id=trace_id,
            source_workflow_revision=source_workflow_revision,
            input_sanitization=input_sanitization,
        )
        if str(response.get("generation_mode") or "") != "guardrails-blocked":
            response.update(_generation_metadata("llm-rag"))
    elif guarded_response:
        trace_packets.append(
            make_trace_packet(
                "guardrails",
                "event",
                title="Guardrails blocked or degraded the RCA response",
                service="rca-service",
                timestamp=trace_now(),
                payload={
                    "status": str((guarded_response.get("guardrails") or {}).get("status") or ""),
                    "reason": str((guarded_response.get("guardrails") or {}).get("reason") or ""),
                    "message": str((guarded_response.get("guardrails") or {}).get("message") or ""),
                },
            )
        )
        response = normalize_response(guarded_response, documents, anomaly_type, request.incident_id)
        response.update(_generation_metadata(str(guarded_response.get("generation_mode") or "guardrails-error")))
    else:
        response = normalize_response(response, documents, anomaly_type, request.incident_id)
        response.update(_generation_metadata("local-rag"))

    if len(response.get("evidence", [])) < 2:
        raise ValueError("RCA output must include at least two evidence sources")

    record_rca(
        str(request.context.get("project", "ani-demo")),
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
            "rca_request_id": response.get("rca_request_id"),
            "trace_id": response.get("trace_id"),
            "rca_schema_version": response.get("rca_schema_version"),
            "source_workflow_revision": response.get("source_workflow_revision"),
            "rca_state": response.get("rca_state"),
            "generation_mode": response.get("generation_mode"),
            "generation_source_label": response.get("generation_source_label"),
            "llm_used": response.get("llm_used"),
            "llm_configured": response.get("llm_configured"),
            "llm_model": response.get("llm_model"),
            "llm_runtime": response.get("llm_runtime"),
            "guardrails": response.get("guardrails"),
            "retrieved_documents": response.get("retrieved_documents", []),
            "debug_trace": trace_packets,
        },
    )
    return response
