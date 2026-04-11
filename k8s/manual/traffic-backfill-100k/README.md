# SIPp 100k Backfill

This kustomization is intentionally manual-only. It is not referenced by the demo overlay or GitOps applications, so it will never start unless you apply it directly.

This path is for dataset backfill only. It does not create demo incidents because every Job sets `SIPP_EMIT_CONTROL_PLANE_INCIDENT=false`.

Backfill now always writes into one shared dataset version: `backfill-sipp-100k`.

## Recommended Workflow

Use these make targets when you want to generate live incidents, prepare separate training-only backfill data, build an incident release from incident-linked windows, and deploy the model the app uses:

1. Create one live incident in the app:

```sh
make step-1-generate-demo-incident DEMO_INCIDENT_SCENARIO=busy_destination
```

2. Optionally generate a larger training-only dataset in MinIO:

```sh
make step-2-backfill-training-dataset
```

3. Discover active backfill dataset versions later if you need them for training analysis:

```sh
make list-incident-release-datasets
```

4. Compile the incident-release bundle from the incident-linked live dataset:

```sh
make step-3-build-incident-release
```

5. Publish the feature-store-ready bundle:

```sh
make step-4-publish-feature-bundle
```

6. Train, register, and deploy the classifier the app uses for live scoring:

```sh
make step-5-train-and-deploy-classifier
```

The preferred serving path is `ani-predictive-fs`. The app uses that feature-store-backed endpoint for classification. The older `legacy-train-and-deploy-classifier` target exists only as a compatibility path.

## What it does

- writes feature windows into a caller-selected dataset version
- reuses the existing IMS target path `ims-pcscf.ani-sipp.svc.cluster.local:5060`
- disables control-plane incident emission so the demo UI and incident history are not flooded
- distributes the 100k target across the current scenario taxonomy using one-shot `Job` resources

## Start

```sh
make step-2-backfill-training-dataset
```

The target always writes into the shared dataset version `backfill-sipp-100k`. Custom backfill dataset versions are disabled.

To discover dataset versions later, list both active backfill runs and stored MinIO datasets:

```sh
make list-incident-release-datasets
```

For incident release, do not pass the backfill dataset version into Step 3. Incident release must use the dataset whose feature windows actually created the live incidents. By default that is `live-sipp-v1`:

```sh
make step-3-build-incident-release
```

If you need to override it, only use another incident-linked dataset version:

```sh
make step-3-build-incident-release INCIDENT_RELEASE_SOURCE_DATASET_VERSION=live-sipp-v1
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

- `trigger-incident-release` -> `step-2-backfill-training-dataset`
- `trigger-incident-release-pipeline` -> `step-3-build-incident-release`
- `trigger-feature-bundle-pipeline` -> `step-4-publish-feature-bundle`
- `trigger-featurestore-pipeline` -> `step-5-train-and-deploy-classifier`

Each trigger creates a fresh set of Jobs. If you reuse the same dataset version, the new run appends more feature-window objects under the same S3 prefix. Use a new dataset version when you want a separate dataset.

## Tune

- all backfill jobs default to `parallelism: 1` to keep demo impact bounded
- if the cluster can absorb more load, raise `parallelism` on selected jobs before applying
- each pod creates `100` feature windows for its assigned scenario via `--repeat-count 100`
