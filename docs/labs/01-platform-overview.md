# Lab 01: Platform Overview

## Objective

Understand the deployment layout before applying manifests to a cluster.

## Demo narrative

The demo shows an IMS assurance workflow running natively on OpenShift:

- IMS control-plane services emit traffic and operational signals
- SIPp generates both nominal and anomalous scenarios
- feature windows feed predictive anomaly scoring
- incidents trigger RCA enrichment backed by retrieval and LLM inference
- the operator sees the complete workflow in a thin demo console

## Core stack

- `k8s/base/ims`: IMS lab services and supporting data stores
- `k8s/base/traffic`: SIPp runner and traffic scenarios
- `k8s/base/rhoai`: OpenShift AI control-plane CRs
- `k8s/base/serving`: KServe serving runtimes and inference services
- `k8s/base/platform`: feature, anomaly, RCA, and UI services
- `k8s/base/milvus`: standalone vector database for RCA evidence retrieval

## What the customer should see

- a clean separation between telecom simulation, AI, and operator experience
- traceability from feature window to incident to RCA output
- an architecture that maps to standard OpenShift operational controls

