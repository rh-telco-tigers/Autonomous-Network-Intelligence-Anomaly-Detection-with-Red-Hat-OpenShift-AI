# Training Assets

`train_and_register.py` now prefers real IMS feature windows captured from SIPp traffic runs and stored in MinIO under the dataset prefix. If the live dataset is missing or too small, it falls back to synthetic bootstrap data so the KFP pipeline still completes, evaluates baseline and candidate models, writes model artifacts plus registry metadata, and uploads the selected predictive artifacts into the demo MinIO bucket by default.

The additive feature-store path lives alongside it:

- `build_feature_bundle.py` snapshots persisted feature windows plus control-plane incident and RCA history into a bundle dataset with Parquet tables and a manifest contract
- `featurestore_train.py` now supports `build-bundle`, `resolve-bundle`, `validate-bundle`, feature-store sync/retrieval, serving export for both Triton and MLServer sklearn bundles, model-registry publication, and deployment-manifest generation
- `serving_smoke_check.py` compares `ims-predictive` and `ims-predictive-fs` with shared sample vectors before cutover
- `featurestore_runtime_smoke_check.py` compares the feature-store Triton endpoint with the side-by-side MLServer endpoint before any serving cutover

The candidate path is AutoGluon-based. The trainer image installs AutoGluon by default, and the training workflow is expected to fail fast if AutoGluon is unavailable rather than silently degrading to a different candidate model.

The pipeline step contract now matches the engineering spec:

- `ingest-data`
- `feature-engineering`
- `label-generation`
- `train-baseline`
- `train-automl`
- `evaluate`
- `select-best`
- `register-model`
- `deploy-model`

`select-best` records the model chosen by the evaluation gate. `register-model` writes the registry and serving artifact metadata. `deploy-model` uploads the selected assets, registry document, and Triton model repository into MinIO.

For the feature-store path, the serving export writes both a versioned artifact URI and a stable `current/` alias consumed by `k8s/base/serving/featurestore-serving.yaml` or the opt-in `featurestore-serving-mlserver.yaml`.

## Default MinIO upload target

- endpoint: `http://model-storage-minio.ims-demo-lab.svc.cluster.local:9000`
- access key: `minioadmin`
- secret key: `minioadmin`
- bucket: `ims-models`
- prefix: `predictive/`

Use `--skip-minio-upload` only for local offline runs.
