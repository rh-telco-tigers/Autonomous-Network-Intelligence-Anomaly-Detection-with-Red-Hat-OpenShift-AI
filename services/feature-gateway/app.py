import os
import socket
import time
from datetime import datetime, timezone
from statistics import mean
from typing import Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field
import requests

from shared.cors import install_cors
from shared.metrics import install_metrics


class Event(BaseModel):
    method: str
    latency_ms: float = 0.0
    response_code: int = 200
    node_id: str = "pcscf-1"
    node_role: str = "P-CSCF"
    payload_size: int = 0
    retransmission: bool = False
    timestamp: Optional[datetime] = None


class FeatureWindowRequest(BaseModel):
    scenario: str = "normal"
    node_id: str = "pcscf-1"
    node_role: str = "P-CSCF"
    duration_seconds: int = 30
    events: List[Event] = Field(default_factory=list)


app = FastAPI(title="feature-gateway", version="0.1.0")
install_cors(app)
install_metrics(app, "feature-gateway")

IMS_PCSCF_HOST = os.getenv("IMS_PCSCF_HOST", "ims-pcscf.ims-demo-lab.svc.cluster.local")
IMS_PCSCF_PORT = int(os.getenv("IMS_PCSCF_PORT", "5060"))
IMS_PCSCF_TELEMETRY = os.getenv("IMS_PCSCF_TELEMETRY_URL", "http://ims-pcscf.ims-demo-lab.svc.cluster.local:8080")
IMS_SCSCF_TELEMETRY = os.getenv("IMS_SCSCF_TELEMETRY_URL", "http://ims-scscf.ims-demo-lab.svc.cluster.local:8080")
IMS_HSS_TELEMETRY = os.getenv("IMS_HSS_TELEMETRY_URL", "http://ims-hss.ims-demo-lab.svc.cluster.local:8080")


def aggregate_features(events: List[Event], node_id: str, node_role: str, duration_seconds: int, scenario: str) -> Dict[str, object]:
    method_counts = {
        "REGISTER": 0,
        "INVITE": 0,
        "BYE": 0,
    }
    latencies = []
    error_4xx = 0
    error_5xx = 0
    retransmissions = 0
    payload_sizes = []

    for event in events:
        method_counts[event.method.upper()] = method_counts.get(event.method.upper(), 0) + 1
        latencies.append(event.latency_ms)
        payload_sizes.append(event.payload_size)
        retransmissions += int(event.retransmission)
        if 400 <= event.response_code < 500:
            error_4xx += 1
        if event.response_code >= 500:
            error_5xx += 1

    total_events = max(len(events), 1)
    return {
        "window_id": f"{scenario}-{node_id}-{int(datetime.now(tz=timezone.utc).timestamp())}",
        "start_time": datetime.now(tz=timezone.utc).isoformat(),
        "duration": f"{duration_seconds}s",
        "node_id": node_id,
        "node_role": node_role,
        "schema_version": "feature_schema_v1",
        "features": {
            "register_rate": round(method_counts.get("REGISTER", 0) / duration_seconds, 2),
            "invite_rate": round(method_counts.get("INVITE", 0) / duration_seconds, 2),
            "bye_rate": round(method_counts.get("BYE", 0) / duration_seconds, 2),
            "error_4xx_ratio": round(error_4xx / total_events, 3),
            "error_5xx_ratio": round(error_5xx / total_events, 3),
            "latency_p95": round(max(latencies) if latencies else 0.0, 2),
            "retransmission_count": retransmissions,
            "inter_arrival_mean": round(duration_seconds / total_events, 3),
            "payload_variance": round(max(payload_sizes) - min(payload_sizes), 2) if payload_sizes else 0.0,
            "node_id": node_id,
            "node_role": node_role,
        },
        "labels": {
            "anomaly": scenario != "normal",
            "anomaly_type": None if scenario == "normal" else scenario,
        },
    }


def scenario_events(scenario: str) -> List[Event]:
    if scenario == "registration_storm":
        return [
            Event(method="REGISTER", latency_ms=80 + i, payload_size=280, retransmission=i % 4 == 0)
            for i in range(180)
        ]
    if scenario == "malformed_invite":
        return [
            Event(method="INVITE", latency_ms=400 + i, payload_size=120 + i, response_code=488, retransmission=i % 3 == 0)
            for i in range(40)
        ]
    return [
        Event(method="REGISTER", latency_ms=22, payload_size=220),
        Event(method="INVITE", latency_ms=30, payload_size=260),
        Event(method="BYE", latency_ms=25, payload_size=180),
        Event(method="REGISTER", latency_ms=24, payload_size=220),
    ]


