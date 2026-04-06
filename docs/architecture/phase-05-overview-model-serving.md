# Phase 05 Overview — Model Serving

## Purpose

This phase exposes the selected anomaly model through a stable inference runtime so live platform services can predict one canonical incident class per traffic window, plus confidence, probabilities, and top alternatives.

## Status

This phase is live through the legacy `ims-predictive` Triton path plus the feature-store rollout endpoints `ims-predictive-fs` (Triton) and `ims-predictive-fs-mlserver` (MLServer). Triton remains the default remote-scoring runtime; MLServer is live as a side-by-side parity path.

## What This Phase Covers

- package the winning model for serving
- publish versioned and stable-alias artifacts into object storage
- expose the runtime through OpenShift AI model serving
- provide stable REST and gRPC inference endpoints for multiclass probabilities and derived incident signals
- support side-by-side serving when a new runtime path is introduced

## Stage Diagram

```mermaid
flowchart TD
  Registry["approved model version"] --> TritonRepo["Triton model repository"]
  Registry --> MLRepo["MLServer model bundle"]
  TritonRepo --> MinIO["MinIO model storage"]
  MLRepo --> MinIO
  MinIO --> TritonServe["KServe / Triton InferenceService"]
  MinIO --> MLServe["KServe / MLServer InferenceService"]
  TritonServe --> API["REST and gRPC endpoints"]
  MLServe --> API
  API --> Consumers["anomaly-service and platform consumers"]
```

## Inputs

- approved source model artifact and metadata
- serving compatibility contract including class order, normal-class identity, and probability output shape
- runtime configuration for KServe, Triton, and MLServer paths

## Outputs

- deployed inference runtimes
- stable inference endpoints
- serving metadata that downstream services can trust, including class labels and taxonomy version
- side-by-side comparison path across Triton and MLServer

## Current Repo Touchpoints

- `ai/training/featurestore_train.py`
- `services/shared/model_store.py`
- `k8s/base/serving/`
- `docs/architecture/feature-store-training-path.md`
- `docs/architecture/engineering-spec.md`

## Why It Matters

Serving is where model lifecycle work becomes operational behavior. If serving contracts drift from training or registry metadata, anomaly scoring results become difficult to interpret and troubleshoot.

## Related Docs

- [Architecture by phase](./README.md)
- [Engineering specification](./engineering-spec.md)
- [Feature store training path](./feature-store-training-path.md)
