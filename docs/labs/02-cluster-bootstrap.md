# Lab 02: Cluster Bootstrap

## Objective

Install the platform prerequisites and render the GitOps-managed core demo bundle.

## Steps

1. Create or select an OpenShift cluster with cluster-admin access.
2. Deploy the in-cluster Gitea instance:

```sh
oc apply -k deploy/gitea
```

3. Confirm the Gitea route and push this repository into the cluster-hosted repo:

```sh
GITEA_HOST="$(oc get route gitea -n gitea -o jsonpath='{.spec.host}')"
GIT_SSL_NO_VERIFY=true git push "https://gitadmin:GiteaAdmin123!@${GITEA_HOST}/gitadmin/IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI.git" main:main
```

4. Bootstrap OpenShift GitOps and let Argo CD manage the operator subscriptions:

```sh
oc apply -k deploy/argocd
```

5. Review the demo overlay:

```sh
make kustomize-demo
```

6. Confirm the `ims-demo-operators` Argo CD application has synced the operator subscriptions from `deploy/gitops/operators`.
7. Do not expect the first repo push above to build images yet. The Tekton `EventListener` is created later by the demo overlay in Lab 03, so the first push only seeds the in-cluster GitOps source.
8. In Lab 03, after Argo CD has synced the `ims-demo-platform` application for `k8s/overlays/demo`, trigger the first image population into the internal registry by either:
   - pushing a new commit to `main` in the in-cluster Gitea repository, which starts the Tekton pipeline automatically
   - or running `make trigger-build-pipeline` to create a `PipelineRun` for `ims-demo-container-build`
9. If you skip that first image build, workloads that reference `image-registry.openshift-image-registry.svc:5000/...:latest` can remain in `ImagePullBackOff`.
10. Review the demo API token secret and service monitors created under `k8s/base/platform` and `k8s/base/observability`.
11. The `ims-demo-platform` application syncs only `k8s/overlays/demo`, which is the core platform overlay. It does not include `k8s/base/feature-store`, `k8s/base/kafka`, or `k8s/base/kfp`.
12. In Lab 04, apply those AI extras explicitly with `make apply-demo-ai-extras`. Do not create a separate Feature Store manually in the OpenShift AI UI.
13. Treat the checked-in Plane and LLM defaults as demo placeholders. On a fresh cluster, patch `plane-integration-auth`, `llm-provider-config`, and any model-registry endpoint assumptions before expecting those integrations to work.

## Notes

- The overlay is intentionally opinionated for a demo namespace, `ims-demo-lab`.
- The GitOps source for this demo is the in-cluster Gitea repository, not an external Git provider.
- Demo Gitea credentials are `gitadmin` / `GiteaAdmin123!`.
- The GitOps bootstrap applies only standard Kubernetes and OLM resources; the bootstrap job waits for the GitOps CRDs before creating the Argo CD application.
- After bootstrap, the `ims-demo-platform` Argo CD application owns `k8s/overlays/demo`; use Git pushes plus Argo reconciliation instead of `oc apply -k k8s/overlays/demo`.
- The initial `git push` seeds GitOps state only. Tekton image builds begin only after the demo overlay has created the pipeline and trigger resources.
- The Feature Store instance, Kafka resources, and KFP bootstrap jobs are repo-managed, but they are intentionally applied later in Lab 04 rather than through `k8s/overlays/demo`.
- Raw KServe deployment mode is used to keep the serving path simpler than a full serverless mesh install.
- Demo model storage is provided by an in-cluster MinIO deployment with the default credentials `minioadmin` / `minioadmin`.
- `demo-incident-pulse` and the `sipp-*` CronJobs also depend on the internal registry images from the first Tekton build.
- This repo assumes a model registry endpoint reachable as `ims-demo-modelregistry` in `rhoai-model-registries`; verify that service in Lab 04 or update the endpoint references for your cluster.
- `plane-integration-auth` and `llm-provider-config` include environment-specific demo defaults for the current sandbox and are not universal fresh-cluster values.
- If GPU-backed generative serving is required, add the appropriate cluster node labeling and accelerator operator prerequisites before applying the vLLM workload.
