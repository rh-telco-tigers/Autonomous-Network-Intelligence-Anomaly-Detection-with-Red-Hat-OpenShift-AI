# Training Assets

`train_and_register.py` generates synthetic IMS feature windows, evaluates baseline and candidate models, writes model artifacts plus registry metadata into `ai/models/artifacts` and `ai/registry`, and uploads the selected predictive artifacts into the demo MinIO bucket by default.

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

`select-best` records the model chosen by the evaluation gate. `register-model` writes the registry and serving artifact metadata. `deploy-model` uploads the selected assets and registry document into MinIO.

## Default MinIO upload target

- endpoint: `http://model-storage-minio.ims-demo-lab.svc.cluster.local:9000`
- access key: `minioadmin`
- secret key: `minioadmin`
- bucket: `ims-models`
- prefix: `predictive/`

Use `--skip-minio-upload` only for local offline runs.
