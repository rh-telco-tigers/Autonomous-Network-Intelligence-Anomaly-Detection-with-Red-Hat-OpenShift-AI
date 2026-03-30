# Lab 04: OpenShift AI and Model Serving

## Objective

Enable the OpenShift AI control plane and prepare predictive and generative inference paths.

## Components

- OpenShift AI operator via OLM
- `DSCInitialization` and `DataScienceCluster` resources
- Kubeflow pipeline source in `ai/pipelines`
- predictive KServe runtime and inference service
- generative vLLM KServe runtime and inference service
- Milvus for vector-backed retrieval
- control-plane-backed incident persistence and approval flow

## Steps

1. Apply `k8s/base/operators`.
2. Apply `k8s/base/rhoai`.
3. Populate the example model storage secret in `k8s/base/serving`.
4. Apply `k8s/base/milvus`.
5. Apply `k8s/base/serving`.
6. Build and push the platform services and deploy `k8s/base/platform`.
7. Apply `k8s/base/observability` to scrape service metrics.
8. Build the trainer image and run the training pipeline; it uploads predictive artifacts into MinIO automatically under `s3://ims-models/predictive/`.

## Validation targets

- `DataScienceCluster` reconciles successfully
- model registry and pipelines are enabled
- the predictive and generative `ServingRuntime` objects are present
- demo MinIO object storage is running with `minioadmin` / `minioadmin`
- inference services resolve to ready pods once the demo model buckets are populated
- incidents persist through the control-plane service
