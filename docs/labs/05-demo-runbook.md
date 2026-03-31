# Lab 05: Demo Runbook

## Objective

Run the full demo from traffic generation through anomaly detection, incident review, and RCA.

## Before You Start

Make sure the following items are ready:

- the demo UI route is reachable
- the IMS lab workloads are healthy
- the `demo-incident-pulse` CronJob is present
- the `live-sipp-v1` dataset exists in MinIO
- a recent KFP workflow completed successfully
- the predictive model is available through model serving

Recommended checks:

```sh
oc get deploy -n ims-demo-lab
oc get cronjob -n ims-demo-lab
oc get workflow -n ims-demo-lab
oc get inferenceservice -n ims-demo-lab
```

## Demo Sequence

1. Open the demo UI.
2. Confirm the platform is healthy before generating new traffic.
3. Confirm that the console shows auto refresh enabled and that the page is updating on its own.
4. Explain that the `demo-incident-pulse` CronJob posts a scenario to the control-plane every three minutes, so the incident list can update without pressing a UI button.
5. Show the IMS workloads in OpenShift:

```sh
oc get pods -n ims-demo-lab
```

6. Show that feature-window data already exists from previous SIPp runs.
7. If you want an immediate contrast, run or display a `normal` scenario and confirm that the resulting feature window does not trigger an incident.
8. Either wait for the next background pulse or run a `registration_storm` scenario manually.
9. Use the UI or scoring API to confirm that the anomaly is detected and an incident is created.
10. Open the incident details and confirm that the record includes the feature window and model version.
11. Open the RCA result and review the evidence and recommendation fields.
12. Show the latest workflow in OpenShift AI and confirm that the model-serving resources are present.
13. If needed, open Attu and confirm that the `ims_runbooks` and `ims_incidents` collections are available.

## What To Verify During The Demo

- a normal scenario produces a normal result
- an anomaly scenario produces an anomaly result
- the dashboard refreshes on its own without using the button
- a new incident appears after the scheduled pulse or a manual trigger
- the incident record includes the feature window ID
- the incident record includes the model version
- RCA is available for the incident
- model-serving resources are ready in the cluster

## Suggested Short Flow

If you need a shorter run:

1. Show the UI and cluster health.
2. Point out that the dashboard refreshes automatically and that the background pulse is active.
3. Show one normal scenario if you need a clean baseline.
4. Show one `registration_storm` scenario or wait for the next pulse-created incident.
5. Show the created incident.
6. Show the RCA result.

## Troubleshooting During The Demo

- If the UI does not load, check the route and the backing pods.
- If the dashboard is stale, confirm that auto refresh is enabled and that the `demo-incident-pulse` CronJob is running.
- If no incident is created, confirm that model serving is ready and inspect the latest `demo-incident-pulse` Job logs.
- If RCA is missing, confirm that the control-plane and retrieval services are healthy.
- If the latest training run failed, open the failed workflow before retrying the demo.
