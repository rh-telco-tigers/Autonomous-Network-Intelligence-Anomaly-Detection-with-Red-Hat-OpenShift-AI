# Lab 03: IMS and SIPp Lab

## Objective

Deploy the IMS lab plane and start repeatable SIP traffic generation.

## Components

- OpenIMSs-derived control-plane images:
  - Open5GS image for HSS
  - OpenSIPS image for P-CSCF and S-CSCF
- MongoDB backing store for the HSS path
- MySQL backing store for OpenSIPS state where required by the upstream image contract
- SIPp runner with scenario files from `lab-assets/sipp`

## Steps

1. Trigger the upstream image builds defined in `k8s/base/builds`.
2. Apply `k8s/base/ims`.
3. Apply `k8s/base/traffic`.
4. Run the normal SIPp scenario first.
5. Switch to the registration storm or malformed INVITE scenario to generate anomaly-driving traffic.

## Demo checkpoints

- IMS services start and expose internal cluster services
- SIPp jobs can target the P-CSCF service endpoint
- traffic scenarios are mounted from ConfigMaps instead of being embedded in ad hoc pods

