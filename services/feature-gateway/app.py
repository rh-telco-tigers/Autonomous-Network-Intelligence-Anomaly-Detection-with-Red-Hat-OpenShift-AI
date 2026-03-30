from datetime import datetime, timezone
from statistics import mean
from typing import Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

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
install_metrics(app, "feature-gateway")


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
