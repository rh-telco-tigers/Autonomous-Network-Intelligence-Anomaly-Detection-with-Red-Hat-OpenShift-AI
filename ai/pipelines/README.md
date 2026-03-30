# AI Pipeline Assets

This directory contains source for the predictive training workflow expected to run on OpenShift AI data science pipelines.

## Included asset

- `ims_anomaly_pipeline.py`: Kubeflow pipeline source for ingestion, feature engineering, baseline training, AutoML candidate generation, evaluation, registration, and deployment handoff

## Usage

Package or upload the pipeline from a workbench image that has `kfp` available, then bind the produced model artifact locations to the KServe inference services defined under `k8s/base/serving`.

