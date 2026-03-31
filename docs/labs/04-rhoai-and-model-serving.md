# Lab 04: OpenShift AI and Model Serving

## Objective

Enable the OpenShift AI control plane and prepare predictive and generative inference paths.

## Components

- OpenShift AI operator via Argo CD managed OLM subscription
- Kubeflow pipeline source in `ai/pipelines`
- predictive KServe runtime and inference service in `ims-demo-lab`
- shared cluster vLLM endpoint consumed through the in-namespace `ims-generative-proxy` service
- Milvus for vector-backed retrieval
- Attu as the Milvus UI
- control-plane-backed incident persistence and approval flow

## Steps

1. Sync the `ims-demo-operators` Argo CD application from `deploy/gitops/operators`.
2. Verify the OpenShift AI subscription in `redhat-ods-operator` has reached `AtLatestKnown`.
3. Apply `k8s/base/milvus`.
4. Apply `k8s/base/serving`.
5. Build and push the platform services and deploy `k8s/base/platform`.
6. Apply `k8s/base/observability` to scrape service metrics.
7. Build the trainer image and run the training pipeline; it uploads predictive artifacts into MinIO automatically under `s3://ims-models/predictive/`.
8. Verify the `milvus-bootstrap` job has loaded the runbooks into Milvus.
9. Open the Attu route:

```sh
oc get route milvus-attu -n ims-demo-lab -o jsonpath='{.spec.host}{"\n"}'
```

Access notes:

- Attu does not require a separate UI username or password in this demo deployment.
- MinIO console uses `minioadmin` / `minioadmin`.

## Validation targets

- the OpenShift AI subscription resolves successfully in `redhat-ods-operator`
- model registry and pipelines are enabled if the cluster's OpenShift AI defaults instantiate them
- the predictive `InferenceService` resolves in `ims-demo-lab`
- the in-namespace `ims-generative-proxy` resolves to the shared vLLM endpoint
- demo MinIO object storage is running with `minioadmin` / `minioadmin`
- Attu is reachable from its OpenShift route and shows the `ims_runbooks` collection
- the predictive model is uploaded as `model.joblib` into MinIO and served by KServe
- incidents persist through the control-plane service
