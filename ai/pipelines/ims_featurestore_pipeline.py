"""Kubeflow pipeline source for the feature-store-backed IMS anomaly workflow."""

from kfp import dsl


PIPELINE_IMAGE = "image-registry.openshift-image-registry.svc:5000/ims-demo-lab/ims-ai-featurestore-trainer:latest"
WORKSPACE_ROOT = "/tmp/ims-featurestore"
ARTIFACT_DIR = "/tmp/ims-featurestore/models/artifacts"
FEATURE_REPO_PATH = "/workspace/ai/featurestore/feature_repo"
CONTROL_PLANE_URL = "http://control-plane.ims-demo-lab.svc.cluster.local:8080"
CONTROL_PLANE_API_KEY = "demo-token"
DATASET_STORE_MODE = "s3"
DATASET_STORE_ENDPOINT = "http://model-storage-minio.ims-demo-lab.svc.cluster.local:9000"
DATASET_STORE_BUCKET = "ims-models"
DATASET_STORE_PREFIX = "pipelines/ims-demo-lab/datasets"
DATASET_STORE_ACCESS_KEY = "minioadmin"
DATASET_STORE_SECRET_KEY = "minioadmin"
MODEL_REGISTRY_ENDPOINT = "http://ims-demo-modelregistry.rhoai-model-registries.svc.cluster.local:8080"
FEATURESTORE_MODE = "remote"
FEATURESTORE_PROJECT = "ims_anomaly_featurestore"
FEATURESTORE_REGISTRY_PATH = "feast-ims-featurestore-registry.ims-demo-lab.svc.cluster.local:443"
FEATURESTORE_ONLINE_STORE_PATH = "https://feast-ims-featurestore-online.ims-demo-lab.svc.cluster.local:443"
FEATURESTORE_CA_CERT_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt"
FEATURESTORE_AUTH_TYPE = "no_auth"
FEATURESTORE_S3_ENDPOINT_URL = "http://model-storage-minio.ims-demo-lab.svc.cluster.local:9000"
FEATURESTORE_AWS_REGION = "us-east-1"
FEATURESTORE_AWS_ACCESS_KEY_ID = "minioadmin"
FEATURESTORE_AWS_SECRET_ACCESS_KEY = "minioadmin"


