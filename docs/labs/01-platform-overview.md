# Lab 01: Platform Overview

## Objective

Understand the deployment layout and the fresh-cluster bring-up sequence before applying manifests.

## Demo narrative

The demo shows an IMS assurance workflow running natively on OpenShift:

- IMS control-plane services emit traffic and operational signals
- SIPp and the background pulse generate both nominal and anomalous scenarios
- feature windows feed multiclass predictive anomaly scoring
- incidents trigger RCA enrichment backed by retrieval and LLM inference
- the operator can open a detailed execution trace from pre-model feature fetch through model and RCA/LLM packets

## Core stack

- `k8s/base/ims`: IMS lab services and supporting data stores
- `k8s/base/traffic`: SIPp runner and scheduled traffic scenarios
- `k8s/base/platform`: control-plane, feature-gateway, anomaly-service, RCA service, and demo UI
- `k8s/base/serving`: KServe serving runtimes and the feature-store-backed Triton and MLServer endpoints
- `k8s/base/milvus`: standalone vector database for RCA evidence retrieval
- `k8s/base/rhoai`: OpenShift AI control-plane CRs
- `k8s/base/feature-store`: Feast Feature Store instance and bootstrap job
- `k8s/base/kafka`: Kafka resources used by the release pipeline path
- `k8s/base/kfp`: DSPA and KFP bootstrap assets

## Fresh-Cluster Sequence

- Labs 01-03 bring up the GitOps-managed demo overlay and the internal image build pipeline.
- The demo overlay now includes the OpenShift AI, Feature Store, Kafka, and KFP resources needed for the full bring-up path.
- Lab 04 validates that those AI resources reconciled correctly and enables optional integrations such as live LLM-backed RCA only when needed.
- Lab 05 validates the live operator flow, including scheduled incident generation, multiclass scoring, detailed trace inspection, RCA, and remediation.

## What the customer should see

- a clean separation between telecom simulation, AI, and operator experience
- traceability from feature window to incident, model decision, and RCA output
- an architecture that maps to standard OpenShift operational controls

