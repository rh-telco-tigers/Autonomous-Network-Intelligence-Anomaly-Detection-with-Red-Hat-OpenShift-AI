# Lab 04: OpenShift AI and Model Serving

## Objective

Run the training pipeline in OpenShift AI, verify that it uses the captured dataset from Lab 03, and confirm that the selected model is available for serving.

## Before You Begin

- Complete Lab 03 and confirm that `live-sipp-v1` feature windows exist in MinIO.
- Make sure the OpenShift AI operator is installed.
- Make sure you can access the `ims-demo-lab` namespace with `oc`.
- Make sure the Tekton image build from Lab 03 has already populated the internal registry tags used by the trainer and serving components.

## What This Lab Uses

- `DataSciencePipelinesApplication` named `dspa`
- KFP assets from `k8s/base/kfp`
- trainer image from `ai/training`
- model storage in MinIO bucket `ims-models`
- predictive serving resources in `k8s/base/serving`

## Run The Lab

1. Verify the OpenShift AI subscription is ready:

```sh
oc get csv -n redhat-ods-operator
```

2. Apply the AI and serving resources:

```sh
oc apply -k k8s/base/kfp
oc apply -k k8s/base/serving
oc apply -k k8s/base/milvus
```

3. Verify that the DSPA is ready:

```sh
oc get dspa -n ims-demo-lab
```

4. Confirm that the live dataset exists before starting training. The expected dataset version is `live-sipp-v1`.
5. Start the pipeline bootstrap job or verify that it has already created a run:

```sh
oc get job -n ims-demo-lab | rg 'ims-kfp-bootstrap'
```

6. Watch the workflow progress:

```sh
oc get workflow -n ims-demo-lab
```

7. When the workflow completes, inspect the `ingest-data` step logs. The expected source is `openims-sipp-lab`, and the expected dataset kind is `feature_windows`.
8. Verify that the model-serving resources are ready:

```sh
oc get inferenceservice,servingruntime -n ims-demo-lab
```

9. Open the Attu route if you also want to confirm the retrieval data store is present:

```sh
oc get route milvus-attu -n ims-demo-lab -o jsonpath='{.spec.host}{"\n"}'
```

## Expected Result

After this lab:

- the DSPA `dspa` is ready
- the training pipeline completes successfully
- the pipeline reads `live-sipp-v1` feature windows when they are available
- the selected model is written to the registry
- the predictive serving resources are available in `ims-demo-lab`

## Useful Checks

Check recent workflows:

```sh
oc get workflow -n ims-demo-lab --sort-by=.metadata.creationTimestamp
```

Check the serving resources:

```sh
oc get inferenceservice,servingruntime -n ims-demo-lab
```

Check the MinIO-backed registry output:

```sh
oc logs job/ims-kfp-bootstrap -n ims-demo-lab
```

## If The Pipeline Does Not Use The Live Dataset

The pipeline prefers the `live-sipp-v1` dataset, but it can fall back to bootstrap data if the live dataset is missing or too small. If you want to test the real-traffic path, make sure Lab 03 has already produced feature-window objects in MinIO before starting the run.

## Quick Troubleshooting

- If the DSPA is not ready, check the OpenShift AI operator status first.
- If the workflow fails, inspect the failed pod logs before rerunning.
- If a training or serving pod is stuck in `ImagePullBackOff`, confirm that Lab 03 finished building the demo images into `image-registry.openshift-image-registry.svc:5000/ims-demo-lab`.
- If model serving is not ready, check `oc describe inferenceservice -n ims-demo-lab`.
