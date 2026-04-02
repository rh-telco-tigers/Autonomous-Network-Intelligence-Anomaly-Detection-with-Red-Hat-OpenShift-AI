# AI Pipeline Assets

This directory contains source for the predictive training workflow expected to run on OpenShift AI data science pipelines.

## Included Assets

- `ims_anomaly_pipeline.py`: Kubeflow pipeline source for ingestion, feature engineering, baseline training, AutoML candidate generation, evaluation, registration, and deployment handoff
- `ims_feature_bundle_pipeline.py`: Kubeflow pipeline source that publishes bundle datasets with feature-window, incident, and RCA tables
- `ims_featurestore_pipeline.py`: Kubeflow pipeline source for the additive feature-store-backed training and model-registry flow
- `generated/ims_anomaly_pipeline.yaml`: compiled KFP package tracked for GitOps-driven registration
- `generated/ims_feature_bundle_pipeline.yaml`: compiled KFP package for the bundle publish path
- `generated/ims_featurestore_pipeline.yaml`: compiled KFP package for the feature-store path
- `publish_pipeline.py`: in-cluster bootstrap client that registers the pipeline and creates the demo run
- `publish_feature_bundle_pipeline.py`: bootstrap client for the bundle publish pipeline
- `publish_featurestore_pipeline.py`: feature-store-specific bootstrap client with separate pipeline defaults

## Usage

`k8s/base/kfp` deploys the namespace-scoped `DataSciencePipelinesApplication` and a bootstrap Job that uploads `generated/ims_anomaly_pipeline.yaml` into the local DSPA and creates the `ims-anomaly-platform-demo` run.

The same kustomization also carries:

- a bundle bootstrap for `ims-feature-bundle-publish`
- a feature-store bootstrap for `ims-featurestore-train-and-register`

Both new bootstraps register their pipelines without auto-running them, so live data collection can finish first and manual runs can target the intended bundle version explicitly.
