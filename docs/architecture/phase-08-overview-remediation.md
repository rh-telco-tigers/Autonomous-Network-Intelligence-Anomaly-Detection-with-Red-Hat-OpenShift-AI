# Phase 08 Overview — Remediation

## Purpose

This phase turns RCA into controlled action by suggesting remediations, capturing human approval where required, executing the selected path, verifying the result, and learning from the outcome.

## Status

This is active in the current platform. The control-plane now bootstraps AAP Controller and Event-Driven Ansible resources on startup, wires live OpenShift credentials into controller job templates, and supports both human-approved remediation from the UI and selective low-risk event-driven execution.

Current live coverage:

- manual UI execution through AAP Controller for `scale_scscf`, `rate_limit_pcscf`, and `quarantine_imsi`
- controller callback templates for event-driven incident transitions and event-driven action execution
- EDA policy `IMS Critical Incident Escalation` for critical RCA-attached incidents that should move to `ESCALATED` and sync Plane
- EDA policy `IMS Critical Signal Guardrail` for selected critical signaling incidents that can auto-apply `rate_limit_pcscf`
- runner-job fallback when controller write operations are blocked by the current AAP license

## What This Phase Covers

- generate remediation suggestions from RCA and prior knowledge
- keep high-impact approval explicitly human-controlled while allowing selected low-risk policy automation
- execute the chosen action through manual, ticketing, or automation paths
- sync relevant status into Plane and other workflow surfaces
- record verification results and reusable knowledge

## Stage Diagram

```mermaid
flowchart LR
  RCA["RCA result"] --> Suggest["rank remediation options"]
  Suggest --> Human["operator review in platform UI"]
  Suggest --> Event["control-plane publishes EDA event"]
  Human -->|reject| Backlog["ticket or follow-up workflow"]
  Human -->|approve + execute| JT["AAP controller job template"]
  Event --> Policy{"EDA policy match"}
  Policy -->|critical escalation| Ticket["callback template: transition incident + Plane sync"]
  Policy -->|low-risk guardrail| Callback["callback template: execute incident action"]
  Callback --> JT
  JT --> Playbook["Ansible playbook on OpenShift"]
  Playbook --> Verify["verification and outcome capture"]
  Ticket --> Verify
  Verify --> Incident["incident state update"]
  Verify --> Learn["verified knowledge and audit trail"]
```

## Inputs

- RCA payloads
- remediation ranking logic
- operator approval and notes
- automation bootstrap configuration, AAP/EDA project sync, and callback templates
- OpenShift RBAC and dynamic Kubernetes credentials for controller execution

## Outputs

- remediation suggestions
- approvals, action records, and AAP job identifiers
- execution status, Plane comments, and verification results
- updated incident workflow state
- reusable resolution knowledge

## Current Repo Touchpoints

- `services/control-plane/`
- `services/shared/aap.py`
- `services/shared/eda.py`
- `services/shared/tickets.py`
- `automation/ansible/playbooks/scale-scscf.yaml`
- `automation/ansible/playbooks/rate-limit-pcscf.yaml`
- `automation/ansible/playbooks/quarantine-imsi.yaml`
- `automation/eda/playbooks/transition-incident-state.yml`
- `automation/eda/playbooks/execute-incident-action.yml`
- `rulebooks/critical-incident-escalation.yml`
- `rulebooks/critical-signal-guardrail.yml`
- `k8s/base/platform/aap-remediation-rbac.yaml`
- `k8s/base/platform/platform-services.yaml`
- `docs/architecture/rca-remediation.md`

## Why It Matters

This phase closes the loop. It is where the platform proves that its analysis can lead to controlled action, not just observation. Human approval remains the default guardrail for impactful remediation, while carefully selected EDA policies show how the same control-plane can also automate low-risk response and escalation without bypassing audit, ticket sync, or verification.

## Related Docs

- [Architecture by phase](./README.md)
- [Engineering specification](./engineering-spec.md)
- [RCA and remediation](./rca-remediation.md)
