# IMS Anomaly Detection and RCA Platform on OpenShift AI

## Engineering Specification (v1.0)

## 1. Overview

### 1.1 Problem Statement

IMS environments generate:

- high-volume SIP signaling traffic
- low-frequency but operationally significant anomalies
- failure propagation across multiple network functions
- slow, manual root cause analysis (RCA)

Conventional monitoring stacks are typically:

- rule-driven, resulting in high false-positive rates
- siloed, which limits cross-component correlation
- reactive, with limited support for repeatable fault analysis

### 1.2 Proposed Solution

Build a cloud-native service assurance platform that:

1. Simulates IMS behavior by using OpenIMSs and SIPp
2. Detects anomalous behavior with baseline ML models and AutoML
3. Produces RCA output by combining topology context, retrieval, and LLM inference
4. Supports operator review before any remediation action is executed

### 1.3 Architectural Principle

Partition the system into four primary planes:

- IMS Lab: telecom system under test
- Traffic and Fault Engine: workload and fault generation
- Intelligence Plane: data processing, model training, inference, and RCA
- Experience Plane: operator-facing APIs and UI

This separation keeps data generation, model lifecycle management, and operator workflows independently testable and deployable.

## 2. Goals and Non-Goals

### Goals

- Simulate representative IMS signaling behavior
- Inject repeatable traffic and fault scenarios
- Establish an MLOps workflow with KFP and model registry integration
- Compare AutoGluon AutoML outputs with baseline models
- Support near-real-time anomaly scoring
- Generate RCA output using RAG, vLLM, and structured evidence
- Provide a lightweight operator console for demonstration workflows

### Non-Goals

- Full deep packet inspection
- Fully autonomous remediation
- Production-grade NOC replacement
- Advanced topology rendering
- Graph-neural-network-based RCA in the initial phase

## 3. System Architecture

### 3.1 High-Level Architecture

```text
[ OpenIMSs ] <--- SIP signaling ---> [ SIPp ]

        |
        v

[ Data Ingestion / Feature Windows ]

        |
        v

[ OpenShift AI ]

   |- KFP Pipelines
   |- AutoGluon AutoML
   |- Baseline Model Training
   |- Model Registry
   |- Model Serving (KServe)

        |
        v

[ RCA Layer ]

   |- Milvus
   |- vLLM
   |- Retrieval and Prompt Assembly

        |
        v

[ API Layer ]

        |
        v

[ Demo Console UI ]

        |
        v

[ Slack / Ansible / Jira (Optional) ]
```

### 3.2 Core Domain Entities

The platform operates on a small set of first-class entities. These entities define the contracts between data processing, inference, RCA, and operator workflows.

#### Incident

```yaml
incident:
  id: string
  timestamp: datetime
  node_id: string
  anomaly_score: float
  anomaly_type: string
  model_version: string
  feature_window_id: string
  feature_snapshot: object
  status: [open, acknowledged, resolved]
```

#### FeatureWindow

```yaml
feature_window:
  window_id: string
  start_time: timestamp
  end_time: timestamp
  duration: 30s
  node_id: string
  source: string
  scenario_name: string
  label: 0_or_1
  anomaly_type: string
  features: map<string, float>
  schema_version: string
```

#### ModelVersion

```yaml
model:
  id: string
  version: string
  type: [baseline, autogluon]
  dataset_version: string
  feature_schema_version: string
  metrics:
    precision: float
    recall: float
    f1: float
```

### 3.3 Ownership Boundaries

Each entity has a clear producer and system of record.

| Entity | Producer | System of Record | Consumers |
| --- | --- | --- | --- |
| FeatureWindow | ingestion and feature pipeline | dataset store | training pipeline, scoring services |
| ModelVersion | KFP training pipeline | model registry | anomaly-service, UI, deployment automation |
| Incident | anomaly-service | incident store | rca-service, UI, collaboration, automation |
| RCAResult | rca-service | RCA store or incident enrichment layer | UI, approval workflow, audit pipeline |

## 4. Workstream Breakdown

### 4.1 IMS Lab (OpenIMSs)

#### Responsibilities

- Deploy core IMS functions
- Expose signaling endpoints for test execution
- Emit logs, metrics, and operational events

#### Minimum Components

- P-CSCF
- S-CSCF
- HSS

#### Outputs

- SIP signaling exchanges
- node-level metrics
- fault and degradation signals

### 4.2 Traffic and Fault Engine (SIPp)

#### Responsibilities

