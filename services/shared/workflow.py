from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, Iterable, List

from shared.incident_taxonomy import NORMAL_ANOMALY_TYPE, canonical_anomaly_type, severity_for_anomaly_type

NEW = "NEW"
RCA_GENERATED = "RCA_GENERATED"
REMEDIATION_SUGGESTED = "REMEDIATION_SUGGESTED"
AWAITING_APPROVAL = "AWAITING_APPROVAL"
APPROVED = "APPROVED"
EXECUTING = "EXECUTING"
EXECUTED = "EXECUTED"
VERIFIED = "VERIFIED"
CLOSED = "CLOSED"
RCA_REJECTED = "RCA_REJECTED"
EXECUTION_FAILED = "EXECUTION_FAILED"
VERIFICATION_FAILED = "VERIFICATION_FAILED"
FALSE_POSITIVE = "FALSE_POSITIVE"
ESCALATED = "ESCALATED"

WORKFLOW_STATES = [
    NEW,
    RCA_GENERATED,
    REMEDIATION_SUGGESTED,
    AWAITING_APPROVAL,
    APPROVED,
    EXECUTING,
    EXECUTED,
    VERIFIED,
    CLOSED,
    RCA_REJECTED,
    EXECUTION_FAILED,
    VERIFICATION_FAILED,
    FALSE_POSITIVE,
    ESCALATED,
]

LEGACY_STATE_MAP = {
    "open": NEW,
    "acknowledged": AWAITING_APPROVAL,
    "resolved": CLOSED,
}

ACTIVE_STATES = {
    NEW,
    RCA_GENERATED,
    REMEDIATION_SUGGESTED,
    AWAITING_APPROVAL,
    APPROVED,
    EXECUTING,
    EXECUTED,
    RCA_REJECTED,
    EXECUTION_FAILED,
    VERIFICATION_FAILED,
    ESCALATED,
}

# Keep these aligned with the default Plane workflow states provisioned for the demo
# project so the UI and outbound syncs refer to the same labels.
PLANE_STATE_MAP = {
    NEW: "Todo",
    RCA_GENERATED: "Todo",
    REMEDIATION_SUGGESTED: "Todo",
    AWAITING_APPROVAL: "In Progress",
    APPROVED: "In Progress",
    EXECUTING: "In Progress",
    EXECUTED: "In Progress",
    ESCALATED: "In Progress",
    EXECUTION_FAILED: "In Progress",
    VERIFICATION_FAILED: "In Progress",
    VERIFIED: "Done",
    CLOSED: "Done",
    FALSE_POSITIVE: "Cancelled",
    RCA_REJECTED: "In Progress",
}

ALLOWED_TRANSITIONS = {
    NEW: {RCA_GENERATED, FALSE_POSITIVE, ESCALATED},
    RCA_GENERATED: {REMEDIATION_SUGGESTED, RCA_REJECTED, ESCALATED},
    REMEDIATION_SUGGESTED: {AWAITING_APPROVAL, RCA_REJECTED, ESCALATED},
    AWAITING_APPROVAL: {APPROVED, RCA_REJECTED, ESCALATED},
    APPROVED: {REMEDIATION_SUGGESTED, EXECUTING, EXECUTED, EXECUTION_FAILED, ESCALATED},
    EXECUTING: {EXECUTED, EXECUTION_FAILED},
    EXECUTED: {REMEDIATION_SUGGESTED, VERIFIED, VERIFICATION_FAILED, ESCALATED},
    VERIFIED: {CLOSED},
    RCA_REJECTED: {RCA_GENERATED, ESCALATED},
    EXECUTION_FAILED: {REMEDIATION_SUGGESTED, ESCALATED},
    VERIFICATION_FAILED: {RCA_GENERATED, REMEDIATION_SUGGESTED, ESCALATED},
    FALSE_POSITIVE: {CLOSED},
    ESCALATED: {AWAITING_APPROVAL, APPROVED, VERIFIED, CLOSED},
    CLOSED: set(),
}

RISK_PENALTIES = {
    "low": 0.10,
    "medium": 0.25,
    "high": 0.45,
}

COST_PENALTIES = {
    "low": 0.05,
    "medium": 0.15,
    "high": 0.30,
}

AUTOMATION_BONUSES = {
    "manual": 0.15,
    "human_approved": 0.12,
    "notify": 0.10,
    "ticket_only": 0.08,
}

