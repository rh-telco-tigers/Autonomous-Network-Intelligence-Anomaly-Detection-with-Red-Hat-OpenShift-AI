# OpenShift Manifests

This directory contains the OpenShift deployment assets for the full demo platform.

## Structure

```text
base/
  namespaces/   demo and operator namespaces
  operators/    OLM subscriptions
  rhoai/        OpenShift AI initialization and component CRs
  builds/       upstream BuildConfig assets for OpenIMSs and SIPp
  ims/          IMS lab workloads
  traffic/      SIPp scenario runners
  platform/     feature, anomaly, RCA, and UI services
  milvus/       vector database stack
  serving/      KServe runtimes and inference services
  pipelines/    Tekton pipelines and PipelineRuns
overlays/
  demo/         opinionated demo deployment bundle
```

## Render

```sh
kustomize build k8s/overlays/demo
```

