# Phase 05 Overview — Model Serving

## Purpose

This phase exposes the selected anomaly model through a stable inference runtime so live platform services can score traffic windows consistently.

## Status

This phase is live in the current demo through the `ims-predictive` serving path. A side-by-side Feature-Store-driven serving path is planned for future rollout.

## What This Phase Covers

- package the winning model for serving
- publish the model into the Triton repository layout
- expose the runtime through OpenShift AI model serving
- provide stable REST and gRPC inference endpoints
- support side-by-side serving when a new path is introduced

## Stage Diagram

```mermaid
flowchart LR
  Registry["approved model version"] --> Repo["Triton model repository"]
  Repo --> MinIO["MinIO model storage"]
  MinIO --> Serve["KServe / Triton InferenceService"]
  Serve --> API["REST and gRPC endpoints"]
  API --> Consumers["anomaly-service and platform consumers"]
```

## Inputs

- approved model artifact and metadata
- serving compatibility contract
- runtime configuration for KServe and Triton

## Outputs

- deployed inference runtime
- stable inference endpoints
- serving metadata that downstream services can trust

## Current Repo Touchpoints

- `ai/models/`
- `k8s/`
- `docs/architecture/feature-store-training-path.md`
- `docs/architecture/engineering-spec.md`

## Why It Matters

Serving is where model lifecycle work becomes operational behavior. If serving contracts drift from training or registry metadata, anomaly scoring results become difficult to interpret and troubleshoot.

## Related Docs

- [Architecture by phase](./README.md)
- [Engineering specification](./engineering-spec.md)
- [Feature store training path](./feature-store-training-path.md)
