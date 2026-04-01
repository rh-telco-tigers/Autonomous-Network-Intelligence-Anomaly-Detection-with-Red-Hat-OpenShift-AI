# SIPp 100k Backfill

This kustomization is intentionally manual-only. It is not referenced by the demo overlay or GitOps applications, so it will never start unless you apply it directly.

## What it does

- writes feature windows into the separate dataset version `backfill-sipp-100k-v1`
- reuses the existing IMS target path `ims-pcscf.ims-demo-lab.svc.cluster.local:5060`
- disables control-plane incident emission so the demo UI and incident history are not flooded
- distributes the 100k target across the current scenario taxonomy using one-shot `Job` resources

## Start

```sh
make trigger-incident-release
```

## Watch

```sh
oc get jobs -n ims-demo-lab -l app.kubernetes.io/part-of=sipp-backfill-100k
oc get pods -n ims-demo-lab -l app.kubernetes.io/part-of=sipp-backfill-100k
```

To estimate progress in object storage:

- count objects under `pipelines/ims-demo-lab/datasets/datasets/backfill-sipp-100k-v1/feature-windows/` with your preferred S3 or MinIO client

## Stop Early

```sh
make stop-incident-release
```

## Tune

- all backfill jobs default to `parallelism: 1` to keep demo impact bounded
- if the cluster can absorb more load, raise `parallelism` on selected jobs before applying
- each pod creates `100` feature windows for its assigned scenario via `--repeat-count 100`
