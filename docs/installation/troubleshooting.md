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

## Bootstrap Job Looks Idle Or Stops Before `ani-platform`

The `argocd-bootstrap-application-v3` Job in `openshift-gitops` creates `ani-operators` first and, on the current branch, waits for the OpenShift AI operator subscription and CSV before it creates `ani-platform`.

Check:

```sh
oc get job,pod -n openshift-gitops | rg 'argocd-bootstrap'
oc logs -n openshift-gitops job/argocd-bootstrap-application-v3 -f
oc get application.argoproj.io -n openshift-gitops ani-operators ani-platform
oc get subscription rhods-operator -n redhat-ods-operator
oc get csv -n redhat-ods-operator
```

If `oc apply -k deploy/argocd` fails with `The Job "argocd-bootstrap-application-v3" is invalid ... field is immutable`, the previous Job still exists and Kubernetes will not patch its pod template in place. Recreate it:

```sh
oc delete job -n openshift-gitops argocd-bootstrap-application-v3 --ignore-not-found
oc apply -k deploy/argocd
oc logs -n openshift-gitops job/argocd-bootstrap-application-v3 -f
```

Expected log progression on the current branch:

- `Applying ani-operators root application`
- `Waiting for redhat-ods-operator/rhods-operator to report an installed CSV`
- `Waiting for ClusterServiceVersion ... to reach phase Succeeded`
- `Applying ani-platform root application after OpenShift AI operator install`

If the cluster is still on an older revision, this Job can look silent because the wait loop did not print progress. Sync to the latest branch state and rerun:

```sh
oc apply -k deploy/argocd
```

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

## OpenShift AI Or Datascience Resources Are Not Ready

Missing `FeatureStore`, `DataSciencePipelinesApplication`, `ServingRuntime`, `InferenceService`, or `ModelRegistry` resources on a fresh cluster usually means the OpenShift AI operator install or `default-dsc` reconciliation is still in progress.

```sh
oc get application.argoproj.io -n openshift-gitops ani-rhoai-platform ani-datascience
oc get subscription rhods-operator -n redhat-ods-operator -o jsonpath='{.status.installedCSV}{"\n"}'
oc get csv -n redhat-ods-operator
oc get dsc -n redhat-ods-operator
oc get crd dscinitializations.dscinitialization.opendatahub.io datascienceclusters.datasciencecluster.opendatahub.io datasciencepipelinesapplications.datasciencepipelinesapplications.opendatahub.io featurestores.feast.dev servingruntimes.serving.kserve.io inferenceservices.serving.kserve.io modelregistries.modelregistry.opendatahub.io
oc get deploy -n redhat-ods-applications
oc get dspa,featurestore,servingruntime,inferenceservice -n ani-datascience
oc get modelregistries.modelregistry.opendatahub.io -n rhoai-model-registries
```

Wait until:

- the `installedCSV` field for `rhods-operator` is non-empty
- the RHODS CSV phase is `Succeeded`
- `default-dsc` is `Ready=True`
- the listed CRDs exist
- `ani-rhoai-platform` and `ani-datascience` reach `Synced` / `Healthy`

On the current branch, `ani-rhoai-platform` and `ani-datascience` retry automatically once the operator and CRDs are ready. On older revisions, resync those two applications after `default-dsc` becomes ready.

On the current branch, GitOps also manages the KFP pipeline definitions declaratively. Check:

```sh
oc get pipelines.pipelines.kubeflow.org,pipelineversions.pipelines.kubeflow.org -n ani-datascience
```

## Older Revisions Wait On `ani-kfp-bootstrap` And Later Resources Never Appear

On older revisions, the first KFP bootstrap hook waits for the trainer image stream tag before it publishes the pipeline definition. While that hook is still running, Argo CD does not advance to the later serving waves, so the `InferenceService` and some metrics resources can remain missing even though `dspa` itself is already `Ready`.

Check:

```sh
oc get application.argoproj.io ani-datascience -n openshift-gitops
oc get job,pod -n ani-datascience | rg 'ani-kfp-bootstrap'
oc get is -n ani-datascience
oc logs -n ani-datascience job/ani-kfp-bootstrap
```

