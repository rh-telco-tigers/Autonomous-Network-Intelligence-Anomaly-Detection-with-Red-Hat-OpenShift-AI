import time
from collections import Counter as CollectionCounter
from typing import Iterable, Mapping

from fastapi import FastAPI, Response, Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from shared.workflow import is_active_state


REQUEST_COUNT = Counter(
    "ani_demo_http_requests_total",
    "HTTP requests processed by demo services",
    ["service", "method", "path", "status"],
)

REQUEST_LATENCY = Histogram(
    "ani_demo_http_request_duration_seconds",
    "HTTP request duration for demo services",
    ["service", "method", "path"],
)

INCIDENT_COUNT = Counter(
    "ani_demo_incidents_total",
    "Incidents created in the demo control plane",
    ["project", "anomaly_type", "status"],
)

ACTIVE_INCIDENTS = Gauge(
    "ani_demo_active_incidents",
    "Current active incidents grouped by predictive model output and model version",
    ["project", "anomaly_type", "model_version"],
)

RCA_CONFIDENCE = Histogram(
    "ani_demo_rca_confidence",
    "Confidence distribution for generated RCA responses",
    ["project", "generation_mode"],
    buckets=(0.0, 0.25, 0.5, 0.75, 0.9, 1.0),
)

AUTOMATION_ACTIONS = Counter(
    "ani_demo_automation_actions_total",
    "Automation approvals and executions observed in the demo control plane",
    ["action", "status"],
)

INTEGRATION_EVENTS = Counter(
    "ani_demo_integration_events_total",
    "Slack and Jira integration events emitted by the demo platform",
    ["integration", "status"],
)

MODEL_PROMOTIONS = Counter(
    "ani_demo_model_promotions_total",
    "Model promotion operations recorded by the demo model registry",
    ["stage", "status"],
)

WORKFLOW_TRANSITIONS = Counter(
    "ani_demo_workflow_transitions_total",
    "Workflow transitions recorded by the demo control plane",
    ["from_state", "to_state"],
)

TICKET_SYNC_EVENTS = Counter(
    "ani_demo_ticket_sync_events_total",
    "Ticket creation and sync operations observed by the demo control plane",
    ["provider", "direction", "status"],
)

VERIFICATION_EVENTS = Counter(
    "ani_demo_verification_events_total",
    "Verification outcomes captured by the demo control plane",
    ["status"],
)


def record_incident(project: str, anomaly_type: str, status: str) -> None:
    INCIDENT_COUNT.labels(project, anomaly_type, status).inc()


def set_active_incidents(incidents: Iterable[Mapping[str, object]]) -> None:
    ACTIVE_INCIDENTS.clear()
    counts = CollectionCounter(
        (
            str(incident.get("project") or "ani-demo"),
            str(incident.get("anomaly_type") or "unknown"),
            str(incident.get("model_version") or "unknown"),
        )
        for incident in incidents
        if is_active_state(str(incident.get("status") or incident.get("workflow_state") or "NEW"))
    )
    for (project, anomaly_type, model_version), count in counts.items():
        ACTIVE_INCIDENTS.labels(project, anomaly_type, model_version).set(count)


def record_rca(project: str, generation_mode: str, confidence: float) -> None:
    RCA_CONFIDENCE.labels(project, generation_mode).observe(confidence)


def record_automation(action: str, status: str) -> None:
    AUTOMATION_ACTIONS.labels(action, status).inc()


def record_integration(integration: str, status: str) -> None:
    INTEGRATION_EVENTS.labels(integration, status).inc()


def record_model_promotion(stage: str, status: str) -> None:
    MODEL_PROMOTIONS.labels(stage, status).inc()


def record_workflow_transition(from_state: str, to_state: str) -> None:
    WORKFLOW_TRANSITIONS.labels(from_state, to_state).inc()


def record_ticket_sync(provider: str, direction: str, status: str) -> None:
    TICKET_SYNC_EVENTS.labels(provider, direction, status).inc()


def record_verification(status: str) -> None:
    VERIFICATION_EVENTS.labels(status).inc()


def install_metrics(app: FastAPI, service_name: str) -> None:
    @app.middleware("http")
    async def instrument_requests(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start
        path = request.url.path
        REQUEST_COUNT.labels(service_name, request.method, path, str(response.status_code)).inc()
        REQUEST_LATENCY.labels(service_name, request.method, path).observe(duration)
        response.headers["x-service-name"] = service_name
        return response

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
