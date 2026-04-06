from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

NORMAL_SCENARIO_NAME = "normal"
NORMAL_ANOMALY_TYPE = "normal_operation"


SCENARIO_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "normal": {
        "scenario_name": "normal",
        "anomaly_type": NORMAL_ANOMALY_TYPE,
        "display_name": "Normal Operation",
        "description": "Nominal REGISTER, INVITE, and BYE flow with healthy signaling latency.",
        "category": "nominal",
        "tone": "emerald",
        "summary": "IMS signaling is operating normally with healthy registration and session setup.",
        "blast_radius": "Nominal UE, P-CSCF, S-CSCF, and HSS control-plane path.",
        "topology": ["UE", "P-CSCF", "S-CSCF", "HSS"],
        "recommendation": "No remediation required. Keep monitoring the steady-state path.",
        "root_cause": "Nominal IMS signaling path with no production anomaly detected.",
        "primary_metric": "latency_p95",
        "metric_weights": {
            "latency_p95": 0.3,
            "register_rate": 0.25,
            "invite_rate": 0.25,
            "bye_rate": 0.2,
        },
        "base_conditions": [],
        "default_call_limit": 12,
        "default_rate": 2,
        "transport": "udp",
        "packet_sample": (
            "REGISTER sip:ims.demo.lab SIP/2.0\n"
            "Via: SIP/2.0/UDP 10.0.8.10:5060\n"
            "From: <sip:user@ims.demo.lab>\n"
            "To: <sip:user@ims.demo.lab>\n"
            "Call-ID: nominal-register\n"
            "CSeq: 1 REGISTER"
        ),
        "event_profiles": [
            {
                "method": "REGISTER",
                "count": 18,
                "latency_ms": 22.0,
                "response_code": 200,
                "payload_size": 220,
                "path": "UE -> P-CSCF -> S-CSCF",
            },
            {
                "method": "INVITE",
                "count": 12,
                "latency_ms": 30.0,
                "response_code": 200,
                "payload_size": 260,
                "path": "UE -> P-CSCF -> S-CSCF",
            },
            {
                "method": "BYE",
                "count": 12,
                "latency_ms": 25.0,
                "response_code": 200,
                "payload_size": 180,
                "path": "UE -> P-CSCF -> S-CSCF",
            },
        ],
    },
    "registration_storm": {
        "scenario_name": "registration_storm",
        "anomaly_type": "registration_storm",
        "display_name": "Registration Storm",
        "description": "Burst of REGISTER traffic saturating the registration path and amplifying retries.",
        "category": "signaling",
        "tone": "rose",
        "summary": "P-CSCF registration saturation is causing retry amplification across the control plane.",
        "blast_radius": "UE, P-CSCF, S-CSCF, anomaly-service, and feature-gateway registration path.",
        "topology": ["UE", "P-CSCF", "S-CSCF", "HSS"],
        "recommendation": "Scale the registration path and review the active traffic profile before approving remediation.",
        "root_cause": "P-CSCF registration saturation causing retransmission amplification.",
        "primary_metric": "register_rate",
        "metric_weights": {
            "register_rate": 0.4,
            "retransmission_count": 0.3,
            "latency_p95": 0.2,
            "error_4xx_ratio": 0.1,
        },
        "base_conditions": ["traffic_surge", "retry_spike"],
        "default_call_limit": 180,
        "default_rate": 6,
        "transport": "udp",
        "packet_sample": (
            "REGISTER sip:ims.demo.lab SIP/2.0\n"
            "Via: SIP/2.0/UDP 10.0.8.12:5060\n"
            "From: <sip:user@ims.demo.lab>\n"
            "To: <sip:user@ims.demo.lab>\n"
            "Call-ID: registration-surge\n"
            "CSeq: 314159 REGISTER"
        ),
        "event_profiles": [
            {
                "method": "REGISTER",
                "count": 72,
                "latency_ms": 210.0,
                "latency_step": 0.8,
                "response_code": 401,
                "payload_size": 280,
                "retransmission_every": 3,
                "path": "UE -> P-CSCF -> S-CSCF",
            },
            {
                "method": "REGISTER",
                "count": 60,
                "latency_ms": 150.0,
                "latency_step": 0.5,
                "response_code": 202,
                "payload_size": 276,
                "retransmission_every": 4,
                "path": "UE -> P-CSCF -> S-CSCF",
            },
            {
                "method": "REGISTER",
                "count": 48,
                "latency_ms": 92.0,
                "latency_step": 0.2,
                "response_code": 200,
                "payload_size": 272,
                "path": "UE -> P-CSCF -> S-CSCF",
            },
        ],
    },
    "registration_failure": {
        "scenario_name": "registration_failure",
        "anomaly_type": "registration_failure",
        "display_name": "Registration Failure",
        "description": "REGISTER requests are rejected or loop through failed registration attempts.",
        "category": "signaling",
        "tone": "amber",
        "summary": "Registration attempts are being rejected on the signaling path.",
        "blast_radius": "UE, P-CSCF, authentication flow, and registration policy path.",
        "topology": ["UE", "P-CSCF", "S-CSCF", "HSS"],
        "recommendation": "Inspect registration policies, credentials, and reject causes before reopening traffic.",
        "root_cause": "Registration requests are being rejected before the session can be established.",
        "primary_metric": "error_4xx_ratio",
        "metric_weights": {
            "error_4xx_ratio": 0.35,
            "register_rate": 0.25,
            "retransmission_count": 0.2,
            "latency_p95": 0.2,
        },
        "base_conditions": ["registration_reject", "auth_challenge_loop"],
        "default_call_limit": 24,
        "default_rate": 4,
        "transport": "udp",
        "packet_sample": (
            "REGISTER sip:ims.demo.lab SIP/2.0\n"
            "Via: SIP/2.0/UDP 10.0.8.18:5060\n"
            "From: <sip:user@ims.demo.lab>\n"
            "To: <sip:user@ims.demo.lab>\n"
            "Call-ID: registration-failure\n"
            "CSeq: 45 REGISTER"
        ),
        "event_profiles": [
            {
                "method": "REGISTER",
                "count": 44,
                "latency_ms": 135.0,
                "response_code": 403,
                "payload_size": 260,
                "retransmission_every": 4,
                "path": "UE -> P-CSCF -> Registration Policy",
            },
            {
                "method": "REGISTER",
                "count": 24,
                "latency_ms": 118.0,
                "response_code": 401,
                "payload_size": 255,
                "retransmission_every": 3,
                "path": "UE -> P-CSCF -> Registration Policy",
            },
        ],
    },
    "authentication_failure": {
        "scenario_name": "authentication_failure",
        "anomaly_type": "authentication_failure",
        "display_name": "Authentication Failure",
        "description": "Authentication challenges loop and prevent a successful registration flow.",
        "category": "auth",
        "tone": "amber",
        "summary": "Authentication handshakes are looping and blocking subscriber registration.",
        "blast_radius": "UE, P-CSCF, authentication policy, and HSS challenge path.",
        "topology": ["UE", "P-CSCF", "Auth", "HSS"],
        "recommendation": "Inspect subscriber credentials, HSS responses, and challenge loops before retrying registrations.",
        "root_cause": "Authentication challenge loops are preventing successful IMS registration.",
        "primary_metric": "error_4xx_ratio",
        "metric_weights": {
            "error_4xx_ratio": 0.35,
            "register_rate": 0.25,
            "retransmission_count": 0.25,
            "latency_p95": 0.15,
        },
        "base_conditions": ["auth_challenge_loop", "retry_spike"],
        "default_call_limit": 24,
        "default_rate": 4,
        "transport": "udp",
        "packet_sample": (
            "REGISTER sip:ims.demo.lab SIP/2.0\n"
            "Via: SIP/2.0/UDP 10.0.8.22:5060\n"
            "Proxy-Authorization: missing\n"
            "Call-ID: auth-loop\n"
            "CSeq: 73 REGISTER"
        ),
        "event_profiles": [
            {
                "method": "REGISTER",
                "count": 66,
                "latency_ms": 128.0,
                "response_code": 401,
                "payload_size": 258,
                "retransmission_every": 2,
                "path": "UE -> P-CSCF -> Auth",
            },
            {
                "method": "REGISTER",
                "count": 18,
                "latency_ms": 146.0,
                "response_code": 407,
                "payload_size": 258,
                "retransmission_every": 2,
                "path": "UE -> P-CSCF -> Auth",
            },
        ],
    },
    "malformed_invite": {
        "scenario_name": "malformed_invite",
        "anomaly_type": "malformed_sip",
        "display_name": "Malformed INVITE",
        "description": "Malformed SIP payloads trigger validation failures on the ingress path.",
        "category": "validation",
        "tone": "amber",
        "summary": "Malformed SIP payloads are failing validation on the ingress path.",
        "blast_radius": "Ingress parser, validation path, P-CSCF, and anomaly-service.",
        "topology": ["UE", "P-CSCF", "Validation", "S-CSCF"],
        "recommendation": "Quarantine the malformed traffic source and inspect the SIP generator profile.",
        "root_cause": "Malformed INVITE traffic rejected by the validation path before session setup.",
        "primary_metric": "error_4xx_ratio",
        "metric_weights": {
            "error_4xx_ratio": 0.4,
            "payload_variance": 0.25,
            "invite_rate": 0.2,
            "retransmission_count": 0.15,
        },
        "base_conditions": ["payload_anomaly", "4xx_burst"],
        "default_call_limit": 12,
        "default_rate": 3,
        "transport": "udp",
        "packet_sample": (
            "INVITE sip:user@ims.demo.lab SIP/2.0\n"
            "Via: SIP/2.0/UDP 10.0.8.44:5060\n"
            "From malformed header\n"
            "To: <sip:user@ims.demo.lab>\n"
            "Call-ID: malformed-invite\n"
            "CSeq: 11 INVITE"
        ),
        "event_profiles": [
            {
                "method": "INVITE",
                "count": 40,
                "latency_ms": 380.0,
                "latency_step": 1.0,
                "response_code": 488,
                "payload_size": 120,
                "payload_step": 4,
                "retransmission_every": 3,
                "malformed": True,
                "path": "UE -> P-CSCF -> Validation",
            },
            {
                "method": "REGISTER",
                "count": 10,
                "latency_ms": 110.0,
                "response_code": 200,
                "payload_size": 220,
                "path": "UE -> P-CSCF -> S-CSCF",
            },
        ],
    },
    "routing_error": {
        "scenario_name": "routing_error",
        "anomaly_type": "routing_error",
        "display_name": "Routing Error",
        "description": "INVITE traffic reaches an unreachable or invalid route target.",
        "category": "routing",
        "tone": "amber",
        "summary": "Session setup requests are failing because the route target cannot be resolved.",
        "blast_radius": "UE, P-CSCF, route policy, and destination lookup path.",
        "topology": ["UE", "P-CSCF", "Routing", "S-CSCF"],
        "recommendation": "Review route policy, destination lookup, and downstream registration state.",
        "root_cause": "Routing policy or destination lookup is rejecting INVITE setup requests.",
        "primary_metric": "error_4xx_ratio",
        "metric_weights": {
            "error_4xx_ratio": 0.35,
            "invite_rate": 0.25,
            "latency_p95": 0.2,
            "retransmission_count": 0.2,
        },
        "base_conditions": ["route_unreachable", "4xx_burst"],
        "default_call_limit": 18,
        "default_rate": 3,
        "transport": "udp",
        "packet_sample": (
            "INVITE sip:missing-route@ims.demo.lab SIP/2.0\n"
            "Via: SIP/2.0/UDP 10.0.8.54:5060\n"
            "Route: <sip:unknown@ims.demo.lab>\n"
            "Call-ID: routing-error\n"
            "CSeq: 22 INVITE"
        ),
        "event_profiles": [
            {
                "method": "INVITE",
                "count": 34,
                "latency_ms": 225.0,
                "response_code": 404,
                "payload_size": 250,
                "retransmission_every": 3,
                "path": "UE -> P-CSCF -> Routing",
            },
            {
                "method": "INVITE",
                "count": 12,
                "latency_ms": 240.0,
                "response_code": 483,
                "payload_size": 248,
                "retransmission_every": 4,
                "path": "UE -> P-CSCF -> Routing",
            },
        ],
    },
    "busy_destination": {
        "scenario_name": "busy_destination",
        "anomaly_type": "busy_destination",
        "display_name": "Busy Destination",
        "description": "INVITE traffic reaches a destination that is already busy or rejecting new sessions.",
        "category": "session",
        "tone": "sky",
        "summary": "Session setup is failing because the destination is busy and rejecting calls.",
        "blast_radius": "UE, P-CSCF, destination endpoint, and call setup path.",
        "topology": ["UE", "P-CSCF", "S-CSCF", "Destination"],
        "recommendation": "Confirm destination capacity and session admission rules before retrying the call path.",
        "root_cause": "The destination endpoint is busy and returning SIP busy responses.",
        "primary_metric": "invite_rate",
        "metric_weights": {
            "invite_rate": 0.25,
            "error_4xx_ratio": 0.35,
            "retransmission_count": 0.2,
            "latency_p95": 0.2,
        },
        "base_conditions": ["destination_busy", "retry_spike"],
        "default_call_limit": 12,
        "default_rate": 3,
        "transport": "udp",
        "packet_sample": (
            "INVITE sip:busy@ims.demo.lab SIP/2.0\n"
            "Via: SIP/2.0/UDP 10.0.8.61:5060\n"
            "To: <sip:busy@ims.demo.lab>\n"
            "Call-ID: busy-destination\n"
            "CSeq: 36 INVITE"
        ),
        "event_profiles": [
            {
                "method": "INVITE",
                "count": 36,
                "latency_ms": 96.0,
                "response_code": 486,
                "payload_size": 242,
                "retransmission_every": 4,
                "path": "UE -> P-CSCF -> Destination",
            },
            {
                "method": "BYE",
                "count": 12,
                "latency_ms": 64.0,
                "response_code": 200,
                "payload_size": 182,
                "path": "UE -> P-CSCF -> Destination",
            },
        ],
    },
    "call_setup_timeout": {
        "scenario_name": "call_setup_timeout",
        "anomaly_type": "call_setup_timeout",
        "display_name": "Call Setup Timeout",
        "description": "INVITE transactions are timing out before the session can be established.",
        "category": "session",
        "tone": "rose",
        "summary": "Session setup is timing out and forcing repeated INVITE retries.",
        "blast_radius": "UE, P-CSCF, S-CSCF, and downstream session setup path.",
        "topology": ["UE", "P-CSCF", "S-CSCF", "Destination"],
        "recommendation": "Inspect session setup latency, timeouts, and retransmission pressure before retrying the call.",
        "root_cause": "Session setup is exceeding timeout thresholds and causing retry storms.",
        "primary_metric": "latency_p95",
        "metric_weights": {
            "latency_p95": 0.35,
            "retransmission_count": 0.25,
            "invite_rate": 0.2,
            "error_5xx_ratio": 0.2,
        },
        "base_conditions": ["session_setup_delay", "latency_high", "retry_spike"],
        "default_call_limit": 12,
        "default_rate": 2,
        "transport": "udp",
        "packet_sample": (
            "INVITE sip:timeout@ims.demo.lab SIP/2.0\n"
            "Via: SIP/2.0/UDP 10.0.8.66:5060\n"
            "Expires: 30\n"
            "Call-ID: call-setup-timeout\n"
            "CSeq: 58 INVITE"
        ),
        "event_profiles": [
            {
                "method": "INVITE",
                "count": 24,
                "latency_ms": 410.0,
                "latency_step": 1.2,
                "response_code": 408,
                "payload_size": 246,
                "retransmission_every": 2,
                "path": "UE -> P-CSCF -> S-CSCF",
            },
            {
                "method": "INVITE",
                "count": 12,
                "latency_ms": 435.0,
                "latency_step": 1.1,
                "response_code": 504,
                "payload_size": 246,
                "retransmission_every": 2,
                "path": "UE -> P-CSCF -> S-CSCF",
            },
        ],
    },
    "call_drop_mid_session": {
        "scenario_name": "call_drop_mid_session",
        "anomaly_type": "call_drop_mid_session",
        "display_name": "Call Drop Mid Session",
        "description": "Established sessions terminate unexpectedly after setup succeeds.",
        "category": "session",
        "tone": "sky",
        "summary": "Established sessions are dropping unexpectedly after setup completes.",
        "blast_radius": "UE, P-CSCF, S-CSCF, and active session handling path.",
        "topology": ["UE", "P-CSCF", "S-CSCF", "Session"],
        "recommendation": "Inspect mid-session signaling, keepalive behavior, and BYE handling before restoring traffic.",
        "root_cause": "Mid-session signaling instability is dropping established sessions.",
        "primary_metric": "bye_rate",
        "metric_weights": {
            "bye_rate": 0.35,
            "retransmission_count": 0.25,
            "latency_p95": 0.2,
            "error_4xx_ratio": 0.2,
        },
        "base_conditions": ["session_drop", "retry_spike"],
        "default_call_limit": 10,
        "default_rate": 2,
        "transport": "udp",
        "packet_sample": (
            "BYE sip:user@ims.demo.lab SIP/2.0\n"
            "Via: SIP/2.0/UDP 10.0.8.72:5060\n"
            "Reason: unexpected-session-drop\n"
            "Call-ID: call-drop-mid-session\n"
            "CSeq: 63 BYE"
        ),
        "event_profiles": [
            {
                "method": "INVITE",
                "count": 16,
                "latency_ms": 92.0,
                "response_code": 200,
                "payload_size": 242,
                "path": "UE -> P-CSCF -> S-CSCF",
            },
            {
                "method": "BYE",
                "count": 22,
                "latency_ms": 215.0,
                "response_code": 481,
                "payload_size": 188,
                "retransmission_every": 4,
                "path": "UE -> P-CSCF -> Session",
            },
        ],
    },
    "server_internal_error": {
        "scenario_name": "server_internal_error",
        "anomaly_type": "server_internal_error",
        "display_name": "Server Internal Error",
        "description": "Core signaling services are returning 5xx responses under dependency stress.",
        "category": "server",
        "tone": "rose",
        "summary": "Server-side failures are surfacing as 5xx responses on the IMS control plane.",
        "blast_radius": "P-CSCF, S-CSCF, downstream services, and anomaly-service dependency path.",
        "topology": ["UE", "P-CSCF", "S-CSCF", "App Server"],
        "recommendation": "Inspect service logs, dependency health, and saturation before restoring full traffic.",
        "root_cause": "Downstream service instability is returning internal server errors on signaling requests.",
        "primary_metric": "error_5xx_ratio",
        "metric_weights": {
            "error_5xx_ratio": 0.4,
            "latency_p95": 0.25,
            "retransmission_count": 0.2,
            "register_rate": 0.15,
        },
        "base_conditions": ["dependency_instability", "5xx_burst"],
        "default_call_limit": 16,
        "default_rate": 3,
        "transport": "udp",
        "packet_sample": (
            "OPTIONS sip:core@ims.demo.lab SIP/2.0\n"
            "Via: SIP/2.0/UDP 10.0.8.84:5060\n"
            "Call-ID: server-internal-error\n"
            "CSeq: 8 OPTIONS"
        ),
        "event_profiles": [
            {
                "method": "OPTIONS",
                "count": 18,
                "latency_ms": 180.0,
                "response_code": 503,
                "payload_size": 198,
                "path": "UE -> P-CSCF -> App Server",
            },
            {
                "method": "REGISTER",
                "count": 16,
                "latency_ms": 210.0,
                "response_code": 500,
                "payload_size": 252,
                "retransmission_every": 3,
                "path": "UE -> P-CSCF -> S-CSCF",
            },
            {
                "method": "INVITE",
                "count": 12,
                "latency_ms": 238.0,
                "response_code": 503,
                "payload_size": 242,
                "retransmission_every": 3,
                "path": "UE -> P-CSCF -> App Server",
            },
        ],
    },
    "network_degradation": {
        "scenario_name": "network_degradation",
        "anomaly_type": "network_degradation",
        "display_name": "Network Degradation",
        "description": "Packet loss and latency drift are degrading signaling quality across the IMS path.",
        "category": "network",
        "tone": "rose",
        "summary": "Network instability is increasing latency and retransmissions across signaling flows.",
        "blast_radius": "UE, P-CSCF, S-CSCF, HSS, and transport path.",
        "topology": ["UE", "P-CSCF", "S-CSCF", "HSS"],
        "recommendation": "Inspect network transport health, packet loss indicators, and retry behavior before reopening traffic.",
        "root_cause": "Network degradation is increasing latency and retransmissions across the IMS path.",
        "primary_metric": "latency_p95",
        "metric_weights": {
            "latency_p95": 0.35,
            "retransmission_count": 0.25,
            "error_5xx_ratio": 0.2,
            "inter_arrival_mean": 0.2,
        },
        "base_conditions": ["latency_high", "retry_spike", "packet_loss_suspected"],
        "default_call_limit": 16,
        "default_rate": 3,
        "transport": "udp",
        "packet_sample": (
            "REGISTER sip:ims.demo.lab SIP/2.0\n"
            "Via: SIP/2.0/UDP 10.0.8.91:5060\n"
            "X-Network-Drift: high\n"
            "Call-ID: network-degradation\n"
            "CSeq: 91 REGISTER"
        ),
        "event_profiles": [
            {
                "method": "REGISTER",
                "count": 26,
                "latency_ms": 318.0,
                "latency_step": 0.8,
                "response_code": 200,
                "payload_size": 226,
                "retransmission_every": 2,
                "path": "UE -> P-CSCF -> S-CSCF",
            },
            {
                "method": "INVITE",
                "count": 20,
                "latency_ms": 352.0,
                "latency_step": 0.8,
                "response_code": 408,
                "payload_size": 246,
                "retransmission_every": 3,
                "path": "UE -> P-CSCF -> S-CSCF",
            },
            {
                "method": "BYE",
                "count": 10,
                "latency_ms": 280.0,
                "response_code": 200,
                "payload_size": 186,
                "retransmission_every": 4,
                "path": "UE -> P-CSCF -> S-CSCF",
            },
        ],
    },
    "retransmission_spike": {
        "scenario_name": "retransmission_spike",
        "anomaly_type": "retransmission_spike",
        "display_name": "Retransmission Spike",
        "description": "Retry traffic spikes sharply even when the underlying session mix stays similar.",
        "category": "network",
        "tone": "rose",
        "summary": "Retransmissions are spiking and signaling instability is amplifying duplicate traffic.",
        "blast_radius": "UE, transport path, P-CSCF, and retry handling path.",
        "topology": ["UE", "Transport", "P-CSCF", "S-CSCF"],
        "recommendation": "Inspect transport reliability and duplicate signaling before restoring the workload rate.",
        "root_cause": "Transport instability is amplifying retransmissions across otherwise routine signaling traffic.",
        "primary_metric": "retransmission_count",
        "metric_weights": {
            "retransmission_count": 0.4,
            "latency_p95": 0.25,
            "register_rate": 0.2,
            "invite_rate": 0.15,
        },
        "base_conditions": ["retry_spike", "packet_loss_suspected"],
        "default_call_limit": 18,
        "default_rate": 3,
        "transport": "udp",
        "packet_sample": (
            "INVITE sip:user@ims.demo.lab SIP/2.0\n"
            "Via: SIP/2.0/UDP 10.0.8.97:5060\n"
            "X-Retrans: true\n"
            "Call-ID: retransmission-spike\n"
            "CSeq: 119 INVITE"
        ),
        "event_profiles": [
            {
                "method": "REGISTER",
                "count": 34,
                "latency_ms": 128.0,
                "response_code": 200,
                "payload_size": 224,
                "retransmission_every": 1,
                "path": "UE -> Transport -> P-CSCF",
            },
            {
                "method": "INVITE",
                "count": 22,
                "latency_ms": 142.0,
                "response_code": 200,
                "payload_size": 246,
                "retransmission_every": 1,
                "path": "UE -> Transport -> P-CSCF",
            },
            {
                "method": "BYE",
                "count": 12,
                "latency_ms": 118.0,
                "response_code": 200,
                "payload_size": 186,
                "retransmission_every": 2,
                "path": "UE -> Transport -> P-CSCF",
            },
        ],
    },
}

