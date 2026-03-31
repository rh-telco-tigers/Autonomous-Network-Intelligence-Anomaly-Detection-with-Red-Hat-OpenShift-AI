# Demo Documentation

This documentation set is organized for a customer-facing demo flow rather than an internal build log.

## Recommended reading order

1. [Platform overview](./labs/01-platform-overview.md)
2. [Cluster bootstrap](./labs/02-cluster-bootstrap.md)
3. [Gitea GitOps source](./labs/02a-gitea-gitops-source.md)
4. [IMS and SIPp lab](./labs/03-ims-and-sipp-lab.md)
5. [OpenShift AI and model serving lab](./labs/04-rhoai-and-model-serving.md)
6. [Demo runbook](./labs/05-demo-runbook.md)

## Reference material

- [Engineering specification](./architecture/engineering-spec.md)

## Demo posture

This repo is meant to support a guided platform demo:

- infrastructure is expressed as OpenShift manifests
- GitOps source control is hosted inside the cluster for demo portability
- AI workflows are modeled explicitly, not hidden in notebooks
- incidents, approvals, and notifications flow through a dedicated control-plane service
- operator-facing flows are documented as labs and runbooks
- integration points are named and versioned so the system is auditable

## Demo access notes

- Gitea user: `gitadmin`
- Gitea password: `GiteaAdmin123!`
- API admin token: `demo-token`
- API operator token: `demo-operator-token`
- API viewer token: `demo-viewer-token`
- Slack and Jira actions: simulated through the control-plane demo relay unless live credentials are configured
- Automation approvals: simulate execution by default; switch to `AUTOMATION_MODE=execute` for live Ansible runs
- MinIO console user: `minioadmin`
- MinIO console password: `minioadmin`
- Milvus UI: Attu on the `milvus-attu` route
- Attu does not use a separate username or password in this demo deployment
