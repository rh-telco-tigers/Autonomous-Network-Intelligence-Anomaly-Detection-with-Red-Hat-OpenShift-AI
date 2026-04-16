# SIPp 100k Backfill

This kustomization is intentionally manual-only. It is not referenced by the demo overlay or GitOps applications, so it will never start unless you apply it directly.

This path is for dataset backfill only. It does not create demo incidents because every Job sets `SIPP_EMIT_CONTROL_PLANE_INCIDENT=false`.

Backfill now always writes into one shared dataset version: `backfill-sipp-100k`.

## Recommended Workflow

Use these make targets when you want to generate live incidents, prepare separate training-only backfill data, build an incident release from incident-linked windows, or train a separate backfill model:

1. Create one live incident in the app:

```sh
make live-step-1-generate-demo-incident DEMO_INCIDENT_SCENARIO=busy_destination
```

2. Generate the shared training-only backfill dataset in MinIO:

```sh
make backfill-step-1-generate-training-dataset
```

3. Build the backfill feature bundle with Parquet and CSV exports:

```sh
make backfill-step-2-build-feature-bundle
```

4. Train and register the backfill AutoGluon model:

```sh
make backfill-step-3-train-and-register-classifier
```

5. Create or refresh the backfill serving endpoint:

```sh
make backfill-step-4-activate-serving-endpoint
```

6. Smoke-check the backfill serving endpoint:

```sh
make backfill-step-5-smoke-check-serving
```

7. Optional: publish the trained backfill model as an OCI modelcar image in the internal registry:

```sh
make backfill-modelcar-step-1-publish-image
```

8. Optional: smoke-check the modelcar predictor path:

```sh
make backfill-modelcar-step-2-smoke-check-serving
```

9. Discover active backfill dataset versions later if you need them for training analysis:

```sh
make list-incident-release-datasets
```

10. Compile the incident-release bundle from the incident-linked live dataset:

```sh
make live-step-2-build-incident-release
```

11. Publish the feature-store-ready live bundle:

```sh
make live-step-3-publish-feature-bundle
```

12. Train, register, and deploy the classifier the app uses for live scoring:

```sh
make live-step-4-train-and-deploy-classifier
```

The preferred live serving path is `ani-predictive-fs`. The separate backfill serving path is `ani-predictive-backfill`. The OCI-packaged variant is `ani-predictive-backfill-modelcar`. All three endpoints can stay active at the same time, and the demo UI can switch classification between them. The older `legacy-train-and-deploy-classifier` target exists only as a compatibility path.

## What it does

- writes feature windows into a caller-selected dataset version
- reuses the existing IMS target path `ims-pcscf.ani-sipp.svc.cluster.local:5060`
- disables control-plane incident emission so the demo UI and incident history are not flooded
- distributes the 100k target across the current scenario taxonomy using one-shot `Job` resources

## Start

```sh
make backfill-step-1-generate-training-dataset
```

The target always writes into the shared dataset version `backfill-sipp-100k`. Custom backfill dataset versions are disabled. Continue with `make backfill-step-2-build-feature-bundle` when the Jobs complete.

Once `make backfill-step-3-train-and-register-classifier` finishes, you can either keep using the MinIO-backed backfill serving path or publish the same trained model as a reusable OCI modelcar image with `make backfill-modelcar-step-1-publish-image`.

To discover dataset versions later, list both active backfill runs and stored MinIO datasets:

```sh
make list-incident-release-datasets
```

For incident release, do not pass the backfill dataset version into Step 3. Incident release must use the dataset whose feature windows actually created the live incidents. By default that is `live-sipp-v1`:

```sh
make live-step-2-build-incident-release
```

If you need to override it, only use another incident-linked dataset version:

```sh
make live-step-2-build-incident-release INCIDENT_RELEASE_SOURCE_DATASET_VERSION=live-sipp-v1
```

The backfill dataset `backfill-sipp-100k` is training-only and is rejected by Step 3.

## Watch

```sh
oc get jobs -n ani-sipp -l app.kubernetes.io/part-of=sipp-backfill-100k,ani.redhat.com/backfill-dataset-version=<dataset-version>
oc get pods -n ani-sipp -l app.kubernetes.io/part-of=sipp-backfill-100k,ani.redhat.com/backfill-dataset-version=<dataset-version>
```

To estimate progress in object storage:

- count objects under `pipelines/ani-datascience/datasets/datasets/<dataset-version>/feature-windows/` with your preferred S3 or MinIO client

## Stop Early

```sh
make stop-incident-release INCIDENT_RELEASE_DATASET_VERSION=<dataset-version>
```

You can also stop the shared backfill run directly:

```sh
make stop-incident-release
```

Compatibility aliases still work:

- `step-1-generate-demo-incident` -> `live-step-1-generate-demo-incident`
- `step-2-backfill-training-dataset` -> `backfill-step-1-generate-training-dataset`
- `step-3-build-incident-release` -> `live-step-2-build-incident-release`
- `step-4-publish-feature-bundle` -> `live-step-3-publish-feature-bundle`
- `step-5-train-and-deploy-classifier` -> `live-step-4-train-and-deploy-classifier`
- `smoke-check-featurestore-serving` -> `live-step-5-smoke-check-serving`
- `trigger-incident-release` -> `backfill-step-1-generate-training-dataset`
- `trigger-incident-release-pipeline` -> `live-step-2-build-incident-release`
- `trigger-feature-bundle-pipeline` -> `live-step-3-publish-feature-bundle`
- `trigger-featurestore-pipeline` -> `live-step-4-train-and-deploy-classifier`

Each trigger creates a fresh set of Jobs. If you reuse the same dataset version, the new run appends more feature-window objects under the same S3 prefix. Use a new dataset version when you want a separate dataset.

## Tune

- all backfill jobs default to `parallelism: 1` to keep demo impact bounded
- if the cluster can absorb more load, raise `parallelism` on selected jobs before applying
- each pod creates `100` feature windows for its assigned scenario via `--repeat-count 100`
