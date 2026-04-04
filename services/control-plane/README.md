# control-plane

Stores the operational state for the demo: incidents, RCA attachments, approvals, audit history, and model registry data.

It is the system of record that connects scoring, RCA, notifications, and remediation actions.

## AAP-backed remediation

The control plane can launch human-approved remediation through Ansible Automation Platform instead of simulating local execution.

- The checked-in AAP/RBAC bootstrap is in `k8s/base/platform/aap-remediation-rbac.yaml`.
- The live scale example is `automation/ansible/playbooks/scale-scscf.yaml`.
- `POST /incidents/{incident_id}/remediation/{remediation_id}/execute` now launches the AAP job, marks the incident `EXECUTING`, and then updates the same incident to `EXECUTED` or `EXECUTION_FAILED` when the controller job completes.
- The control plane reads the generated AAP controller admin password from the `aap` namespace through Kubernetes API, so the password does not need to be duplicated into the app namespace.
