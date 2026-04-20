# Phase 07 Overview — Real-Time Detection and RCA

## Purpose

This phase detects anomalies from live traffic, explains why the model predicted an incident, retrieves relevant prior knowledge, and produces grounded RCA output that operators can act on.

## Status

This is an active part of the current platform and one of the primary differentiators of the demo. `anomaly-service` now scores through KServe V2 against the deployed multiclass feature-store serving path, persists the returned class probabilities with each incident, and stores TrustyAI explanation envelopes alongside the incident record. The RCA path is also evaluated by TrustyAI guardrails before downstream remediation is unlocked.

## What This Phase Covers

- simulate or collect live traffic behavior
- score live windows against the deployed multiclass anomaly model through remote KServe inference
- attach TrustyAI feature attribution to the predicted incident class
- create incidents when the predicted class is not `normal_operation`
- retrieve similar evidence and prior outcomes from Milvus
- generate RCA using deterministic evidence plus LLM-assisted reasoning
- evaluate RCA output through TrustyAI guardrails before remediation unlock

## Stage Diagram

```mermaid
flowchart TD
  Traffic["live SIP traffic"] --> FG["feature-gateway"]
  FG --> Window["feature window"]
  Window --> AN["anomaly-service"]
  AN --> Model["served anomaly model"]
  Model --> AN
  AN --> Expl["TrustyAI explainer"]
  Expl --> AN
  AN --> CP["control-plane incident workflow"]
  CP --> RCA["rca-service"]
  RCA --> Milvus["Milvus retrieval"]
  RCA --> LLM["vLLM / OpenAI-compatible endpoint"]
  Milvus --> RCA
  LLM --> RCA
  RCA --> Guard["TrustyAI guardrails"]
  Guard --> Result["RCA payload, explanation, and guardrail decision"]
  Result --> CP
  CP --> UI["operator UI<br/>incident detail + AI Safety & Trust"]
```

## Embedding Stages

This phase uses multiple embedding layers rather than treating all text as one generic knowledge bucket.

| Collection | What gets embedded | Why it exists |
| --- | --- | --- |
| `ani_runbooks` | curated operational guidance, stable runbooks, and category-specific KB articles | gives the retrieval layer stable operator-authored background knowledge and ensures each incident category has reusable demo-ready guidance |
| `incident_evidence` | incident facts, feature patterns, and evidence summaries | supports diagnosis from concrete observed signals |
| `incident_reasoning` | normalized RCA reasoning and explanation text | supports similarity across RCA narratives |
| `incident_resolution` | verified fixes, outcomes, and resolution summaries | supports remediation ranking and learning from successful outcomes |

## Inputs

- live feature windows
- predicted class, predicted confidence, class probabilities, top alternatives, and model metadata
- TrustyAI explainability endpoint and feature-attribution responses
- incident context from the control plane
- retrieved evidence and runbooks
- category-matched KB articles from Milvus
- TrustyAI RCA guardrail policies and provider decisions

## Outputs

- multiclass incident predictions
- incident records
- per-incident confidence and class-probability context
- persisted model explanation envelopes
- RCA payloads with evidence and recommendation fields
- RCA guardrail decisions that control remediation readiness
- retrieval context that can be shown back to operators
- clickable knowledge article links in the incident UI

## Demo and Cluster Readiness

The Milvus knowledge base is now part of the bootstrap path, not a manual demo-prep step.

- `ai/rag/runbooks/*-knowledge.json` provides category-based KB bundles
- the `rca-service` image carries the corpus into the cluster
- `milvus-bootstrap` loads the corpus into Milvus for a fresh environment
- the incident UI can request category-scoped articles and open the full article content directly

## Current Repo Touchpoints

- `services/anomaly-service/`
- `services/control-plane/`
- `services/rca-service/`
- `services/shared/explainability.py`
- `services/shared/guardrails.py`
- `services/shared/rag.py`
- `docs/architecture/rca-remediation.md`

## Why It Matters

This phase is where the platform moves beyond simple alerting. The value is not only detecting that something is wrong, but explaining why it is likely wrong using evidence that can be reviewed, challenged, and improved over time.

## Related Docs

- [Architecture by phase](./README.md)
- [Engineering specification](./engineering-spec.md)
- [AI Safety And Trust](./ai-safety-and-trust.md)
- [Phase 09 Overview — TrustyAI Integration](./phase-09-overview-trustyai-integration.md)
- [TrustyAI Explainability for Incident Scoring](./trustyai-explainability-for-incident-scoring.md)
- [TrustyAI Guardrails for RCA](./trustyai-guardrails-for-rca.md)
- [RCA and remediation](./rca-remediation.md)