AI_PLAYBOOK_GENERATION_ACTION = "generate_ai_ansible_playbook"


def _ai_playbook_generation_enabled() -> bool:
    return str(os.getenv("AI_PLAYBOOK_GENERATION_ENABLED", "true")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }

REMEDIATION_LIBRARY: Dict[str, Dict[str, Any]] = {
    "scale_scscf": {
        "action_ref": "scale_scscf",
        "title": "Scale the S-CSCF path",
        "suggestion_type": "ansible_playbook",
        "action_mode": "ansible",
        "description": "Increase S-CSCF capacity to absorb elevated signaling load and reduce retry amplification.",
        "risk_level": "medium",
        "automation_level": "human_approved",
        "requires_approval": True,
        "playbook_ref": "scale_scscf",
        "preconditions": ["Operator approval", "Scaling guardrails available"],
        "expected_outcome": "Registration or session setup latency stabilizes and retry volume decreases.",
        "base_success_rate": 0.78,
        "policy_bonus": 0.14,
        "execution_cost_penalty": 0.16,
        "keywords": ["scale", "capacity", "s-cscf", "latency", "saturation"],
    },
    "rate_limit_pcscf": {
        "action_ref": "rate_limit_pcscf",
        "title": "Rate limit the P-CSCF ingress path",
        "suggestion_type": "ansible_playbook",
        "action_mode": "ansible",
        "description": "Apply a temporary throttle to reduce duplicate signaling and protect downstream control-plane services.",
        "risk_level": "medium",
        "automation_level": "human_approved",
        "requires_approval": True,
        "playbook_ref": "rate_limit_pcscf",
        "preconditions": ["Operator approval", "Ingress rate limit policy available"],
        "expected_outcome": "Retry storms slow down and downstream control-plane components recover.",
        "base_success_rate": 0.74,
        "policy_bonus": 0.12,
        "execution_cost_penalty": 0.12,
        "keywords": ["rate", "limit", "p-cscf", "retry", "storm", "retransmission"],
    },
    "quarantine_imsi": {
        "action_ref": "quarantine_imsi",
        "title": "Quarantine the offending subscriber or traffic source",
        "suggestion_type": "ansible_playbook",
        "action_mode": "ansible",
        "description": "Isolate the offending IMSI or traffic source while the malformed or abusive profile is investigated.",
        "risk_level": "high",
        "automation_level": "human_approved",
        "requires_approval": True,
        "playbook_ref": "quarantine_imsi",
        "preconditions": ["Operator approval", "Source identity confirmed"],
        "expected_outcome": "Malformed or abusive traffic stops while the rest of the network remains stable.",
        "base_success_rate": 0.69,
        "policy_bonus": 0.09,
        "execution_cost_penalty": 0.20,
        "keywords": ["quarantine", "subscriber", "imsi", "malformed", "source"],
    },
    AI_PLAYBOOK_GENERATION_ACTION: {
        "action_ref": AI_PLAYBOOK_GENERATION_ACTION,
        "title": "Generate AI Ansible playbook with watsonx",
        "suggestion_type": "ai_playbook_generation",
        "action_mode": "custom",
        "description": "Send the RCA, feature signals, and current remediation context to the watsonx playbook generator so it can return a reviewable Ansible playbook on demand.",
        "risk_level": "low",
        "automation_level": "human_approved",
        "requires_approval": False,
        "playbook_ref": "",
        "preconditions": [
            "RCA is attached",
            "Kafka instruction topic is reachable",
            "External playbook generator callback is configured",
        ],
        "expected_outcome": "A reviewable AI-generated Ansible playbook is attached as a new remediation option for this incident.",
        "base_success_rate": 0.61,
        "policy_bonus": 0.16,
        "execution_cost_penalty": 0.08,
        "keywords": ["ansible", "playbook", "automation", "watsonx", "generated", "rca"],
        "metadata": {
            "ai_generated": True,
            "generation_kind": "request",
            "generation_provider": "watsonx",
            "generation_status": "not_requested",
        },
    },
    "inspect_registration_policy": {
        "action_ref": "inspect_registration_policy",
        "title": "Inspect registration policy and reject causes",
        "suggestion_type": "manual",
        "action_mode": "manual",
        "description": "Review registration policy, credential validation, and reject codes before retrying the signaling path.",
        "risk_level": "low",
        "automation_level": "manual",
        "requires_approval": True,
        "playbook_ref": "",
        "preconditions": ["Access to registration logs or policy data"],
        "expected_outcome": "Registration failures are explained before traffic is retried.",
        "base_success_rate": 0.72,
        "policy_bonus": 0.15,
        "execution_cost_penalty": 0.05,
        "keywords": ["registration", "policy", "credential", "reject", "403", "401"],
    },
    "inspect_authentication_path": {
        "action_ref": "inspect_authentication_path",
        "title": "Trace the authentication challenge loop",
        "suggestion_type": "manual",
        "action_mode": "manual",
        "description": "Validate credentials, challenge responses, and HSS interactions before restoring registrations.",
        "risk_level": "low",
        "automation_level": "manual",
        "requires_approval": True,
        "playbook_ref": "",
        "preconditions": ["Access to auth logs", "Subscriber profile visibility"],
        "expected_outcome": "Authentication rejects are explained and challenge loops stop repeating.",
        "base_success_rate": 0.70,
        "policy_bonus": 0.15,
        "execution_cost_penalty": 0.05,
        "keywords": ["auth", "authentication", "hss", "challenge", "401", "407"],
    },
    "validate_invite_headers": {
        "action_ref": "validate_invite_headers",
        "title": "Validate SIP INVITE mandatory headers",
        "suggestion_type": "manual",
        "action_mode": "manual",
        "description": "Compare malformed INVITE payloads against a known-good baseline and correct missing or corrupt headers.",
        "risk_level": "low",
        "automation_level": "manual",
        "requires_approval": True,
        "playbook_ref": "",
        "preconditions": ["Access to generator profile", "Known-good SIP template"],
        "expected_outcome": "Validation failures stop and the session setup path recovers.",
        "base_success_rate": 0.76,
        "policy_bonus": 0.16,
        "execution_cost_penalty": 0.04,
        "keywords": ["invite", "header", "malformed", "sip", "validation"],
    },
    "inspect_route_policy": {
        "action_ref": "inspect_route_policy",
        "title": "Inspect route lookup and destination policy",
        "suggestion_type": "manual",
        "action_mode": "manual",
        "description": "Review the target route mapping, downstream registration state, and policy matches for failed session setup.",
        "risk_level": "low",
        "automation_level": "manual",
        "requires_approval": True,
        "playbook_ref": "",
        "preconditions": ["Access to route policy state", "Destination mapping visibility"],
        "expected_outcome": "The route failure is identified before traffic is retried.",
        "base_success_rate": 0.67,
        "policy_bonus": 0.14,
        "execution_cost_penalty": 0.05,
        "keywords": ["route", "routing", "destination", "lookup", "policy"],
    },
    "confirm_destination_capacity": {
        "action_ref": "confirm_destination_capacity",
        "title": "Confirm destination capacity and admission controls",
        "suggestion_type": "manual",
        "action_mode": "manual",
        "description": "Validate whether the downstream destination is busy because of real capacity pressure or admission policy drift.",
        "risk_level": "low",
        "automation_level": "manual",
        "requires_approval": True,
        "playbook_ref": "",
        "preconditions": ["Destination telemetry available"],
        "expected_outcome": "Busy destination responses are explained and a safe next step is chosen.",
        "base_success_rate": 0.64,
        "policy_bonus": 0.12,
        "execution_cost_penalty": 0.05,
        "keywords": ["busy", "destination", "capacity", "admission"],
    },
    "trace_session_timeout": {
        "action_ref": "trace_session_timeout",
        "title": "Trace the session setup timeout path",
        "suggestion_type": "manual",
        "action_mode": "manual",
        "description": "Inspect timeout thresholds, downstream responsiveness, and retransmission growth before re-running the call path.",
        "risk_level": "low",
        "automation_level": "manual",
        "requires_approval": True,
        "playbook_ref": "",
        "preconditions": ["Latency timeline available"],
        "expected_outcome": "The timeout source is identified and verified before more traffic is sent.",
        "base_success_rate": 0.71,
        "policy_bonus": 0.15,
        "execution_cost_penalty": 0.06,
        "keywords": ["timeout", "latency", "invite", "setup", "retransmission"],
    },
    "trace_mid_session_signaling": {
        "action_ref": "trace_mid_session_signaling",
        "title": "Trace mid-session signaling instability",
        "suggestion_type": "manual",
        "action_mode": "manual",
        "description": "Inspect keepalive behavior, BYE handling, and session continuity before replaying the affected traffic.",
        "risk_level": "low",
        "automation_level": "manual",
        "requires_approval": True,
        "playbook_ref": "",
        "preconditions": ["Session logs available"],
        "expected_outcome": "The session drop mechanism is identified before changes are rolled out.",
        "base_success_rate": 0.68,
        "policy_bonus": 0.14,
        "execution_cost_penalty": 0.06,
        "keywords": ["session", "drop", "bye", "keepalive", "mid-session"],
    },
    "inspect_app_server_dependencies": {
        "action_ref": "inspect_app_server_dependencies",
        "title": "Inspect app server dependency health",
        "suggestion_type": "manual",
        "action_mode": "manual",
        "description": "Review service logs, dependency saturation, and 5xx propagation before deciding whether to scale or fail over.",
        "risk_level": "low",
        "automation_level": "manual",
        "requires_approval": True,
        "playbook_ref": "",
        "preconditions": ["Service logs available"],
        "expected_outcome": "The dependency or saturation issue is isolated before action is executed.",
        "base_success_rate": 0.73,
        "policy_bonus": 0.15,
        "execution_cost_penalty": 0.05,
        "keywords": ["server", "5xx", "dependency", "app", "error"],
    },
    "investigate_network_transport": {
        "action_ref": "investigate_network_transport",
        "title": "Investigate network transport degradation",
        "suggestion_type": "manual",
        "action_mode": "manual",
        "description": "Inspect packet loss, transport saturation, and route health before pushing automation onto the signaling path.",
        "risk_level": "low",
        "automation_level": "manual",
        "requires_approval": True,
        "playbook_ref": "",
        "preconditions": ["Network telemetry available"],
        "expected_outcome": "Transport degradation is confirmed before any broader mitigation is applied.",
        "base_success_rate": 0.75,
        "policy_bonus": 0.16,
        "execution_cost_penalty": 0.05,
        "keywords": ["network", "transport", "packet", "loss", "latency"],
    },
    "investigate_retransmissions": {
        "action_ref": "investigate_retransmissions",
        "title": "Investigate retransmission amplification",
        "suggestion_type": "manual",
        "action_mode": "manual",
        "description": "Review duplicate signaling, retry cadence, and transport stability before changing rate limits or scaling.",
        "risk_level": "low",
        "automation_level": "manual",
        "requires_approval": True,
        "playbook_ref": "",
        "preconditions": ["Retry traces available"],
        "expected_outcome": "The duplicate signaling pattern is understood before mitigations are applied.",
        "base_success_rate": 0.74,
        "policy_bonus": 0.15,
        "execution_cost_penalty": 0.05,
        "keywords": ["retransmission", "duplicate", "retry", "transport"],
    },
    "open_plane_escalation": {
        "action_ref": "open_plane_escalation",
        "title": "Escalate to Plane for human coordination",
        "suggestion_type": "escalate_ticket",
        "action_mode": "notify",
        "description": "Create or sync a Plane issue so operators can coordinate ownership, notes, and next steps outside the runtime loop.",
        "risk_level": "low",
        "automation_level": "ticket_only",
        "requires_approval": True,
        "playbook_ref": "",
        "preconditions": ["Plane integration configured or demo relay enabled"],
        "expected_outcome": "The incident is visible in Plane with RCA and remediation context attached.",
        "base_success_rate": 0.63,
        "policy_bonus": 0.08,
        "execution_cost_penalty": 0.03,
        "keywords": ["plane", "ticket", "coordination", "escalation"],
    },
}

