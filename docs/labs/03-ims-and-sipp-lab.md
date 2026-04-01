# Lab 03: IMS and SIPp Lab

## Objective

Deploy the IMS lab, run SIP traffic scenarios, and confirm that the scenario output is stored as feature-window data for later model training.

## Before You Begin

- Complete the platform bootstrap steps in Labs 01 and 02.
- Make sure you can access the `ims-demo-lab` namespace with `oc`.
- Make sure MinIO is available because this lab writes captured data there.

## What This Lab Deploys

- OpenIMSs core services:
  - `ims-pcscf`
  - `ims-scscf`
  - `ims-icscf`
  - `ims-hss`
  - `openimss-webui`
- SIPp scenario jobs from `k8s/base/traffic`
- Scenario files from `k8s/base/traffic/scenarios`
- `feature-gateway` for on-demand feature-window generation

## Scenario Names Used In This Lab

- `normal`
  - baseline traffic
- `registration_storm`
  - elevated REGISTER traffic
- `malformed_invite`
  - malformed INVITE requests

## Run The Lab

1. Confirm the demo overlay is being reconciled by Argo CD:

```sh
oc get application.argoproj.io ims-demo-platform -n openshift-gitops
oc get application.argoproj.io ims-demo-platform -n openshift-gitops -o jsonpath='{.status.sync.status}{" / "}{.status.health.status}{"\n"}'
```

If you have changed the manifests, push the updated repo state to the in-cluster Gitea `main` branch and let Argo CD sync `k8s/overlays/demo`. Do not use `oc apply -k k8s/overlays/demo` in the standard GitOps path, or Argo CD and imperative apply can compete for ownership of the same resources.

2. Trigger the first demo image build if the internal registry has not been populated yet. Subsequent pushes to `main` in the in-cluster Gitea repo will trigger this automatically, but the first build is often easiest to start manually:

```sh
make trigger-build-pipeline
```

3. Watch the Tekton run until the images are available in the internal registry:

```sh
oc get pipelinerun -n ims-demo-lab
oc get is -n ims-demo-lab
```

If you skip this build, the overlay can leave workloads in `ImagePullBackOff` while the image stream tags are still empty.

4. Verify the IMS deployments are healthy:

```sh
oc get deploy -n ims-demo-lab
```

5. Verify the IMS services are present:

```sh
oc get svc -n ims-demo-lab | rg 'ims-|openimss'
```

6. Verify the SIPp CronJobs exist:

```sh
oc get cronjob -n ims-demo-lab | rg 'sipp'
```

7. Trigger one traffic run manually if you do not want to wait for the next schedule:

```sh
oc create job --from=cronjob/sipp-normal-traffic sipp-normal-check -n ims-demo-lab
```

8. Wait for the job to finish:

```sh
oc wait --for=condition=complete job/sipp-normal-check -n ims-demo-lab --timeout=5m
```

9. Check the job logs. A successful run prints a `window_uri` that points to the stored feature-window JSON object in MinIO:

```sh
oc logs job/sipp-normal-check -n ims-demo-lab
```

10. Repeat the same check for the anomaly scenarios when needed:

```sh
oc create job --from=cronjob/sipp-registration-storm sipp-storm-check -n ims-demo-lab
oc create job --from=cronjob/sipp-malformed-invite sipp-malformed-check -n ims-demo-lab
```

## Optional On-Demand Check

If you want to confirm the live scoring path, call `feature-gateway` directly:

```sh
oc get route feature-gateway -n ims-demo-lab -o jsonpath='https://{.spec.host}{"\n"}'
```

Then open:

- `/live-window/normal`
- `/live-window/registration_storm`
- `/live-window/malformed_invite`

## Expected Result

After this lab:

- the IMS services are running
- SIPp can reach `ims-pcscf` on port `5060`
- completed SIPp jobs write feature-window JSON documents to MinIO
- the dataset version `live-sipp-v1` starts accumulating scenario output for training

## Quick Troubleshooting

- If `ims-demo-platform` is not synced, check the Argo CD application before debugging the workloads themselves.
- If the IMS deployments are not ready, check `oc get pods -n ims-demo-lab`.
- If a SIPp job fails, read the job logs before retrying.
- If no `window_uri` is printed, confirm MinIO is reachable and the job has the storage credentials.
