from __future__ import annotations

import os


DEFAULT_IMS_NAMESPACE = "ani-demo-lab"
DEFAULT_IMS_PROJECT = "ani-demo"
DEFAULT_MODEL_REGISTRY_NAMESPACE = "rhoai-model-registries"
DEFAULT_MODEL_REGISTRY_SERVICE = "ani-demo-modelregistry"
DEFAULT_DATASET_STORE_BUCKET = "ani-models"
DEFAULT_DATASET_STORE_PORT = 9000
DEFAULT_SERVICE_PORT = 8080
DEFAULT_IMS_PCSCF_PORT = 5060


def _clean(value: str | None) -> str:
    return str(value or "").strip()


def ims_namespace() -> str:
    return _clean(os.getenv("IMS_NAMESPACE")) or _clean(os.getenv("POD_NAMESPACE")) or DEFAULT_IMS_NAMESPACE


def ims_project() -> str:
    return (
        _clean(os.getenv("IMS_PROJECT"))
        or _clean(os.getenv("CONTROL_PLANE_PROJECT"))
        or _clean(os.getenv("DEMO_PROJECT"))
        or DEFAULT_IMS_PROJECT
    )


def console_cluster_name() -> str:
    return _clean(os.getenv("CONSOLE_CLUSTER_NAME")) or ims_namespace()


def service_host(service_name: str, *, namespace: str | None = None) -> str:
    target_namespace = _clean(namespace) or ims_namespace()
    return f"{service_name}.{target_namespace}.svc.cluster.local"


def service_url(
    service_name: str,
    *,
    namespace: str | None = None,
    port: int = DEFAULT_SERVICE_PORT,
    scheme: str = "http",
) -> str:
    return f"{scheme}://{service_host(service_name, namespace=namespace)}:{port}"


def control_plane_url() -> str:
    return _clean(os.getenv("CONTROL_PLANE_URL")) or service_url("control-plane")


def feature_gateway_url() -> str:
    return _clean(os.getenv("FEATURE_GATEWAY_URL")) or service_url("feature-gateway")


def anomaly_service_url() -> str:
    return _clean(os.getenv("ANOMALY_SERVICE_URL")) or service_url("anomaly-service")


def rca_service_url() -> str:
    return _clean(os.getenv("RCA_SERVICE_URL")) or service_url("rca-service")


def predictive_service_url() -> str:
    return (
        _clean(os.getenv("PREDICTIVE_SERVICE_URL"))
        or _clean(os.getenv("PREDICTIVE_ENDPOINT"))
        or service_url("ani-predictive-fs-predictor")
    )


def milvus_uri() -> str:
    return _clean(os.getenv("MILVUS_URI")) or service_url("milvus", port=19530)


def ims_pcscf_host() -> str:
    return _clean(os.getenv("IMS_PCSCF_HOST")) or service_host("ims-pcscf")


def ims_pcscf_port() -> int:
    return int(_clean(os.getenv("IMS_PCSCF_PORT")) or str(DEFAULT_IMS_PCSCF_PORT))


def dataset_store_endpoint() -> str:
    return (
        _clean(os.getenv("DATASET_STORE_ENDPOINT"))
        or _clean(os.getenv("MINIO_ENDPOINT"))
        or service_url("model-storage-minio", port=DEFAULT_DATASET_STORE_PORT)
    )


def dataset_store_bucket() -> str:
    return _clean(os.getenv("DATASET_STORE_BUCKET")) or _clean(os.getenv("MINIO_BUCKET")) or DEFAULT_DATASET_STORE_BUCKET


def dataset_store_prefix() -> str:
    return (_clean(os.getenv("DATASET_STORE_PREFIX")) or f"pipelines/{ims_namespace()}/datasets").strip("/")


def model_registry_endpoint() -> str:
    return (
        _clean(os.getenv("MODEL_REGISTRY_ENDPOINT"))
        or _clean(os.getenv("RHOAI_MODEL_REGISTRY_ENDPOINT"))
        or service_url(
            _clean(os.getenv("MODEL_REGISTRY_SERVICE")) or DEFAULT_MODEL_REGISTRY_SERVICE,
            namespace=_clean(os.getenv("MODEL_REGISTRY_NAMESPACE")) or DEFAULT_MODEL_REGISTRY_NAMESPACE,
        )
    )