- Generate nominal traffic patterns
- Inject abnormal and degraded scenarios
- Produce labeled feature windows from real SIPp-driven IMS traffic

#### Scenario Types

| Type | Example |
| --- | --- |
| Normal | steady REGISTER and INVITE traffic |
| Stress | burst load |
| Fault | malformed SIP messages |
| Degradation | latency injection |
| Regression | replay of known scenarios |

#### Output Contract

```json
{
  "scenario": "registration_storm",
  "timestamp": "...",
  "expected_label": "anomaly",
  "dataset_version": "live-sipp-v1"
}
```

### 4.3 AI and MLOps (OpenShift AI)

#### 4.3.0 Data Flow Contract

```text
IMS (OpenIMSs)
  -> SIP signaling events from SIPp scenarios
  -> captured as labeled feature windows
  -> stored as dataset version X
  -> consumed by KFP pipelines
  -> produces model version Y
  -> deployed to inference service
  -> generates incidents
  -> consumed by RCA service
```

The demo implementation target is:

- SIPp CronJobs generate real traffic against OpenIMSs
- the scenario runner emits labeled feature windows into MinIO
- KFP `ingest-data` reads stored feature windows first
- synthetic data remains only as a bootstrap fallback when the live dataset is empty or undersized

#### 4.3.1 Feature Window Model

All raw traffic and platform signals are normalized into time-windowed feature sets.

Example schema:

```yaml
window:
  start: timestamp
  duration: 30s

features:
  register_rate: float
  invite_rate: float
  bye_rate: float
  error_4xx_ratio: float
  error_5xx_ratio: float
  latency_p95: float
  retransmission_count: int
  inter_arrival_mean: float
  payload_variance: float
  node_id: string
  node_role: string

labels:
  anomaly: true_or_false
  anomaly_type: optional
```

#### 4.3.1.1 Feature Schema Versioning

- Every feature schema is versioned
- Models are tightly coupled to the feature schema version used during training
- Schema changes require retraining before promotion

```text
feature_schema_v1 -> model_v1
feature_schema_v2 -> retrain required
```

#### 4.3.2 Model Selection Strategy

The platform maintains two model paths:

- a baseline anomaly model, required as the fallback path
- an AutoML path used to generate and rank candidate models

AutoGluon is used as a candidate model generation engine, not as an unbounded primary detection mechanism.

AutoGluon is responsible for:

- training multiple model families
- ranking candidates using evaluation metrics
- producing optimized models for tabular IMS feature data

The system enforces:

- a baseline model is always available as fallback
- AutoGluon outputs must pass evaluation gates before promotion

#### 4.3.3 Training Modes

| Mode | Description |
| --- | --- |
| Unsupervised | autoencoder training without explicit labels |
| Weakly supervised | SIPp scenario labels used as supervision |
| Supervised | future extension using incident-labeled data |
| Forecasting | deviation detection against expected trends |

#### 4.3.4 KFP Pipeline

```yaml
pipeline:
  name: ims-anomaly-automl
  steps:
    - ingest-data
    - feature-engineering
    - label-generation
    - train-baseline
    - train-autogluon
    - evaluate
    - select-best
    - register-model
    - deploy
```

#### 4.3.5 Model Evaluation Gate

A model is eligible for promotion only if it satisfies the required evaluation gates.

```yaml
conditions:
  min_precision: 0.80
  max_false_positive_rate: threshold
  latency_p95: <defined_limit>
  stability_score: acceptable
```

#### 4.3.6 Model Registry

Track the following metadata:

- model version
- dataset version
- feature schema version
- evaluation metrics
- threshold configuration
- training mode

Promotion path:

```text
dev -> test -> prod
```

#### 4.3.7 Serving Architecture

##### Modes

| Mode | Description |
| --- | --- |
| synchronous | real-time scoring via API |
| batch | scheduled scoring over feature windows |
| streaming (future) | event-driven scoring over a message bus |

##### Services

| Service | Purpose |
| --- | --- |
| anomaly-service | synchronous or batch scoring and incident creation |
| rca-service | RCA generation, retrieval, and evidence packaging |

##### Inference Flow

```text
FeatureWindow -> /score -> anomaly decision -> Incident created -> RCA triggered
```

##### API Contracts

###### POST `/score`

```json
{
  "features": {}
}
```

Response:

```json
{
  "anomaly_score": 0.91,
  "is_anomaly": true,
  "incident_id": "uuid"
}
```

##### Incident Creation Contract

