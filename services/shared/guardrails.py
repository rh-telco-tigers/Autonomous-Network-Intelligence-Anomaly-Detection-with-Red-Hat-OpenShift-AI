from __future__ import annotations

import copy
import os
import re
import uuid
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import urlparse, urlunparse

import requests
import urllib3


DEFAULT_GUARDRAILS_CONTRACT_VERSION = "ani.guardrails.v1"
DEFAULT_GUARDRAILS_POLICY_VERSION = "v1"
DEFAULT_RCA_SCHEMA_VERSION = "ani.rca.v1"
DEFAULT_CONFIDENCE_ALLOW_THRESHOLD = 0.60
DEFAULT_MIN_ALLOW_EVIDENCE_ITEMS = 2
DEFAULT_TRUSTYAI_PLAYBOOK_TIMEOUT_SECONDS = 8.0
DEFAULT_TRUSTYAI_PROMPT_INJECTION_MAX_CHARS = 400

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
    re.compile(r"ignore\s+(below|above|earlier|prior)\s+instructions?", re.IGNORECASE),
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
_PLAYBOOK_REVIEW_RULES = (
    (
        "manual_instruction_override",
        "medium",
        "A manual full-text instruction override was supplied for AI playbook generation.",
        None,
    ),
    (
        "live_component_restart",
        "medium",
        "The playbook request asks to restart a live component and should be reviewed before Kafka publish.",
        re.compile(r"\b(restart|rollout\s+restart)\b", re.IGNORECASE),
    ),
    (
        "deployment_patch_requested",
        "medium",
        "The playbook request asks to patch or edit a live workload and should be reviewed before Kafka publish.",
        re.compile(
            r"\b(patch|edit|annotate|label)\b.{0,48}\b(deployment|statefulset|daemonset|service|route|configmap)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "scale_change_requested",
        "medium",
        "The playbook request asks to change live replica or traffic levels and should be reviewed before Kafka publish.",
        re.compile(
            r"\bscale\b.{0,48}\b(deployment|statefulset|daemonset|replica|replicas|pcscf|scscf|ingress|service)\b",
            re.IGNORECASE,
        ),
    ),
)
_PLAYBOOK_BLOCK_RULES = (
    (
        "prompt_injection_detected",
        "high",
        "Prompt-injection language was detected in the AI playbook request.",
        None,
    ),
    (
        "destructive_component_delete",
        "high",
        "The playbook request asks to delete or destroy live platform components.",
        re.compile(
            r"\b(delete\w*|destroy\w*|wipe\w*|erase\w*|remove\w*)\b.{0,64}\b(deployment|namespace|pod|service|route|secret|configmap|component|cluster)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "critical_scale_to_zero",
        "high",
        "The playbook request asks to scale a live component to zero.",
        re.compile(r"\bscale\w*\b.{0,64}\b(?:to\s+zero|zero\s+replicas?)\b", re.IGNORECASE),
    ),
    (
        "stateful_data_delete_requested",
        "high",
        "The playbook request asks to delete stored workload state such as PVCs, databases, or stateful components.",
        re.compile(
            r"\b(delete\w*|destroy\w*|wipe\w*|erase\w*|truncate\w*)\b.{0,80}\b(pvc|persistentvolumeclaim|database|db|statefulset|storage|volume|data)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "network_policy_bypass_requested",
        "high",
        "The playbook request asks to disable or broadly bypass network-policy guardrails.",
        re.compile(
            r"\b(delete\w*|remove\w*|disable\w*|bypass\w*|allow\s+all)\b.{0,80}\b(network\s*policy|networkpolicy|ingress|egress|firewall)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "approval_bypass_requested",
        "high",
        "The playbook request asks to bypass approval or safety review.",
        re.compile(r"\b(bypass|skip|ignore)\b.{0,48}\b(approval|review|guardrail|policy)\b", re.IGNORECASE),
    ),
    (
        "privilege_escalation_requested",
        "high",
        "The playbook request asks for cluster-admin or similarly unsafe platform privileges.",
        re.compile(r"\b(cluster-?admin|clusterrolebinding|privileged\s+pod|hostpath)\b", re.IGNORECASE),
    ),
)

_TRUSTYAI_PROVIDER = {
    "key": "trustyai",
    "label": "TrustyAI Guardrails",
    "family": "Guardrails",
}

_LOCAL_POLICY_PROVIDER = {
    "key": "local_policy",
    "label": "Local control-plane policy",
    "family": "Guardrails",
}


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


def playbook_guardrails_provider(trustyai_used: bool) -> Dict[str, str]:
    return dict(_TRUSTYAI_PROVIDER if trustyai_used else _LOCAL_POLICY_PROVIDER)


def trustyai_playbook_guardrails_enabled() -> bool:
    raw = str(os.getenv("ANI_PLAYBOOK_GUARDRAILS_TRUSTYAI_ENABLED", "true")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def trustyai_orchestrator_endpoint() -> str:
    explicit = str(os.getenv("TRUSTYAI_ORCHESTRATOR_ENDPOINT", "")).strip()
    if explicit:
        return explicit.rstrip("/")
    llm_endpoint = str(os.getenv("LLM_ENDPOINT", "")).strip()
    if not llm_endpoint:
        return ""
    parsed = urlparse(llm_endpoint)
    if not parsed.scheme or not parsed.hostname:
        return ""
    target_port = 8032
    target_scheme = "https"
    if parsed.scheme == "https" and (parsed.port in {None, 443}):
        netloc = parsed.hostname
    else:
        netloc = f"{parsed.hostname}:{target_port}"
    return urlunparse((target_scheme, netloc, "", "", "", "")).rstrip("/")


def trustyai_playbook_timeout_seconds() -> float:
    raw = str(
        os.getenv("ANI_PLAYBOOK_GUARDRAILS_TIMEOUT_SECONDS", str(DEFAULT_TRUSTYAI_PLAYBOOK_TIMEOUT_SECONDS))
    ).strip()
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TRUSTYAI_PLAYBOOK_TIMEOUT_SECONDS
    return max(1.0, min(value, 20.0))


def trustyai_prompt_injection_content(content: str) -> str:
    normalized = str(content or "").strip()
    if len(normalized) <= DEFAULT_TRUSTYAI_PROMPT_INJECTION_MAX_CHARS:
        return normalized
    head = normalized[:220].rstrip()
    tail = normalized[-160:].lstrip()
    return f"{head}\n...\n{tail}"


def trustyai_orchestrator_verify_tls() -> bool:
    raw = str(os.getenv("TRUSTYAI_ORCHESTRATOR_VERIFY_TLS", "")).strip().lower()
    if raw:
        return raw not in {"0", "false", "no", "off"}
    endpoint = trustyai_orchestrator_endpoint()
    hostname = str(urlparse(endpoint).hostname or "").strip().lower()
    if hostname.endswith(".svc.cluster.local"):
        return False
    return True


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


def _sanitize_string(
    value: str,
    path: str,
    *,
    strip_prompt_injection: bool = True,
) -> Tuple[str, List[Dict[str, str]], List[Dict[str, str]], bool]:
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

    if strip_prompt_injection:
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


def sanitize_json_like(
    value: Any,
    *,
    path: str = "context",
    strip_prompt_injection: bool = True,
) -> Tuple[Any, Dict[str, Any]]:
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
            sanitized, current_detectors, current_violations, changed = _sanitize_string(
                current,
                current_path,
                strip_prompt_injection=strip_prompt_injection,
            )
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


def _append_unique_findings(items: List[Dict[str, str]], candidate: Dict[str, str]) -> None:
    signature = (
        str(candidate.get("type") or "").strip(),
        str(candidate.get("severity") or "").strip(),
        str(candidate.get("result") or candidate.get("message") or "").strip(),
    )
    for existing in items:
        existing_signature = (
            str(existing.get("type") or "").strip(),
            str(existing.get("severity") or "").strip(),
            str(existing.get("result") or existing.get("message") or "").strip(),
        )
        if existing_signature == signature:
            return
    items.append(candidate)


def _trustyai_text_detection(content: str, detectors: Dict[str, Any]) -> List[Dict[str, Any]]:
    endpoint = trustyai_orchestrator_endpoint()
    if not endpoint or not trustyai_playbook_guardrails_enabled():
        return []
    verify_tls = trustyai_orchestrator_verify_tls()
    if not verify_tls:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    response = requests.post(
        f"{endpoint}/api/v2/text/detection/content",
        json={
            "detectors": detectors,
            "content": str(content or ""),
        },
        timeout=trustyai_playbook_timeout_seconds(),
        verify=verify_tls,
    )
    response.raise_for_status()
    payload = response.json()
    detections = payload.get("detections") if isinstance(payload, dict) else []
    return detections if isinstance(detections, list) else []


def _playbook_rule_detection_hits(
    content: str,
) -> Tuple[List[Tuple[str, str, str]], List[Tuple[str, str, str]], List[Dict[str, str]], bool]:
    raw_content = str(content or "").strip()
    detectors: List[Dict[str, str]] = []
    review_hits: List[Tuple[str, str, str]] = []
    block_hits: List[Tuple[str, str, str]] = []
    trustyai_used = False
    if not raw_content:
        return review_hits, block_hits, detectors, trustyai_used

    def add_review_hit(rule_type: str, severity: str, message: str) -> None:
        candidate = (rule_type, severity, message)
        if candidate not in review_hits:
            review_hits.append(candidate)

    def add_block_hit(rule_type: str, severity: str, message: str) -> None:
        candidate = (rule_type, severity, message)
        if candidate not in block_hits:
            block_hits.append(candidate)

    trustyai_configured = trustyai_playbook_guardrails_enabled() and bool(trustyai_orchestrator_endpoint())
    prompt_detection_failed = False
    review_detection_failed = False
    block_detection_failed = False
    lowered_content = raw_content.lower()

    if trustyai_configured:
        try:
            prompt_detections = _trustyai_text_detection(trustyai_prompt_injection_content(raw_content), {"prompt_injection": {}})
            if prompt_detections or trustyai_orchestrator_endpoint():
                trustyai_used = True
            if prompt_detections:
                add_block_hit(_PLAYBOOK_BLOCK_RULES[0][0], _PLAYBOOK_BLOCK_RULES[0][1], _PLAYBOOK_BLOCK_RULES[0][2])
                _append_unique_findings(
                    detectors,
                    detector_result(
                        "prompt_injection",
                        "high",
                        "fail",
                        "TrustyAI Guardrails detected prompt-injection language in the AI playbook request.",
                    ),
                )
        except requests.RequestException as exc:
            _append_unique_findings(
                detectors,
                detector_result(
                    "trustyai_prompt_injection_unavailable",
                    "medium",
                    "warn",
                    f"TrustyAI prompt-injection detection was unavailable for playbook prompt validation and local fallback policy was used: {exc}",
                ),
            )

        if not block_hits:
            try:
                prompt_patterns = [pattern.pattern for pattern in _PROMPT_INJECTION_PATTERNS]
                prompt_regex_detections = _trustyai_text_detection(lowered_content, {"pii_regex": {"regex": prompt_patterns}})
                if prompt_regex_detections:
                    trustyai_used = True
                    add_block_hit(_PLAYBOOK_BLOCK_RULES[0][0], _PLAYBOOK_BLOCK_RULES[0][1], _PLAYBOOK_BLOCK_RULES[0][2])
                    _append_unique_findings(
                        detectors,
                        detector_result(
                            "trustyai_prompt_injection_regex",
                            "high",
                            "fail",
                            "TrustyAI Guardrails regex detection matched prompt-injection language in the AI playbook request.",
                        ),
                    )
            except requests.RequestException as exc:
                prompt_detection_failed = True
                _append_unique_findings(
                    detectors,
                    detector_result(
                        "trustyai_prompt_injection_regex_unavailable",
                        "medium",
                        "warn",
                        f"TrustyAI prompt-injection regex detection was unavailable for playbook prompt validation and local fallback policy was used: {exc}",
                    ),
                )
    else:
        prompt_detection_failed = True
        review_detection_failed = True
        block_detection_failed = True

    if trustyai_configured:
        try:
            review_patterns = [pattern.pattern for _, _, _, pattern in _PLAYBOOK_REVIEW_RULES[1:] if pattern]
            review_detections = _trustyai_text_detection(lowered_content, {"pii_regex": {"regex": review_patterns}})
            if review_detections:
                trustyai_used = True
            for detection in review_detections:
                matched_text = str(detection.get("text") or "").strip()
                for rule_type, severity, message, pattern in _PLAYBOOK_REVIEW_RULES[1:]:
                    if pattern and matched_text and pattern.search(matched_text):
                        add_review_hit(rule_type, severity, message)
                        _append_unique_findings(detectors, detector_result(f"trustyai_{rule_type}", severity, "warn", message))
        except requests.RequestException as exc:
            review_detection_failed = True
            _append_unique_findings(
                detectors,
                detector_result(
                    "trustyai_review_detection_unavailable",
                    "medium",
                    "warn",
                    f"TrustyAI review-pattern detection was unavailable for playbook prompt validation and local fallback policy was used: {exc}",
                ),
            )

    if trustyai_configured:
        try:
            block_patterns = [pattern.pattern for _, _, _, pattern in _PLAYBOOK_BLOCK_RULES[1:] if pattern]
            block_detections = _trustyai_text_detection(lowered_content, {"pii_regex": {"regex": block_patterns}})
            if block_detections:
                trustyai_used = True
            for detection in block_detections:
                matched_text = str(detection.get("text") or "").strip()
                for rule_type, severity, message, pattern in _PLAYBOOK_BLOCK_RULES[1:]:
                    if pattern and matched_text and pattern.search(matched_text):
                        add_block_hit(rule_type, severity, message)
                        _append_unique_findings(detectors, detector_result(f"trustyai_{rule_type}", severity, "fail", message))
        except requests.RequestException as exc:
            block_detection_failed = True
            _append_unique_findings(
                detectors,
                detector_result(
                    "trustyai_block_detection_unavailable",
                    "medium",
                    "warn",
                    f"TrustyAI block-pattern detection was unavailable for playbook prompt validation and local fallback policy was used: {exc}",
                ),
            )

    if prompt_detection_failed:
        prompt_injection_detected = any(pattern.search(raw_content) for pattern in _PROMPT_INJECTION_PATTERNS)
        if prompt_injection_detected:
            add_block_hit(_PLAYBOOK_BLOCK_RULES[0][0], _PLAYBOOK_BLOCK_RULES[0][1], _PLAYBOOK_BLOCK_RULES[0][2])
            _append_unique_findings(
                detectors,
                detector_result(
                    "prompt_injection_local_fallback",
                    "high",
                    "fail",
                    "Local fallback policy detected prompt-injection language in the AI playbook request.",
                ),
            )

    if review_detection_failed:
        for rule_type, severity, message, pattern in _PLAYBOOK_REVIEW_RULES[1:]:
            if pattern and pattern.search(raw_content):
                add_review_hit(rule_type, severity, message)
                _append_unique_findings(detectors, detector_result(f"local_{rule_type}", severity, "warn", message))

    if block_detection_failed:
        for rule_type, severity, message, pattern in _PLAYBOOK_BLOCK_RULES[1:]:
            if pattern and pattern.search(raw_content):
                add_block_hit(rule_type, severity, message)
                _append_unique_findings(detectors, detector_result(f"local_{rule_type}", severity, "fail", message))

    return review_hits, block_hits, detectors, trustyai_used


def evaluate_ai_playbook_generation_guardrails(
    instruction: str,
    *,
    notes: str = "",
    source_url: str = "",
    instruction_override: str = "",
    override_requested: bool = False,
    treat_instruction_as_operator_text: bool = True,
    evaluation_text: str | None = None,
) -> Dict[str, Any]:
    raw_instruction = str(instruction or "").strip()
    raw_notes = str(notes or "").strip()
    raw_source_url = str(source_url or "").strip()
    raw_override = str(instruction_override or "").strip()
    sanitized_bundle, summary = sanitize_json_like(
        {
            "instruction": raw_instruction,
            "notes": raw_notes,
            "source_url": raw_source_url,
            "instruction_override": raw_override,
        },
        path="ai_playbook_generation",
        strip_prompt_injection=False,
    )
    sanitized_bundle = sanitized_bundle if isinstance(sanitized_bundle, dict) else {}
    sanitized_instruction = str(sanitized_bundle.get("instruction") or raw_instruction).strip()
    sanitized_notes = str(sanitized_bundle.get("notes") or raw_notes).strip()
    detectors = list(summary.get("detector_results") or [])
    violations = list(summary.get("violations") or [])
    raw_evaluation_text = None if evaluation_text is None else str(evaluation_text or "").strip()
    evaluation_text = (
        raw_evaluation_text
        if raw_evaluation_text is not None
        else (raw_instruction if treat_instruction_as_operator_text else (raw_override or raw_instruction))
    )
    secret_exposure_detected = any(
        str(item.get("type") or "").strip() == "secret_exposure"
        for item in detectors
    )

    review_hits, block_hits, trustyai_detectors, trustyai_used = _playbook_rule_detection_hits(evaluation_text)
    detectors.extend(trustyai_detectors)

    if raw_override:
        _append_unique_findings(
            detectors,
            detector_result(
                "manual_instruction_override",
                "low",
                "info",
                "A manual full-text instruction override was supplied for AI playbook generation.",
            ),
        )

    if secret_exposure_detected:
        review_hits.append(
            (
                "secret_exposure_detected",
                "medium",
                "Sensitive token material was detected in the AI playbook request and the stored draft was sanitized.",
            )
        )

    for rule_type, severity, message in review_hits:
        _append_unique_findings(detectors, detector_result("playbook_request_guardrail", severity, "warn", message))
        _append_unique_findings(violations, violation(rule_type, severity, message))
    for rule_type, severity, message in block_hits:
        _append_unique_findings(detectors, detector_result("playbook_request_guardrail", severity, "fail", message))
        _append_unique_findings(violations, violation(rule_type, severity, message))

    status = ALLOW
    reason = "validated"
    if block_hits:
        status = BLOCK
        reason = block_hits[0][0]
    elif review_hits:
        status = REQUIRE_REVIEW
        reason = review_hits[0][0]

    if not sanitized_instruction and raw_instruction:
        status = BLOCK
        reason = "sanitized_instruction_empty"
        message = "The AI playbook request became empty after sanitization and cannot be published safely."
        _append_unique_findings(detectors, detector_result("playbook_request_guardrail", "high", "fail", message))
        _append_unique_findings(violations, violation("sanitized_instruction_empty", "high", message))

    return {
        "surface": "ai_playbook_generation",
        "status": status,
        "reason": reason,
        "provider": playbook_guardrails_provider(trustyai_used),
        "trustyai_used": trustyai_used,
        "policy_version": guardrails_policy_version(),
        "contract_version": guardrails_contract_version(),
        "override_requested": bool(override_requested),
        "override_allowed": status == REQUIRE_REVIEW,
        "override_applied": bool(override_requested and status == REQUIRE_REVIEW),
        "instruction_override_used": bool(raw_override),
        "sanitized_instruction": sanitized_instruction,
        "sanitized_notes": sanitized_notes,
        "violations": violations,
        "detectors": detectors,
        "matches": {
            "review": [item[0] for item in review_hits],
            "block": [item[0] for item in block_hits],
        },
        "summary": {
            "has_prompt_injection": any(item[0] == "prompt_injection_detected" for item in block_hits),
            "has_secret_exposure": secret_exposure_detected,
            "raw_instruction_length": len(raw_instruction),
            "sanitized_instruction_length": len(sanitized_instruction),
            "notes_length": len(raw_notes),
            "evaluation_text_length": len(evaluation_text),
        },
        "instruction_preview": sanitized_instruction[:400],
        "notes_preview": sanitized_notes[:240],
    }
