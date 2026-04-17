from __future__ import annotations

import copy
import os
import re
import uuid
from typing import Any, Dict, Iterable, List, Tuple


DEFAULT_GUARDRAILS_CONTRACT_VERSION = "ani.guardrails.v1"
DEFAULT_GUARDRAILS_POLICY_VERSION = "v1"
DEFAULT_RCA_SCHEMA_VERSION = "ani.rca.v1"
DEFAULT_CONFIDENCE_ALLOW_THRESHOLD = 0.60
DEFAULT_MIN_ALLOW_EVIDENCE_ITEMS = 2

ALLOW = "allow"
REQUIRE_REVIEW = "require_review"
BLOCK = "block"
ERROR = "error"

VALIDATED_ALLOW = "VALIDATED_ALLOW"
VALIDATED_REVIEW = "VALIDATED_REVIEW"
BLOCKED_POLICY = "BLOCKED_POLICY"
BLOCKED_SYSTEM = "BLOCKED_SYSTEM"
OVERRIDDEN = "OVERRIDDEN"

_PROMPT_INJECTION_PATTERNS = (
    re.compile(r"ignore\s+previous\s+instructions?", re.IGNORECASE),
    re.compile(r"ignore\s+all\s+previous\s+instructions?", re.IGNORECASE),
    re.compile(r"disregard\s+previous\s+instructions?", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
    re.compile(r"act\s+as\s+", re.IGNORECASE),
    re.compile(r"override\s+the\s+instructions?", re.IGNORECASE),
)
_SENSITIVE_TOKEN_PATTERNS = (
    re.compile(r"bearer\s+[a-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[:=]\s*)([^\s,;]+)", re.IGNORECASE),
    re.compile(r"(token\s*[:=]\s*)([^\s,;]+)", re.IGNORECASE),
    re.compile(r"(authorization\s*[:=]\s*)([^\s,;]+)", re.IGNORECASE),
)
_UNSAFE_RECOMMENDATION_PATTERNS = (
    re.compile(r"\bdelete\b", re.IGNORECASE),
    re.compile(r"\bwipe\b", re.IGNORECASE),
    re.compile(r"scale\s+.*\bto\s+zero\b", re.IGNORECASE),
    re.compile(r"restart\s+critical", re.IGNORECASE),
    re.compile(r"modify\s+network\s+policy", re.IGNORECASE),
)


def guardrails_contract_version() -> str:
    return str(os.getenv("ANI_GUARDRAILS_CONTRACT_VERSION", DEFAULT_GUARDRAILS_CONTRACT_VERSION)).strip() or DEFAULT_GUARDRAILS_CONTRACT_VERSION


def guardrails_policy_version() -> str:
    return str(os.getenv("ANI_GUARDRAILS_POLICY_VERSION", DEFAULT_GUARDRAILS_POLICY_VERSION)).strip() or DEFAULT_GUARDRAILS_POLICY_VERSION


def rca_schema_version() -> str:
    return str(os.getenv("ANI_RCA_SCHEMA_VERSION", DEFAULT_RCA_SCHEMA_VERSION)).strip() or DEFAULT_RCA_SCHEMA_VERSION


def guardrails_confidence_allow_threshold() -> float:
    raw = str(os.getenv("ANI_GUARDRAILS_CONFIDENCE_ALLOW_THRESHOLD", str(DEFAULT_CONFIDENCE_ALLOW_THRESHOLD))).strip()
    try:
        return max(0.0, min(float(raw), 1.0))
    except ValueError:
        return DEFAULT_CONFIDENCE_ALLOW_THRESHOLD


def guardrails_min_allow_evidence_items() -> int:
    raw = str(os.getenv("ANI_GUARDRAILS_MIN_ALLOW_EVIDENCE_ITEMS", str(DEFAULT_MIN_ALLOW_EVIDENCE_ITEMS))).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MIN_ALLOW_EVIDENCE_ITEMS


def new_rca_request_id() -> str:
    return f"rca-{uuid.uuid4().hex}"


def new_trace_id() -> str:
    return f"trace-{uuid.uuid4().hex}"


def detector_result(detector_type: str, severity: str, result: str, message: str) -> Dict[str, str]:
    return {
        "type": str(detector_type or "").strip(),
        "severity": str(severity or "low").strip(),
        "result": str(result or "pass").strip(),
        "message": str(message or "").strip(),
    }


def violation(violation_type: str, severity: str, message: str) -> Dict[str, str]:
    return {
        "type": str(violation_type or "").strip(),
        "severity": str(severity or "medium").strip(),
        "message": str(message or "").strip(),
    }


def lifecycle_state_for_status(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == ALLOW:
        return VALIDATED_ALLOW
    if normalized == REQUIRE_REVIEW:
        return VALIDATED_REVIEW
    if normalized == BLOCK:
        return BLOCKED_POLICY
    return BLOCKED_SYSTEM


def guardrail_status(payload: Dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    guardrails = payload.get("guardrails")
    if isinstance(guardrails, dict):
        status = str(guardrails.get("status") or guardrails.get("output_status") or "").strip().lower()
        if status:
            return status
    state = str(payload.get("rca_state") or "").strip().upper()
    if state == VALIDATED_ALLOW:
        return ALLOW
    if state == VALIDATED_REVIEW:
        return REQUIRE_REVIEW
    if state == BLOCKED_POLICY:
        return BLOCK
    if state == BLOCKED_SYSTEM:
        return ERROR
    return ""


def remediation_unlock_allowed(payload: Dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict) or not payload:
        return False
    state = str(payload.get("rca_state") or "").strip().upper()
    if state:
        return state in {VALIDATED_ALLOW, OVERRIDDEN}
    status = guardrail_status(payload)
    if status:
        return status == ALLOW
    return True


def recommendation_is_unsafe(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _UNSAFE_RECOMMENDATION_PATTERNS)


def _sanitize_string(value: str, path: str) -> Tuple[str, List[Dict[str, str]], List[Dict[str, str]], bool]:
    sanitized = str(value or "")
    detectors: List[Dict[str, str]] = []
    violations: List[Dict[str, str]] = []
    changed = False

    for pattern in _SENSITIVE_TOKEN_PATTERNS:
        if not pattern.search(sanitized):
            continue
        changed = True
        sanitized = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]" if match.lastindex and match.lastindex >= 2 else "[REDACTED]", sanitized)
    if changed:
        detectors.append(
            detector_result(
                "secret_exposure",
                "medium",
                "warn",
                f"Sensitive token material was redacted from {path}.",
            )
        )
        violations.append(
            violation(
                "secret_exposure",
                "medium",
                f"Sensitive token material was removed from {path} before prompt assembly.",
            )
        )

    filtered_lines: List[str] = []
    removed_prompt_injection = False
    for line in sanitized.splitlines() or [sanitized]:
        if any(pattern.search(line) for pattern in _PROMPT_INJECTION_PATTERNS):
            removed_prompt_injection = True
            changed = True
            continue
        filtered_lines.append(line)
    if removed_prompt_injection:
        detectors.append(
            detector_result(
                "prompt_injection",
                "high",
                "warn",
                f"Instruction-like text was removed from {path}.",
            )
        )
        violations.append(
            violation(
                "retrieval_instruction_removed",
                "medium",
                f"Instruction-like text was stripped from {path} before prompt assembly.",
            )
        )
        sanitized = "\n".join(filtered_lines).strip()
    return sanitized, detectors, violations, changed


def sanitize_json_like(value: Any, *, path: str = "context") -> Tuple[Any, Dict[str, Any]]:
    detectors: List[Dict[str, str]] = []
    violations: List[Dict[str, str]] = []
    modified_paths: List[str] = []

    def _sanitize(current: Any, current_path: str) -> Any:
        nonlocal detectors, violations, modified_paths
        if isinstance(current, dict):
            return {key: _sanitize(nested, f"{current_path}.{key}") for key, nested in current.items()}
        if isinstance(current, list):
            return [_sanitize(item, f"{current_path}[{index}]") for index, item in enumerate(current)]
        if isinstance(current, str):
            sanitized, current_detectors, current_violations, changed = _sanitize_string(current, current_path)
            detectors.extend(current_detectors)
            violations.extend(current_violations)
            if changed:
                modified_paths.append(current_path)
            return sanitized
        return current

    sanitized_value = _sanitize(copy.deepcopy(value), path)
    status = "sanitize" if modified_paths else ALLOW
    return sanitized_value, {
        "status": status,
        "detector_results": detectors,
        "violations": violations,
        "modified_paths": modified_paths,
    }


def sanitize_documents_for_prompt(documents: Iterable[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    sanitized_documents: List[Dict[str, Any]] = []
    detectors: List[Dict[str, str]] = []
    violations: List[Dict[str, str]] = []
    modified_documents = 0
    dropped_documents = 0

    for index, document in enumerate(documents):
        doc_copy = dict(document)
        reference = str(doc_copy.get("reference") or f"document-{index}")
        content = doc_copy.get("content")
        if content is None:
            sanitized_documents.append(doc_copy)
            continue
        sanitized_content, info = sanitize_json_like(content, path=f"retrieved_documents[{reference}]")
        detectors.extend(info.get("detector_results") or [])
        violations.extend(info.get("violations") or [])
        if info.get("modified_paths"):
            modified_documents += 1
        if isinstance(sanitized_content, str) and not sanitized_content.strip():
            dropped_documents += 1
            detectors.append(
                detector_result(
                    "retrieval_content_sanitizer",
                    "medium",
                    "warn",
                    f"Retrieved document {reference} was dropped after sanitization removed all usable prompt content.",
                )
            )
            continue
        doc_copy["content"] = sanitized_content
        sanitized_documents.append(doc_copy)

    status = "sanitize" if modified_documents or dropped_documents else ALLOW
    return sanitized_documents, {
        "status": status,
        "detector_results": detectors,
        "violations": violations,
        "modified_documents": modified_documents,
        "dropped_documents": dropped_documents,
    }
