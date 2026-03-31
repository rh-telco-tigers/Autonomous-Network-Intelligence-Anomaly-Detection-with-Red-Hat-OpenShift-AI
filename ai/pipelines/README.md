# AI Pipeline Assets

This directory contains source for the predictive training workflow expected to run on OpenShift AI data science pipelines.

## Included asset

- `ims_anomaly_pipeline.py`: Kubeflow pipeline source for ingestion, feature engineering, baseline training, AutoML candidate generation, evaluation, registration, and deployment handoff
- `generated/ims_anomaly_pipeline.yaml`: compiled KFP package tracked for GitOps-driven registration
- `publish_pipeline.py`: in-cluster bootstrap client that registers the pipeline and creates the demo run

## Usage

`k8s/base/kfp` deploys the namespace-scoped `DataSciencePipelinesApplication` and a bootstrap Job that uploads `generated/ims_anomaly_pipeline.yaml` into the local DSPA and creates the `ims-anomaly-platform-demo` run.