REMEDIATION_CATALOG = {
    "registration_storm": ["scale_scscf", "rate_limit_pcscf", "open_plane_escalation"],
    "registration_failure": ["inspect_registration_policy", "quarantine_imsi", "open_plane_escalation"],
    "authentication_failure": ["inspect_authentication_path", "quarantine_imsi", "open_plane_escalation"],
    "malformed_sip": ["validate_invite_headers", "quarantine_imsi", "open_plane_escalation"],
    "routing_error": ["inspect_route_policy", "open_plane_escalation"],
    "busy_destination": ["confirm_destination_capacity", "open_plane_escalation"],
    "call_setup_timeout": ["trace_session_timeout", "scale_scscf", "open_plane_escalation"],
    "call_drop_mid_session": ["trace_mid_session_signaling", "open_plane_escalation"],
    "server_internal_error": ["inspect_app_server_dependencies", "scale_scscf", "open_plane_escalation"],
    "network_degradation": ["investigate_network_transport", "rate_limit_pcscf", "open_plane_escalation"],
    "retransmission_spike": ["investigate_retransmissions", "rate_limit_pcscf", "open_plane_escalation"],
}


def normalize_workflow_state(value: str | None) -> str:
    raw = str(value or NEW).strip()
    if not raw:
        return NEW
    legacy = LEGACY_STATE_MAP.get(raw.lower())
    if legacy:
        return legacy
    normalized = raw.upper()
    return normalized if normalized in WORKFLOW_STATES else NEW


