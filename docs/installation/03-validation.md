# Installation 03: Validation

## Objective

Confirm that the platform deployed successfully and that you can navigate the main routes.

## 1. Check Argo CD And Core Workloads

```sh
oc get applications.argoproj.io -n openshift-gitops
oc get deploy -n ani-runtime
oc get deploy -n ani-sipp
oc get dsc -n redhat-ods-operator
oc get dspa,featurestore,servingruntime,inferenceservice -n ani-datascience
oc get pipelines.pipelines.kubeflow.org,pipelineversions.pipelines.kubeflow.org -n ani-datascience
oc get cronjob -n ani-datascience | rg 'kfp-auto-run'
oc get modelregistries.modelregistry.opendatahub.io -n rhoai-model-registries
```

If `ani-predictive` or `ani-predictive-fs` is still `READY=False`, that is expected while the background KFP auto-run `CronJob`s are still publishing the first model artifacts. GitOps creates the `InferenceService` objects first; the live-path workflows catch up afterward.

If `ani-remediation` is the only degraded Argo application at this stage and the secret `aap-lightspeed-chatbot-api-key` does not exist yet, continue with [Installation 05](./05-remediation-using-ansible-lightspeed.md). That manual token step is still required on the current branch.

## 2. Collect The Main Routes

```sh
DEMO_UI_HOST="$(oc get route demo-ui -n ani-runtime -o jsonpath='{.status.ingress[0].host}')"
CONTROL_PLANE_HOST="$(oc get route control-plane -n ani-runtime -o jsonpath='{.status.ingress[0].host}')"
OPENIMSS_HOST="$(oc get route openimss-webui -n ani-sipp -o jsonpath='{.status.ingress[0].host}')"
PLANE_HOST="$(oc get route plane-web -n plane -o jsonpath='{.status.ingress[0].host}')"
ATTU_HOST="$(oc get route milvus-attu -n ani-data -o jsonpath='{.status.ingress[0].host}')"
MINIO_HOST="$(oc get route model-storage-minio-console -n ani-data -o jsonpath='{.status.ingress[0].host}')"

echo "Demo UI:        https://${DEMO_UI_HOST}"
echo "Control plane:  https://${CONTROL_PLANE_HOST}"
echo "OpenIMS WebUI:  https://${OPENIMSS_HOST}"
echo "Plane:          https://${PLANE_HOST}"
echo "Attu:           https://${ATTU_HOST}"
echo "MinIO console:  https://${MINIO_HOST}"
```

## 3. Validate Navigation

Open these routes in a browser:

- Demo UI: `https://${DEMO_UI_HOST}`
- OpenIMS WebUI: `https://${OPENIMSS_HOST}`
- Plane: `https://${PLANE_HOST}`
- Attu: `https://${ATTU_HOST}`
- MinIO console: `https://${MINIO_HOST}`

Use these credentials:

- Gitea: `gitadmin` / `GiteaAdmin123!`
- Plane: `plane-admin@ani-demo.local` / `plane`
- OpenIMS WebUI: `admin` / `1423`
- MinIO console: `minioadmin` / `minioadmin`

## 4. Validate Background Generators

```sh
oc get cronjob -n ani-runtime | rg 'demo-incident-pulse'
oc get cronjob -n ani-sipp | rg 'sipp-'
oc get cronjob -n ani-datascience | rg 'kfp-auto-run'
```

## 5. Run One Manual Traffic Check

Create one normal run:

```sh
NORMAL_JOB="sipp-normal-check-$(date +%s)"
oc create job --from=cronjob/sipp-normal-traffic "${NORMAL_JOB}" -n ani-sipp
oc wait --for=condition=complete "job/${NORMAL_JOB}" -n ani-sipp --timeout=5m
oc logs "job/${NORMAL_JOB}" -n ani-sipp
```

Create one anomaly run:

```sh
STORM_JOB="sipp-storm-check-$(date +%s)"
oc create job --from=cronjob/sipp-registration-storm "${STORM_JOB}" -n ani-sipp
oc wait --for=condition=complete "job/${STORM_JOB}" -n ani-sipp --timeout=5m
oc logs "job/${STORM_JOB}" -n ani-sipp
```

The logs should print a `window_uri`. After the anomaly run, the demo UI should show a new incident.

## 6. Validate The Control-Plane API

```sh
curl -k "https://${CONTROL_PLANE_HOST}/platform/status" \
  -H "x-api-key: demo-operator-token" | python3 -m json.tool
```

## 7. Validate AAP And EDA After License Import

After the AAP license is imported:

```sh
curl -k "https://${CONTROL_PLANE_HOST}/integrations/status" \
  -H "x-api-key: demo-token" | python3 -m json.tool
```

Expect:

- `aap.configured=true`
- `aap.live_configured=true`
- `aap.bootstrapped=true`
- `eda.configured=true`
- `eda.live_configured=true`
- `eda.bootstrapped=true`

## 8. Validate The Existing AAP Playbook From The Incident Flow

1. Open the demo UI and run the `call_setup_timeout` scenario.
2. Open the incident, review the generated remediations, and execute `Scale the S-CSCF path`.
3. Confirm the execution completed and the scale change remained applied:

```sh
oc get deploy ims-scscf -n ani-sipp -o jsonpath='{.spec.replicas}{"\t"}{.status.readyReplicas}{"\t"}{.status.availableReplicas}{"\n"}'
```

Expected result after the approved action runs: the deployment reports `2` desired replicas and stays there until an operator intentionally changes it again.

## What Good Looks Like

- The base platform and RHOAI Argo applications are `Synced` / `Healthy`
- Demo UI opens
- OpenIMS WebUI login works
- Plane login works
- `default-dsc` is `Ready=True`
- `dspa` is `Ready`
- `ani-featurestore` is `Ready`
- the KFP `Pipeline` and `PipelineVersion` resources exist in `ani-datascience`
- the KFP auto-run `CronJob` resources exist in `ani-datascience`
- the serving runtimes exist in `ani-datascience`
- `default-modelregistry` exists in `rhoai-model-registries`
- the predictive `InferenceService` objects exist in `ani-datascience`
- the manual SIPp jobs finish and print `window_uri`
- the control-plane status endpoint returns JSON without errors
- after AAP license import, `aap` and `eda` report `live_configured=true`
- the `Scale the S-CSCF path` incident action finishes through AAP and `ims-scscf` stays at `2` replicas

If the only remaining degraded item is `llama-32-3b-instruct` with `Insufficient nvidia.com/gpu`, the predictive incident workflow can still work, but the generative RCA path is not available until the cluster exposes allocatable GPU capacity.

If the only remaining degraded application is `ani-remediation` and the missing dependency is `aap-lightspeed-chatbot-api-key`, the install is still incomplete. Finish [Installation 05](./05-remediation-using-ansible-lightspeed.md) before calling the full platform healthy.

## Next Step

If you want to inspect or rerun the RHOAI model path, continue with [Installation 04: Data Generation And Model Training](./04-data-generation-and-model-training.md). If you want the remediation slice to converge fully, continue with [Installation 05: Remediation Using Ansible Lightspeed](./05-remediation-using-ansible-lightspeed.md).
