# IMS Anomaly Detection and RCA Demo Platform

This repository packages an OpenShift-native demo stack for IMS anomaly detection and root cause analysis. The target deployment model is:

- IMS lab services on OpenShift
- SIPp-driven traffic generation and fault injection
- in-cluster Gitea as the GitOps source of truth
- operator installation managed by OpenShift GitOps and Argo CD
- predictive and generative model serving on KServe
- Milvus-backed RCA context retrieval
- Attu UI for Milvus inspection through an OpenShift route
- customer-demo documentation and lab guides

## What is in scope

- OpenShift manifests organized with Kustomize
- actual OpenIMSs upstream runtime images and configuration adapted for OpenShift
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
GIT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
if git remote get-url cluster-gitea >/dev/null 2>&1; then
  git remote set-url cluster-gitea "https://${GITEA_HOST}/gitadmin/IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI.git"
else
  git remote add cluster-gitea "https://${GITEA_HOST}/gitadmin/IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI.git"
fi
GIT_SSL_NO_VERIFY=true git push "https://gitadmin:GiteaAdmin123%21@${GITEA_HOST}/gitadmin/IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI.git" "HEAD:${GIT_BRANCH}"
```

Demo credentials for Gitea:

- user: `gitadmin`
- password: `GiteaAdmin123!`

Demo API tokens for the platform services:

- `demo-token` for admin, operator, and automation flows
- `demo-operator-token` for operator-only access
- `demo-viewer-token` for read-limited browser testing
- Slack and Jira actions default to an in-platform demo relay if live credentials are not supplied
- the current `scale_scscf` remediation path is wired to live AAP-backed execution in the demo deployment
- if AAP controller API writes are license-blocked, the platform falls back to an AAP runner job and still updates the incident execution state
- local or non-AAP playbook actions still default to simulated execution unless `AUTOMATION_MODE=execute`
- OpenIMSs WebUI uses the upstream demo credentials `admin` / `1423`

5. Bootstrap Argo CD and the operator subscriptions:

```sh
oc apply -k deploy/argocd
```

6. Render the split GitOps application set:

```sh
make kustomize-gitops
```

7. Follow the lab sequence in `docs/labs`.

## Upstream reference inputs

This repo uses upstream projects as implementation inputs, but keeps the deployment model OpenShift-native:

- NetSentinel Tekton YAML patterns: `https://github.com/rh-telco-tigers/NetSentinel/tree/main/k8s`
- OpenIMSs build and runtime contracts: `https://github.com/VoicenterTeam/openimss`
- SIPp source build inputs: `https://github.com/SIPp/sipp`

## Current implementation boundary

The repository now contains a deployable scaffold for the full demo stack. Operators are installed through Argo CD from `deploy/gitops/operators`, the root app-of-apps lives in `deploy/gitops/apps`, and the workload slices are rendered from `k8s/overlays/gitops`.

Cluster-specific values still need to be supplied before a live deployment:

- image registry destinations for locally built services
- if you want to override the default in-cluster vLLM endpoint, update the GitOps-managed `llm-provider-config` values for your target provider
- route hostnames and TLS policy for the target cluster
- the repository must be pushed into the in-cluster Gitea instance before Argo CD bootstrap

Current live remediation notes:

- the checked-in AAP-backed example is `automation/ansible/playbooks/scale-scscf.yaml`
- the RBAC and configuration bootstrap for that flow is `k8s/base/platform/aap-remediation-rbac.yaml`
