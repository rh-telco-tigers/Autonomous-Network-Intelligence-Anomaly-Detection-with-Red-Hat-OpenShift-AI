"""Compile-time independent KFP pipeline uploader for the feature-store workflow."""

import hashlib
import json
import os
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from kfp import Client


DEFAULT_DSPA_NAME = "dspa"
DEFAULT_PIPELINE_NAME = "ani-featurestore-train-and-register"
DEFAULT_EXPERIMENT_NAME = "ani-featurestore"
DEFAULT_RUN_NAME_PREFIX = "ani-featurestore-manual"
DEFAULT_PACKAGE_PATH = "/opt/kfp/ani_featurestore_pipeline.yaml"
DEFAULT_KFP_HOST_TEMPLATE = "https://ds-pipeline-{dspa}.{namespace}.svc.cluster.local:8443"
DEFAULT_SERVICE_CA_CERT = "/run/secrets/kubernetes.io/serviceaccount/service-ca.crt"
DEFAULT_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
DEFAULT_MODEL_REGISTRY_NAMESPACE = "rhoai-model-registries"
DEFAULT_MODEL_REGISTRY_SERVICE = "default-modelregistry"
DEFAULT_MODEL_REGISTRY_ROUTE_NAME = ""
DEFAULT_KUBERNETES_CA_CERT = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
DEFAULT_STALE_RUN_SECONDS = 1800
DEFAULT_PIPELINE_PARAMETERS = {
    "bundle_version": "ani-feature-bundle-v1",
    "feature_service_name": "ani_anomaly_scoring_v1",
    "candidate_version": "candidate-fs-v1",
    "automl_engine": "autogluon",
    "model_name": "ani-anomaly-featurestore",
    "model_version_name": "ani-anomaly-featurestore-v1",
    "serving_model_name": "ani-predictive-fs",
    "serving_runtime_name": "ani-autogluon-mlserver-runtime",
    "serving_model_format_name": "autogluon",
    "serving_model_format_version": "1",
    "serving_protocol_version": "v2",
    "serving_prefix": "predictive-featurestore",
    "serving_alias": "current",
}


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise ValueError(f"Missing required environment variable {name}")
    return value


def _namespace() -> str:
    if os.getenv("POD_NAMESPACE"):
        return os.environ["POD_NAMESPACE"]
    return Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace").read_text().strip()


def _kubernetes_request(path: str) -> dict[str, Any] | None:
    token_path = Path(os.getenv("KFP_TOKEN_PATH", DEFAULT_SA_TOKEN_PATH))
    if not token_path.exists():
        return None
    api_host = os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
    api_port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", os.getenv("KUBERNETES_SERVICE_PORT", "443"))
    ca_path = Path(DEFAULT_KUBERNETES_CA_CERT)
    context = ssl.create_default_context(cafile=str(ca_path)) if ca_path.exists() else ssl.create_default_context()
    request = Request(
        f"https://{api_host}:{api_port}{path}",
        headers={"Authorization": f"Bearer {token_path.read_text().strip()}"},
    )
    try:
        with urlopen(request, timeout=5, context=context) as response:
            payload = response.read().decode()
    except (HTTPError, URLError, OSError, ValueError):
        return None
    if not payload:
        return {}
    try:
        return json.loads(payload)
    except ValueError:
        return None