SCENARIO_ALIASES = {
    NORMAL_ANOMALY_TYPE: NORMAL_SCENARIO_NAME,
    "malformed_sip": "malformed_invite",
    "service_degradation": "network_degradation",
}

ANOMALY_ALIASES = {
    NORMAL_SCENARIO_NAME: NORMAL_ANOMALY_TYPE,
    NORMAL_ANOMALY_TYPE: NORMAL_ANOMALY_TYPE,
    "service_degradation": "network_degradation",
    "register_storm": "registration_storm",
    "hss_latency": "network_degradation",
    "hss_overload": "network_degradation",
}
for _scenario_name, _definition in SCENARIO_DEFINITIONS.items():
    ANOMALY_ALIASES[_scenario_name] = _definition["anomaly_type"]
    ANOMALY_ALIASES[_definition["anomaly_type"]] = _definition["anomaly_type"]

CANONICAL_ANOMALY_TYPES: List[str] = []
for _definition in SCENARIO_DEFINITIONS.values():
    _anomaly_type = str(_definition["anomaly_type"])
    if _anomaly_type not in CANONICAL_ANOMALY_TYPES:
        CANONICAL_ANOMALY_TYPES.append(_anomaly_type)

ANOMALY_INDEX: Dict[str, int] = {anomaly_type: index for index, anomaly_type in enumerate(CANONICAL_ANOMALY_TYPES)}