If the Job stays `Running` for a long time and the pod logs are empty, inspect the running process:

```sh
oc exec -n ani-datascience "$(oc get pod -n ani-datascience -l job-name=ani-kfp-bootstrap -o jsonpath='{.items[0].metadata.name}')" -- ps -ef
```

If you see the job still sitting in the `ImageStreamTag` wait loop, sync to the current branch state and recreate the Job so it only publishes the pipeline definition and does not block on the first demo run:

```sh
oc delete job -n ani-datascience ani-kfp-bootstrap --ignore-not-found
oc annotate application ani-datascience -n openshift-gitops argocd.argoproj.io/refresh=hard --overwrite
```

## `ani-datascience` Is Degraded Because The Predictors Start Before Models Exist

On the current branch, GitOps creates the predictive `InferenceService` resources before the first training workflows are started. Until you run [Installation 04](./04-data-generation-and-model-training.md), the storage initializer can log `No model found` and KServe will keep retrying because the initial model artifacts are not in object storage yet.

Check:

```sh
oc get inferenceservice -n ani-datascience
oc get pods -n ani-datascience | rg 'ani-predictive'
```

Then run the training flow from Installation 04 and wait for these workflows to finish with `Succeeded`:

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
oc get nodes -o go-template='{{range .items}}{{.metadata.name}}{{"\t"}}{{index .status.allocatable "nvidia.com/gpu"}}{{"\n"}}{{end}}'
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
CONTROL_PLANE_HOST="$(oc get route control-plane -n ani-runtime -o jsonpath='{.status.ingress[0].host}')"
curl -k "https://${CONTROL_PLANE_HOST}/integrations/status" \
  -H "x-api-key: demo-token" | python3 -m json.tool
```

Then:

1. Import the AAP license and finish any first-login prompts in the AAP UI.
2. Confirm `AAP_AUTOMATION_ENABLED`, `EDA_AUTOMATION_ENABLED`, and `AUTOMATION_BOOTSTRAP_ON_STARTUP` are all `"true"` in `aap-automation-config`.
3. If the license was imported after the platform was already running, restart `deployment/control-plane` in `ani-runtime` once so it immediately retries bootstrap against the now-licensed AAP APIs.
4. Wait for the control-plane bootstrap retries to reconcile the templates and activations.
5. Recheck `integrations/status` until:
   - `aap.bootstrapped=true`
   - `aap.project_exists=true`
   - `aap.kubernetes_credential_exists=true`
   - every AAP action and callback template shows `template_exists=true`
   - `eda.bootstrapped=true`
   - every EDA policy activation shows `status=running`

```sh
oc rollout restart deployment/control-plane -n ani-runtime
oc rollout status deployment/control-plane -n ani-runtime
```

## AAP Rule Audit Still Shows Failed `DELETED` Rows

Older Rule Audit entries are historical. If AAP or EDA failed before the license import completed, those failed rows stay visible even after the current activations are healthy.

Check the current health first:

```sh
CONTROL_PLANE_HOST="$(oc get route control-plane -n ani-runtime -o jsonpath='{.status.ingress[0].host}')"
EDA_HOST="$(oc get route aap-eda -n aap -o jsonpath='{.status.ingress[0].host}')"
EDA_PASS="$(oc extract -n aap secret/aap-eda-admin-password --to=- --keys=password 2>/dev/null | tail -n 1)"

curl -k "https://${CONTROL_PLANE_HOST}/integrations/status" \
  -H "x-api-key: demo-token" | python3 -m json.tool

curl -ksu "admin:${EDA_PASS}" "https://${EDA_HOST}/api/eda/v1/activations/?page_size=20" \
  | python3 -m json.tool
```

Expected result:

- `eda.bootstrapped=true`
- both ANI activations show `status=running`
- the activation ids are non-zero

If you want a fresh Rule Audit proof point, create one synthetic critical incident:

```sh
INCIDENT_ID="eda-health-$(date +%s)"
CONTROL_PLANE_HOST="$(oc get route control-plane -n ani-runtime -o jsonpath='{.status.ingress[0].host}')"