def discover_model_registry_endpoint() -> str | None:
    explicit = os.getenv("MODEL_REGISTRY_ENDPOINT", "").strip()
    if explicit and ".svc.cluster.local" not in explicit:
        return explicit.rstrip("/")

    namespace = os.getenv("MODEL_REGISTRY_NAMESPACE", DEFAULT_MODEL_REGISTRY_NAMESPACE).strip() or DEFAULT_MODEL_REGISTRY_NAMESPACE
    service_name = os.getenv("MODEL_REGISTRY_SERVICE", DEFAULT_MODEL_REGISTRY_SERVICE).strip() or DEFAULT_MODEL_REGISTRY_SERVICE
    service_payload = _kubernetes_request(f"/api/v1/namespaces/{namespace}/services/{service_name}") or {}
    annotations = ((service_payload.get("metadata") or {}).get("annotations") or {}) if isinstance(service_payload, dict) else {}
    external_address = str(annotations.get("routing.opendatahub.io/external-address-rest") or "").strip()
    if external_address:
        external_host = external_address.removeprefix("https://").rstrip("/")
        return f"https://{external_host}"

    route_name = os.getenv("MODEL_REGISTRY_ROUTE_NAME", DEFAULT_MODEL_REGISTRY_ROUTE_NAME).strip() or DEFAULT_MODEL_REGISTRY_ROUTE_NAME
    if route_name:
        route_payload = _kubernetes_request(f"/apis/route.openshift.io/v1/namespaces/{namespace}/routes/{route_name}") or {}
        route_spec = (route_payload.get("spec") or {}) if isinstance(route_payload, dict) else {}
        route_host = str(route_spec.get("host") or "").strip()
        if route_host:
            return f"https://{route_host}"
    return explicit.rstrip("/") or None


def _load_pipeline_parameters() -> dict[str, Any]:
    raw = os.getenv("PIPELINE_PARAMETERS_JSON", "").strip()
    if not raw:
        parsed = dict(DEFAULT_PIPELINE_PARAMETERS)
    else:
        parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("PIPELINE_PARAMETERS_JSON must be a JSON object")
    registry_endpoint = discover_model_registry_endpoint()
    if registry_endpoint:
        parsed["model_registry_endpoint"] = registry_endpoint
    return parsed


