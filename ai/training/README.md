# Training Assets

`train_and_register.py` generates synthetic IMS feature windows, evaluates baseline and candidate models, writes model artifacts plus registry metadata into `ai/models/artifacts` and `ai/registry`, and uploads the selected predictive artifacts into the demo MinIO bucket by default.

The candidate path is implemented as a lightweight AutoML-style search that works without cluster dependencies. If a richer AutoML engine is introduced later, it can replace the candidate generation function without changing the registry contract.

## Default MinIO upload target

- endpoint: `http://model-storage-minio.ims-demo-lab.svc.cluster.local:9000`
- access key: `minioadmin`
- secret key: `minioadmin`
- bucket: `ims-models`
- prefix: `predictive/`

Use `--skip-minio-upload` only for local offline runs.