```json
{
  "incident_id": "uuid",
  "model_version": "v3",
  "feature_window_id": "fw-123",
  "anomaly_score": 0.91,
  "created_at": "..."
}
```

###### POST `/rca`

```json
{
  "incident_id": "...",
  "context": {}
}
```

Response:

```json
{
  "root_cause": "HSS latency",
  "confidence": 0.84,
  "evidence": [
    {
      "type": "metric",
      "reference": "hss-latency-p95",
      "weight": 0.61
    },
    {
      "type": "log",
      "reference": "hss-timeout-log",
      "weight": 0.39
    }
  ],
  "recommendation": "increase connection pool"
}
```

## 5. RCA Architecture (vLLM and Milvus)

### 5.1 Data Sources for Milvus

Milvus is the retrieval layer for RCA. It stores semantic knowledge that helps explain an anomaly. It is not the system of record for raw IMS traffic, feature windows, or metric time series.

Primary source categories:

- runbooks
- vendor documentation
- historical incident records and incident logs
- topology metadata
- SIP traces and signaling patterns

Operational guidance for indexed content:

- runbooks are stored as chunked operational procedures and troubleshooting steps
- vendor documentation is stored as chunked reference material for IMS behavior, error handling, and configuration guidance
- historical incidents are stored as symptom, root cause, and resolution narratives with incident metadata
- topology metadata is stored as dependency-aware service relationships and path context
- logs and SIP traces are stored as selective extracted snippets or summarized patterns, not as full raw streams

Representative incident record:

```json
{
  "incident_id": "123",
  "symptoms": ["high 5xx", "latency spike"],
  "root_cause": "HSS overload",
  "resolution": "increase pool size"
}
```

### 5.1.1 Milvus Scope Boundaries

Milvus must not be used as a general-purpose telemetry store.

Explicit exclusions:

- raw SIP traffic streams
- full metrics time series
- feature windows used by the ML pipeline
- model outputs and scoring history

These data types remain in their respective operational systems, such as Prometheus, the feature store or dataset layer, and the incident or audit store.

### 5.2 Role of Milvus

Milvus functions as the RCA knowledge retrieval layer.

Clean separation of responsibilities:

| Layer | Purpose |
| --- | --- |
| predictive model | detect anomaly |
| Milvus | retrieve relevant RCA context |
| LLM | reason over retrieved context and generate RCA output |

For the demo profile, the Milvus corpus should remain intentionally small and preloaded. A few hundred documents is sufficient.

Recommended logical collections:

- `runbooks`
- `incidents`
- `topology`

Optional collections:

- `log-snippets`
- `sip-patterns`
- `vendor-docs`

### 5.3 Processing Flow

```text
1. anomaly detected
2. incident created
3. RCA query built from node_id, error patterns, and feature deviations
4. query embedded
5. Milvus searched
6. supporting context retrieved
7. prompt assembled
8. LLM inference executed
9. structured RCA output returned
```

Retrieved context should preferentially include:

- operational runbooks
- similar historical incidents
- topology relationships
- selective supporting logs or SIP patterns

### 5.4 Prompt Inputs

Prompt construction includes:

- alarm and incident data
- runtime context
- topology relationships
- retrieved reference material

The output must be grounded in retrieved evidence and returned in a structured schema.

### 5.5 RCA Output Contract

RCA responses must conform to a strict output schema.

```json
{
  "root_cause": "string",
  "confidence": 0.0,
  "evidence": [
    {
      "type": "metric|log|doc",
      "reference": "string",
      "weight": 0.0
    }
  ],
  "recommendation": "string"
}
```

### 5.6 RCA Validation Rules

- RCA output must include at least two evidence sources
- RCA output must reference retrieved documents or runtime artifacts
- Confidence must be derived from a defined scoring method rather than free-form generation

## 6. Demo Console

### 6.1 Design Principle

The UI is a thin orchestration layer. It should expose system state and operator actions without attempting to replace native OpenShift observability or administration interfaces.

### 6.2 Screens

#### 1. Overview

- traffic status
- anomaly count
- active incidents
- deployed model version

#### 2. Incident Detail

- anomaly score
- impacted nodes
- RCA output
- evidence set
- recommended action

#### 3. MLOps View

- pipeline runs
- model versions
- deployment status

#### 4. Action and Collaboration

- send incident to Slack
- open Jira ticket
- approve remediation step

## 7. Automation Layer

### 7.1 Candidate Actions

- quarantine IMSI
- apply rate limiting
- scale S-CSCF

### 7.2 Execution Flow

