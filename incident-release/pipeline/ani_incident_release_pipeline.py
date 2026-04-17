"""Kubeflow pipeline source for the incident release workflow."""

from kfp import dsl


PIPELINE_IMAGE = "quay.io/autonomousnetworkintelligence/ani-incident-release:latest"
WORKSPACE_ROOT = "/tmp/ani-incident-release"
CONTROL_PLANE_APPROVAL_LIMIT = "1000"
CONTROL_PLANE_AUDIT_LIMIT = "1000"


@dsl.container_component
def snapshot_sources(
    release_version: str,
    source_dataset_version: str,
    project: str,
    output_manifest: dsl.OutputPath(str),
    workspace_root: str = WORKSPACE_ROOT,
    source_snapshot_id: str = "",
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "incident-release/python/release_cli.py"],
        args=[
            "--step",
            "snapshot-sources",
            "--release-version",
            release_version,
            "--source-dataset-version",
            source_dataset_version,
            "--project",
            project,
            "--workspace-root",
            workspace_root,
            "--source-snapshot-id",
            source_snapshot_id,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def normalize_release(
    release_version: str,
    snapshot_manifest: str,
    public_record_target: int,
    output_manifest: dsl.OutputPath(str),
    workspace_root: str = WORKSPACE_ROOT,
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "incident-release/python/release_cli.py"],
        args=[
            "--step",
            "normalize-release",
            "--release-version",
            release_version,
            "--snapshot-manifest",
            snapshot_manifest,
            "--public-record-target",
            public_record_target,
            "--workspace-root",
            workspace_root,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def validate_release(
    release_version: str,
    normalized_manifest: str,
    output_manifest: dsl.OutputPath(str),
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "incident-release/python/release_cli.py"],
        args=[
            "--step",
            "validate-release",
            "--release-version",
            release_version,
            "--normalized-manifest",
            normalized_manifest,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def publish_release(
    release_version: str,
    validation_manifest: str,
    release_mode: str,
    previous_release_version: str,
    output_manifest: dsl.OutputPath(str),
    workspace_root: str = WORKSPACE_ROOT,
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "incident-release/python/release_cli.py"],
        args=[
            "--step",
            "publish-release",
            "--release-version",
            release_version,
            "--validation-manifest",
            validation_manifest,
            "--release-mode",
            release_mode,
            "--previous-release-version",
            previous_release_version,
            "--workspace-root",
            workspace_root,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def run_release(
    release_version: str,
    source_dataset_version: str,
    project: str,
    public_record_target: int,
    release_mode: str,
    previous_release_version: str,
    output_manifest: dsl.OutputPath(str),
    workspace_root: str = WORKSPACE_ROOT,
    source_snapshot_id: str = "",
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "incident-release/python/release_cli.py"],
        args=[
            "--step",
            "run-release",
            "--release-version",
            release_version,
            "--source-dataset-version",
            source_dataset_version,
            "--project",
            project,
            "--public-record-target",
            public_record_target,
            "--release-mode",
            release_mode,
            "--previous-release-version",
            previous_release_version,
            "--workspace-root",
            workspace_root,
            "--source-snapshot-id",
            source_snapshot_id,
            "--approval-limit",
            CONTROL_PLANE_APPROVAL_LIMIT,
            "--audit-limit",
            CONTROL_PLANE_AUDIT_LIMIT,
            "--output",
            output_manifest,
        ],
    )


@dsl.pipeline(name="ani-incident-release")
def ani_incident_release_pipeline(
    release_version: str = "live-sipp-v1-draft",
    source_dataset_version: str = "live-sipp-v1",
    project: str = "ani-demo",
    public_record_target: int = 10000,
    release_mode: str = "draft-replacement",
    previous_release_version: str = "",
    source_snapshot_id: str = "",
):
    run_task = run_release(
        release_version=release_version,
        source_dataset_version=source_dataset_version,
        project=project,
        public_record_target=public_record_target,
        release_mode=release_mode,
        previous_release_version=previous_release_version,
        source_snapshot_id=source_snapshot_id,
    )
    run_task.set_env_variable("CONTROL_PLANE_URL", "http://control-plane.ani-runtime.svc.cluster.local:8080")
    run_task.set_env_variable("CONTROL_PLANE_API_KEY", "demo-token")
    run_task.set_env_variable("DATASET_STORE_PREFIX", "pipelines/ani-datascience/datasets")
    run_task.set_env_variable("KAFKA_ENABLED", "true")
    run_task.set_env_variable(
        "KAFKA_BOOTSTRAP_SERVERS",
        "ani-release-kafka-kafka-bootstrap.ani-data.svc.cluster.local:9092",
    )