DEFAULT_SEVERITY_BY_ANOMALY_TYPE: Dict[str, str] = {
    NORMAL_ANOMALY_TYPE: "Low",
    "registration_storm": "Critical",
    "registration_failure": "Warning",
    "authentication_failure": "Warning",
    "malformed_sip": "Warning",
    "routing_error": "Warning",
    "busy_destination": "Warning",
    "call_setup_timeout": "Critical",
    "call_drop_mid_session": "Warning",
    "server_internal_error": "Critical",
    "network_degradation": "Critical",
    "retransmission_spike": "Critical",
}


def console_scenario_names() -> List[str]:
    return list(SCENARIO_DEFINITIONS.keys())


def normalize_scenario_name(value: str | None) -> str:
    raw_value = str(value or "").strip()
    if not raw_value:
        return NORMAL_SCENARIO_NAME
    if raw_value in SCENARIO_DEFINITIONS:
        return raw_value
    if raw_value in SCENARIO_ALIASES:
        return SCENARIO_ALIASES[raw_value]
    for scenario_name, definition in SCENARIO_DEFINITIONS.items():
        if definition["anomaly_type"] == raw_value:
            return scenario_name
    return raw_value


def canonical_anomaly_type(value: str | None) -> str:
    raw_value = str(value or "").strip()
    if not raw_value:
        return NORMAL_ANOMALY_TYPE
    if raw_value in ANOMALY_ALIASES:
        return ANOMALY_ALIASES[raw_value]
    scenario_name = normalize_scenario_name(raw_value)
    definition = SCENARIO_DEFINITIONS.get(scenario_name)
    if definition:
        return str(definition["anomaly_type"])
    return raw_value


