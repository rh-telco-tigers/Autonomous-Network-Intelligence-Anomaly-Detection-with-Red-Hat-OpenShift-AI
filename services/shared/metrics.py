import time

from fastapi import FastAPI, Response, Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest


REQUEST_COUNT = Counter(
    "ims_demo_http_requests_total",
    "HTTP requests processed by demo services",
    ["service", "method", "path", "status"],
)

REQUEST_LATENCY = Histogram(
    "ims_demo_http_request_duration_seconds",
    "HTTP request duration for demo services",
    ["service", "method", "path"],
)


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

