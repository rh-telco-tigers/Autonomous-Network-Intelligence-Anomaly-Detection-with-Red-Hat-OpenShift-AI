# Lab 03: IMS and SIPp Lab

## Objective

Deploy the IMS lab plane and start repeatable SIP traffic generation.

## Components

- Actual OpenIMSs upstream components for:
  - P-CSCF
  - S-CSCF
  - I-CSCF
  - HSS
  - WebUI
- SIPp runner image built from this repo
- scenario files mounted from `k8s/base/traffic/scenarios`
- `feature-gateway` live traffic generation against the actual P-CSCF service

## Steps

1. Sync the `ims-demo-platform` Argo CD application or apply `k8s/overlays/demo`.
2. Verify the `mongo`, `mysql`, `ims-pcscf`, `ims-icscf`, `ims-scscf`, `ims-hss`, and `openimss-webui` deployments are `Running`.
3. Verify the SIPp CronJobs exist in `ims-demo-lab`.
4. Use the demo UI or call `feature-gateway/live-window/{scenario}` to drive live SIP traffic through the OpenIMSs P-CSCF endpoint and convert the resulting responses into a feature window.
5. Use `normal`, `registration_storm`, and `malformed_invite` scenarios to generate nominal and anomalous traffic patterns.

## Demo checkpoints

- IMS services start and expose internal cluster services
- OpenIMSs WebUI is reachable through the `openimss-webui` route
- live traffic reaches the OpenIMSs P-CSCF on `ims-pcscf:5060`
- `feature-gateway` can turn live scenario traffic into a feature window even when upstream IMS components do not expose demo-specific telemetry
- SIPp scenarios remain mounted from ConfigMaps instead of being embedded in ad hoc pods
