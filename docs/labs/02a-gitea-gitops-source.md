# Lab 02A: Gitea GitOps Source

## Objective

Use an in-cluster Gitea instance as the Git source for Argo CD so the demo can run without depending on GitHub.

## Demo-only credentials

- user: `gitadmin`
- password: `GiteaAdmin123!`

These credentials are intentionally hardcoded for demo portability and should not be reused outside this lab setup.

## Steps

1. Deploy Gitea:

```sh
oc apply -k deploy/gitea
```

2. Capture the public route:

```sh
oc get route gitea -n gitea
```

3. Push the current repository into the cluster-hosted repo:

```sh
GITEA_HOST="$(oc get route gitea -n gitea -o jsonpath='{.spec.host}')"
if git remote get-url cluster-gitea >/dev/null 2>&1; then
  git remote set-url cluster-gitea "https://${GITEA_HOST}/gitadmin/IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI.git"
else
  git remote add cluster-gitea "https://${GITEA_HOST}/gitadmin/IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI.git"
fi
GIT_SSL_NO_VERIFY=true git push "https://gitadmin:GiteaAdmin123!@${GITEA_HOST}/gitadmin/IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI.git" main:main
```

4. Bootstrap Argo CD:

```sh
oc apply -k deploy/argocd
```

5. Verify the Argo CD project and application point to the in-cluster repo:

```sh
oc get appproject ims-demo -n openshift-gitops -o jsonpath='{.spec.sourceRepos[0]}{"\n"}'
oc get application.argoproj.io ims-demo-operators -n openshift-gitops -o jsonpath='{.spec.source.repoURL}{"\n"}'
```

Expected value:

```text
http://gitea-http.gitea.svc.cluster.local:3000/gitadmin/IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI.git
```

## Important Trigger Behavior

- `deploy/gitea` creates a Gitea webhook that targets the Tekton `EventListener` service at `el-ims-demo-gitea.ims-demo-lab.svc.cluster.local:8080`.
- That webhook does not become active until the demo overlay applies the Tekton trigger resources from `k8s/base/pipelines`.
- The first push in this lab seeds the in-cluster GitOps source for Argo CD, but it does not build images yet.
- After Argo CD has synced the `ims-demo-platform` application for `k8s/overlays/demo`, a later push to `main` triggers the Tekton build automatically.
- If you need the first image build without adding another commit, start a `PipelineRun` manually for `ims-demo-container-build`.

## Why this flow exists

- The demo becomes self-contained inside the OpenShift cluster.
- Argo CD can continue reconciling even if external GitHub access is unavailable.
- The same bootstrap pattern can be reused on a fresh sandbox cluster without changing the manifests.
