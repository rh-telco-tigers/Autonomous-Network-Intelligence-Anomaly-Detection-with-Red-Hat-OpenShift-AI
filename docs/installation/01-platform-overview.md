# Installation 01: Platform Overview

## Objective

Understand what gets deployed and which namespaces own each part of the platform before you start the install.

## What Gets Installed

- in-cluster Gitea as the GitOps source of truth
- OpenShift GitOps and the Argo CD app-of-apps
- IMS core services and SIPp traffic generators
- control-plane, demo UI, anomaly scoring, and RCA services
- OpenShift AI, Feature Store, model registry, and model serving
- optional Plane and AAP/EDA integrations

## Namespace Map

| Namespace | Purpose |
| --- | --- |
| `gitea` | In-cluster Git server used as the Argo CD source repository |
| `openshift-gitops` | Argo CD project and `Application` objects |
| `redhat-ods-operator` | OpenShift AI operator and `DataScienceCluster` control objects |
| `redhat-ods-applications` | OpenShift AI shared controllers and supporting services |
| `redhat-ods-monitoring` | OpenShift AI monitoring components |
| `rhoai-model-registries` | Model registry service used by the training and serving flow |
| `ims-runtime` | `demo-ui`, `control-plane`, `feature-gateway`, `anomaly-service`, and `rca-service` |
| `ims-sipp` | OpenIMSs runtime, MySQL, MongoDB, OpenIMS WebUI, and SIPp workloads |
| `ims-data` | Milvus, Attu, Kafka, and supporting data services |
| `ims-datascience` | DSPA, Feature Store, KFP jobs, predictive serving, and vLLM |
| `ims-tekton` | Image build pipeline and Git webhook trigger |
| `ims-observability` | Service monitors, dashboards, and other observability assets |
| `aap` | Optional AAP Controller, EDA, Hub, and MCP components |
| `plane` | Optional Plane web, API, live, space, and storage services |

## Argo CD Application Layout

The main applications you will see after bootstrap are:

- `ims-operators`
- `ims-platform`
- `ims-namespaces`
- `ims-runtime`
- `ims-sipp-core`
- `ims-sipp-traffic`
- `ims-data`
- `ims-datascience`
- `ims-tekton`
- `ims-observability`
- `ims-aap`
- `ims-plane`

## Install Flow

1. Deploy Gitea.
2. Push the branch you want to deploy into the in-cluster Gitea repo.
3. Bootstrap Argo CD.
4. Wait for the child applications to appear.
5. Trigger the first image build so the runtime namespaces get their `:latest` images.
6. Validate the routes, workloads, and AAP/EDA integration after AAP license import.
