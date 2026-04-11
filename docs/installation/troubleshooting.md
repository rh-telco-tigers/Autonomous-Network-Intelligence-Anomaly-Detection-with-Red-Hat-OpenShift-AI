# Troubleshooting

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
oc get pipelinerun -n ani-tekton
```

If `ani-sipp` is still missing `openimss-open5gs:latest` or `openimss-opensips:latest`, check the OpenShift builds:

```sh
oc get builds -n ani-sipp
oc start-build -n ani-sipp openimss-open5gs --follow
oc start-build -n ani-sipp openimss-opensips --follow
```

After the builds complete, restart the IMS deployments:

```sh
oc rollout restart deployment -n ani-sipp
oc get pods -n ani-sipp
```

## `ani-tekton` Fails Early With Missing Tekton CRDs

On a fresh cluster, `ani-tekton` can fail its first sync before the Tekton CRDs exist. The Argo application usually recovers on its own once the Pipelines operator finishes installing the CRDs.

Check:

```sh
oc get applications.argoproj.io ani-tekton -n openshift-gitops -o yaml | sed -n '1,220p'
oc get crd tasks.tekton.dev pipelines.tekton.dev eventlisteners.triggers.tekton.dev triggerbindings.triggers.tekton.dev triggertemplates.triggers.tekton.dev
```

If the app message mentions `no matches for kind "Task" in version "tekton.dev/v1"` or similar, wait for the CRDs to appear and then recheck `ani-tekton`. When the app reaches `Synced` / `Healthy`, rerun:

```sh
make trigger-build-pipeline
```

## OpenShift AI Or DSPA Is Not Ready

```sh
oc get dsc -n redhat-ods-operator
oc get deploy -n redhat-ods-applications
oc get dspa,featurestore,inferenceservice -n ani-datascience
```

If `default-dsc` is not `Ready=True`, wait for the `redhat-ods-applications` controllers to come up before retrying the `ani-datascience` validation.

## `ani-datascience` Is Degraded Because The Predictors Start Before Models Exist

On a fresh install, the predictive `InferenceService` resources can start before the bootstrap training workflows publish the initial models into object storage. In that case the storage initializer logs `No model found` and KServe keeps retrying until the artifacts land.

Check:

```sh
oc get wf -n ani-datascience
oc get inferenceservice -n ani-datascience
oc get pods -n ani-datascience | rg 'ani-predictive'
```

Wait for these workflows to finish with `Succeeded`:

- `ani-anomaly-platform-train-and-register-*`
- `ani-feature-bundle-publish-*`
- `ani-featurestore-train-and-register-*`

Then recheck:

```sh
oc get inferenceservice -n ani-datascience
```

The predictive services should recover automatically once the model artifacts exist.

## `llama-32-3b-instruct` Stays `Pending` With `Insufficient nvidia.com/gpu`

The cluster needs allocatable GPU capacity, not just a GPU node label.

Check:

```sh
oc get nodes -o custom-columns=NAME:.metadata.name,GPU:.status.allocatable.nvidia\\.com/gpu
oc describe pod -n ani-datascience "$(oc get pod -n ani-datascience -o name | rg 'llama-32-3b-instruct' | head -n1 | cut -d/ -f2)"
```

If every node shows an empty `GPU` column, the vLLM pod cannot schedule. The rest of the predictive incident workflow can still work, but RCA generation falls back away from the live LLM path until the cluster exposes at least one allocatable GPU.

## Plane Route Loads But Login Loops

The current branch fixes this by creating the missing Plane admin profile during bootstrap. Make sure the cluster is synced to the latest GitOps revision and that the latest Plane bootstrap job ran.

Check:

```sh
oc get applications.argoproj.io -n openshift-gitops ani-plane ani-runtime
oc get jobs -A | rg 'plane-integration-secret-bootstrap'
oc get route -n plane
```

If the cluster is on an older revision, sync to the latest branch state and let the new Plane bootstrap job run again.

## Plane Host Lookup Returns An Empty String

The Plane routes use `spec.subdomain`, so `spec.host` can be empty on fresh clusters even though the route is admitted.

Use:

```sh
oc get route plane-web -n plane -o jsonpath='{.status.ingress[0].host}{"\n"}'
```

## AAP Or EDA Is Installed But No Job Templates Exist

This usually means the AAP license import was not finished yet, or the cluster is still running an older control-plane pod that started before the enabled-by-default automation config was applied.

Check:

```sh
oc get route -n aap
oc get configmap aap-automation-config -n ani-runtime -o yaml
oc get deploy control-plane -n ani-runtime
```

Then:

1. Import the AAP license and finish any first-login prompts in the AAP UI.
2. Confirm `AAP_AUTOMATION_ENABLED`, `EDA_AUTOMATION_ENABLED`, and `AUTOMATION_BOOTSTRAP_ON_STARTUP` are all `"true"` in `aap-automation-config`.
3. Wait for the control-plane bootstrap retries to reconcile the templates and activations.
4. If this cluster was already running before the enabled-by-default config landed, restart `deployment/control-plane` in `ani-runtime` once so it picks up the new config values.

```sh
oc rollout restart deployment/control-plane -n ani-runtime
oc rollout status deployment/control-plane -n ani-runtime
```

## AAP Job Succeeds But Argo CD Reverts The Change

The current branch allows the approved AAP-managed drift for:

- `ims-scscf` replica changes from `Scale the S-CSCF path`
- `ims-pcscf` annotation changes from the ingress guardrail action

If an AAP remediation completes but the workload snaps back to the Git value immediately, confirm `ani-sipp-core` is synced to the latest branch revision:

```sh
oc get application.argoproj.io ani-sipp-core -n openshift-gitops
oc get application.argoproj.io ani-sipp-core -n openshift-gitops -o jsonpath='{.status.sync.revision}{"\n"}'
```

## The Demo UI Opens But No New Incidents Appear

Check the generators first:

```sh
oc get cronjob -n ani-runtime | rg 'demo-incident-pulse'
oc get cronjob -n ani-sipp | rg 'sipp-'
```

Then run one anomaly job manually:

```sh
STORM_JOB="sipp-storm-check-$(date +%s)"
oc create job --from=cronjob/sipp-registration-storm "${STORM_JOB}" -n ani-sipp
oc wait --for=condition=complete "job/${STORM_JOB}" -n ani-sipp --timeout=5m
oc logs "job/${STORM_JOB}" -n ani-sipp
```

If that works but the UI still stays empty, check the control-plane status:

```sh
CONTROL_PLANE_HOST="$(oc get route control-plane -n ani-runtime -o jsonpath='{.spec.host}')"
curl -k "https://${CONTROL_PLANE_HOST}/platform/status" \
  -H "x-api-key: demo-operator-token" | python3 -m json.tool
```
