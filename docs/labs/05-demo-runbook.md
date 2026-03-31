# Lab 05: Demo Runbook

## Objective

Run the full demo from traffic generation through anomaly detection, incident review, and RCA.

## Before You Start

Make sure the following items are ready:

- the demo UI route is reachable
- the IMS lab workloads are healthy
- the `live-sipp-v1` dataset exists in MinIO
- a recent KFP workflow completed successfully
- the predictive model is available through model serving

Recommended checks:

```sh
oc get deploy -n ims-demo-lab
oc get workflow -n ims-demo-lab
oc get inferenceservice -n ims-demo-lab
```

## Demo Sequence

1. Open the demo UI.
2. Confirm the platform is healthy before generating new traffic.
3. Show the IMS workloads in OpenShift:

```sh
oc get pods -n ims-demo-lab
```

4. Show that feature-window data already exists from previous SIPp runs.
5. Run or display a `normal` scenario and confirm that the resulting feature window does not trigger an incident.
6. Run or display a `registration_storm` scenario.
7. Use the UI or scoring API to confirm that the anomaly is detected and an incident is created.
8. Open the incident details and confirm that the record includes the feature window and model version.
9. Open the RCA result and review the evidence and recommendation fields.
10. Show the latest workflow in OpenShift AI and confirm that the model-serving resources are present.
11. If needed, open Attu and confirm that the `ims_runbooks` collection is available.

## What To Verify During The Demo

- a normal scenario produces a normal result
- an anomaly scenario produces an anomaly result
- the incident record includes the feature window ID
- the incident record includes the model version
- RCA is available for the incident
- model-serving resources are ready in the cluster

## Suggested Short Flow

If you need a shorter run:

1. Show the UI and cluster health.
2. Show one normal scenario.
3. Show one `registration_storm` scenario.
4. Show the created incident.
5. Show the RCA result.

## Troubleshooting During The Demo

- If the UI does not load, check the route and the backing pods.
- If no incident is created, confirm that model serving is ready.
- If RCA is missing, confirm that the control-plane and retrieval services are healthy.
- If the latest training run failed, open the failed workflow before retrying the demo.
