# Installation 04: Troubleshooting

## Argo CD Is Tracking The Wrong Branch

Check the current local branch:

```sh
git branch --show-current
```

Check what Argo CD is tracking:

```sh
oc get applications.argoproj.io -n openshift-gitops -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.source.targetRevision}{"\n"}{end}'
```

If the branches do not match, push the branch you want to deploy to in-cluster Gitea and update the Argo bootstrap input before resyncing.

## Pods Are In `ImagePullBackOff`

First rerun the Tekton build:

```sh
make trigger-build-pipeline
oc get pipelinerun -n ims-tekton
```

If the IMS namespace is still missing `openimss-open5gs:latest` or `openimss-opensips:latest`, check the OpenShift builds:

```sh
oc get builds -n ims-sipp
oc start-build -n ims-sipp openimss-open5gs --follow
oc start-build -n ims-sipp openimss-opensips --follow
```

After the builds complete, restart the IMS deployments:

```sh
oc rollout restart deployment -n ims-sipp
oc get pods -n ims-sipp
```

## OpenShift AI Or DSPA Is Not Ready

```sh
oc get dsc -n redhat-ods-operator
oc get deploy -n redhat-ods-applications
oc get dspa,featurestore,inferenceservice -n ims-datascience
```

If `default-dsc` is not `Ready=True`, wait for the `redhat-ods-applications` controllers to come up before retrying the `ims-datascience` validation.

## Plane Route Loads But Login Loops

The current branch fixes this by creating the missing Plane admin profile during bootstrap. Make sure the cluster is synced to the latest GitOps revision and that the latest Plane bootstrap job ran.

Check:

```sh
oc get applications.argoproj.io -n openshift-gitops ims-plane ims-runtime
oc get jobs -A | rg 'plane-integration-secret-bootstrap'
oc get route -n plane
```

If the cluster is on an older revision, sync to the latest branch state and let the new Plane bootstrap job run again.

## AAP Or EDA Is Installed But No Job Templates Exist

This usually means the first-login or license step was not finished, or automation is still disabled in `aap-automation-config`.

Check:

```sh
oc get route -n aap
oc get configmap aap-automation-config -n ims-runtime -o yaml
```

Then:

1. Finish the AAP license or subscription prompts in the UI.
2. Set `AAP_AUTOMATION_ENABLED` and `EDA_AUTOMATION_ENABLED` to `"true"`.
3. Set `EDA_CONTROL_PLANE_API_KEY` to `demo-token`.
4. Restart `deployment/control-plane` in `ims-runtime`.
5. Call `POST /automation/bootstrap`.

```sh
CONTROL_PLANE_HOST="$(oc get route control-plane -n ims-runtime -o jsonpath='{.spec.host}')"
curl -k -X POST "https://${CONTROL_PLANE_HOST}/automation/bootstrap" \
  -H "x-api-key: demo-token"
```

## The Demo UI Opens But No New Incidents Appear

Check the generators first:

```sh
oc get cronjob -n ims-runtime | rg 'demo-incident-pulse'
oc get cronjob -n ims-sipp | rg 'sipp-'
```

Then run one anomaly job manually:

```sh
STORM_JOB="sipp-storm-check-$(date +%s)"
oc create job --from=cronjob/sipp-registration-storm "${STORM_JOB}" -n ims-sipp
oc wait --for=condition=complete "job/${STORM_JOB}" -n ims-sipp --timeout=5m
oc logs "job/${STORM_JOB}" -n ims-sipp
```

If that works but the UI still stays empty, check the control-plane status:

```sh
CONTROL_PLANE_HOST="$(oc get route control-plane -n ims-runtime -o jsonpath='{.spec.host}')"
curl -k "https://${CONTROL_PLANE_HOST}/platform/status" \
  -H "x-api-key: demo-operator-token" | python3 -m json.tool
```
