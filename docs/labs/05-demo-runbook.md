# Lab 05: Demo Runbook

## Objective

Run the end-to-end demo in a way that is easy for an end user to follow, from real traffic generation through anomaly detection, incident creation, and RCA.

## The One-Sentence Story

This demo shows how real SIP traffic sent through an IMS stack is converted into feature windows, used to train an anomaly model, scored in real time, and tied to a transparent RCA workflow.

## Audience Outcome

By the end of the demo, the audience should understand four things:

1. The platform is using real IMS traffic, not only synthetic sample rows.
2. The anomaly model and the generative RCA path are separate and traceable.
3. Every decision can be linked back to a feature window, dataset version, and model version.
4. The whole flow runs as cluster services and pipelines, not as one-off notebook work.

## Before You Start

Make sure these items are ready before you present:

- the demo UI route is reachable
- the IMS lab workloads are healthy
- SIPp-generated feature windows exist in MinIO under `live-sipp-v1`
- a recent KFP run has completed successfully
- the predictive model is served and the RCA path is reachable

If time is short, check these first:

```sh
oc get deploy -n ims-demo-lab
oc get workflow -n ims-demo-lab
oc get inferenceservice -n ims-demo-lab
```

## Recommended Demo Flow

### 1. Open With The Business Problem

Start in the demo UI and explain the problem in simple terms:

> Telecom platforms generate large volumes of signaling activity. The goal of this platform is to detect abnormal behavior early, link it to evidence, and guide the operator toward the next action.

Keep the opening focused on the outcome, not the implementation.

### 2. Explain The Four Planes

Describe the system in plain language:

- IMS plane
  - where real SIP traffic is processed
- AI and model-serving plane
  - where feature windows are used to train and serve the anomaly model
- control plane
  - where incidents, approvals, and actions are tracked
- RCA plane
  - where evidence is retrieved and the explanation is generated

### 3. Show That The Traffic Is Real

Move to OpenShift and show the IMS workloads plus the SIPp runners.

What to say:

> We are not scoring fake API payloads. SIPp is sending real scenario traffic into OpenIMSs, and the platform converts the outcome of those runs into labeled feature windows.

This is the point where you establish credibility.

### 4. Show A Normal Scenario First

Trigger or display the `normal` scenario.

Explain:

- nominal traffic goes into the IMS entry point
- the platform builds a feature window from that traffic
- a normal window should not create operator noise

This gives the audience a baseline.

### 5. Trigger An Anomalous Scenario

Use `registration_storm` first because it is easy to explain:

- traffic volume increases sharply
- registration behavior becomes abnormal
- the resulting feature window moves away from the normal pattern

If useful, follow with `malformed_invite` to show a different anomaly shape.

### 6. Show The Detection Outcome

Use the demo UI or the anomaly scoring API to show:

- anomaly score
- anomaly decision
- incident creation
- linked feature window
- linked model version

What to say:

> The important point is not just that the system says "anomaly." It also records which feature window was scored and which model version made the decision.

### 7. Show Traceability

Open the incident record and highlight:

- feature window ID
- model version
- anomaly type
- timestamp

This is the best moment to explain governance and auditability.

### 8. Show The RCA Experience

Open the RCA response and walk through:

- evidence
- confidence
- likely cause
- recommendation

Explain clearly that RCA is a separate path from anomaly detection:

> The predictive model detects the problem. The generative RCA path explains the problem. They are connected, but they are not the same component.

### 9. Show Retrieval Transparency

Open Attu and show the `ims_runbooks` collection.

What to say:

> The RCA path is grounded in retrievable content. We can inspect the collection directly instead of treating retrieval as a black box.

### 10. Show The AI Operations Side

Show OpenShift AI resources:

- recent pipeline run
- model-serving resources
- registry output in MinIO

Explain:

> The model was trained from stored feature windows generated from real SIP traffic. That is why the training path and the live scoring path tell a consistent story.

### 11. Close With The Operational Actions

Demonstrate:

- incident persistence
- Slack and Jira actions
- approval flow
- automation playbooks and audit trail

Frame it as operator assistance, not full autonomy.

## Suggested Talk Track For The New Training Story

Use this wording if you want a simple explanation:

> We use SIPp to generate known scenarios against the IMS stack. Each run is converted into a labeled feature window and stored in MinIO. Kubeflow Pipelines trains from those stored windows, and the selected model is deployed for live scoring. That means the model is trained from the same kind of operational data that the platform later evaluates in production-like flows.

## What To Emphasize

- the anomaly model and RCA path are explicitly separated
- every incident can be traced back to a feature window and model version
- the training dataset is derived from real SIPp-generated IMS traffic
- the live scoring schema and the training schema are aligned
- Milvus retrieval is inspectable through Attu
- the deployment model is cluster-native rather than notebook-centric

## Good Questions To Expect

### "Is the model trained on real traffic or synthetic data?"

Answer:

> The preferred path is real SIPp-driven traffic captured from the IMS lab and stored as labeled feature windows. Synthetic data remains only as a bootstrap fallback if the live dataset is missing or too small.

### "Can I trace a result back to the input?"

Answer:

> Yes. The incident links to the feature window and the model version, and the feature window links to the dataset version used in training.

### "Is RCA the same model as anomaly detection?"

Answer:

> No. The anomaly detector scores the feature window. The RCA path uses retrieval plus a generative model to explain likely causes and recommended next steps.

## If You Need A Shorter Demo

If you only have a few minutes:

1. Show the UI and the four-plane story.
2. Show one normal scenario and one registration storm.
3. Show anomaly detection and incident creation.
4. Show traceability to feature window and model version.
5. End with the statement that training used stored feature windows from real SIP traffic.

## Final Message To Leave With The Audience

This is not just a dashboard with a model behind it. It is a traceable operational platform where real telecom traffic becomes structured data, structured data becomes a trained model, and the model output becomes an auditable incident and RCA workflow.