```text
RCA recommendation -> operator approval -> automation execution
```

## 8. Observability

### Metrics

- anomaly rate
- false-positive rate
- model inference latency
- RCA confidence distribution
- pipeline success rate

### Logs

- inference logs
- RCA request and response logs
- pipeline execution logs

## 9. Security and Invariants

### 9.1 Security Considerations

- API access requires authentication
- model endpoints are namespace-isolated
- RCA data access is scoped per project or tenant
- audit logs are mandatory for inference, RCA, and automation actions

### 9.2 Invariants

- A model must be registered before it can be served
- The feature schema must be versioned and traceable
- RCA output must include both confidence and evidence
- LLM inference must not be used for primary anomaly detection
- Remediation actions require explicit human approval

## 10. Failure Modes

| Failure Mode | Mitigation |
| --- | --- |
| noisy data | smoothing and feature stabilization |
| model drift | retraining and threshold review |
| hallucinated RCA | retrieval grounding and evidence checks |
| missing features | fallback rules and degraded inference mode |
| false positives | threshold tuning and model comparison |

## 11. Repository Structure

```text
IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI/
  ai/
    models/
    pipelines/
    rag/
    registry/
    training/
  automation/
  docs/
    architecture/
    labs/
  k8s/
    base/
    overlays/
  lab-assets/
    sipp/
  services/
    control-plane/
    feature-gateway/
    anomaly-service/
    rca-service/
    demo-ui/
```

## 12. Phased Delivery Plan

### Phase 1: Foundation

- deploy OpenIMSs
- implement SIPp scenarios
- build feature generation pipeline
- train baseline and AutoGluon models
- register and serve selected model
- expose anomaly scoring endpoint

### Phase 2: RCA

- deploy Milvus
- deploy vLLM
- implement RCA service and retrieval flow

### Phase 3: Experience

- build demo UI
- wire end-to-end incident workflow
- integrate Slack and Jira actions

### Phase 4: Automation

- integrate Ansible-based execution
- add approval workflow for remediation actions

## 13. Demo Flow

1. Deploy the OpenIMSs lab
2. Start baseline SIP traffic through SIPp
3. Persist labeled feature windows from the SIPp/OpenIMS run into MinIO
4. Inject a fault scenario such as a registration storm
5. Generate feature windows from the resulting traffic
6. Call `/score` and detect the anomaly
7. Create an Incident object
8. Trigger the RCA service
9. Retrieve supporting context from Milvus
10. Generate structured RCA output through vLLM
11. Display the anomaly, evidence, and recommendation in the UI
12. Route the incident to Slack or an approval workflow
13. Optionally execute a remediation action through Ansible

## 14. Acceptance Criteria

### Phase 1

- anomaly detection identifies real SIPp-driven fault scenarios
- pipeline runs successfully end to end
- selected model is registered and deployed

### Phase 2

- RCA output is generated with evidence references
- retrieval behavior is visible and auditable

### Phase 3

- UI presents the full detection-to-RCA flow
- Slack or Jira integration can be triggered from the console

### 14.1 Demo Access Inventory

The demo environment publishes a small set of known access points and credentials so the runbook does not depend on ad hoc discovery during customer delivery.

| Component | Access Pattern | Credentials |
| --- | --- | --- |
| Demo Console UI | OpenShift route for `demo-ui` | none |
| API services | OpenShift routes for `feature-gateway`, `anomaly-service`, `control-plane`, and `rca-service` | `demo-token` (admin), `demo-operator-token` (operator), `demo-viewer-token` (viewer) |
| MinIO console | OpenShift route for `model-storage-minio-console` | `minioadmin` / `minioadmin` |
| Milvus UI (Attu) | OpenShift route for `milvus-attu` | no separate UI username or password in this demo |
| Gitea | OpenShift route for `gitea-gitea` | `gitadmin` / `GiteaAdmin123!` |
| Slack and Jira actions | control-plane demo relay when external endpoints are not configured | no additional credentials in demo relay mode |
| Automation approvals | control-plane approval endpoint with simulated execution by default | `demo-token` |

## 15. Positioning

This PoC demonstrates:

- telecom-oriented data simulation
- a complete MLOps lifecycle instead of notebook-only experimentation
- AutoML with model governance
- GenAI applied to RCA rather than primary detection
- an architecture that maps cleanly onto OpenShift AI

## 16. Key Statement

> This PoC is not a model showcase. It is an engineering demonstration of an AI-assisted telecom operations platform.
