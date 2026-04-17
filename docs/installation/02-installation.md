# Installation 02: Install The Platform

## Objective

Deploy the platform on a fresh cluster through the GitOps path and bring it to a usable state.

## Before You Start

- Log in to the target cluster with cluster-admin access.
- For the default GitOps install path, use `main`. If your change requires new runtime images, push or merge it to `main` first and wait for the external Quay builds to finish before expecting the cluster to converge on those image changes.
- This installation flow is only tested on OpenShift on AWS.
- **GPU workers:** GitOps installs the NVIDIA GPU Operator, Node Feature Discovery, and OpenShift AI model serving that targets GPU runtimes (including vLLM). Use a cluster or machine pool with **GPU-capable worker nodes** and enough capacity for the operator DaemonSets; without that, the `ani-datascience` slice will not fully converge and the LLM-backed RCA flow will not work.

If the AWS cluster does not already expose allocatable GPU capacity, add a GPU worker before continuing:

```sh
oc get nodes -o go-template='{{range .items}}{{.metadata.name}}{{"\t"}}{{index .status.allocatable "nvidia.com/gpu"}}{{"\n"}}{{end}}'
make add-gpu-node-pool
oc get machineset -n openshift-machine-api | rg 'gpu'
oc get nodes -o go-template='{{range .items}}{{.metadata.name}}{{"\t"}}{{index .status.allocatable "nvidia.com/gpu"}}{{"\n"}}{{end}}'
```

Continue only when:

- the GPU MachineSet exists
- at least one node reports a non-empty `GPU` value such as `1`

A GPU label by itself is not enough for the vLLM workload.

```sh
git branch --show-current
```

## 1. Deploy Gitea

```sh
oc apply -k deploy/gitea
oc rollout status deployment/gitea -n gitea
```

## 2. Push The Branch To In-Cluster Gitea

```sh
GITEA_HOST="$(oc get route gitea -n gitea -o jsonpath='{.spec.host}')"
GIT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
GIT_SSL_NO_VERIFY=true git push "https://gitadmin:GiteaAdmin123%21@${GITEA_HOST}/gitadmin/IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI.git" "HEAD:${GIT_BRANCH}"
```

## 3. Bootstrap Argo CD

```sh
if oc get namespace openshift-gitops >/dev/null 2>&1; then
  oc delete job -n openshift-gitops argocd-bootstrap-application-v3 --ignore-not-found
fi
oc apply -k deploy/argocd
```

Follow the bootstrap Job while it creates the root Argo CD applications. On the current branch it waits for the OpenShift AI operator subscription and CSV before it creates `ani-platform`, so this step can take several minutes on a fresh cluster:

```sh
oc get job,pod -n openshift-gitops | rg 'argocd-bootstrap'
oc logs -n openshift-gitops job/argocd-bootstrap-application-v3 -f
```

If you see `The Job "argocd-bootstrap-application-v3" is invalid ... field is immutable`, the cluster is still holding the previous Job object. Delete it and rerun the bootstrap command block above.

## 4. Confirm Argo CD Is Tracking The Expected Branch

```sh
oc get applications.argoproj.io -n openshift-gitops
oc get applications.argoproj.io -n openshift-gitops -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.source.targetRevision}{"\n"}{end}'
```

Continue when the child applications exist and the `targetRevision` is the branch you pushed.

On a fresh cluster this can take several minutes. It is normal for the bootstrap Job to wait on the `rhods-operator` CSV before `ani-platform` appears, and it is normal for `ani-tekton` to retry once while the Tekton CRDs are still being installed.

## 5. Trigger The First GitOps-Managed Pipeline Jobs

The default GitOps path on the current branch uses the published Quay images referenced by the manifests. Quay builds are triggered from `main`, not from ad hoc in-cluster bootstrap Jobs, and new image builds can take several minutes to appear after a push. On a fresh cluster you should **not** start with the older bootstrap build or `make trigger-build-pipeline` unless you are intentionally testing the in-cluster image-build flow.

GitOps still only creates the KFP pipeline definitions and their background auto-run `CronJob`s. A fresh cluster does not have the initial dataset, feature-bundle, feature-store, model-registry, or incident-release artifacts until those jobs run. If you want the cluster to converge immediately instead of waiting for the scheduled offsets, create one Job from each `CronJob` in this order and wait for each submitted workflow to finish before starting the next one:

```sh
oc get cronjob -n ani-datascience | rg 'kfp-auto-run'

ANOMALY_JOB="ani-kfp-auto-run-manual-$(date +%s)"
oc create job --from=cronjob/ani-kfp-auto-run "${ANOMALY_JOB}" -n ani-datascience
oc logs -f -n ani-datascience "job/${ANOMALY_JOB}"

FEATURE_BUNDLE_JOB="ani-feature-bundle-kfp-auto-run-manual-$(date +%s)"
oc create job --from=cronjob/ani-feature-bundle-kfp-auto-run "${FEATURE_BUNDLE_JOB}" -n ani-datascience
oc logs -f -n ani-datascience "job/${FEATURE_BUNDLE_JOB}"

FEATURESTORE_JOB="ani-featurestore-kfp-auto-run-manual-$(date +%s)"
oc create job --from=cronjob/ani-featurestore-kfp-auto-run "${FEATURESTORE_JOB}" -n ani-datascience
oc logs -f -n ani-datascience "job/${FEATURESTORE_JOB}"

INCIDENT_RELEASE_JOB="ani-incident-release-kfp-auto-run-manual-$(date +%s)"
oc create job --from=cronjob/ani-incident-release-kfp-auto-run "${INCIDENT_RELEASE_JOB}" -n ani-datascience
oc logs -f -n ani-datascience "job/${INCIDENT_RELEASE_JOB}"

oc get jobs,wf -n ani-datascience
```

