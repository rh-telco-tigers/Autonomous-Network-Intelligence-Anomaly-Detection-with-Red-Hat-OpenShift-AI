# Lab 04: OpenShift AI and Model Serving

## Objective

Run the training pipeline in OpenShift AI, verify that it uses the captured dataset from Lab 03, and confirm that the feature-store-backed multiclass model is available for serving through both Triton and MLServer. The demo overlay no longer keeps the legacy `ims-predictive` service.

## Before You Begin

- Complete Lab 03 and confirm that `live-sipp-v1` feature windows exist in MinIO.
- Make sure the OpenShift AI operator is installed.
- Make sure you can access the `ims-demo-lab` namespace with `oc`.
- Make sure the Tekton image build from Lab 03 has already populated the internal registry tags used by the trainer and serving components.
- Do not create a Feature Store manually in the OpenShift AI UI. This repo bootstraps `FeatureStore/ims-featurestore` from repo-managed manifests.
- Verify that a model registry service is reachable as `ims-demo-modelregistry` in `rhoai-model-registries`, or be ready to patch the repo's model registry endpoint references for your cluster.
- Live LLM-backed RCA is disabled by default on a fresh cluster. Enable it only after the core AI path is healthy.

## What This Lab Uses

- `DataSciencePipelinesApplication` named `dspa`
- KFP assets from `k8s/base/kfp`
- trainer images from `ai/training` and `ai/featurestore`
- model storage in MinIO bucket `ims-models`
- predictive serving resources in `k8s/base/serving`

## Fresh-Cluster Note

The GitOps demo overlay in `k8s/overlays/demo` now includes the AI extras needed for the full demo path:

- `k8s/base/feature-store`
- `k8s/base/kafka`
- `k8s/base/kfp`

If Argo CD is the source of truth, prefer waiting for `ims-demo-platform` to reconcile. Use `make apply-demo-ai-extras` only as an imperative recovery path.

## RCA LLM Provider Configuration

The RCA service can call an OpenAI-compatible chat completions endpoint, but the fresh-cluster default is local RAG fallback until you configure a provider.

Runtime settings now live in:

- `ConfigMap/llm-provider-config` for `LLM_ENDPOINT`, `LLM_MODEL`, and `LLM_REQUEST_TIMEOUT_SECONDS`
- `Secret/llm-provider-auth` for `LLM_API_KEY`

Fresh-cluster default values:

- `LLM_ENDPOINT=` (blank, which keeps RCA on the local fallback path)
- `LLM_MODEL=` (blank)
- `LLM_REQUEST_TIMEOUT_SECONDS=20`
- `LLM_API_KEY` is blank

Fresh-cluster note:

- if you want live LLM-backed RCA, patch `llm-provider-config` and `llm-provider-auth` after the rest of the platform is healthy

## Swap To Another OpenAI-Compatible Endpoint Later

No code changes are required. Update the runtime config, restart `rca-service`, and verify the new settings.

1. Update the endpoint, model, and timeout:

```sh
oc patch configmap llm-provider-config -n ims-demo-lab --type merge -p '{"data":{"LLM_ENDPOINT":"https://api.openai.com","LLM_MODEL":"gpt-4.1-mini","LLM_REQUEST_TIMEOUT_SECONDS":"20"}}'
```

2. Set or rotate the API key if the provider requires one:

```sh
oc patch secret llm-provider-auth -n ims-demo-lab --type merge -p '{"stringData":{"LLM_API_KEY":"<provider-api-key>"}}'
```

3. Restart the RCA service and wait for rollout:

```sh
oc rollout restart deploy/rca-service -n ims-demo-lab
oc rollout status deploy/rca-service -n ims-demo-lab
```

4. Verify the live runtime config from inside the pod:

```sh
oc exec deploy/rca-service -n ims-demo-lab -- python -c "import requests; print(requests.get('http://localhost:8080/healthz', timeout=5).json())"
```

Notes:

- `LLM_ENDPOINT` can be either the provider root, such as `https://api.openai.com`, or a base URL that already ends with `/v1`
- do not set `LLM_ENDPOINT` to the full `/chat/completions` path unless you intend to pin the service to that exact route
- if you only want to test a different model on the same vLLM deployment, change `LLM_MODEL` only

## Run The Lab

