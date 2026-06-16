# OpenShift Manifests

This directory contains the OpenShift deployment assets for the full demo platform.

## Structure

```text
base/
  namespaces/   namespace RBAC and baseline defaults
  builds/       upstream BuildConfig assets for OpenIMSs and SIPp
  kagenti/      vendored upstream Kagenti charts kept with base assets
  ims/          IMS lab workloads
  traffic/      SIPp scenario runners
  platform/     feature, anomaly, RCA, and UI services
  feature-store/ cluster defaults for the Feature Store and Model Registry path
  milvus/       vector database stack
  kfp/          OpenShift AI DSPA and Kubeflow pipeline bootstrap
  serving/      KServe runtimes and inference services
  rhoai/        OpenShift AI platform operators and cluster config
manual/
  traffic-backfill-100k/ on-demand SIPp backfill jobs outside the demo overlay
overlays/
  gitops/       split app-specific deployment bundles consumed by Argo CD
```

Operator subscriptions are managed separately through `deploy/argocd` and `deploy/gitops/operators`, while the root Argo CD app-of-apps is rendered from `deploy/gitops/apps`.

Vendored third-party packaged assets that are not yet rendered into the main
GitOps output can still live under `base/` when they are part of the platform
source tree, such as `base/kagenti/`.

## Render

```sh
kustomize build deploy/gitops/apps
```
