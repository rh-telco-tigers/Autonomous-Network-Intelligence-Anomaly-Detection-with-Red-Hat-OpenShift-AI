"""Compile-time independent KFP pipeline uploader for the feature-store workflow."""

import hashlib
import json
import os
import ssl
import time
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


def _run_name() -> str:
    explicit = os.getenv("RUN_NAME", "").strip()
    if explicit:
        return explicit
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

    version_name = os.getenv("PIPELINE_VERSION_NAME", f"{pipeline_name}-{_package_digest(package_path)}")
    existing_versions = getattr(client.list_pipeline_versions(pipeline_id=pipeline_id, page_size=100), "pipeline_versions", None)
    if _find_by_display_name(existing_versions, version_name) is not None:
        return
    client.upload_pipeline_version(
        pipeline_package_path=package_path,
        pipeline_version_name=version_name,
        pipeline_id=pipeline_id,
    )


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
    experiment: Any,
    experiment_name: str,
    namespace: str,
    run_name: str,
    parameters: dict[str, Any],
    service_account: str | None,
) -> None:
    runs = getattr(
        client.list_runs(page_size=100, experiment_id=experiment.experiment_id, namespace=namespace),
        "runs",
        None,
    )
    existing = _find_by_display_name(runs, run_name)
    if existing is not None and getattr(existing, "state", "") in {"RUNNING", "SUCCEEDED"}:
        return

    client.create_run_from_pipeline_package(
        pipeline_file=package_path,
        arguments=parameters,
        run_name=run_name,
        experiment_name=experiment_name,
        namespace=namespace,
        service_account=service_account,
        enable_caching=False,
    )


def main() -> None:
    namespace = _namespace()
    dspa_name = os.getenv("DSPA_NAME", DEFAULT_DSPA_NAME)
    package_path = _env("PIPELINE_PACKAGE_PATH", DEFAULT_PACKAGE_PATH)
    pipeline_name = os.getenv("PIPELINE_NAME", DEFAULT_PIPELINE_NAME)
    experiment_name = os.getenv("EXPERIMENT_NAME", DEFAULT_EXPERIMENT_NAME)
    run_name = _run_name()
    service_account = os.getenv("PIPELINE_SERVICE_ACCOUNT", "").strip() or None
    parameters = _load_pipeline_parameters()

    host = discover_kfp_host(namespace, dspa_name)
    client = wait_for_client(host=host, namespace=namespace)
    ensure_pipeline(client, package_path=package_path, pipeline_name=pipeline_name, namespace=namespace)
    experiment = ensure_experiment(client, experiment_name=experiment_name, namespace=namespace)
    if not _env_flag("PIPELINE_SKIP_DEMO_RUN", False):
        ensure_demo_run(
            client,
            package_path=package_path,
            experiment=experiment,
            experiment_name=experiment_name,
            namespace=namespace,
            run_name=run_name,
            parameters=parameters,
            service_account=service_account,
        )
    print(json.dumps({"host": host, "experiment": experiment_name, "run_name": run_name}, indent=2))


if __name__ == "__main__":
    main()
