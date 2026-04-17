# Installation 04: Data Generation And Model Training

## Objective

Watch or force-rerun the live model path after installation, generate a separate backfill model path when needed, and verify that the app can switch between the active predictive endpoints.

## Before You Start

- Finish [Installation](./02-installation.md) and [Validation](./03-validation.md).
- Confirm `ani-runtime` is `Synced` / `Healthy` and the predictive `InferenceService` resources already exist in `ani-datascience`.
- Run the initial image build at least once with `make trigger-build-pipeline`.
- Use the feature-store path in this guide. The live serving endpoint is `ani-predictive-fs`.
- The backfill serving endpoint is `ani-predictive-backfill`.

If `ani-datascience` is degraded only because `llama-32-3b-instruct` is pending on missing GPU capacity, you can still continue with this predictive-model workflow.

## 1. Watch The Automatic Live Path

On the current branch, `ani-datascience` includes background KFP auto-run `CronJob`s. After the first image build finishes, those CronJobs submit the live-path workflows against the declaratively managed `Pipeline` / `PipelineVersion` resources.

Watch:

```sh
oc get cronjob -n ani-datascience | rg 'kfp-auto-run'
oc get jobs -n ani-datascience
oc get wf -n ani-datascience
oc get inferenceservice -n ani-datascience
```

Continue when the relevant workflows finish with `Succeeded` and `ani-predictive-fs` becomes `READY=True`.

If you want to accelerate or force a rerun of the live path instead of waiting for the next CronJob tick, use the manual steps below.

## 2. Optionally Generate One Live Incident

Create one incident from the live dataset path:

```sh
make live-step-1-generate-demo-incident DEMO_INCIDENT_SCENARIO=busy_destination
```

Expected result:

- the command returns a `feature_window.dataset_version` such as `live-sipp-v1`
- the `score` block returns `scoring_mode: remote-kserve`
- the demo UI shows a new incident

## 3. Optionally Generate A Larger Training-Only Backfill

Use this only when you want extra feature windows for offline analysis or model experiments:

```sh
make backfill-step-1-generate-training-dataset
```

Watch progress:

```sh
oc get jobs -n ani-sipp -l app.kubernetes.io/part-of=sipp-backfill-100k
oc get pods -n ani-sipp -l app.kubernetes.io/part-of=sipp-backfill-100k
```

Important:

- backfill writes to the shared dataset version `backfill-sipp-100k`
- backfill is training-only
- backfill is not a valid source for the incident-release bundle step
- if you want a separate backfill-trained model, continue with the Backfill Model Path later in this guide

## 4. List Dataset Versions If You Need To Inspect State

```sh
make list-incident-release-datasets
```

Use this when you want to see:

- active backfill runs
- stored dataset versions in object storage

For incident release, always use the incident-linked live dataset unless you intentionally override it with another linked dataset.

## 5. Build The Incident Release Bundle

Compile the incident-linked dataset into the incident release bundle:

```sh
make live-step-2-build-incident-release
```

If you need to pin a linked dataset version explicitly:

```sh
make live-step-2-build-incident-release INCIDENT_RELEASE_SOURCE_DATASET_VERSION=live-sipp-v1
```

Watch the trigger job and workflow:

```sh
oc get jobs -n ani-datascience
oc get wf -n ani-datascience | rg 'ani-incident-release'
```

Continue when the workflow finishes with `Succeeded`.

## 6. Publish The Feature Bundle

Publish the bundle that the feature-store training pipeline consumes:

```sh
make live-step-3-publish-feature-bundle
```

Watch:

```sh
oc get wf -n ani-datascience | rg 'ani-feature-bundle'
```

Continue when the publish workflow finishes with `Succeeded`.

## 7. Train, Register, And Deploy The Predictive Model

Train and deploy the feature-store model:

```sh
make live-step-4-train-and-deploy-classifier
```

Watch:

```sh
oc get wf -n ani-datascience | rg 'ani-featurestore-train-and-register'
oc get inferenceservice -n ani-datascience
```

This is the preferred training path. It deploys the model behind `ani-predictive-fs`, which the app uses for live classification.

## 8. Run The Serving Smoke Check

```sh
make live-step-5-smoke-check-serving
```

Then inspect the latest smoke job:

```sh
oc get jobs -n ani-datascience | rg 'ani-featurestore-serving-smoke'
oc logs -n ani-datascience job/<latest-smoke-job-name>
```

Expected result:

- ready endpoint returns `200`
- smoke status is `passed`

## 9. Verify The App Uses The Deployed Predictor

Call the anomaly service directly:

