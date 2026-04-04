# Incident Release Workflow

This folder isolates the new release-corpus implementation from the existing live demo training flow.

It is the starting point for the `ims-incident-release` workflow described in `docs/architecture/incident-release-corpus-and-offline-training.md`.

## Layout

- `pipeline/`: KFP pipeline source for the release workflow
- `python/`: runtime code used by the pipeline containers
- `Containerfile`: runtime image definition for the release workflow
- `requirements.txt`: Python dependencies for the runtime image

## Current implementation slice

This first implementation focuses on the release path only. It does not train or deploy models.

1. export a frozen source snapshot from the control-plane API and MinIO feature-window store
2. normalize the snapshot into release-ready incident and training example artifacts
3. generate public parquet and csv outputs
4. generate a balanced convenience export with a configurable target size
5. validate core release gates
6. package the release bundle and publish it back to MinIO under a release prefix

The logical stages remain separate in `python/release_runtime.py`, but the DSPA/KFP package executes them in a single container step so the release workspace remains local for the full snapshot, normalize, validate, and publish flow.

The runtime reads the same MinIO feature-window prefix used by `services/sipp-runner` and `ai/training/train_and_register.py`, so the release corpus is anchored to persisted SIPp/OpenIMS traffic instead of a separate dummy-data path.

If control-plane incidents contain only reconstructed `feature_snapshot` data, those rows are retained for auditability but remain ineligible for balanced training export. `quality_report.json` now includes a quality scorecard with authoritative-window ratio, category coverage, normal-case ratio, eligibility ratio, and feature-variance checks so releases can show how close they are to a production-grade real-data corpus.

Current public outputs include:

- `ims_incident_history.parquet`
- `ims_incident_history.csv`
- `ims_training_examples.parquet`
- `ims_training_examples.csv`
- `ims_training_examples_balanced.parquet`
- `ims_training_examples_balanced.csv`
- `training_split_manifest.json`
- `training_split_manifest.csv`
- `schema.json`
- `label_dictionary.csv`
- `public_field_mapping.csv`
- `dataset_card.md`
- `quality_report.json`
- `release_manifest.json`
- `ims_incident_release_bundle.zip`

The follow-on slices can extend this folder with:

- Kaggle publication
- Kafka-backed incremental export or event-driven processing

## Kafka

Kafka infrastructure now lives in the shared repo layout instead of this folder:

- operator subscription: `deploy/gitops/operators/subscriptions/amq-streams.yaml`
- Kafka cluster and topics: `k8s/base/kafka/`

Those manifests follow the same general direction as the NetSentinel repository, but they are updated for what this OpenShift cluster currently offers:

- operator: `amq-streams`
- channel: `stable`
- operator version on this cluster: `3.1.0-14`
- Kafka mode: KRaft with `KafkaNodePool`
- Kafka version: `4.1.0`
- sandbox profile: single dual-role node with ephemeral storage

NetSentinel's older ZooKeeper-based Kafka manifests are not compatible with this cluster's latest AMQ Streams channel, so this folder uses the modern KRaft shape instead.

The current node-pool profile is intentionally lightweight so it comes up quickly on the sandbox. That means Kafka data is not durable across broker restarts until the storage section is switched to persistent claims.

When `KAFKA_ENABLED=true`, the runtime mirrors snapshot and publish events to Kafka without changing the source-of-truth model:

- incidents exported from the control-plane snapshot -> `ims-incidents-bronze`
- feature windows exported from MinIO -> `ims-feature-windows-bronze`
- published release artifact notifications -> `ims-release-artifacts`

Object storage and `release_manifest.json` remain authoritative. Kafka is only an integration and notification surface for downstream consumers.

## Quick start

Install the operator:

```bash
oc apply -f deploy/gitops/operators/subscriptions/amq-streams.yaml
```

Wait for the operator to settle, then create the Kafka cluster and topics:

```bash
oc apply -k k8s/base/kafka
```

The release runtime entry point is:

```bash
python incident-release/python/release_cli.py --help
```
