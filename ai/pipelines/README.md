# AI Pipeline Assets

This directory contains source for the predictive training workflow expected to run on OpenShift AI data science pipelines.

## Included Assets

- `ani_anomaly_pipeline.py`: Kubeflow pipeline source for ingestion, feature engineering, baseline training, AutoML candidate generation, evaluation, registration, and deployment handoff
- `ani_feature_bundle_pipeline.py`: Kubeflow pipeline source that publishes bundle datasets with feature-window, incident, and RCA tables
- `ani_featurestore_pipeline.py`: Kubeflow pipeline source for the additive feature-store-backed training and model-registry flow
- `generated/ani_anomaly_pipeline.yaml`: compiled KFP package tracked for GitOps-driven `PipelineVersion` reconciliation
- `generated/ani_feature_bundle_pipeline.yaml`: compiled KFP package for the bundle publish path
- `generated/ani_featurestore_pipeline.yaml`: compiled KFP package for the feature-store path
- `publish_pipeline.py`: helper client retained for manual trigger jobs
- `publish_feature_bundle_pipeline.py`: helper client retained for manual bundle trigger jobs
- `publish_featurestore_pipeline.py`: helper client retained for manual feature-store trigger jobs

## Usage

`k8s/base/kfp` deploys the namespace-scoped `DataSciencePipelinesApplication` and declarative `Pipeline` / `PipelineVersion` resources that mirror the tracked KFP packages into the local DSPA namespace.

The same kustomization also carries shared ConfigMaps and RBAC for the background KFP auto-run `CronJob`s and the manual trigger Jobs under `k8s/manual/demo-triggers`.

GitOps registers the pipeline definitions declaratively, and the background auto-run `CronJob`s submit first-run executions by referencing the registered pipeline versions instead of re-uploading YAML. Use `docs/installation/04-data-generation-and-model-training.md` to watch those runs or force a manual rerun after the build pipeline completes.
