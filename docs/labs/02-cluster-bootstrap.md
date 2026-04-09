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
GIT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
GIT_SSL_NO_VERIFY=true git push "https://gitadmin:GiteaAdmin123%21@${GITEA_HOST}/gitadmin/IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI.git" "HEAD:${GIT_BRANCH}"
```

4. Bootstrap OpenShift GitOps and let Argo CD manage the operator subscriptions:

```sh
oc apply -k deploy/argocd
```

5. Confirm the `ims-operators` and `ims-platform` Argo CD applications have both been created by the bootstrap job.
6. Once the operator installs are healthy and the operator CSVs you need are `Succeeded`, wait for the `ims-platform` root application to create and reconcile the split child apps.
7. Do not expect the first repo push above to build images yet. The Tekton `EventListener` is created later by the split GitOps apps in Lab 03, so the first push only seeds the in-cluster GitOps source.
8. In Lab 03, after Argo CD has synced the `ims-platform` root application and created the `ims-tekton` child app, trigger the first image population into the internal registry by either:
   - pushing a new commit to the same branch you pushed above into the in-cluster Gitea repository, which starts the Tekton pipeline automatically
   - or running `make trigger-build-pipeline` to create a `PipelineRun` for `ims-platform-container-build`
9. If you skip that first image build, workloads that reference `image-registry.openshift-image-registry.svc:5000/...:latest` can remain in `ImagePullBackOff`.
10. Review the demo API token secret and service monitors created under `k8s/base/platform` and `k8s/base/observability`.
11. The `ims-platform` application now fans out into child Argo CD apps for `ims-sipp`, `ims-runtime`, `ims-data`, `ims-datascience`, `ims-tekton`, `ims-observability`, `aap`, and `plane`.
12. Do not create a separate Feature Store manually in the OpenShift AI UI. The repo-managed `FeatureStore/ims-featurestore` is part of the GitOps path.
13. AAP/EDA stay intentionally disabled by default for fresh clusters. When the in-cluster Plane base is enabled, the Plane bootstrap job seeds the demo Plane admin, workspace, and project, then populates `plane-integration-auth` automatically. The GitOps-managed LLM path now points at the in-cluster vLLM service when GPU capacity exists; otherwise the RCA service stays on the local fallback path.

## Notes

- The GitOps layout is intentionally opinionated for a split namespace topology: `ims-sipp`, `ims-runtime`, `ims-data`, `ims-datascience`, `ims-tekton`, `ims-observability`, `aap`, and `plane`.
- The GitOps source for this demo is the in-cluster Gitea repository, not an external Git provider.
- Demo Gitea credentials are `gitadmin` / `GiteaAdmin123!`.
- The GitOps bootstrap applies the `ims-demo` Argo project, `ims-operators`, and the `ims-platform` root application automatically.
- After the bootstrap finishes, use Git pushes plus Argo reconciliation instead of imperative `oc apply` against the runtime manifests.
- The initial `git push` seeds GitOps state only. Tekton image builds begin only after the `ims-tekton` child app has created the pipeline and trigger resources.
- The Feature Store instance, Kafka resources, and KFP bootstrap jobs are now part of the split GitOps apps, so the GitOps path is the primary fresh-cluster bring-up path again.
- Raw KServe deployment mode is used to keep the serving path simpler than a full serverless mesh install.
- Demo model storage is provided by an in-cluster MinIO deployment with the default credentials `minioadmin` / `minioadmin`.
- `demo-incident-pulse` and the `sipp-*` CronJobs also depend on the internal registry images from the first Tekton build.
- This repo assumes a model registry endpoint reachable as `ims-demo-modelregistry` in `rhoai-model-registries`; verify that service in Lab 04 or update the endpoint references for your cluster.
- `plane-integration-auth` remains a safe-by-default placeholder in Git, but the Plane bootstrap job now seeds the demo Plane records and repopulates the secret automatically when Plane is deployed. The default split GitOps path now wires `llm-provider-config` to the in-cluster vLLM endpoint automatically; `aap-automation-config` still stays placeholder-only until you intentionally configure AAP/EDA.
- If GPU-backed generative serving is required, add the appropriate cluster node labeling and accelerator operator prerequisites before applying the vLLM workload.
