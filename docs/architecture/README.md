# Architecture By Phase

This directory contains a small set of deep-dive architecture documents. They are easier to navigate when grouped into the end-to-end platform phases below.

## Phase Flow

```mermaid
flowchart TD
  P1["Phase 1<br/>Data Generation"] --> P2["Phase 2<br/>Feature Store"]
  P2 --> P3["Phase 3<br/>Model Training (KFP)"]
  P3 --> P4["Phase 4<br/>Model Registry"]
  P4 --> P5["Phase 5<br/>Model Serving"]
  P5 --> P6["Phase 6<br/>Custom Services"]
  P6 --> P7["Phase 7<br/>Real-Time Detection + RCA"]
  P7 --> P8["Phase 8<br/>Remediation"]
```

## Phase Breakdown

| Phase | Stage doc | Primary focus | Main components | Main outputs | Deep dive docs |
| --- | --- | --- | --- | --- | --- |
| Phase 1: Data Generation | [Phase 01 Overview](./phase-01-overview-data-generation.md) | Generate IMS traffic, fault conditions, and persisted raw runtime evidence | OpenIMSs IMS core, SIPp, XML scenarios, `sipp-runner`, MinIO | feature windows, scenario labels, raw logs, incident-linked evidence | [Engineering specification](./engineering-spec.md), [Incident release and offline training](./incident-release-corpus-and-offline-training.md) |
| Phase 2: Feature Store | [Phase 02 Overview](./phase-02-overview-feature-store.md) | Convert persisted runtime data into stable feature definitions and training projections | OpenShift AI Feature Store, Feast UI, entities, feature views, feature services, offline store, optional online store | live feature contracts, reusable offline training projections, managed Feature Store metadata | [Feature store training path](./feature-store-training-path.md) |
| Phase 3: Model Training (KFP) | [Phase 03 Overview](./phase-03-overview-model-training-kfp.md) | Train and evaluate anomaly models from persisted data using reproducible pipelines | Kubeflow Pipelines, `ani-feature-bundle-publish`, `ani-featurestore-train-and-register`, evaluation and export stages | bundle manifests, trained model artifacts, metrics, reproducible training and export runs | [AutoGluon training and model selection](./autogluon-training-and-model-selection.md), [Engineering specification](./engineering-spec.md), [Feature store training path](./feature-store-training-path.md), [Incident release and offline training](./incident-release-corpus-and-offline-training.md) |
| Phase 4: Model Registry | [Phase 04 Overview](./phase-04-overview-model-registry.md) | Track trained model lineage, metadata, and promotion decisions | MinIO artifact storage, compatibility registry metadata, Red Hat OpenShift AI Model Registry | model versions, lineage metadata, deployment handoff records, promotion-ready registry entries | [Feature store training path](./feature-store-training-path.md), [Incident release and offline training](./incident-release-corpus-and-offline-training.md) |
| Phase 5: Model Serving | [Phase 05 Overview](./phase-05-overview-model-serving.md) | Expose trained models through a stable inference runtime | OpenShift AI model serving, KServe, NVIDIA Triton, MLServer sklearn runtime, `ani-predictive`, `ani-predictive-fs`, `ani-predictive-fs-mlserver` | REST and gRPC inference endpoints, multiclass probability outputs, side-by-side runtime parity path | [Engineering specification](./engineering-spec.md), [Feature store training path](./feature-store-training-path.md) |
| Phase 6: Custom Services | [Phase 06 Overview](./phase-06-overview-custom-services.md) | Connect runtime data, inference, incident orchestration, and UI workflows | `feature-gateway`, `anomaly-service`, `control-plane`, `rca-service`, `demo-ui`, shared service library | end-to-end orchestration from traffic to incident state | [Engineering specification](./engineering-spec.md), [RCA and remediation](./rca-remediation.md) |
| Phase 7: Real-Time Detection + RCA | [Phase 07 Overview](./phase-07-overview-real-time-detection-and-rca.md) | Score live windows, retrieve similar incidents, explain the model decision, and generate grounded RCA | anomaly scoring path, control-plane, TrustyAI explainability, Milvus, vLLM, incident evidence, reasoning, and resolution embeddings | anomaly decisions, model explanations, RCA payloads, related evidence, operator-facing explanations | [Engineering specification](./engineering-spec.md), [AI Safety And Trust](./ai-safety-and-trust.md), [RCA and remediation](./rca-remediation.md), [TrustyAI Explainability for Incident Scoring](./trustyai-explainability-for-incident-scoring.md), [TrustyAI Guardrails for RCA](./trustyai-guardrails-for-rca.md) |
| Phase 8: Remediation | [Phase 08 Overview](./phase-08-overview-remediation.md) | Suggest, approve, execute, verify, and learn from incident response actions | control-plane workflow, Plane integration, AAP/Ansible automation, verification loop, audit trail | remediation suggestions, approvals, execution records, verification outcomes, reusable knowledge | [AI Safety And Trust](./ai-safety-and-trust.md), [RCA and remediation](./rca-remediation.md), [TrustyAI Guardrails for RCA](./trustyai-guardrails-for-rca.md), [Remediation suggestions and playbooks](./remediation-suggestions-and-playbooks.md), [AI playbook generation](./ai-playbook-generation.md), [Event-Driven Ansible](./event-driven-ansible.md) |

