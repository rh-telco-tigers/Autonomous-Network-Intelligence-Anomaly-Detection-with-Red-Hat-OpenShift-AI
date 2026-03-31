# Lab 03: IMS and SIPp Lab

## Objective

Deploy the IMS lab plane and start repeatable SIP traffic generation.

## Components

- lightweight IMS function simulators for:
  - P-CSCF
  - S-CSCF
  - HSS
- SIPp runner image built from this repo
- scenario files mounted from `k8s/base/traffic/scenarios`
- live telemetry endpoints on each IMS function, consumed by `feature-gateway`

## Steps

1. Sync the `ims-demo-platform` Argo CD application or apply `k8s/overlays/demo`.
2. Verify the `ims-pcscf`, `ims-scscf`, and `ims-hss` deployments are `Running`.
3. Verify the SIPp CronJobs exist in `ims-demo-lab`.
4. Use the demo UI or call `feature-gateway/live-window/{scenario}` to drive live SIP traffic through the P-CSCF endpoint and convert the resulting telemetry into a feature window.
5. Use `normal`, `registration_storm`, and `malformed_invite` scenarios to generate nominal and anomalous traffic patterns.

## Demo checkpoints

- IMS services start and expose internal cluster services
- live traffic reaches the P-CSCF simulator on `ims-pcscf:5060`
- `feature-gateway` can turn live node telemetry into a feature window
- SIPp scenarios remain mounted from ConfigMaps instead of being embedded in ad hoc pods
