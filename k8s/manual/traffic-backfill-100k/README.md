# SIPp 100k Backfill

This kustomization is intentionally manual-only. It is not referenced by the demo overlay or GitOps applications, so it will never start unless you apply it directly.

## What it does

- writes feature windows into a caller-selected dataset version
- reuses the existing IMS target path `ims-pcscf.ani-demo-lab.svc.cluster.local:5060`
- disables control-plane incident emission so the demo UI and incident history are not flooded
- distributes the 100k target across the current scenario taxonomy using one-shot `Job` resources

## Start

```sh
make trigger-incident-release
```

By default the target picks a timestamped dataset version such as `backfill-sipp-100k-20260401-141500` and prints it after creating the Jobs.

To keep multiple datasets separate, pass an explicit version:

```sh
make trigger-incident-release INCIDENT_RELEASE_DATASET_VERSION=backfill-sipp-100k-v2
```

## Watch

```sh
oc get jobs -n ani-demo-lab -l app.kubernetes.io/part-of=sipp-backfill-100k,ani.redhat.com/backfill-dataset-version=<dataset-version>
oc get pods -n ani-demo-lab -l app.kubernetes.io/part-of=sipp-backfill-100k,ani.redhat.com/backfill-dataset-version=<dataset-version>
```

To estimate progress in object storage:

- count objects under `pipelines/ani-demo-lab/datasets/datasets/<dataset-version>/feature-windows/` with your preferred S3 or MinIO client

## Stop Early

```sh
make stop-incident-release INCIDENT_RELEASE_DATASET_VERSION=<dataset-version>
```

Each trigger creates a fresh set of Jobs. If you reuse the same dataset version, the new run appends more feature-window objects under the same S3 prefix. Use a new dataset version when you want a separate dataset.

## Tune

- all backfill jobs default to `parallelism: 1` to keep demo impact bounded
- if the cluster can absorb more load, raise `parallelism` on selected jobs before applying
- each pod creates `100` feature windows for its assigned scenario via `--repeat-count 100`