def can_transition(current_state: str | None, target_state: str | None) -> bool:
    current = normalize_workflow_state(current_state)
    target = normalize_workflow_state(target_state)
    if current == target:
        return True
    return target in ALLOWED_TRANSITIONS.get(current, set())


def is_active_state(value: str | None) -> bool:
    return normalize_workflow_state(value) in ACTIVE_STATES


def plane_state_for_workflow(value: str | None) -> str:
    state = normalize_workflow_state(value)
    return PLANE_STATE_MAP.get(state, "Todo")


def titleize_state(value: str | None) -> str:
    return normalize_workflow_state(value).replace("_", " ").title()


def severity_from_prediction(anomaly_type: str | None, confidence: float | None) -> str:
    normalized_type = canonical_anomaly_type(anomaly_type)
    if normalized_type == NORMAL_ANOMALY_TYPE:
        return "Low"
    base_severity = severity_for_anomaly_type(normalized_type)
    numeric_confidence = max(0.0, min(float(confidence or 0.0), 1.0))
    if numeric_confidence < 0.45:
        return "Medium"
    if numeric_confidence < 0.7 and base_severity == "Critical":
        return "Warning"
    if numeric_confidence < 0.7 and base_severity == "Warning":
        return "Medium"
    return base_severity


def severity_from_score(score: float) -> str:
    if float(score) >= 0.95:
        return "Critical"
    if float(score) >= 0.80:
        return "Warning"
    return "Medium"


