# Lab 05: Demo Runbook

## Objective

Run the full customer demo sequence from traffic generation through RCA.

## Demo script

1. Start from the demo UI route and explain the four system planes.
2. Show the IMS lab workloads and the SIPp scenario runner in OpenShift.
3. Trigger the normal traffic scenario and confirm low anomaly score output.
4. Trigger the registration storm scenario.
5. Call the anomaly scoring API or use the demo UI to show incident creation.
6. Open the persisted incident record and show the linked model version and feature window.
7. Open the RCA response and walk through evidence, confidence, and recommendation fields.
8. Show the OpenShift AI resources for pipelines, model serving, and model registry.
9. Demonstrate Slack, Jira, and approval actions from the UI.
10. Close by showing the approval-oriented automation playbooks and audit trail.

## What to emphasize

- The anomaly model and RCA path are explicitly separated
- every incident can be traced back to a feature window and model version
- the deployment model is cluster-native rather than notebook-centric
