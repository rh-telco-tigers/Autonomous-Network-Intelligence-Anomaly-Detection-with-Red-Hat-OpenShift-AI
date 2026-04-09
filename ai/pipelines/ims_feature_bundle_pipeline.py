"""Kubeflow pipeline source for publishing feature-store bundle datasets."""

from kfp import dsl


PIPELINE_IMAGE = "image-registry.openshift-image-registry.svc:5000/ims-demo-lab/ims-ai-featurestore-trainer:latest"
WORKSPACE_ROOT = "/tmp/ims-featurestore"
CONTROL_PLANE_URL = "http://control-plane.ims-demo-lab.svc.cluster.local:8080"
CONTROL_PLANE_API_KEY = "demo-token"
DATASET_STORE_MODE = "s3"
DATASET_STORE_ENDPOINT = "http://model-storage-minio.ims-demo-lab.svc.cluster.local:9000"
DATASET_STORE_BUCKET = "ims-models"
DATASET_STORE_PREFIX = "pipelines/ims-demo-lab/datasets"
DATASET_STORE_ACCESS_KEY = "minioadmin"
DATASET_STORE_SECRET_KEY = "minioadmin"


@dsl.container_component
def build_bundle(
    bundle_version: str,
    source_dataset_versions_json: str,
    project: str,
    output_manifest: dsl.OutputPath(str),
    workspace_root: str = WORKSPACE_ROOT,
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/featurestore_train.py"],
        args=[
            "--step",
            "build-bundle",
            "--bundle-version",
            bundle_version,
            "--source-dataset-versions-json",
            source_dataset_versions_json,
            "--project",
            project,
            "--workspace-root",
            workspace_root,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def validate_bundle(
    bundle_manifest_path: str,
    output_manifest: dsl.OutputPath(str),
    workspace_root: str = WORKSPACE_ROOT,
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/featurestore_train.py"],
        args=[
            "--step",
            "validate-bundle",
            "--bundle-manifest",
            bundle_manifest_path,
            "--workspace-root",
            workspace_root,
            "--output",
            output_manifest,
        ],
    )


def _configure_bundle_task(
    task,
    *,
    control_plane_url: str,
    control_plane_api_key: str,
    dataset_store_mode: str,
    dataset_store_endpoint: str,
    dataset_store_bucket: str,
    dataset_store_prefix: str,
    dataset_store_access_key: str,
    dataset_store_secret_key: str,
) -> None:
    task.set_env_variable("HOME", "/tmp")
    task.set_env_variable("CONTROL_PLANE_URL", CONTROL_PLANE_URL)
    task.set_env_variable("CONTROL_PLANE_API_KEY", CONTROL_PLANE_API_KEY)
    task.set_env_variable("DATASET_STORE_MODE", DATASET_STORE_MODE)
    task.set_env_variable("DATASET_STORE_ENDPOINT", DATASET_STORE_ENDPOINT)
    task.set_env_variable("DATASET_STORE_BUCKET", DATASET_STORE_BUCKET)
    task.set_env_variable("DATASET_STORE_ACCESS_KEY", DATASET_STORE_ACCESS_KEY)
    task.set_env_variable("DATASET_STORE_SECRET_KEY", DATASET_STORE_SECRET_KEY)
    task.set_env_variable("DATASET_STORE_PREFIX", DATASET_STORE_PREFIX)
    task.set_env_variable("MINIO_ENDPOINT", DATASET_STORE_ENDPOINT)
    task.set_env_variable("MINIO_BUCKET", DATASET_STORE_BUCKET)
    task.set_env_variable("MINIO_ACCESS_KEY", DATASET_STORE_ACCESS_KEY)
    task.set_env_variable("MINIO_SECRET_KEY", DATASET_STORE_SECRET_KEY)
    task.set_env_variable("BUNDLE_REQUIRE_CONTROL_PLANE_HISTORY", "true")


@dsl.pipeline(name="ims-feature-bundle-publish")
def ims_feature_bundle_pipeline(
    bundle_version: str = "ims-feature-bundle-v1",
    source_dataset_versions_json: str = "[\"live-sipp-v1\"]",
    project: str = "ims-demo",
    control_plane_url: str = CONTROL_PLANE_URL,
    control_plane_api_key: str = CONTROL_PLANE_API_KEY,
    dataset_store_mode: str = DATASET_STORE_MODE,
    dataset_store_endpoint: str = DATASET_STORE_ENDPOINT,
    dataset_store_bucket: str = DATASET_STORE_BUCKET,
    dataset_store_prefix: str = DATASET_STORE_PREFIX,
    dataset_store_access_key: str = DATASET_STORE_ACCESS_KEY,
    dataset_store_secret_key: str = DATASET_STORE_SECRET_KEY,
):
    published = build_bundle(
        bundle_version=bundle_version,
        source_dataset_versions_json=source_dataset_versions_json,
        project=project,
    )
    validated = validate_bundle(bundle_manifest_path=published.outputs["output_manifest"])
    config = {
        "control_plane_url": control_plane_url,
        "control_plane_api_key": control_plane_api_key,
        "dataset_store_mode": dataset_store_mode,
        "dataset_store_endpoint": dataset_store_endpoint,
        "dataset_store_bucket": dataset_store_bucket,
        "dataset_store_prefix": dataset_store_prefix,
        "dataset_store_access_key": dataset_store_access_key,
        "dataset_store_secret_key": dataset_store_secret_key,
    }
    _configure_bundle_task(published, **config)
    _configure_bundle_task(validated, **config)
