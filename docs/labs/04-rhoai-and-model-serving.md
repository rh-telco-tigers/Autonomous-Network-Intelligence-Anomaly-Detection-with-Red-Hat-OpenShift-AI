# Lab 04: OpenShift AI and Model Serving

## Objective

Enable the OpenShift AI control plane and prepare predictive and generative inference paths.

## Components

- OpenShift AI operator via Argo CD managed OLM subscription
- Kubeflow pipeline source in `ai/pipelines`
- predictive KServe runtime and inference service
- generative vLLM KServe runtime and inference service
- Milvus for vector-backed retrieval
- control-plane-backed incident persistence and approval flow

## Steps

1. Sync the `ims-demo-operators` Argo CD application from `deploy/gitops/operators`.
2. Verify the OpenShift AI subscription in `redhat-ods-operator` has reached `AtLatestKnown`.
3. Apply `k8s/base/milvus`.
4. Apply `k8s/base/serving`.
5. Build and push the platform services and deploy `k8s/base/platform`.
6. Apply `k8s/base/observability` to scrape service metrics.
7. Build the trainer image and run the training pipeline; it uploads predictive artifacts into MinIO automatically under `s3://ims-models/predictive/`.

## Validation targets

- the OpenShift AI subscription resolves successfully in `redhat-ods-operator`
- model registry and pipelines are enabled if the cluster's OpenShift AI defaults instantiate them
- the predictive and generative `ServingRuntime` objects are present
- demo MinIO object storage is running with `minioadmin` / `minioadmin`
- inference services resolve to ready pods once the demo model buckets are populated
- incidents persist through the control-plane service