def canonical_anomaly_types() -> List[str]:
    return list(CANONICAL_ANOMALY_TYPES)


def anomaly_index(value: str | None) -> int:
    normalized = canonical_anomaly_type(value)
    if normalized not in ANOMALY_INDEX:
        raise KeyError(f"Unknown anomaly type {value!r}")
    return ANOMALY_INDEX[normalized]


def anomaly_type_from_index(index: int) -> str:
    if index < 0 or index >= len(CANONICAL_ANOMALY_TYPES):
        raise IndexError(f"Anomaly index {index} is out of range")
    return CANONICAL_ANOMALY_TYPES[index]


def severity_for_anomaly_type(value: str | None) -> str:
    normalized = canonical_anomaly_type(value)
    return DEFAULT_SEVERITY_BY_ANOMALY_TYPE.get(normalized, "Warning")


def is_nominal(value: str | None) -> bool:
    return canonical_anomaly_type(value) == NORMAL_ANOMALY_TYPE


def scenario_definition(value: str | None) -> Dict[str, Any]:
    scenario_name = normalize_scenario_name(value)
    definition = SCENARIO_DEFINITIONS.get(scenario_name, SCENARIO_DEFINITIONS[NORMAL_SCENARIO_NAME])
    return deepcopy(definition)


def anomaly_definition(value: str | None) -> Dict[str, Any]:
    return scenario_definition(canonical_anomaly_type(value))


def metric_weights(value: str | None) -> Dict[str, float]:
    definition = anomaly_definition(value)
    return {str(name): float(weight) for name, weight in dict(definition.get("metric_weights", {})).items()}


def event_profiles(value: str | None) -> List[Dict[str, Any]]:
    definition = scenario_definition(value)
    return deepcopy(list(definition.get("event_profiles", [])))


def console_scenario_catalog() -> List[Dict[str, Any]]:
    catalog: List[Dict[str, Any]] = []
    for scenario_name in console_scenario_names():
        definition = SCENARIO_DEFINITIONS[scenario_name]
        catalog.append(
            {
                "scenario_name": scenario_name,
                "anomaly_type": definition["anomaly_type"],
                "display_name": definition["display_name"],
                "description": definition["description"],
                "category": definition["category"],
                "tone": definition["tone"],
                "is_nominal": definition["anomaly_type"] == NORMAL_ANOMALY_TYPE,
                "summary": definition["summary"],
                "recommendation": definition["recommendation"],
                "packet_sample": definition["packet_sample"],
                "event_profiles": deepcopy(list(definition.get("event_profiles", []))),
            }
        )
    return catalog
