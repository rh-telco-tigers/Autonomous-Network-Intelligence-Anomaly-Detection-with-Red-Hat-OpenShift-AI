# Feature Store Assets

This directory contains the additive Feature Store path for the IMS anomaly platform.

The first implementation target is offline training from a versioned feature bundle while preserving the current MinIO-backed live demo training path.

## Layout

- `feature_repo/feature_store.yaml`: local Feast repo config used by the Python SDK and CLI
- `feature_repo/entities.py`: entity definitions
- `feature_repo/feature_views.py`: batch feature views projected from the bundle dataset
- `feature_repo/feature_services.py`: named feature contracts for training and future online serving

## Current intent

- the bundle dataset remains the immutable training source of truth for this path
- Feature Store is used first for offline training retrieval
- online materialization is a later milestone
- the new KFP pipeline will use this repo without altering the current `ani_anomaly_pipeline.py`