def plane_priority_for_severity(severity: str | None) -> str:
    normalized = str(severity or "medium").strip().lower()
    if normalized == "critical":
        return "urgent"
    if normalized == "warning":
        return "high"
    if normalized == "medium":
        return "medium"
    return "low"


def ticket_creation_exclusion_reason(incident: Dict[str, Any]) -> str | None:
    severity = str(incident.get("severity") or "Medium")
    state = normalize_workflow_state(str(incident.get("status") or incident.get("workflow_state") or NEW))
    if state == FALSE_POSITIVE:
        return "Incident is already classified as a false positive."
    if incident.get("duplicate_of_incident_id"):
        return "Incident is a duplicate of an already tracked parent incident."
    if state in {VERIFIED, CLOSED}:
        return "Incident already completed local resolution before human workflow was required."
    severity_order = {"Critical": 3, "Warning": 2, "Medium": 1, "Low": 0}
    threshold = severity_order["Medium"]
    if severity_order.get(severity, 1) < threshold:
        return "Incident severity is below the configured ticket threshold."
    return None


def resolution_quality(metric_based: bool, notes: str, custom_resolution: str) -> str:
    resolution_text = f"{notes} {custom_resolution}".strip()
    if metric_based and len(resolution_text) >= 48:
        return "high"
    if metric_based or len(resolution_text) >= 24:
        return "medium"
    return "low"


def _stable_id(parts: Iterable[str]) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:20]


def _keyword_similarity(text: str, keywords: Iterable[str]) -> float:
    normalized = text.lower()
    keywords_list = [keyword.lower() for keyword in keywords if keyword]
    if not keywords_list:
        return 0.0
    matches = sum(1 for keyword in keywords_list if keyword in normalized)
    return round(min(matches / len(keywords_list), 1.0), 4)


