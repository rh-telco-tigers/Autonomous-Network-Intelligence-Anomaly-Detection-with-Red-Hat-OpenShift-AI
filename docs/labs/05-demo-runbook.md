# Lab 05: Demo Runbook

## Objective

Run the full demo from traffic generation through anomaly detection, incident review, RCA, and human-approved remediation.

## Before You Start

Make sure the following items are ready:

- the demo UI route is reachable
- the IMS lab workloads are healthy
- the `demo-incident-pulse` and `sipp-*` CronJobs are present and healthy
- the `live-sipp-v1` dataset exists in MinIO
- a recent KFP workflow completed successfully
- the feature-store predictive model is available through both Triton and MLServer serving
- any optional integrations you want to demonstrate, such as live LLM-backed RCA or Plane/AAP flows, have been enabled explicitly after bootstrap

Recommended checks:

```sh
make check-fresh-cluster
oc get deploy -n ims-runtime
oc get deploy -n ims-sipp
oc get cronjob -n ims-runtime | rg 'demo-incident-pulse'
oc get cronjob -n ims-sipp | rg 'sipp-'
oc get workflow -n ims-datascience
oc get inferenceservice -n ims-datascience | rg 'ims-predictive-fs|ims-predictive-fs-mlserver'
```

## Demo Sequence

1. Open the demo UI.
2. Confirm the platform is healthy before generating new traffic.
3. Confirm that the console shows auto refresh enabled and that the page is updating on its own.
4. Explain that `demo-incident-pulse` posts a scenario to `/console/run-scenario` every three minutes, and that the `sipp-*` CronJobs also generate feature windows and incidents on their own schedules once model serving is ready.
5. Show the IMS workloads in OpenShift:

```sh
oc get pods -n ims-sipp
```

6. Show that feature-window data already exists from previous SIPp runs.
7. If you want an immediate contrast, run or display a `normal` scenario and confirm that the resulting feature window does not trigger an incident.
8. Either wait for the next background pulse or run a `registration_storm` scenario manually.
9. Use the UI or scoring API to confirm that the anomaly is detected and an incident is created.
10. Open the incident details and confirm that the record includes the feature window and model version.
11. Open the detailed trace route and show the ordered feature-gateway, model, API, and RCA/LLM packets for the incident.
12. Open the RCA result and review the evidence and recommendation fields. Call out whether the source label shows local fallback or live LLM generation.
13. Review the ranked remediation options and explain that execution stays human-approved.
14. Approve and execute `Scale the S-CSCF path`.
15. Confirm that the incident transitions through approval and execution states and that the action result reflects the automation mode you configured for the cluster.
16. Show the latest workflow in OpenShift AI and confirm that `ims-predictive-fs` and `ims-predictive-fs-mlserver` are present.
17. If needed, open Attu and confirm that the `ims_runbooks`, `incident_evidence`, `incident_reasoning`, and `incident_resolution` collections are available.
18. If you want to prove the runtime effect, show that the `ims-scscf` deployment replica count increased after the approved action.

## What To Verify During The Demo

- a normal scenario produces a normal result
- an anomaly scenario produces an anomaly result
- the dashboard refreshes on its own without using the button
- a new incident appears after the scheduled pulse, a scheduled `sipp-*` run, or a manual trigger
- the incident record includes the feature window ID
- the incident record includes the model version
- the detailed trace route shows the pre-model feature fetch, model infer payloads, and RCA/LLM activity
- RCA is available for the incident, even if the fresh-cluster default is local fallback instead of a live LLM
- remediation options are generated from the RCA context
- the approved `Scale the S-CSCF path` action updates the incident to `EXECUTING` and then `EXECUTED`
- `ims-predictive-fs` and `ims-predictive-fs-mlserver` are ready in the cluster

## Suggested Short Flow

If you need a shorter run:

1. Show the UI and cluster health.
2. Point out that the dashboard refreshes automatically and that the background pulse and `sipp-*` schedules are active.
3. Show one normal scenario if you need a clean baseline.
4. Show one `registration_storm` scenario or wait for the next pulse-created incident.
5. Show the created incident.
6. Open the detailed trace route.
7. Show the RCA result.
8. Approve and execute `Scale the S-CSCF path`, then show the execution status on the same incident.

## Troubleshooting During The Demo

- If the UI does not load, check the route and the backing pods.
- If the dashboard is stale, confirm that auto refresh is enabled and that both `demo-incident-pulse` and the `sipp-*` CronJobs are healthy.
- If no incident is created, confirm that model serving is ready and inspect the latest `demo-incident-pulse` and `sipp-*` Job logs.
- If RCA is missing, confirm that the control-plane and retrieval services are healthy. A blank `LLM_ENDPOINT` does not block fallback RCA.
- If the detailed trace route is missing packets, confirm that the latest control-plane, anomaly-service, and rca-service images were rolled out.
- If the approved remediation stays in `EXECUTING`, inspect the control-plane logs and the configured automation backend. Fresh clusters ship with external automation integrations disabled by default.
- If controller-backed AAP launch is blocked by license, the runner-job fallback is the expected behavior once automation has been enabled.
- If the latest training run failed, open the failed workflow before retrying the demo.
