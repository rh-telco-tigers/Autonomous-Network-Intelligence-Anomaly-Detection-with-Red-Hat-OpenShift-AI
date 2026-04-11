# Installation Guide

Use this guide when you want to deploy the platform on a fresh cluster, validate that it came up correctly, and recover from the common first-run failures.

## Reading Order

1. [Platform overview](./01-platform-overview.md)
2. [Installation](./02-installation.md)
3. [Validation](./03-validation.md)
4. [Data generation and model training](./04-data-generation-and-model-training.md)
5. [Troubleshooting](./troubleshooting.md)

## What This Guide Covers

- the GitOps bootstrap path
- the first image build needed for runtime workloads
- the main routes and demo credentials
- the incident-data, feature-bundle, and model-training workflow
- optional Plane and AAP/EDA onboarding
- common recovery commands when the first install does not converge

## What Stays In `docs/architecture`

The files under `docs/architecture` remain the detailed design references for model serving, RCA, remediation, and the phase-by-phase architecture.
