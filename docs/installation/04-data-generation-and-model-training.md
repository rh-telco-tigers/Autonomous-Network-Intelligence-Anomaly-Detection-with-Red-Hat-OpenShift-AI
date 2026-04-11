# Installation 04: Data Generation And Model Training

## Objective

Generate incident-linked data, publish the feature bundle, train and deploy the predictive model, and verify that the app is using the deployed feature-store-backed predictor.

## Before You Start

- Finish [Installation](./02-installation.md) and [Validation](./03-validation.md).
- Confirm `ani-runtime` is `Synced` / `Healthy` and the predictive `InferenceService` resources in `ani-datascience` are `READY=True`.
- Run the initial image build at least once with `make trigger-build-pipeline`.
- Use the feature-store path in this guide. The preferred serving endpoint is `ani-predictive-fs`.

If `ani-datascience` is degraded only because `llama-32-3b-instruct` is pending on missing GPU capacity, you can still continue with this predictive-model workflow.

## 1. Generate One Live Incident

Create one incident from the live dataset path:

```sh
make step-1-generate-demo-incident DEMO_INCIDENT_SCENARIO=busy_destination
```

Expected result:

- the command returns a `feature_window.dataset_version` such as `live-sipp-v1`
- the `score` block returns `scoring_mode: remote-kserve`
- the demo UI shows a new incident

## 2. Optionally Generate A Larger Training-Only Backfill

Use this only when you want extra feature windows for offline analysis or model experiments:

```sh
make step-2-backfill-training-dataset
```

Watch progress:

```sh
oc get jobs -n ani-sipp -l app.kubernetes.io/part-of=sipp-backfill-100k
oc get pods -n ani-sipp -l app.kubernetes.io/part-of=sipp-backfill-100k
```

Important:

- backfill writes to the shared dataset version `backfill-sipp-100k`
- backfill is training-only
- backfill is not a valid source for Step 3

## 3. List Dataset Versions If You Need To Inspect State

```sh
make list-incident-release-datasets
```

Use this when you want to see:

- active backfill runs
- stored dataset versions in object storage

For incident release, always use the incident-linked live dataset unless you intentionally override it with another linked dataset.

## 4. Build The Incident Release Bundle

Compile the incident-linked dataset into the incident release bundle:

```sh
make step-3-build-incident-release
```

If you need to pin a linked dataset version explicitly:

```sh
make step-3-build-incident-release INCIDENT_RELEASE_SOURCE_DATASET_VERSION=live-sipp-v1
```

Watch the trigger job and workflow:

```sh
oc get jobs -n ani-datascience
oc get wf -n ani-datascience | rg 'ani-incident-release'
```

Continue when the workflow finishes with `Succeeded`.

## 5. Publish The Feature Bundle

Publish the bundle that the feature-store training pipeline consumes:

```sh
make step-4-publish-feature-bundle
```

Watch:

```sh
oc get wf -n ani-datascience | rg 'ani-feature-bundle'
```

Continue when the publish workflow finishes with `Succeeded`.

## 6. Train, Register, And Deploy The Predictive Model

Train and deploy the feature-store model:

```sh
make step-5-train-and-deploy-classifier
```

Watch:

```sh
oc get wf -n ani-datascience | rg 'ani-featurestore-train-and-register'
oc get inferenceservice -n ani-datascience
```

This is the preferred training path. It deploys the model behind `ani-predictive-fs`, which the app uses for live classification.

## 7. Run The Serving Smoke Check

```sh
make smoke-check-featurestore-serving
```

Then inspect the latest smoke job:

```sh
oc get jobs -n ani-datascience | rg 'ani-featurestore-serving-smoke'
oc logs -n ani-datascience job/<latest-smoke-job-name>
```

Expected result:

- ready endpoint returns `200`
- smoke status is `passed`

## 8. Verify The App Uses The Deployed Predictor

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
make step-1-generate-demo-incident DEMO_INCIDENT_SCENARIO=busy_destination
```

If this succeeds and the incident shows up in the demo UI, the UI path is consuming the deployed predictive model through the live control-plane and anomaly-service flow.

## Notes

- `legacy-train-and-deploy-classifier` is the older compatibility path. Do not use it for the preferred demo flow.
- If Step 1 starts returning `502` after Step 5, check [Troubleshooting](./troubleshooting.md), especially the sections around image drift and serving readiness.
