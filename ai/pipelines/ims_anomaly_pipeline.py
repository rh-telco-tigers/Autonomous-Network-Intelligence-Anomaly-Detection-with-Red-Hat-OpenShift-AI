"""Kubeflow pipeline source for the IMS anomaly demo workflow."""

from kfp import dsl


PIPELINE_IMAGE = "image-registry.openshift-image-registry.svc:5000/ims-demo-lab/ims-ai-trainer:latest"
WORKSPACE_ROOT = "/tmp/ims-pipeline"
ARTIFACT_DIR = "/tmp/ims-pipeline/models/artifacts"
REGISTRY_PATH = "/tmp/ims-pipeline/registry/model_registry.json"


@dsl.container_component
def ingest_data(
    dataset_version: str,
    output_manifest: dsl.OutputPath(str),
    workspace_root: str = WORKSPACE_ROOT,
    size_per_class: int = 120,
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/train_and_register.py"],
        args=[
            "--step",
            "ingest-data",
            "--dataset-version",
            dataset_version,
            "--workspace-root",
            workspace_root,
            "--size-per-class",
            size_per_class,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def feature_engineering(
    dataset_version: str,
    dataset_manifest: str,
    output_manifest: dsl.OutputPath(str),
    workspace_root: str = WORKSPACE_ROOT,
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/train_and_register.py"],
        args=[
            "--step",
            "feature-engineering",
            "--dataset-version",
            dataset_version,
            "--dataset-manifest",
            dataset_manifest,
            "--workspace-root",
            workspace_root,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def label_generation(
    dataset_version: str,
    feature_manifest: str,
    output_manifest: dsl.OutputPath(str),
    workspace_root: str = WORKSPACE_ROOT,
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/train_and_register.py"],
        args=[
            "--step",
            "label-generation",
            "--dataset-version",
            dataset_version,
            "--feature-manifest",
            feature_manifest,
            "--workspace-root",
            workspace_root,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def train_baseline(
    baseline_version: str,
    label_manifest: str,
    output_manifest: dsl.OutputPath(str),
    artifact_dir: str = ARTIFACT_DIR,
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/train_and_register.py"],
        args=[
            "--step",
            "train-baseline",
            "--baseline-version",
            baseline_version,
            "--label-manifest",
            label_manifest,
            "--artifact-dir",
            artifact_dir,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def train_autogluon(
    candidate_version: str,
    label_manifest: str,
    output_manifest: dsl.OutputPath(str),
    workspace_root: str = WORKSPACE_ROOT,
    artifact_dir: str = ARTIFACT_DIR,
    automl_engine: str = "autogluon",
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/train_and_register.py"],
        args=[
            "--step",
            "train-automl",
            "--candidate-version",
            candidate_version,
            "--label-manifest",
            label_manifest,
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
    dataset_version: str,
    label_manifest: str,
    baseline_manifest: str,
    candidate_manifest: str,
    output_manifest: dsl.OutputPath(str),
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/train_and_register.py"],
        args=[
            "--step",
            "evaluate",
            "--dataset-version",
            dataset_version,
            "--label-manifest",
            label_manifest,
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
    dataset_version: str,
    evaluation_manifest: str,
    output_manifest: dsl.OutputPath(str),
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/train_and_register.py"],
        args=[
            "--step",
            "select-best",
            "--dataset-version",
            dataset_version,
            "--evaluation-manifest",
            evaluation_manifest,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def register_model(
    label_manifest: str,
    selection_manifest: str,
    output_manifest: dsl.OutputPath(str),
    artifact_dir: str = ARTIFACT_DIR,
    registry_path: str = REGISTRY_PATH,
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/train_and_register.py"],
        args=[
            "--step",
            "register-model",
            "--label-manifest",
            label_manifest,
            "--selection-manifest",
            selection_manifest,
            "--artifact-dir",
            artifact_dir,
            "--registry-path",
            registry_path,
            "--output",
            output_manifest,
        ],
    )


@dsl.container_component
def deploy_model(
    registry_manifest: str,
    output_manifest: dsl.OutputPath(str),
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/train_and_register.py"],
        args=[
            "--step",
            "deploy-model",
            "--registry-path",
            registry_manifest,
            "--output",
            output_manifest,
        ],
    )


@dsl.pipeline(name="ims-anomaly-platform-train-and-register")
def ims_anomaly_pipeline(
    dataset_version: str = "live-sipp-v1",
    baseline_version: str = "baseline-v1",
    automl_version: str = "candidate-v1",
    automl_engine: str = "autogluon",
):
    ingest_task = ingest_data(dataset_version=dataset_version)
    feature_task = feature_engineering(
        dataset_version=dataset_version,
        dataset_manifest=ingest_task.outputs["output_manifest"],
    )
    label_task = label_generation(
        dataset_version=dataset_version,
        feature_manifest=feature_task.outputs["output_manifest"],
    )
    baseline_task = train_baseline(
        baseline_version=baseline_version,
        label_manifest=label_task.outputs["output_manifest"],
    )
    automl_task = train_autogluon(
        candidate_version=automl_version,
        label_manifest=label_task.outputs["output_manifest"],
        automl_engine=automl_engine,
    )
    evaluation_task = evaluate_models(
        dataset_version=dataset_version,
        label_manifest=label_task.outputs["output_manifest"],
        baseline_manifest=baseline_task.outputs["output_manifest"],
        candidate_manifest=automl_task.outputs["output_manifest"],
    )
    selection_task = select_best(
        dataset_version=dataset_version,
        evaluation_manifest=evaluation_task.outputs["output_manifest"],
    )
    registry_task = register_model(
        label_manifest=label_task.outputs["output_manifest"],
        selection_manifest=selection_task.outputs["output_manifest"],
    )
    deploy_model(
        registry_manifest=registry_task.outputs["output_manifest"],
    )
