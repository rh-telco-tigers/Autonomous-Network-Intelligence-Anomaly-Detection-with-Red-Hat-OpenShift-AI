# Lab 04: OpenShift AI and Model Serving

## Objective

Enable OpenShift AI, train a model from captured IMS feature windows, register the selected model, and expose it through model serving.

## Why This Lab Matters

This lab connects the traffic story from Lab 03 to the AI story.

By the end of this lab, you are not just showing that the platform can run a pipeline. You are showing that:

- real SIPp-driven IMS traffic was captured
- the captured traffic was converted into labeled feature windows
- Kubeflow Pipelines trained on those windows
- the winning model was registered and deployed for scoring

That is much easier for an end user to understand than a generic "we ran AutoML in a notebook" explanation.

## Main Components

- OpenShift AI operator
- `DataSciencePipelinesApplication` named `dspa`
- Kubeflow pipeline source in `ai/pipelines`
- trainer image from `ai/training`
- MinIO bucket `ims-models`
- predictive Triton `ServingRuntime` and `InferenceService`
- shared vLLM endpoint exposed through `ims-generative-proxy`
- Milvus plus Attu for RCA retrieval visibility

## Simple End-User Data Flow

Use this explanation when presenting:

1. Lab 03 creates labeled feature windows from real SIP traffic.
2. Those feature windows are stored in MinIO under dataset version `live-sipp-v1`.
3. The KFP pipeline reads those windows first.
4. The pipeline trains a baseline model and an AutoGluon candidate.
5. The evaluation gate selects the better model.
6. The selected model is written to the registry and deployed for serving.

If the live dataset is temporarily missing, the pipeline can still fall back to synthetic bootstrap data. That fallback keeps the platform resilient, but the preferred demo path is the real-traffic dataset.

## Step-By-Step Walkthrough

1. Sync the `ims-demo-operators` Argo CD application from `deploy/gitops/operators`.
2. Verify the OpenShift AI subscription in `redhat-ods-operator` has reached `AtLatestKnown`.
3. Apply `k8s/base/milvus`.
4. Apply `k8s/base/kfp` to create the demo namespace DSPA.
5. Apply `k8s/base/serving`.
6. Build and push the platform services and deploy `k8s/base/platform`.
7. Apply `k8s/base/observability` to scrape service metrics.
8. Confirm the live dataset exists in MinIO before training. The expected dataset version is `live-sipp-v1`.
9. Build the trainer image and run the pipeline. The preferred training input is the SIPp-captured feature-window dataset from MinIO. The pipeline only falls back to synthetic bootstrap data if that live dataset is unavailable or too small.
10. Verify the `ims-kfp-bootstrap` job has registered the compiled pipeline and created a run.
11. Verify the `milvus-bootstrap` job has loaded the runbooks into Milvus.
12. Open the Attu route:

```sh
oc get route milvus-attu -n ims-demo-lab -o jsonpath='{.spec.host}{"\n"}'
```

## What To Show The Audience

- OpenShift AI is managing the pipeline and serving resources, not an ad hoc notebook session.
- The pipeline input is a named dataset version in MinIO.
- The selected model is tied to a feature schema version and dataset version.
- The predictive model is served through the cluster-native serving stack.
- The generative RCA path remains separate from the predictive anomaly detector.

## Suggested Validation Commands

Check the DSPA:

```sh
oc get dspa -n ims-demo-lab
```

Check the serving resources:

```sh
oc get inferenceservice,servingruntime -n ims-demo-lab
```

Check the pipeline bootstrap job:

```sh
oc get job -n ims-demo-lab | rg 'ims-kfp-bootstrap'
```

## Access Notes

- Attu does not require a separate UI username or password in this demo deployment.
- MinIO console uses `minioadmin` / `minioadmin`.

## What Success Looks Like

- the OpenShift AI subscription resolves successfully in `redhat-ods-operator`
- the DSPA named `dspa` reaches `Ready`
- the compiled pipeline is visible in Kubeflow Pipelines and a run exists
- SIPp-derived feature windows are present in MinIO before the training run
- the predictive `InferenceService` resolves in `ims-demo-lab`
- the in-namespace `ims-generative-proxy` resolves to the shared vLLM endpoint
- Attu is reachable and shows the `ims_runbooks` collection
- the predictive model is uploaded in Triton repository layout into MinIO and served by KServe
- incidents persist through the control-plane service

## Key Message For The Audience

This lab proves that the AI portion of the platform is grounded in observable telecom behavior. The model is not trained in isolation. It is trained from the same kind of feature windows that the live system will later score.