curl -ksS -X POST "https://${CONTROL_PLANE_HOST}/incidents" \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: demo-token' \
  --data-binary @- <<JSON
{
  "incident_id": "${INCIDENT_ID}",
  "project": "ani-demo",
  "anomaly_score": 0.98,
  "anomaly_type": "network_degradation",
  "predicted_confidence": 0.98,
  "class_probabilities": {"network_degradation": 0.98, "normal_operation": 0.02},
  "top_classes": [{"label": "network_degradation", "probability": 0.98}],
  "is_anomaly": true,
  "model_version": "ani-predictive-fs",
  "feature_window_id": "eda-health-${INCIDENT_ID}",
  "feature_snapshot": {"scenario_name": "network_degradation", "source": "manual-health-check"},
  "severity": "Critical",
  "source_system": "manual-health-check",
  "auto_generate_rca": true
}
JSON
```

Wait about 30 seconds, then recheck Rule Audit:

```sh
EDA_HOST="$(oc get route aap-eda -n aap -o jsonpath='{.status.ingress[0].host}')"
EDA_PASS="$(oc extract -n aap secret/aap-eda-admin-password --to=- --keys=password 2>/dev/null | tail -n 1)"

curl -ksu "admin:${EDA_PASS}" "https://${EDA_HOST}/api/eda/v1/audit-rules/?page_size=20" \
  | python3 -m json.tool
```

Expected result:

- the newest `Escalate critical incidents for coordination` row is `successful`
- the newest `Execute the low-risk signaling guardrail` row is `successful`
- the newest rows reference live activation names, not `DELETED`

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

First check whether the traffic generators are still firing:

```sh
oc get cronjob -n ani-runtime | rg 'demo-incident-pulse'
oc get cronjob -n ani-sipp | rg 'sipp-'
```

If the `ani-sipp` CronJobs are unsuspended and recent Jobs are still being created, the usual failure mode is no longer the scheduler. The more common problem is that the live predictive model returned `is_anomaly=false` for a scenario CronJob, so the SIPp runner finished with `incident: null` and nothing new showed up in the UI.

Run one anomaly job manually:

```sh
STORM_JOB="sipp-storm-check-$(date +%s)"
oc create job --from=cronjob/sipp-registration-storm "${STORM_JOB}" -n ani-sipp
oc wait --for=condition=complete "job/${STORM_JOB}" -n ani-sipp --timeout=5m
oc logs "job/${STORM_JOB}" -n ani-sipp
```

Interpret the job output:

- If the log contains a populated `incident` object, the control-plane path is still working.
- If the log ends with `"incident": null`, the feature window was uploaded but no incident was created.

If you hit the `incident: null` case, confirm the anomaly CronJobs have scenario-label fallback enabled:

```sh
oc get cronjob sipp-registration-storm -n ani-sipp \
  -o jsonpath='{range .spec.jobTemplate.spec.template.spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}'
oc get cronjob sipp-malformed-invite -n ani-sipp \
  -o jsonpath='{range .spec.jobTemplate.spec.template.spec.containers[0].env[*]}{.name}={.value}{"\n"}{end}'
```

Expected for anomaly CronJobs:

- `SIPP_EMIT_CONTROL_PLANE_INCIDENT=true`
- `CONTROL_PLANE_INCIDENT_REQUIRED=true`
- `SIPP_FALLBACK_TO_SCENARIO_LABELER=true`

Expected for `sipp-normal-traffic`:

- no `SIPP_FALLBACK_TO_SCENARIO_LABELER` override

Then check the latest incident list directly:

```sh
CONTROL_PLANE_HOST="$(oc get route control-plane -n ani-runtime -o jsonpath='{.status.ingress[0].host}')"
curl -ksS "https://${CONTROL_PLANE_HOST}/incidents?limit=5" \
  -H "x-api-key: demo-token" | python3 -m json.tool
```

Recovery:

1. Sync the cluster to a revision where the anomaly CronJobs include `SIPP_FALLBACK_TO_SCENARIO_LABELER=true`.
2. Re-run one anomaly job manually.
3. Confirm the job log now shows a non-null `incident`.
4. Confirm the new incident appears in `/incidents`.

On the current `main` branch, this is fixed for the anomaly traffic CronJobs in `ani-sipp`.
