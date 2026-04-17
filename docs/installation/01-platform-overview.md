# Installation 01: Platform Overview

## Objective

Understand what gets deployed and which namespaces own each part of the platform before you start the install.

Fresh clusters should be brought up by following [Installation](./02-installation.md) and the rest of this guide end to end. GitOps uses **ANI**-prefixed platform namespaces, Argo CD `Application` names, and the Argo CD project **`ani-demo`** (not legacy `ims-*` platform labels).

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
| `ani-runtime` | `demo-ui`, `control-plane`, `feature-gateway`, `anomaly-service`, and `rca-service` |
| `ani-sipp` | OpenIMSs runtime, MySQL, MongoDB, OpenIMS WebUI, and SIPp workloads (IMS deployments such as `ims-scscf` / `ims-pcscf` run here) |
| `ani-data` | Milvus, Attu, Kafka, and supporting data services |
| `ani-datascience` | DSPA, Feature Store, KFP jobs, predictive serving, and vLLM |
| `ani-tekton` | Image build pipeline and Git webhook trigger |
| `ani-observability` | Service monitors, dashboards, and other observability assets |
| `aap` | Optional AAP Controller, EDA, Hub, and MCP components |
| `plane` | Optional Plane web, API, live, space, and storage services |

## Argo CD Application Layout

The main applications you will see after bootstrap are:

- `ani-operators` (root operator subscriptions and supporting config)
- `ani-platform` (app-of-apps for the child applications below)
- `ani-namespaces`
- `ani-rhoai-platform`
- `ani-data`
- `ani-sipp-core`
- `ani-runtime`
- `ani-plane`
- `ani-datascience`
- `ani-tekton`
- `ani-observability`
- `ani-sipp-traffic`
- `ani-remediation`

## Install Flow

1. Deploy Gitea.
2. Push the branch you want to deploy into the in-cluster Gitea repo.
3. Bootstrap Argo CD.
4. Wait for the child applications to appear.
5. Wait for the first GitOps-managed KFP `CronJob`s and workflows to publish the initial model artifacts.
6. Validate the routes, workloads, and AAP/EDA integration after AAP license import.
