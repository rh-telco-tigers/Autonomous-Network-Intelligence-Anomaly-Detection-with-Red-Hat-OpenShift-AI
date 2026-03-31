# OpenShift Manifests

This directory contains the OpenShift deployment assets for the full demo platform.

## Structure

```text
base/
  namespaces/   demo namespaces
  builds/       upstream BuildConfig assets for OpenIMSs and SIPp
  ims/          IMS lab workloads
  traffic/      SIPp scenario runners
  platform/     feature, anomaly, RCA, and UI services
  milvus/       vector database stack
  kfp/          OpenShift AI DSPA and Kubeflow pipeline bootstrap
  serving/      KServe runtimes and inference services
  pipelines/    Tekton pipelines and PipelineRuns
overlays/
  demo/         opinionated demo deployment bundle
```

Operator subscriptions are managed separately through `deploy/argocd` and `deploy/gitops/operators` so the demo overlay does not compete with Argo CD ownership.

## Render

```sh
kustomize build k8s/overlays/demo
```