@dsl.container_component
def resolve_bundle(
    bundle_version: str,
    output_manifest: dsl.OutputPath(str),
    workspace_root: str = WORKSPACE_ROOT,
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/featurestore_train.py"],
        args=[
            "--step",
            "resolve-bundle",
            "--bundle-version",
            bundle_version,
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


@dsl.container_component
def sync_feature_store_definitions(
    bundle_manifest_path: str,
    output_manifest: dsl.OutputPath(str),
    feature_repo_path: str = FEATURE_REPO_PATH,
    workspace_root: str = WORKSPACE_ROOT,
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/featurestore_train.py"],
        args=[
            "--step",
            "sync-feature-store-definitions",
            "--bundle-manifest",
            bundle_manifest_path,
            "--workspace-root",
            workspace_root,
            "--feature-repo-path",
            feature_repo_path,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def retrieve_training_dataset(
    bundle_manifest_path: str,
    feature_service_name: str,
    output_manifest: dsl.OutputPath(str),
    feature_repo_path: str = FEATURE_REPO_PATH,
    workspace_root: str = WORKSPACE_ROOT,
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/featurestore_train.py"],
        args=[
            "--step",
            "retrieve-training-dataset",
            "--bundle-manifest",
            bundle_manifest_path,
            "--feature-service-name",
            feature_service_name,
            "--feature-repo-path",
            feature_repo_path,
            "--workspace-root",
            workspace_root,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def train_baseline(
    training_manifest: str,
    baseline_version: str,
    output_manifest: dsl.OutputPath(str),
    artifact_dir: str = ARTIFACT_DIR,
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/featurestore_train.py"],
        args=[
            "--step",
            "train-baseline",
            "--training-manifest",
            training_manifest,
            "--baseline-version",
            baseline_version,
            "--artifact-dir",
            artifact_dir,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def train_automl(
    training_manifest: str,
    candidate_version: str,
    output_manifest: dsl.OutputPath(str),
    workspace_root: str = WORKSPACE_ROOT,
    artifact_dir: str = ARTIFACT_DIR,
    automl_engine: str = "autogluon",
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/featurestore_train.py"],
        args=[
            "--step",
            "train-automl",
            "--training-manifest",
            training_manifest,
            "--candidate-version",
            candidate_version,
            "--workspace-root",
            workspace_root,
            "--artifact-dir",
            artifact_dir,
            "--automl-engine",
            automl_engine,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def evaluate_models(
    training_manifest: str,
    baseline_manifest: str,
    candidate_manifest: str,
    output_manifest: dsl.OutputPath(str),
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/featurestore_train.py"],
        args=[
            "--step",
            "evaluate",
            "--training-manifest",
            training_manifest,
            "--baseline-manifest",
            baseline_manifest,
            "--candidate-manifest",
            candidate_manifest,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def select_best(
    evaluation_manifest: str,
    output_manifest: dsl.OutputPath(str),
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/featurestore_train.py"],
        args=[
            "--step",
            "select-best",
            "--evaluation-manifest",
            evaluation_manifest,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def export_serving_artifact(
    training_manifest: str,
    selection_manifest: str,
    output_manifest: dsl.OutputPath(str),
    artifact_dir: str = ARTIFACT_DIR,
    serving_model_name: str = "ims-predictive-fs",
    serving_runtime_name: str = "nvidia-triton-runtime",
    serving_model_format_name: str = "triton",
    serving_model_format_version: str = "2",
    serving_protocol_version: str = "v2",
    serving_prefix: str = "predictive-featurestore",
    serving_alias: str = "current",
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/featurestore_train.py"],
        args=[
            "--step",
            "export-serving-artifact",
            "--training-manifest",
            training_manifest,
            "--selection-manifest",
            selection_manifest,
            "--artifact-dir",
            artifact_dir,
            "--serving-model-name",
            serving_model_name,
            "--serving-runtime-name",
            serving_runtime_name,
            "--serving-model-format-name",
            serving_model_format_name,
            "--serving-model-format-version",
            serving_model_format_version,
            "--serving-protocol-version",
            serving_protocol_version,
            "--serving-prefix",
            serving_prefix,
            "--serving-alias",
            serving_alias,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def register_model_version(
    export_manifest: str,
    feature_service_name: str,
    model_name: str,
    model_version_name: str,
    output_manifest: dsl.OutputPath(str),
    pipeline_name: str = "ims-featurestore-train-and-register",
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/featurestore_train.py"],
        args=[
            "--step",
            "register-model-version",
            "--export-manifest",
            export_manifest,
            "--feature-service-name",
            feature_service_name,
            "--model-name",
            model_name,
            "--model-version-name",
            model_version_name,
            "--pipeline-name",
            pipeline_name,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def publish_deployment_manifest(
    export_manifest: str,
    model_registry_manifest: str,
    output_manifest: dsl.OutputPath(str),
    service_account_name: str = "model-storage-sa",
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/featurestore_train.py"],
        args=[
            "--step",
            "publish-deployment-manifest",
            "--export-manifest",
            export_manifest,
            "--model-registry-manifest",
            model_registry_manifest,
            "--service-account-name",
            service_account_name,
            "--output",
            output_manifest,
        ],
    )


def _configure_bundle_task(task, *, control_plane_url: str, control_plane_api_key: str, storage_config: dict[str, str]) -> None:
    _configure_storage_task(task, **storage_config)
    task.set_env_variable("CONTROL_PLANE_URL", CONTROL_PLANE_URL)
    task.set_env_variable("CONTROL_PLANE_API_KEY", CONTROL_PLANE_API_KEY)
    task.set_env_variable("BUNDLE_REQUIRE_CONTROL_PLANE_HISTORY", "true")


def _configure_storage_task(
    task,
    *,
    dataset_store_mode: str,
    dataset_store_endpoint: str,
    dataset_store_bucket: str,
    dataset_store_prefix: str,
    dataset_store_access_key: str,
    dataset_store_secret_key: str,
    aws_region: str,
    aws_access_key_id: str,
    aws_secret_access_key: str,
) -> None:
    task.set_env_variable("HOME", "/tmp")
    task.set_env_variable("MPLCONFIGDIR", "/tmp/matplotlib")
    task.set_env_variable("DATASET_STORE_MODE", DATASET_STORE_MODE)
    task.set_env_variable("DATASET_STORE_ENDPOINT", DATASET_STORE_ENDPOINT)
    task.set_env_variable("DATASET_STORE_BUCKET", DATASET_STORE_BUCKET)
    task.set_env_variable("DATASET_STORE_PREFIX", DATASET_STORE_PREFIX)
    task.set_env_variable("DATASET_STORE_ACCESS_KEY", DATASET_STORE_ACCESS_KEY)
    task.set_env_variable("DATASET_STORE_SECRET_KEY", DATASET_STORE_SECRET_KEY)
    task.set_env_variable("MINIO_ENDPOINT", DATASET_STORE_ENDPOINT)
    task.set_env_variable("MINIO_BUCKET", DATASET_STORE_BUCKET)
    task.set_env_variable("MINIO_ACCESS_KEY", DATASET_STORE_ACCESS_KEY)
    task.set_env_variable("MINIO_SECRET_KEY", DATASET_STORE_SECRET_KEY)
    task.set_env_variable("AWS_DEFAULT_REGION", FEATURESTORE_AWS_REGION)
    task.set_env_variable("AWS_REGION", FEATURESTORE_AWS_REGION)
    task.set_env_variable("AWS_ACCESS_KEY_ID", FEATURESTORE_AWS_ACCESS_KEY_ID)
    task.set_env_variable("AWS_SECRET_ACCESS_KEY", FEATURESTORE_AWS_SECRET_ACCESS_KEY)


def _configure_featurestore_task(
    task,
    *,
    storage_config: dict[str, str],
    featurestore_mode: str,
    featurestore_project: str,
    featurestore_registry_path: str,
    featurestore_online_store_path: str,
    featurestore_ca_cert_path: str,
    featurestore_auth_type: str,
    featurestore_s3_endpoint_url: str,
    featurestore_aws_region: str,
    featurestore_aws_access_key_id: str,
    featurestore_aws_secret_access_key: str,
) -> None:
    _configure_storage_task(task, **storage_config)
    task.set_env_variable("IMS_FEATURESTORE_MODE", FEATURESTORE_MODE)
    task.set_env_variable("IMS_FEATURESTORE_PROJECT", FEATURESTORE_PROJECT)
    task.set_env_variable("IMS_FEATURESTORE_REGISTRY_PATH", FEATURESTORE_REGISTRY_PATH)
    task.set_env_variable("IMS_FEATURESTORE_ONLINE_STORE_PATH", FEATURESTORE_ONLINE_STORE_PATH)
    task.set_env_variable("IMS_FEATURESTORE_CA_CERT_PATH", FEATURESTORE_CA_CERT_PATH)
    task.set_env_variable("IMS_FEATURESTORE_AUTH_TYPE", FEATURESTORE_AUTH_TYPE)
    task.set_env_variable("FEAST_S3_ENDPOINT_URL", FEATURESTORE_S3_ENDPOINT_URL)
    task.set_env_variable("AWS_S3_ENDPOINT", FEATURESTORE_S3_ENDPOINT_URL)
    task.set_env_variable("AWS_DEFAULT_REGION", FEATURESTORE_AWS_REGION)
    task.set_env_variable("AWS_REGION", FEATURESTORE_AWS_REGION)
    task.set_env_variable("AWS_ACCESS_KEY_ID", FEATURESTORE_AWS_ACCESS_KEY_ID)
    task.set_env_variable("AWS_SECRET_ACCESS_KEY", FEATURESTORE_AWS_SECRET_ACCESS_KEY)


@dsl.pipeline(name="ims-featurestore-train-and-register")
def ims_featurestore_pipeline(
    bundle_version: str = "ims-feature-bundle-v1",
    feature_service_name: str = "ims_anomaly_scoring_v1",
    baseline_version: str = "baseline-fs-v1",
    candidate_version: str = "candidate-fs-v1",
    automl_engine: str = "autogluon",
    model_name: str = "ims-anomaly-featurestore",
    model_version_name: str = "ims-anomaly-featurestore-v1",
    serving_model_name: str = "ims-predictive-fs",
    serving_runtime_name: str = "nvidia-triton-runtime",
    serving_model_format_name: str = "triton",
    serving_model_format_version: str = "2",
    serving_protocol_version: str = "v2",
    serving_prefix: str = "predictive-featurestore",
    serving_alias: str = "current",
    mlserver_serving_model_name: str = "ims-predictive-fs-mlserver",
    mlserver_serving_runtime_name: str = "mlserver-sklearn-runtime",
    mlserver_serving_model_format_name: str = "sklearn",
    mlserver_serving_model_format_version: str = "1",
    mlserver_serving_protocol_version: str = "v2",
    mlserver_serving_prefix: str = "predictive-featurestore-mlserver",
    mlserver_serving_alias: str = "current",
    control_plane_url: str = CONTROL_PLANE_URL,
    control_plane_api_key: str = CONTROL_PLANE_API_KEY,
    dataset_store_mode: str = DATASET_STORE_MODE,
    dataset_store_endpoint: str = DATASET_STORE_ENDPOINT,
    dataset_store_bucket: str = DATASET_STORE_BUCKET,
    dataset_store_prefix: str = DATASET_STORE_PREFIX,
    dataset_store_access_key: str = DATASET_STORE_ACCESS_KEY,
    dataset_store_secret_key: str = DATASET_STORE_SECRET_KEY,
    featurestore_mode: str = FEATURESTORE_MODE,
    featurestore_project: str = FEATURESTORE_PROJECT,
    featurestore_registry_path: str = FEATURESTORE_REGISTRY_PATH,
    featurestore_online_store_path: str = FEATURESTORE_ONLINE_STORE_PATH,
    featurestore_ca_cert_path: str = FEATURESTORE_CA_CERT_PATH,
    featurestore_auth_type: str = FEATURESTORE_AUTH_TYPE,
    featurestore_s3_endpoint_url: str = FEATURESTORE_S3_ENDPOINT_URL,
    featurestore_aws_region: str = FEATURESTORE_AWS_REGION,
    featurestore_aws_access_key_id: str = FEATURESTORE_AWS_ACCESS_KEY_ID,
    featurestore_aws_secret_access_key: str = FEATURESTORE_AWS_SECRET_ACCESS_KEY,
    model_registry_endpoint: str = MODEL_REGISTRY_ENDPOINT,
):
    storage_config = {
        "dataset_store_mode": dataset_store_mode,
        "dataset_store_endpoint": dataset_store_endpoint,
        "dataset_store_bucket": dataset_store_bucket,
        "dataset_store_prefix": dataset_store_prefix,
        "dataset_store_access_key": dataset_store_access_key,
        "dataset_store_secret_key": dataset_store_secret_key,
        "aws_region": featurestore_aws_region,
        "aws_access_key_id": featurestore_aws_access_key_id,
        "aws_secret_access_key": featurestore_aws_secret_access_key,
    }
    featurestore_config = {
        "storage_config": storage_config,
        "featurestore_mode": featurestore_mode,
        "featurestore_project": featurestore_project,
        "featurestore_registry_path": featurestore_registry_path,
        "featurestore_online_store_path": featurestore_online_store_path,
        "featurestore_ca_cert_path": featurestore_ca_cert_path,
        "featurestore_auth_type": featurestore_auth_type,
        "featurestore_s3_endpoint_url": featurestore_s3_endpoint_url,
        "featurestore_aws_region": featurestore_aws_region,
        "featurestore_aws_access_key_id": featurestore_aws_access_key_id,
        "featurestore_aws_secret_access_key": featurestore_aws_secret_access_key,
    }
    resolved = resolve_bundle(bundle_version=bundle_version)
    _configure_featurestore_task(resolved, **featurestore_config)

    validated = validate_bundle(bundle_manifest_path=resolved.outputs["output_manifest"])
    synced = sync_feature_store_definitions(bundle_manifest_path=resolved.outputs["output_manifest"])
    training_data = retrieve_training_dataset(
        bundle_manifest_path=resolved.outputs["output_manifest"],
        feature_service_name=feature_service_name,
    )
    for task in (validated, synced, training_data):
        _configure_featurestore_task(task, **featurestore_config)
    training_data.after(validated, synced)

    baseline = train_baseline(
        training_manifest=training_data.outputs["output_manifest"],
        baseline_version=baseline_version,
    )
    automl = train_automl(
        training_manifest=training_data.outputs["output_manifest"],
        candidate_version=candidate_version,
        automl_engine=automl_engine,
    )
    evaluated = evaluate_models(
        training_manifest=training_data.outputs["output_manifest"],
        baseline_manifest=baseline.outputs["output_manifest"],
        candidate_manifest=automl.outputs["output_manifest"],
    )
    selected = select_best(
        evaluation_manifest=evaluated.outputs["output_manifest"],
    )
    exported = export_serving_artifact(
        training_manifest=training_data.outputs["output_manifest"],
        selection_manifest=selected.outputs["output_manifest"],
        serving_model_name=serving_model_name,
        serving_runtime_name=serving_runtime_name,
        serving_model_format_name=serving_model_format_name,
        serving_model_format_version=serving_model_format_version,
        serving_protocol_version=serving_protocol_version,
        serving_prefix=serving_prefix,
        serving_alias=serving_alias,
    )
    mlserver_exported = export_serving_artifact(
        training_manifest=training_data.outputs["output_manifest"],
        selection_manifest=selected.outputs["output_manifest"],
        serving_model_name=mlserver_serving_model_name,
        serving_runtime_name=mlserver_serving_runtime_name,
        serving_model_format_name=mlserver_serving_model_format_name,
        serving_model_format_version=mlserver_serving_model_format_version,
        serving_protocol_version=mlserver_serving_protocol_version,
        serving_prefix=mlserver_serving_prefix,
        serving_alias=mlserver_serving_alias,
    )
    registered = register_model_version(
        export_manifest=exported.outputs["output_manifest"],
        feature_service_name=feature_service_name,
        model_name=model_name,
        model_version_name=model_version_name,
    )
    published = publish_deployment_manifest(
        export_manifest=exported.outputs["output_manifest"],
        model_registry_manifest=registered.outputs["output_manifest"],
    )
    published_mlserver = publish_deployment_manifest(
        export_manifest=mlserver_exported.outputs["output_manifest"],
        model_registry_manifest=registered.outputs["output_manifest"],
    )

    for task in (baseline, automl, evaluated, selected, exported, mlserver_exported, registered, published, published_mlserver):
        _configure_featurestore_task(task, **featurestore_config)
    registered.set_env_variable("RHOAI_MODEL_REGISTRY_ENDPOINT", MODEL_REGISTRY_ENDPOINT)
    registered.set_env_variable("RHOAI_MODEL_REGISTRY_REQUIRED", "false")
