# Lab 04: OpenShift AI and Model Serving

## Objective

Enable the OpenShift AI control plane and prepare predictive and generative inference paths.

## Components

- OpenShift AI operator via Argo CD managed OLM subscription
- Kubeflow pipeline source in `ai/pipelines`
- namespace-scoped `DataSciencePipelinesApplication` in `ims-demo-lab`
- predictive NVIDIA Triton `ServingRuntime` and `InferenceService` in `ims-demo-lab`
- shared cluster vLLM endpoint consumed through the in-namespace `ims-generative-proxy` service
- Milvus for vector-backed retrieval
- Attu as the Milvus UI
- control-plane-backed incident persistence and approval flow

## Steps

1. Sync the `ims-demo-operators` Argo CD application from `deploy/gitops/operators`.
2. Verify the OpenShift AI subscription in `redhat-ods-operator` has reached `AtLatestKnown`.
3. Apply `k8s/base/milvus`.
4. Apply `k8s/base/kfp` to create the demo namespace DSPA.
5. Apply `k8s/base/serving`.
6. Build and push the platform services and deploy `k8s/base/platform`.
7. Apply `k8s/base/observability` to scrape service metrics.
8. Build the trainer image and run the training pipeline; it uploads a Triton model repository into MinIO automatically under `s3://ims-models/predictive/`.
9. Verify the `ims-kfp-bootstrap` job has registered the compiled pipeline and created the `ims-anomaly-platform-demo` run.
10. Verify the `milvus-bootstrap` job has loaded the runbooks into Milvus.
11. Open the Attu route:

```sh
oc get route milvus-attu -n ims-demo-lab -o jsonpath='{.spec.host}{"\n"}'
```

Access notes:

- Attu does not require a separate UI username or password in this demo deployment.
- MinIO console uses `minioadmin` / `minioadmin`.

## Validation targets

- the OpenShift AI subscription resolves successfully in `redhat-ods-operator`
- the namespace DSPA named `dspa` reaches `Ready`
- the compiled pipeline is visible in the Kubeflow Pipelines UI and a demo run exists
- the predictive `InferenceService` resolves in `ims-demo-lab`
- the in-namespace `ims-generative-proxy` resolves to the shared vLLM endpoint
- demo MinIO object storage is running with `minioadmin` / `minioadmin`
- Attu is reachable from its OpenShift route and shows the `ims_runbooks` collection
- the predictive model is uploaded in Triton repository layout into MinIO and served by KServe with the NVIDIA Triton runtime
- incidents persist through the control-plane service