## Phase Files

1. [Phase 01 Overview — Data Generation](./phase-01-overview-data-generation.md)
2. [Phase 02 Overview — Feature Store](./phase-02-overview-feature-store.md)
3. [Phase 03 Overview — Model Training (KFP)](./phase-03-overview-model-training-kfp.md)
4. [Phase 04 Overview — Model Registry](./phase-04-overview-model-registry.md)
5. [Phase 05 Overview — Model Serving](./phase-05-overview-model-serving.md)
6. [Phase 06 Overview — Custom Services](./phase-06-overview-custom-services.md)
7. [Phase 07 Overview — Real-Time Detection and RCA](./phase-07-overview-real-time-detection-and-rca.md)
8. [Phase 08 Overview — Remediation](./phase-08-overview-remediation.md)

## How The Current Docs Map

- the `phase-01-overview` through `phase-08-overview` files are the fastest way to read the architecture stage by stage
- `engineering-spec.md` is the umbrella architecture reference across phases 1 to 8.
- `autogluon-training-and-model-selection.md` is the focused explainer for how the Phase 3 candidate model is trained, compared, and promoted versus the serving artifact.
- `incident-release-corpus-and-offline-training.md` is a cross-phase release and offline-training contract. It draws on persisted outputs from phases 1 to 4 and defines how they become a public corpus and offline-training input.
- `feature-store-training-path.md` is the primary deep dive for the current Feature Store, KFP, model registry, and serving rollout across phases 2 to 5.
- `rca-remediation.md` is the primary deep dive for phases 6 to 8.
- `trustyai-explainability-for-incident-scoring.md` is the focused design for adding TrustyAI feature attribution to live incident predictions before RCA and remediation.
- `ai-safety-and-trust.md` is the cross-cutting design for explainability, guardrails, monitoring, governance, and the `Safety Controls` trust surface across phases 7 and 8.
- `trustyai-guardrails-for-rca.md` is the focused design for inserting TrustyAI Guardrails into the RCA path before remediation unlock.
- `remediation-suggestions-and-playbooks.md` is the focused explainer for how Phase 8 ranks remediation actions and maps them to playbooks.
- `ai-playbook-generation.md` is the focused contract for the Kafka request and callback flow that turns RCA into an AI-generated Ansible playbook, including the TrustyAI-backed prompt guardrail boundary before Kafka publish.
- `event-driven-ansible.md` is the focused explainer for the EDA webhook and callback flow inside Phase 8.

## Which Docs To Keep

Keep both layers of documentation:

- the `phase-01-overview` through `phase-08-overview` files are short stage summaries with focused diagrams
- the larger architecture documents remain the detailed design references and should not be deleted yet

Those larger docs still contain material that the phase files intentionally do not repeat in full, including:

- runtime and repository mapping in `engineering-spec.md`
- release manifest, privacy, linkage, and quality-gate rules in `incident-release-corpus-and-offline-training.md`
- Feature Store objects, KFP contracts, serving rollout details, and remaining transition constraints in `feature-store-training-path.md`
- RCA workflow, data model, APIs, embedding strategy, and remediation execution rules in `rca-remediation.md`

## Embedding Stages In The RCA Path

Phase 7 uses multiple retrieval layers rather than one generic embedding bucket:

- runbooks and curated knowledge in `ani_runbooks`
- incident evidence embeddings in `incident_evidence`
- RCA reasoning embeddings in `incident_reasoning`
- verified remediation and outcome embeddings in `incident_resolution`

This separation keeps retrieval grounded by stage: evidence retrieval supports diagnosis, reasoning retrieval supports RCA context, and resolution retrieval supports remediation suggestions and verified learning.

## Suggested Reading Order

1. Read [Engineering specification](./engineering-spec.md) for the end-to-end platform.
2. Read [Incident release and offline training](./incident-release-corpus-and-offline-training.md) for persisted release data, dataset policy, and offline-training inputs.
3. Read [Feature store training path](./feature-store-training-path.md) for phases 2 to 5.
4. Read [RCA and remediation](./rca-remediation.md) for phases 6 to 8.
5. Read [TrustyAI Explainability for Incident Scoring](./trustyai-explainability-for-incident-scoring.md) for the proposed feature-attribution layer between prediction and RCA.
6. Read [AI Safety And Trust](./ai-safety-and-trust.md) for the cross-cutting trust architecture that ties explainability, guardrails, monitoring, and governance together.
7. Read [TrustyAI Guardrails for RCA](./trustyai-guardrails-for-rca.md) for the proposed safety boundary between RCA generation and remediation unlock.
8. Read [Remediation suggestions and playbooks](./remediation-suggestions-and-playbooks.md) for the current ranking and playbook-mapping flow.
9. Read [AI playbook generation](./ai-playbook-generation.md) for the playbook generation request and callback contract.
10. Read [Event-Driven Ansible](./event-driven-ansible.md) for the event-driven automation path in remediation.
