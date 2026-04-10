# Installation 02: Install The Platform

## Objective

Deploy the platform on a fresh cluster through the GitOps path and bring it to a usable state.

## Before You Start

- Log in to the target cluster with cluster-admin access.
- Check out the git branch you want Argo CD to follow.
- **GPU workers:** GitOps installs the NVIDIA GPU Operator, Node Feature Discovery, and OpenShift AI model serving that targets GPU runtimes (including vLLM). Use a cluster or machine pool with **GPU-capable worker nodes** and enough capacity for the operator DaemonSets; without that, the `ani-datascience` slice and inference validation in this guide typically will not converge.

If the cluster does not already expose a GPU worker, add one before continuing:

```sh
oc get nodes -l node-role.kubernetes.io/gpu
make add-gpu-node-pool
oc get machineset -n openshift-machine-api | rg 'gpu'
oc get nodes -l node-role.kubernetes.io/gpu
```

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

## 4. Confirm Argo CD Is Tracking The Expected Branch

```sh
oc get applications.argoproj.io -n openshift-gitops
oc get applications.argoproj.io -n openshift-gitops -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.source.targetRevision}{"\n"}{end}'
```

Continue when the child applications exist and the `targetRevision` is the branch you pushed.

## 5. Trigger The First Image Build

The first Git push seeds GitOps state, but it does not populate all runtime images. Run the build pipeline once:

```sh
make trigger-build-pipeline
oc get pipelinerun -n ani-tekton
oc get is -n ani-runtime
oc get is -n ani-sipp
oc get is -n ani-datascience
```

## 6. Wait For Core Workloads

```sh
oc get deploy -n ani-runtime
oc get deploy -n ani-sipp
oc get dsc -n redhat-ods-operator
oc get dspa,featurestore,inferenceservice -n ani-datascience
```

Continue when:

- `ani-runtime` deployments are available
- `ani-sipp` deployments are no longer waiting on missing images
- `default-dsc` is `Ready=True`
- `dspa` exists
- `ani-featurestore` is `Ready`
- the predictive `InferenceService` resources are `READY=True`

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

4. Verify the integration state:

```sh
CONTROL_PLANE_HOST="$(oc get route control-plane -n ani-runtime -o jsonpath='{.spec.host}')"
curl -k "https://${CONTROL_PLANE_HOST}/integrations/status" \
  -H "x-api-key: demo-token" | python3 -m json.tool
```

Expected result after the AAP license is imported:

- `aap.configured=true`
- `aap.live_configured=true`
- `aap.bootstrapped=true`
- `eda.configured=true`
- `eda.live_configured=true`
- `eda.bootstrapped=true`
