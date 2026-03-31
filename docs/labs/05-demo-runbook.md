# Lab 05: Demo Runbook

## Objective

Run the full customer demo sequence from traffic generation through RCA.

## Demo script

1. Start from the demo UI route and explain the four system planes.
2. Show the IMS lab workloads and the SIPp scenario runner in OpenShift.
3. Trigger the normal traffic scenario and confirm the feature window is generated from live IMS telemetry.
4. Trigger the registration storm scenario.
5. Call the anomaly scoring API or use the demo UI to show incident creation.
6. Open the persisted incident record and show the linked model version and feature window.
7. Open the RCA response and walk through evidence, confidence, and recommendation fields.
8. Open the Attu route and show the `ims_runbooks` collection used for retrieval grounding.
9. Show the OpenShift AI resources for pipelines, predictive model serving, and model registry.
10. Explain that generative RCA is using the shared cluster vLLM endpoint through the `ims-generative-proxy` service.
11. Demonstrate Slack, Jira, and approval actions from the UI.
12. Close by showing the approval-oriented automation playbooks and audit trail.

## What to emphasize

- The anomaly model and RCA path are explicitly separated
- every incident can be traced back to a feature window and model version
- Milvus retrieval is inspectable through Attu instead of being treated as a black box
- the GitOps source and operator catalog flow are hosted inside the cluster for demo portability
- the deployment model is cluster-native rather than notebook-centric