1. Verify the OpenShift AI subscription is ready:

```sh
oc get csv -n redhat-ods-operator
```

2. Verify that the AI extras are being reconciled by the demo overlay:

```sh
oc get application.argoproj.io ims-demo-platform -n openshift-gitops -o jsonpath='{.status.sync.status}{" / "}{.status.health.status}{"\n"}'
```

If you need an imperative recovery path outside GitOps, you can still run:

```sh
make apply-demo-ai-extras
```

3. Verify that the DSPA, Feature Store, Kafka, and model registry endpoint are ready:

```sh
make check-fresh-cluster-ai
oc get dspa,featurestore -n ims-demo-lab
oc get kafka -n ims-demo-lab
oc get svc -n rhoai-model-registries | rg 'ims-demo-modelregistry'
```

4. Confirm that the live dataset exists before starting training. The expected dataset version is `live-sipp-v1`.
5. Start the bundle publish pipeline, then the feature-store training pipeline:

```sh
make trigger-feature-bundle-pipeline
make trigger-featurestore-pipeline
```

6. Watch the workflow progress:

```sh
oc get workflow -n ims-demo-lab
```

7. When the workflow completes, inspect the `ingest-data` step logs. The expected source is `openims-sipp-lab`, and the expected dataset kind is `feature_windows`.
8. Confirm that the OpenShift AI Feature Store UI shows `ims-featurestore`. If the page was already open, do a hard refresh first.
9. Verify that both feature-store model-serving resources are ready:

```sh
oc get inferenceservice -n ims-demo-lab | rg 'ims-predictive-fs|ims-predictive-fs-mlserver'
oc get servingruntime -n ims-demo-lab
```

10. Run the side-by-side serving smoke check:

```sh
make smoke-check-featurestore-serving
```

11. Open the Attu route if you also want to confirm the retrieval data store is present:

```sh
oc get route milvus-attu -n ims-demo-lab -o jsonpath='{.spec.host}{"\n"}'
```

## Expected Result

After this lab:

- the DSPA `dspa` is ready
- the Feature Store instance `ims-featurestore` is ready and visible in the OpenShift AI UI
- the bundle publish pipeline completes successfully
- the feature-store training pipeline completes successfully
- the pipeline reads `live-sipp-v1` feature windows when they are available
- the selected model is written to the model registry
- both `ims-predictive-fs` and `ims-predictive-fs-mlserver` are available in `ims-demo-lab`
- the legacy `ims-predictive` service is intentionally absent from the demo overlay

## Useful Checks

Check recent workflows:

```sh
oc get workflow -n ims-demo-lab --sort-by=.metadata.creationTimestamp
```

Check the serving resources:

```sh
oc get inferenceservice -n ims-demo-lab | rg 'ims-predictive-fs|ims-predictive-fs-mlserver'
oc get servingruntime -n ims-demo-lab
```

Check the MinIO-backed registry output:

```sh
oc logs job/ims-kfp-bootstrap -n ims-demo-lab
```

Check the model registry service assumption:

```sh
oc get svc -n rhoai-model-registries | rg 'ims-demo-modelregistry'
```

## If The Pipeline Does Not Use The Live Dataset

The pipeline prefers the `live-sipp-v1` dataset, but it can fall back to bootstrap data if the live dataset is missing or too small. If you want to test the real-traffic path, make sure Lab 03 has already produced feature-window objects in MinIO before starting the run.

## Quick Troubleshooting

- If the DSPA is not ready, check the OpenShift AI operator status first.
- If the Feature Store overview still shows an empty state, hard refresh the browser and confirm `oc get featurestore -n ims-demo-lab` shows `ims-featurestore` in `Ready`.
- If the workflow fails, inspect the failed pod logs before rerunning.
- If a training or serving pod is stuck in `ImagePullBackOff`, confirm that Lab 03 finished building the demo images into `image-registry.openshift-image-registry.svc:5000/ims-demo-lab`.
- If model registration fails early, confirm that `ims-demo-modelregistry` exists in `rhoai-model-registries` or patch the model registry endpoint config before rerunning.
- If model serving is not ready, check `oc describe inferenceservice -n ims-demo-lab`.
- If RCA stays on the local fallback path, that is expected until you configure `llm-provider-config`.
