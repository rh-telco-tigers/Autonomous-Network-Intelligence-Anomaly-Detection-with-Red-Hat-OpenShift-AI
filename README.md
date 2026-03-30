# IMS Anomaly Detection and RCA Demo Platform

This repository packages an OpenShift-native demo stack for IMS anomaly detection and root cause analysis. The target deployment model is:

- IMS lab services on OpenShift
- SIPp-driven traffic generation and fault injection
- in-cluster Gitea as the GitOps source of truth
- operator installation managed by OpenShift GitOps and Argo CD
- predictive and generative model serving on KServe
- Milvus-backed RCA context retrieval
- customer-demo documentation and lab guides

## What is in scope

- OpenShift manifests organized with Kustomize
- FastAPI demo services for feature aggregation, anomaly scoring, and RCA orchestration
- Tekton image-build assets adapted from the NetSentinel reference repository
- Kubeflow pipeline source for the predictive training workflow
- automatic predictive artifact upload into the in-cluster MinIO model-storage bucket
- customer-facing demo documentation under `docs/labs`

## Repository layout

```text
ai/                 Kubeflow pipeline source and AI workflow stubs
automation/         Ansible playbooks for operator-approved actions
deploy/             Argo CD bootstrap and GitOps-managed operator manifests
docs/               customer-demo docs, labs, and architecture references
k8s/                OpenShift manifests and Kustomize overlays
lab-assets/         SIPp scenarios and reusable demo data
services/           demo services and UI source images
```

## Quick start

1. Review [docs/README.md](./docs/README.md).
2. Inspect the end-to-end architecture in [docs/architecture/engineering-spec.md](./docs/architecture/engineering-spec.md).
3. Deploy the in-cluster Gitea instance:

```sh
oc apply -k deploy/gitea
```

4. Push this repository into the cluster Gitea instance:

```sh
GITEA_HOST="$(oc get route gitea -n gitea -o jsonpath='{.spec.host}')"
if git remote get-url cluster-gitea >/dev/null 2>&1; then
  git remote set-url cluster-gitea "https://${GITEA_HOST}/gitadmin/IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI.git"
else
  git remote add cluster-gitea "https://${GITEA_HOST}/gitadmin/IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI.git"
fi
GIT_SSL_NO_VERIFY=true git push "https://gitadmin:GiteaAdmin123!@${GITEA_HOST}/gitadmin/IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI.git" main:main
```

Demo credentials for Gitea:

- user: `gitadmin`
- password: `GiteaAdmin123!`

5. Bootstrap Argo CD and the operator subscriptions:

```sh
oc apply -k deploy/argocd
```

6. Render the demo overlay:

```sh
make kustomize-demo
```

7. Follow the lab sequence in `docs/labs`.

## Upstream reference inputs

This repo uses upstream projects as implementation inputs, but keeps the deployment model OpenShift-native:

- NetSentinel Tekton YAML patterns: `https://github.com/rh-telco-tigers/NetSentinel/tree/main/k8s`
- OpenIMSs build and runtime contracts: `https://github.com/VoicenterTeam/openimss`
- SIPp source build inputs: `https://github.com/SIPp/sipp`

## Current implementation boundary

The repository now contains a deployable scaffold for the full demo stack. Operators are intended to be installed through Argo CD from `deploy/gitops/operators`, while the application stack remains under `k8s/overlays/demo`.

Cluster-specific values still need to be supplied before a live deployment:

- image registry destinations for locally built services
- OpenIMSs environment values appropriate for the target lab network
- route hostnames and TLS policy for the target cluster
- the repository must be pushed into the in-cluster Gitea instance before Argo CD bootstrap