def _sip_payload(method: str, malformed: bool = False, retransmission: bool = False) -> bytes:
    body = "MALFORMED SIP PAYLOAD" if malformed else "v=0"
    extras = "X-Retrans: true\r\n" if retransmission else ""
    return (
        f"{method} sip:ims-demo@example.com SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP feature-gateway\r\n"
        f"{extras}"
        f"Content-Length: {len(body)}\r\n\r\n{body}"
    ).encode("utf-8")


def _send_udp_traffic(payloads: List[bytes]) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(0.5)
        for payload in payloads:
            sock.sendto(payload, (IMS_PCSCF_HOST, IMS_PCSCF_PORT))
            try:
                sock.recvfrom(2048)
            except Exception:
                pass


def _reset_telemetry() -> None:
    for base_url in [IMS_PCSCF_TELEMETRY, IMS_SCSCF_TELEMETRY, IMS_HSS_TELEMETRY]:
        try:
            requests.post(f"{base_url}/reset", timeout=5)
        except Exception:
            continue


def _fetch_telemetry(base_url: str) -> Dict[str, object]:
    response = requests.get(f"{base_url}/telemetry", timeout=10)
    response.raise_for_status()
    return response.json()


def _telemetry_to_window(scenario: str, telemetry: Dict[str, object]) -> Dict[str, object]:
    duration_seconds = max(int(round(float(telemetry.get("duration_seconds", 30.0)))), 1)
    features = {
        "register_rate": float(telemetry.get("register_rate", 0.0)),
        "invite_rate": float(telemetry.get("invite_rate", 0.0)),
        "bye_rate": float(telemetry.get("bye_rate", 0.0)),
        "error_4xx_ratio": float(telemetry.get("error_4xx_ratio", 0.0)),
        "error_5xx_ratio": float(telemetry.get("error_5xx_ratio", 0.0)),
        "latency_p95": float(telemetry.get("latency_p95", 0.0)),
        "retransmission_count": float(telemetry.get("retransmission_count", 0.0)),
        "inter_arrival_mean": float(telemetry.get("inter_arrival_mean", 0.0)),
        "payload_variance": float(telemetry.get("payload_variance", 0.0)),
        "node_id": str(telemetry.get("node_id", "pcscf-1")),
        "node_role": str(telemetry.get("node_role", "P-CSCF")),
    }
    return {
        "window_id": f"{scenario}-{features['node_id']}-{int(datetime.now(tz=timezone.utc).timestamp())}",
        "start_time": datetime.now(tz=timezone.utc).isoformat(),
        "duration": f"{duration_seconds}s",
        "node_id": features["node_id"],
        "node_role": features["node_role"],
        "schema_version": "feature_schema_v1",
        "features": features,
        "labels": {
            "anomaly": scenario != "normal",
            "anomaly_type": None if scenario == "normal" else scenario,
        },
        "telemetry_sources": {
            "pcscf": IMS_PCSCF_TELEMETRY,
            "scscf": IMS_SCSCF_TELEMETRY,
            "hss": IMS_HSS_TELEMETRY,
        },
    }


def live_window_for_scenario(scenario: str) -> Dict[str, object]:
    scenario_key = "normal" if scenario == "normal" else scenario
    _reset_telemetry()

    payloads: List[bytes] = []
    if scenario_key == "registration_storm":
        payloads = [_sip_payload("REGISTER", retransmission=index % 4 == 0) for index in range(180)]
    elif scenario_key == "malformed_invite":
        payloads = [_sip_payload("INVITE", malformed=True, retransmission=index % 3 == 0) for index in range(48)]
    else:
        payloads = [
            _sip_payload("REGISTER"),
            _sip_payload("INVITE"),
            _sip_payload("BYE"),
            _sip_payload("REGISTER"),
        ]

    _send_udp_traffic(payloads)
    time.sleep(1.0)
    telemetry = _fetch_telemetry(IMS_PCSCF_TELEMETRY)
    window = _telemetry_to_window(scenario_key, telemetry)
    window["event_count"] = int(telemetry.get("message_count", len(payloads)))
    window["latency_mean"] = round(float(telemetry.get("latency_mean", 0.0)), 2)
    return window


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/sample-window/{scenario}")
def sample_window(scenario: str):
    scenario_key = "normal" if scenario == "normal" else scenario
    return aggregate_features(
        events=scenario_events(scenario_key),
        node_id="pcscf-1",
        node_role="P-CSCF",
        duration_seconds=30,
        scenario=scenario_key,
    )


@app.get("/live-window/{scenario}")
def live_window(scenario: str):
    return live_window_for_scenario(scenario)


@app.post("/feature-windows")
def build_feature_window(request: FeatureWindowRequest):
    events = request.events or scenario_events(request.scenario)
    window = aggregate_features(
        events=events,
        node_id=request.node_id,
        node_role=request.node_role,
        duration_seconds=request.duration_seconds,
        scenario=request.scenario,
    )
    window["event_count"] = len(events)
    window["latency_mean"] = round(mean([event.latency_ms for event in events]) if events else 0.0, 2)
    return window