Those Jobs only submit the KFP runs. The actual pipeline execution happens through the workflow objects in `ani-datascience`, so keep watching `oc get wf -n ani-datascience` until the corresponding workflows reach `Succeeded`.

## 6. Wait For Core Workloads

```sh
oc get deploy -n ani-runtime
oc get deploy -n ani-sipp
oc get dsc -n redhat-ods-operator
oc get dspa,featurestore,servingruntime,inferenceservice -n ani-datascience
oc get jobs,wf -n ani-datascience
oc get pipelines.pipelines.kubeflow.org,pipelineversions.pipelines.kubeflow.org -n ani-datascience
oc get cronjob -n ani-datascience | rg 'kfp-auto-run'
oc get modelregistries.modelregistry.opendatahub.io -n rhoai-model-registries
```

Continue when:

- `ani-runtime` deployments are available
- `ani-sipp` deployments are no longer waiting on missing images
- `default-dsc` is `Ready=True`
- `dspa` is `Ready`
- `ani-featurestore` is `Ready`
- the initial manual or scheduled KFP Jobs exist in `ani-datascience`
- the corresponding workflow objects reach `Succeeded`
- the KFP `Pipeline` and `PipelineVersion` resources exist in `ani-datascience`
- the KFP auto-run `CronJob` resources exist in `ani-datascience`
- the serving runtimes exist in `ani-datascience`
- `default-modelregistry` exists in `rhoai-model-registries`
- `ani-predictive-fs` and `ani-predictive-backfill-modelcar` report `READY=True`

At this point GitOps has created the OpenShift AI resources, reconciled the KFP pipeline definitions as Kubernetes CRs, and created non-hook auto-run `CronJob`s that submit the live-path workflows. The model registry resource itself appears before it has any registered models; the first successful feature-store workflow is what creates the initial model versions and the S3 artifacts consumed by `ani-predictive-fs`.

If `ani-predictive-fs` starts as `READY=False`, watch the workflows in [Installation 04: Data Generation And Model Training](./04-data-generation-and-model-training.md) or rerun the `CronJob`-backed submitters above. KServe retries automatically after the model artifacts appear. If `llama-32-3b-instruct` stays `Pending` with `Insufficient nvidia.com/gpu`, the RCA generation flow will stay degraded until you add a GPU worker. Use [Troubleshooting](./troubleshooting.md).

## 7. List The Main Routes

```sh
oc get route -n ani-runtime
oc get route -n ani-sipp
oc get route -n ani-data
oc get route -n plane
```

At this point you should have enough to open the demo UI, OpenIMS WebUI, Attu, MinIO, and Plane.

## 8. Finish AAP And EDA After AAP License Import

1. Get the AAP routes and admin passwords:

```sh
oc get route -n aap
oc extract -n aap secret/aap-admin-password --to=- --keys=password
oc extract -n aap secret/aap-controller-admin-password --to=- --keys=password
oc extract -n aap secret/aap-eda-admin-password --to=- --keys=password
```

2. Import the AAP license in the AAP UI and complete any first-login prompts.

3. Wait for the control-plane bootstrap worker to finish creating the controller-side inventory, project, Kubernetes credential, job templates, callback templates, EDA project, decision environment, and activations. The GitOps runtime config enables AAP and EDA by default, there is no separate `ani-remediation` bootstrap application anymore, and the control-plane retries bootstrap automatically until the AAP APIs accept writes.

4. If AAP was already running without a license and you only imported the license later, force an immediate retry instead of waiting for the next background attempt:

```sh
oc rollout restart deployment/control-plane -n ani-runtime
oc rollout status deployment/control-plane -n ani-runtime --timeout=5m
```

5. Verify the integration state:

```sh
CONTROL_PLANE_HOST="$(oc get route control-plane -n ani-runtime -o jsonpath='{.status.ingress[0].host}')"
curl -k "https://${CONTROL_PLANE_HOST}/integrations/status" \
  -H "x-api-key: demo-token" | python3 -m json.tool
```

Expected result after the AAP license is imported:

- `aap.configured=true`
- `aap.live_configured=true`
- `aap.bootstrapped=true`
- `aap.project_exists=true`
- `aap.kubernetes_credential_exists=true`
- all AAP `template_exists=true`
- `eda.configured=true`
- `eda.live_configured=true`
- `eda.bootstrapped=true`
- all EDA activations show `status=running`

The AAP Rule Audit view can keep older failed rows from activations that were created before the license was imported. Treat the newest rows that reference the live activation names, not `DELETED`, as the current health signal.
