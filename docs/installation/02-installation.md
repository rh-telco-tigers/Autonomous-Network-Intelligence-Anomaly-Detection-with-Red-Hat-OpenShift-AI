# Installation 02: Install The Platform

## Objective

Deploy the platform on a fresh cluster through the GitOps path and bring it to a usable state.

On the current branch, a first-time deployer can bring up the base platform and the RHOAI path by following this guide. The remaining manual install step is the Ansible Lightspeed API token described in [Installation 05](./05-remediation-using-ansible-lightspeed.md).

## Before You Start

- Log in to the target cluster with cluster-admin access.
- Check out the git branch you want Argo CD to follow.
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
oc apply -k deploy/argocd
```

Follow the bootstrap Job while it creates the root Argo CD applications. On the current branch it waits for the OpenShift AI operator subscription and CSV before it creates `ani-platform`, so this step can take several minutes on a fresh cluster:

```sh
oc get job,pod -n openshift-gitops | rg 'argocd-bootstrap'
oc logs -n openshift-gitops job/argocd-bootstrap-application-v3 -f
```

## 4. Confirm Argo CD Is Tracking The Expected Branch

```sh
oc get applications.argoproj.io -n openshift-gitops
oc get applications.argoproj.io -n openshift-gitops -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.source.targetRevision}{"\n"}{end}'
```

Continue when the child applications exist and the `targetRevision` is the branch you pushed.

On a fresh cluster this can take several minutes. It is normal for the bootstrap Job to wait on the `rhods-operator` CSV before `ani-platform` appears, and it is normal for `ani-tekton` to retry once while the Tekton CRDs are still being installed.

If you want to build a branch through Tekton later, the current pipeline now builds into the OpenShift internal registry by default and offers an optional final Quay publish stage. That keeps the normal build path secret-free while still allowing manual branch publication when you explicitly provide the `tekton-quay-push` secret and enable the extra PipelineRun params in [k8s/manual/demo-triggers/tekton-build-pipelinerun.yaml](/Users/bkpandey/Documents/workspace/activepoc/IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI/k8s/manual/demo-triggers/tekton-build-pipelinerun.yaml).

## 5. Wait For The First GitOps-Managed Pipeline Jobs

The default GitOps path on the current branch uses the published Quay images referenced by the manifests. On a fresh cluster you should **not** start with the older bootstrap build or `make trigger-build-pipeline` unless you are intentionally testing the in-cluster image-build flow.

GitOps creates the KFP pipeline definitions and their background auto-run `CronJob`s. On a healthy fresh cluster, those CronJobs submit the first live-path runs automatically after `ani-datascience` is ready, and the full stack converges without any extra bootstrap Job.

Check the CronJobs and their first scheduled Jobs:

```sh
oc get cronjob -n ani-datascience | rg 'kfp-auto-run'
oc get jobs,wf -n ani-datascience
```

Expected automatic sequence:

- `ani-kfp-auto-run` submits the anomaly training run first
- `ani-incident-release-kfp-auto-run` submits the incident-release run
- `ani-feature-bundle-kfp-auto-run` submits the feature-bundle publish run
- `ani-featurestore-kfp-auto-run` submits the feature-store train-and-register run

Those Jobs only submit KFP runs. The actual pipeline execution happens through the workflow objects in `ani-datascience`, so keep watching `oc get wf -n ani-datascience` until the corresponding workflows reach `Succeeded`.

If you want to accelerate the first convergence instead of waiting for the scheduled offsets, create one Job from each `CronJob` in this order and wait for each submitted workflow to finish before starting the next one:

```sh
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
- `ani-predictive`, `ani-predictive-fs`, and `ani-predictive-backfill-modelcar` report `READY=True`

At this point GitOps has created the OpenShift AI resources, reconciled the KFP pipeline definitions as Kubernetes CRs, and created non-hook auto-run `CronJob`s that submit the live-path workflows. The model registry resource itself appears before it has any registered models; the first successful feature-store workflow is what creates the initial model versions and the S3 artifacts consumed by `ani-predictive-fs`.

If `ani-predictive` or `ani-predictive-fs` starts as `READY=False`, watch the workflows in [Installation 04: Data Generation And Model Training](./04-data-generation-and-model-training.md) or rerun the `CronJob`-backed submitters above. KServe retries automatically after the model artifacts appear, but it can take a few minutes for the next clean storage-initializer attempt to observe the newly published objects. If `llama-32-3b-instruct` stays `Pending` with `Insufficient nvidia.com/gpu`, the RCA generation flow will stay degraded until you add a GPU worker. Use [Troubleshooting](./troubleshooting.md).

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

3. Wait for the control-plane bootstrap worker to finish creating the controller-side inventory, project, Kubernetes credential, job templates, callback templates, EDA project, decision environment, and activations. The GitOps runtime config now enables AAP and EDA by default and the control-plane retries bootstrap automatically until the AAP APIs accept writes.

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

The Ansible Lightspeed integration remains a manual step on the current branch. The platform does not create or manage the required Lightspeed secrets through Gitea. Before the remediation flow can become healthy, create both manual secrets in the `aap` namespace:

- `lightspeed-secret`
- `aap-lightspeed-chatbot-api-key`

Until those secrets exist, the Lightspeed workloads or the remediation Job `aap-controller-lightspeed-template-config` can stay degraded and `ani-remediation` can remain unhealthy. Use [Installation 05: Remediation Using Ansible Lightspeed](./05-remediation-using-ansible-lightspeed.md) to create those secrets and rerun the remediation sync.
