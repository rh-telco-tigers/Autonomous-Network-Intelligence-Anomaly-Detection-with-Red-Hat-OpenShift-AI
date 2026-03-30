"""Kubeflow pipeline source for the IMS anomaly demo workflow."""

from kfp import dsl


PIPELINE_IMAGE = "image-registry.openshift-image-registry.svc:5000/ims-demo-lab/ims-ai-trainer:latest"


@dsl.container_component
def ingest_data(dataset_version: str):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "-c"],
        args=[f"print('ingesting dataset version {dataset_version}')"],
    )


@dsl.container_component
def feature_engineering():
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "-c"],
        args=["print('materializing feature windows for feature_schema_v1')"],
    )


@dsl.container_component
def train_and_register(
    dataset_version: str,
    baseline_version: str,
    candidate_version: str,
):
    return dsl.ContainerSpec(
        image=PIPELINE_IMAGE,
        command=["python", "ai/training/train_and_register.py"],
        args=[
            "--dataset-version",
            dataset_version,
            "--baseline-version",
            baseline_version,
            "--candidate-version",
            candidate_version,
            "--artifact-dir",
            "ai/models/artifacts",
            "--registry-path",
            "ai/registry/model_registry.json",
        ],
    )


@dsl.pipeline(name="ims-anomaly-platform-train-and-register")
def ims_anomaly_pipeline(
    dataset_version: str = "synthetic-v1",
    baseline_version: str = "baseline-v1",
    automl_version: str = "candidate-v1",
):
    ingest_task = ingest_data(dataset_version=dataset_version)
    feature_task = feature_engineering().after(ingest_task)
    train_and_register(
        dataset_version=dataset_version,
        baseline_version=baseline_version,
        candidate_version=automl_version,
    ).after(feature_task)
