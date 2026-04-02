"""Kubeflow pipeline source for publishing feature-store bundle datasets."""

from kfp import dsl


PIPELINE_IMAGE = "image-registry.openshift-image-registry.svc:5000/ims-demo-lab/ims-ai-featurestore-trainer:latest"
WORKSPACE_ROOT = "/tmp/ims-featurestore"
CONTROL_PLANE_URL = "http://control-plane.ims-demo-lab.svc.cluster.local:8080"
CONTROL_PLANE_API_KEY = "demo-token"
DATASET_STORE_PREFIX = "pipelines/ims-demo-lab/datasets"


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


def _configure_bundle_task(task) -> None:
    task.set_env_variable("CONTROL_PLANE_URL", CONTROL_PLANE_URL)
    task.set_env_variable("CONTROL_PLANE_API_KEY", CONTROL_PLANE_API_KEY)
    task.set_env_variable("DATASET_STORE_PREFIX", DATASET_STORE_PREFIX)
    task.set_env_variable("BUNDLE_REQUIRE_CONTROL_PLANE_HISTORY", "true")


@dsl.pipeline(name="ims-feature-bundle-publish")
def ims_feature_bundle_pipeline(
    bundle_version: str = "ims-feature-bundle-v1",
    source_dataset_versions_json: str = "[\"live-sipp-v1\"]",
    project: str = "ims-demo",
):
    published = build_bundle(
        bundle_version=bundle_version,
        source_dataset_versions_json=source_dataset_versions_json,
        project=project,
    )
    validated = validate_bundle(bundle_manifest_path=published.outputs["output_manifest"])
    _configure_bundle_task(published)
    _configure_bundle_task(validated)