```sh
ANOMALY_HOST="$(oc get route anomaly-service -n ani-runtime -o jsonpath='{.spec.host}')"
curl -k "https://${ANOMALY_HOST}/score" \
  -H "x-api-key: demo-token" \
  -H "Content-Type: application/json" \
  -d '{"project":"ani-demo","scenario_name":"busy_destination","features":{"register_rate":0.5,"invite_rate":5.0,"bye_rate":0.4,"error_4xx_ratio":0.01,"error_5xx_ratio":0.01,"latency_p95":180.0,"retransmission_count":6.0,"inter_arrival_mean":0.8,"payload_variance":55.0}}' \
  | python3 -m json.tool
```

Expected result:

- `scoring_mode` is `remote-kserve`
- `model_version` is `ani-predictive-fs`

Then run one more incident through the control plane:

```sh
make live-step-1-generate-demo-incident DEMO_INCIDENT_SCENARIO=busy_destination
```

If this succeeds and the incident shows up in the demo UI, the UI path is consuming the deployed predictive model through the live control-plane and anomaly-service flow.

## Backfill Model Path

Use this path when you want a separate model trained from the full backfill dataset instead of the incident-linked live bundle.

### 1. Generate Or Refresh The Shared Backfill Dataset

```sh
make backfill-step-1-generate-training-dataset
```

### 2. Build The Backfill Bundle

This publishes a bundle with both Parquet and CSV exports so the result is ready for offline analysis or Kaggle-style release packaging.

```sh
make backfill-step-2-build-feature-bundle
```

Watch:

```sh
oc get jobs -n ani-datascience | rg 'ani-backfill-feature-bundle'
oc get wf -n ani-datascience | rg 'ani-backfill-feature-bundle-publish'
```

### 3. Train And Register The Backfill Model

```sh
make backfill-step-3-train-and-register-classifier
```

Watch:

```sh
oc get jobs -n ani-datascience | rg 'ani-backfill-featurestore'
oc get wf -n ani-datascience | rg 'ani-backfill-featurestore-train-and-register'
```

Expected result:

- the AutoGluon backfill model is registered
- the exported serving artifact is published under the backfill storage path
- the live endpoint `ani-predictive-fs` stays up at the same time

### 4. Activate The Backfill Serving Endpoint

This step creates the backfill `InferenceService` and metrics monitor after the trained artifact path exists.

```sh
make backfill-step-4-activate-serving-endpoint
```

Validate:

```sh
oc get inferenceservice -n ani-datascience | rg 'ani-predictive-backfill'
```

### 5. Smoke-Check The Backfill Predictor

```sh
make backfill-step-5-smoke-check-serving
```

Then inspect the latest smoke job:

```sh
oc get jobs -n ani-datascience | rg 'ani-backfill-serving-smoke'
oc logs -n ani-datascience job/<latest-backfill-smoke-job-name>
```

### 6. Optional: Publish The Backfill Model As A Modelcar Image

Use this branch when you want to promote the already-trained backfill model into an OCI image so other demo clusters can reuse it without regenerating the large dataset first.

```sh
make backfill-modelcar-step-1-publish-image
```

Watch:

```sh
oc get pipelinerun -n ani-tekton | rg 'ani-backfill-modelcar'
```

Expected result:

- the modelcar image is pushed to `image-registry.openshift-image-registry.svc:5000/ani-datascience/ani-predictive-backfill-modelcar`
- the OCI artifact is registered in the model registry under the modelcar model name
- the GitOps-managed `ani-predictive-backfill-modelcar` endpoint can resolve the `current` tag without rerunning backfill generation

### 7. Optional: Smoke-Check The Modelcar Predictor

```sh
make backfill-modelcar-step-2-smoke-check-serving
```

Then inspect the latest smoke job:

```sh
oc get jobs -n ani-datascience | rg 'ani-backfill-modelcar-serving-smoke'
oc logs -n ani-datascience job/<latest-backfill-modelcar-smoke-job-name>
```

### 8. Switch The Demo UI Between Live, Backfill, And Modelcar

Open the demo UI overview page. The `Classifier routing` card lets you switch:

- `Live model` -> `ani-predictive-fs`
- `Backfill model` -> `ani-predictive-backfill`
- `Modelcar model` -> `ani-predictive-backfill-modelcar`

Changing that selector updates the control-plane classifier profile. New classifications and new incidents then use the selected predictor path without shutting down the other model.

## Notes

- `legacy-train-and-deploy-classifier` is the older compatibility path. Do not use it for the preferred demo flow.
- The modelcar branch assumes the serving service account can resolve and pull `oci://image-registry.openshift-image-registry.svc:5000/...` from the same cluster. If your cluster blocks that path, add the required registry pull secret to `model-storage-sa` before switching the UI profile to `modelcar`.
- If the live incident generation step starts returning `502` after the serving smoke check, check [Troubleshooting](./troubleshooting.md), especially the sections around image drift and serving readiness.
