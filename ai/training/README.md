# Training Assets

`train_and_register.py` now prefers real IMS feature windows captured from SIPp traffic runs and stored in MinIO under the dataset prefix. If the live dataset is missing or too small, it falls back to synthetic bootstrap data so the KFP pipeline still completes, evaluates baseline and candidate models, writes model artifacts plus registry metadata, and uploads the selected predictive artifacts into the demo MinIO bucket by default.

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

## Default MinIO upload target

- endpoint: `http://model-storage-minio.ims-demo-lab.svc.cluster.local:9000`
- access key: `minioadmin`
- secret key: `minioadmin`
- bucket: `ims-models`
- prefix: `predictive/`

Use `--skip-minio-upload` only for local offline runs.
