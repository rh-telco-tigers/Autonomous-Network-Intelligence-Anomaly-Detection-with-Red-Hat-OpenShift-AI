# Installation 03: Validation

## Objective

Confirm that the platform deployed successfully and that you can navigate the main routes.

## 1. Check Argo CD And Core Workloads

```sh
oc get applications.argoproj.io -n openshift-gitops
oc get deploy -n ims-runtime
oc get deploy -n ims-sipp
oc get dsc -n redhat-ods-operator
oc get dspa,featurestore,inferenceservice -n ims-datascience
```

## 2. Collect The Main Routes

```sh
DEMO_UI_HOST="$(oc get route demo-ui -n ims-runtime -o jsonpath='{.spec.host}')"
CONTROL_PLANE_HOST="$(oc get route control-plane -n ims-runtime -o jsonpath='{.spec.host}')"
OPENIMSS_HOST="$(oc get route openimss-webui -n ims-sipp -o jsonpath='{.spec.host}')"
PLANE_HOST="$(oc get route plane-web -n plane -o jsonpath='{.spec.host}')"
ATTU_HOST="$(oc get route milvus-attu -n ims-data -o jsonpath='{.spec.host}')"
MINIO_HOST="$(oc get route model-storage-minio-console -n ims-data -o jsonpath='{.spec.host}')"

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
- Plane: `plane-admin@ims-demo.local` / `plane`
- OpenIMS WebUI: `admin` / `1423`
- MinIO console: `minioadmin` / `minioadmin`

## 4. Validate Background Generators

```sh
oc get cronjob -n ims-runtime | rg 'demo-incident-pulse'
oc get cronjob -n ims-sipp | rg 'sipp-'
```

## 5. Run One Manual Traffic Check

Create one normal run:

```sh
NORMAL_JOB="sipp-normal-check-$(date +%s)"
oc create job --from=cronjob/sipp-normal-traffic "${NORMAL_JOB}" -n ims-sipp
oc wait --for=condition=complete "job/${NORMAL_JOB}" -n ims-sipp --timeout=5m
oc logs "job/${NORMAL_JOB}" -n ims-sipp
```

Create one anomaly run:

```sh
STORM_JOB="sipp-storm-check-$(date +%s)"
oc create job --from=cronjob/sipp-registration-storm "${STORM_JOB}" -n ims-sipp
oc wait --for=condition=complete "job/${STORM_JOB}" -n ims-sipp --timeout=5m
oc logs "job/${STORM_JOB}" -n ims-sipp
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
oc get deploy ims-scscf -n ims-sipp -o jsonpath='{.spec.replicas}{"\t"}{.status.readyReplicas}{"\t"}{.status.availableReplicas}{"\n"}'
```

Expected result after the approved action runs: the deployment reports `2` desired replicas and stays there until an operator intentionally changes it again.

## What Good Looks Like

- Argo applications are present and mostly `Synced` / `Healthy`
- Demo UI opens
- OpenIMS WebUI login works
- Plane login works
- `default-dsc` is `Ready=True`
- `dspa` exists
- `ims-featurestore` is `Ready`
- the predictive `InferenceService` resources are `READY=True`
- the manual SIPp jobs finish and print `window_uri`
- the control-plane status endpoint returns JSON without errors
- after AAP license import, `aap` and `eda` report `live_configured=true`
- the `Scale the S-CSCF path` incident action finishes through AAP and `ims-scscf` stays at `2` replicas