def _env_flag(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _explicit_run_name() -> str:
    return os.getenv("RUN_NAME", "").strip()


def _run_name(explicit_run_name: str = "") -> str:
    if explicit_run_name:
        return explicit_run_name
    return f"{DEFAULT_RUN_NAME_PREFIX}-{time.strftime('%Y%m%d-%H%M%S')}"


def discover_kfp_host(namespace: str, dspa_name: str) -> str:
    explicit = os.getenv("KFP_HOST", "").strip()
    if explicit:
        return explicit.rstrip("/")
    return DEFAULT_KFP_HOST_TEMPLATE.format(dspa=dspa_name, namespace=namespace)


def _find_by_display_name(items: list[Any] | None, expected: str) -> Any | None:
    for item in items or []:
        if getattr(item, "display_name", None) == expected:
            return item
    return None


def _package_digest(package_path: str) -> str:
    return hashlib.sha256(Path(package_path).read_bytes()).hexdigest()[:12]


def _pipeline_version_name(package_path: str, pipeline_name: str, *, use_existing_version: bool) -> str:
    explicit = os.getenv("PIPELINE_VERSION_NAME", "").strip()
    if explicit:
        return explicit
    if use_existing_version:
        return pipeline_name
    return f"{pipeline_name}-{_package_digest(package_path)}"


def _stale_run_seconds() -> int:
    raw_value = os.getenv("PIPELINE_STALE_RUN_SECONDS", str(DEFAULT_STALE_RUN_SECONDS)).strip()
    if not raw_value:
        return DEFAULT_STALE_RUN_SECONDS
    try:
        return max(int(raw_value), 0)
    except ValueError:
        return DEFAULT_STALE_RUN_SECONDS


def _timestamp_value(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc).timestamp()
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _run_timestamp(run: Any) -> float | None:
    for attr in ("created_at", "scheduled_at", "finished_at"):
        timestamp = _timestamp_value(getattr(run, attr, None))
        if timestamp is not None:
            return timestamp
    return None


def _run_state(run: Any) -> str:
    return str(getattr(run, "state", "") or "").strip().upper()


def _run_display_name(run: Any) -> str:
    return str(getattr(run, "display_name", "") or "").strip()


def _related_runs(runs: list[Any] | None, base_name: str) -> list[Any]:
    related = []
    retry_prefix = f"{base_name}-retry-"
    for run in runs or []:
        display_name = _run_display_name(run)
        if display_name == base_name or display_name.startswith(retry_prefix):
            related.append(run)
    return sorted(related, key=lambda run: _run_timestamp(run) or 0.0, reverse=True)


def _is_active_run(run: Any) -> bool:
    return _run_state(run) in {"PENDING", "RUNNING"}


def _is_stale_run(run: Any, stale_run_seconds: int) -> bool:
    if not _is_active_run(run):
        return False
    if stale_run_seconds <= 0:
        return False
    started_at = _run_timestamp(run)
    if started_at is None:
        return False
    return (time.time() - started_at) >= stale_run_seconds


def resolve_run_submission_name(
    runs: list[Any] | None,
    requested_run_name: str,
    explicit_run_name: str,
) -> tuple[str | None, str]:
    if not explicit_run_name:
        existing = _find_by_display_name(runs, requested_run_name)
        if existing is not None and _run_state(existing) in {"RUNNING", "SUCCEEDED"}:
            return None, "existing_active_or_succeeded"
        return requested_run_name, "requested"

    related_runs = _related_runs(runs, explicit_run_name)
    if any(_run_state(run) == "SUCCEEDED" for run in related_runs):
        return None, "already_succeeded"

    active_runs = [run for run in related_runs if _is_active_run(run)]
    if any(not _is_stale_run(run, _stale_run_seconds()) for run in active_runs):
        return None, "active"

    if related_runs:
        return f"{explicit_run_name}-retry-{time.strftime('%Y%m%d-%H%M%S')}", "retry"
    return requested_run_name, "requested"


def wait_for_client(host: str, namespace: str, timeout_seconds: int = 600) -> Client:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    ssl_ca_cert = os.getenv("KFP_SSL_CA_CERT", DEFAULT_SERVICE_CA_CERT)
    if ssl_ca_cert and not Path(ssl_ca_cert).exists():
        ssl_ca_cert = None
    token_path = Path(os.getenv("KFP_TOKEN_PATH", DEFAULT_SA_TOKEN_PATH))
    existing_token = token_path.read_text().strip() if token_path.exists() else None
    while time.time() < deadline:
        try:
            client = Client(
                host=host,
                ssl_ca_cert=ssl_ca_cert,
                verify_ssl=bool(ssl_ca_cert),
                existing_token=existing_token,
            )
            client.list_pipelines(page_size=1, namespace=namespace)
            return client
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(10)
    raise RuntimeError(f"KFP API at {host} did not become ready in time") from last_error


def ensure_pipeline(client: Client, package_path: str, pipeline_name: str, namespace: str) -> None:
    existing = _find_by_display_name(
        getattr(client.list_pipelines(page_size=100, namespace=namespace), "pipelines", None),
        pipeline_name,
    )
    if existing is None:
        client.upload_pipeline(
            pipeline_package_path=package_path,
            pipeline_name=pipeline_name,
            namespace=namespace,
        )
        return

    pipeline_id = getattr(existing, "pipeline_id", None)
    if not pipeline_id:
        return

    version_name = _pipeline_version_name(package_path, pipeline_name, use_existing_version=False)
    existing_versions = getattr(client.list_pipeline_versions(pipeline_id=pipeline_id, page_size=100), "pipeline_versions", None)
    if _find_by_display_name(existing_versions, version_name) is not None:
        return
    client.upload_pipeline_version(
        pipeline_package_path=package_path,
        pipeline_version_name=version_name,
        pipeline_id=pipeline_id,
    )


def resolve_pipeline_version(client: Client, package_path: str, pipeline_name: str, namespace: str) -> tuple[str, str]:
    pipeline = _find_by_display_name(
        getattr(client.list_pipelines(page_size=100, namespace=namespace), "pipelines", None),
        pipeline_name,
    )
    if pipeline is None:
        raise RuntimeError(f"Pipeline {pipeline_name!r} is not registered in namespace {namespace!r}")

    pipeline_id = getattr(pipeline, "pipeline_id", None)
    if not pipeline_id:
        raise RuntimeError(f"Pipeline {pipeline_name!r} is missing a pipeline_id")

    version_name = _pipeline_version_name(package_path, pipeline_name, use_existing_version=True)
    versions = getattr(client.list_pipeline_versions(pipeline_id=pipeline_id, page_size=100), "pipeline_versions", None)
    version = _find_by_display_name(versions, version_name)
    if version is None:
        raise RuntimeError(f"Pipeline version {version_name!r} is not registered for pipeline {pipeline_name!r}")

    version_id = getattr(version, "pipeline_version_id", None)
    if not version_id:
        raise RuntimeError(f"Pipeline version {version_name!r} is missing a pipeline_version_id")

    return pipeline_id, version_id


def ensure_experiment(client: Client, experiment_name: str, namespace: str) -> Any:
    existing = _find_by_display_name(
        getattr(client.list_experiments(page_size=100, namespace=namespace), "experiments", None),
        experiment_name,
    )
    if existing is not None:
        return existing
    return client.create_experiment(name=experiment_name, namespace=namespace)


def ensure_demo_run(
    client: Client,
    package_path: str,
    pipeline_name: str,
    experiment: Any,
    experiment_name: str,
    namespace: str,
    requested_run_name: str,
    explicit_run_name: str,
    parameters: dict[str, Any],
    service_account: str | None,
) -> dict[str, str]:
    runs = getattr(
        client.list_runs(page_size=100, experiment_id=experiment.experiment_id, namespace=namespace),
        "runs",
        None,
    )
    submission_run_name, run_action = resolve_run_submission_name(runs, requested_run_name, explicit_run_name)
    if not submission_run_name:
        return {"action": f"skipped:{run_action}", "run_name": requested_run_name}

    if _env_flag("PIPELINE_USE_EXISTING_VERSION", False):
        pipeline_id, version_id = resolve_pipeline_version(
            client,
            package_path=package_path,
            pipeline_name=pipeline_name,
            namespace=namespace,
        )
        client.run_pipeline(
            experiment_id=experiment.experiment_id,
            job_name=submission_run_name,
            params=parameters,
            pipeline_id=pipeline_id,
            version_id=version_id,
            enable_caching=False,
            service_account=service_account,
        )
        return {"action": run_action, "run_name": submission_run_name}

    client.create_run_from_pipeline_package(
        pipeline_file=package_path,
        arguments=parameters,
        run_name=submission_run_name,
        experiment_name=experiment_name,
        namespace=namespace,
        service_account=service_account,
        enable_caching=False,
    )
    return {"action": run_action, "run_name": submission_run_name}


def main() -> None:
    namespace = _namespace()
    dspa_name = os.getenv("DSPA_NAME", DEFAULT_DSPA_NAME)
    package_path = _env("PIPELINE_PACKAGE_PATH", DEFAULT_PACKAGE_PATH)
    pipeline_name = os.getenv("PIPELINE_NAME", DEFAULT_PIPELINE_NAME)
    experiment_name = os.getenv("EXPERIMENT_NAME", DEFAULT_EXPERIMENT_NAME)
    explicit_run_name = _explicit_run_name()
    requested_run_name = _run_name(explicit_run_name)
    service_account = os.getenv("PIPELINE_SERVICE_ACCOUNT", "").strip() or None
    parameters = _load_pipeline_parameters()
    use_existing_version = _env_flag("PIPELINE_USE_EXISTING_VERSION", False)

    host = discover_kfp_host(namespace, dspa_name)
    client = wait_for_client(host=host, namespace=namespace)
    if not use_existing_version:
        ensure_pipeline(client, package_path=package_path, pipeline_name=pipeline_name, namespace=namespace)
    experiment = ensure_experiment(client, experiment_name=experiment_name, namespace=namespace)

    run_result = {"action": "skipped:disabled", "run_name": requested_run_name}
    if not _env_flag("PIPELINE_SKIP_DEMO_RUN", False):
        run_result = ensure_demo_run(
            client,
            package_path=package_path,
            pipeline_name=pipeline_name,
            experiment=experiment,
            experiment_name=experiment_name,
            namespace=namespace,
            requested_run_name=requested_run_name,
            explicit_run_name=explicit_run_name,
            parameters=parameters,
            service_account=service_account,
        )

    print(
        json.dumps(
            {
                "host": host,
                "experiment": experiment_name,
                "requested_run_name": requested_run_name,
                "submitted_run_name": run_result["run_name"],
                "run_action": run_result["action"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
