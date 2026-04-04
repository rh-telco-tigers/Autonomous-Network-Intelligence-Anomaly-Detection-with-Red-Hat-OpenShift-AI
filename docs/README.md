# Demo Documentation

This documentation set is organized for a customer-facing demo flow rather than an internal build log.

## Recommended reading order

1. [Platform overview](./labs/01-platform-overview.md)
2. [Cluster bootstrap](./labs/02-cluster-bootstrap.md)
3. [Gitea GitOps source](./labs/02a-gitea-gitops-source.md)
4. [IMS and SIPp lab](./labs/03-ims-and-sipp-lab.md)
5. [OpenShift AI and model serving lab](./labs/04-rhoai-and-model-serving.md)
6. [Demo runbook](./labs/05-demo-runbook.md)

Labs 03 to 05 are written as a connected story:

- Lab 03 explains how real SIPp-driven IMS traffic becomes labeled feature windows
- Lab 04 explains how OpenShift AI trains and serves the predictive model from that dataset and how the RCA service is wired to an OpenAI-compatible LLM endpoint
- Lab 05 explains how to present the full flow to an end user or customer audience

## Reference material

- [Architecture by phase](./architecture/README.md)
- [Engineering specification](./architecture/engineering-spec.md)
- [Feature store training path](./architecture/feature-store-training-path.md)

## Demo posture

This repo is meant to support a guided platform demo:

- infrastructure is expressed as OpenShift manifests
- GitOps source control is hosted inside the cluster for demo portability
- AI workflows are modeled explicitly, not hidden in notebooks
- incidents, approvals, and notifications flow through a dedicated control-plane service
- the primary `scale_scscf` remediation path is human-approved and executed through AAP-backed automation
- operator-facing flows are documented as labs and runbooks
- integration points are named and versioned so the system is auditable
- the IMS plane uses actual upstream OpenIMSs images and config, adapted for OpenShift service discovery

## Demo access notes

- Gitea user: `gitadmin`
- Gitea password: `GiteaAdmin123!`
- API admin token: `demo-token`
- API operator token: `demo-operator-token`
- API viewer token: `demo-viewer-token`
- Slack and Jira actions: simulated through the control-plane demo relay unless live credentials are configured
- Automation approvals: the current `scale_scscf` path is wired to live AAP-backed execution in this demo deployment
- If AAP controller API writes are license-blocked, the platform falls back to an AAP runner job and still updates the incident with execution status
- Non-AAP playbook actions still honor `AUTOMATION_MODE` for local simulated or local live execution
- MinIO console user: `minioadmin`
- MinIO console password: `minioadmin`
- Milvus UI: Attu on the `milvus-attu` route
- Attu does not use a separate username or password in this demo deployment
- Plane route: `https://plane-ims-demo-lab.apps.ocp.4h2g6.sandbox195.opentlc.com/`
- Plane user (email login): `plane-admin@ims-demo.local`
- Plane password: `plane`
- OpenIMSs WebUI route: `openimss-webui`
- OpenIMSs WebUI user: `admin`
- OpenIMSs WebUI password: `1423`
