# Lab 02: Cluster Bootstrap

## Objective

Install the platform prerequisites and render the end-to-end deployment bundle.

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
7. Build or mirror the service images referenced by the overlay.
8. Review the demo API token secret and service monitors created under `k8s/base/platform` and `k8s/base/observability`.

## Notes

- The overlay is intentionally opinionated for a demo namespace, `ims-demo-lab`.
- The GitOps source for this demo is the in-cluster Gitea repository, not an external Git provider.
- Demo Gitea credentials are `gitadmin` / `GiteaAdmin123!`.
- The GitOps bootstrap applies only standard Kubernetes and OLM resources; the bootstrap job waits for the GitOps CRDs before creating the Argo CD application.
- Raw KServe deployment mode is used to keep the serving path simpler than a full serverless mesh install.
- Demo model storage is provided by an in-cluster MinIO deployment with the default credentials `minioadmin` / `minioadmin`.
- If GPU-backed generative serving is required, add the appropriate cluster node labeling and accelerator operator prerequisites before applying the vLLM workload.