def generate_remediation_suggestions(
    incident: Dict[str, Any],
    rca_payload: Dict[str, Any],
    historical_success_rates: Dict[str, float] | None = None,
) -> List[Dict[str, Any]]:
    anomaly_type = canonical_anomaly_type(str(incident.get("anomaly_type") or NORMAL_ANOMALY_TYPE))
    template_ids = list(REMEDIATION_CATALOG.get(anomaly_type, ["open_plane_escalation"]))
    if _ai_playbook_generation_enabled() and AI_PLAYBOOK_GENERATION_ACTION not in template_ids:
        template_ids.append(AI_PLAYBOOK_GENERATION_ACTION)
    historical_success_rates = historical_success_rates or {}
    retrieved_documents = rca_payload.get("retrieved_documents") or []
    retrieval_max = 0.0
    if isinstance(retrieved_documents, list):
        retrieval_max = max((float(item.get("score", 0.0)) for item in retrieved_documents if isinstance(item, dict)), default=0.0)
    rationale_text = " ".join(
        [
            str(incident.get("anomaly_type") or ""),
            str(incident.get("recommendation") or ""),
            str(rca_payload.get("root_cause") or ""),
            str(rca_payload.get("recommendation") or ""),
        ]
    )
    rca_confidence = max(min(float(rca_payload.get("confidence") or 0.0), 0.98), 0.0)
    suggestions: List[Dict[str, Any]] = []
    for template_id in template_ids:
        template = dict(REMEDIATION_LIBRARY[template_id])
        historical_success = historical_success_rates.get(
            str(template.get("action_ref") or template_id),
            float(template.get("base_success_rate", 0.6)),
        )
        keyword_overlap = _keyword_similarity(rationale_text, template.get("keywords", []))
        retrieval_similarity = round(max(retrieval_max, keyword_overlap), 4)
        policy_bonus = float(template.get("policy_bonus", AUTOMATION_BONUSES.get(str(template.get("automation_level")), 0.08)))
        risk_penalty = RISK_PENALTIES.get(str(template.get("risk_level", "medium")).lower(), 0.25)
        execution_cost_penalty = float(
            template.get(
                "execution_cost_penalty",
                COST_PENALTIES.get(str(template.get("risk_level", "medium")).lower(), 0.15),
            )
        )
        rank_score = (
            (0.40 * historical_success)
            + (0.25 * retrieval_similarity)
            + (0.20 * rca_confidence)
            + (0.15 * policy_bonus)
            - (0.20 * risk_penalty)
            - (0.10 * execution_cost_penalty)
        )
        suggestion = {
            "suggestion_id": _stable_id(
                [
                    str(incident.get("id") or incident.get("incident_id") or "incident"),
                    str(incident.get("workflow_revision") or 1),
                    template_id,
                    str(template.get("title") or template_id),
                ]
            ),
            "title": str(template["title"]),
            "suggestion_type": str(template["suggestion_type"]),
            "action_mode": str(template["action_mode"]),
            "action_ref": str(template.get("action_ref") or template_id),
            "description": str(template["description"]),
            "risk_level": str(template.get("risk_level", "medium")),
            "confidence": round(max(min((historical_success * 0.55) + (rca_confidence * 0.45), 0.98), 0.35), 2),
            "automation_level": str(template.get("automation_level", "manual")),
            "requires_approval": bool(template.get("requires_approval", True)),
            "playbook_ref": str(template.get("playbook_ref") or ""),
            "preconditions": list(template.get("preconditions", [])),
            "expected_outcome": str(template.get("expected_outcome") or ""),
            "metadata": dict(template.get("metadata") or {}),
            "historical_success_rate": round(historical_success, 4),
            "retrieval_similarity": retrieval_similarity,
            "rca_confidence": round(rca_confidence, 4),
            "policy_bonus": round(policy_bonus, 4),
            "risk_penalty": round(risk_penalty, 4),
            "execution_cost_penalty": round(execution_cost_penalty, 4),
            "rank_score": round(rank_score, 4),
        }
        suggestions.append(suggestion)
    suggestions.sort(
        key=lambda item: (
            -float(item["rank_score"]),
            float(item["risk_penalty"]),
            0 if str(item["automation_level"]) == "manual" else 1,
            str(item["title"]),
        )
    )
    for index, suggestion in enumerate(suggestions, start=1):
        suggestion["suggestion_rank"] = index
    return suggestions
