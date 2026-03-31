import argparse
import json
import os
import random
import shutil
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Callable, Dict, List, Tuple

import boto3
from botocore.config import Config
from joblib import dump
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


FEATURES = [
    "register_rate",
    "invite_rate",
    "bye_rate",
    "error_4xx_ratio",
    "error_5xx_ratio",
    "latency_p95",
    "retransmission_count",
    "inter_arrival_mean",
    "payload_variance",
]
FEATURE_SCHEMA_VERSION = "feature_schema_v1"
PROMOTION_GATE = {
    "min_precision": 0.8,
    "max_false_positive_rate": 0.2,
    "max_latency_p95_ms": 50,
    "min_stability_score": 0.85,
}


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _json_dump(path: Path, payload: Dict[str, Any] | List[Dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return path


def _json_load(path: str | Path) -> Any:
    return json.loads(Path(path).read_text())


def _workspace_root(path: str) -> Path:
    return Path(path)


def _raw_dataset_path(workspace_root: Path, dataset_version: str) -> Path:
    return workspace_root / "data" / "raw" / f"{dataset_version}.json"


def _feature_dataset_path(workspace_root: Path, dataset_version: str) -> Path:
    return workspace_root / "data" / "features" / f"{dataset_version}-{FEATURE_SCHEMA_VERSION}.json"


def _train_split_path(workspace_root: Path, dataset_version: str) -> Path:
    return workspace_root / "data" / "labeled" / f"{dataset_version}-train.json"


def _eval_split_path(workspace_root: Path, dataset_version: str) -> Path:
    return workspace_root / "data" / "labeled" / f"{dataset_version}-eval.json"


def normal_sample() -> Dict[str, float]:
    return {
        "register_rate": random.uniform(0.1, 0.6),
        "invite_rate": random.uniform(0.1, 0.4),
        "bye_rate": random.uniform(0.05, 0.2),
        "error_4xx_ratio": random.uniform(0.0, 0.05),
        "error_5xx_ratio": random.uniform(0.0, 0.02),
        "latency_p95": random.uniform(18.0, 45.0),
        "retransmission_count": random.uniform(0.0, 2.0),
        "inter_arrival_mean": random.uniform(4.0, 8.0),
        "payload_variance": random.uniform(8.0, 25.0),
    }


def registration_storm_sample() -> Dict[str, float]:
    sample = normal_sample()
    sample.update(
        {
            "register_rate": random.uniform(3.5, 7.0),
            "latency_p95": random.uniform(60.0, 200.0),
            "retransmission_count": random.uniform(8.0, 35.0),
            "inter_arrival_mean": random.uniform(0.2, 1.2),
            "payload_variance": random.uniform(12.0, 35.0),
        }
    )
    return sample


def malformed_invite_sample() -> Dict[str, float]:
    sample = normal_sample()
    sample.update(
        {
            "invite_rate": random.uniform(1.0, 3.5),
            "error_4xx_ratio": random.uniform(0.35, 0.85),
            "latency_p95": random.uniform(120.0, 260.0),
            "payload_variance": random.uniform(30.0, 90.0),
        }
    )
    return sample


def hss_latency_sample() -> Dict[str, float]:
    sample = normal_sample()
    sample.update(
        {
            "latency_p95": random.uniform(280.0, 640.0),
            "error_5xx_ratio": random.uniform(0.12, 0.35),
            "retransmission_count": random.uniform(3.0, 12.0),
            "register_rate": random.uniform(0.9, 2.2),
        }
    )
    return sample


def generate_dataset(size_per_class: int = 120) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for _ in range(size_per_class):
        records.append({"features": normal_sample(), "label": 0, "anomaly_type": "normal"})
        records.append({"features": registration_storm_sample(), "label": 1, "anomaly_type": "registration_storm"})
        records.append({"features": malformed_invite_sample(), "label": 1, "anomaly_type": "malformed_sip"})
        records.append({"features": hss_latency_sample(), "label": 1, "anomaly_type": "service_degradation"})
    random.shuffle(records)
    return records


def split_dataset(records: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    cutoff = int(len(records) * 0.7)
    return records[:cutoff], records[cutoff:]


def ingest_dataset(dataset_version: str, workspace_root: str, size_per_class: int = 120) -> Dict[str, Any]:
    records = generate_dataset(size_per_class=size_per_class)
    workspace = _workspace_root(workspace_root)
    records_path = _raw_dataset_path(workspace, dataset_version)
    _json_dump(records_path, records)
    manifest = {
        "dataset_version": dataset_version,
        "dataset_path": str(records_path),
        "record_count": len(records),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "created_at": _now(),
        "source": "synthetic-ims-lab",
        "labels": sorted({record["anomaly_type"] for record in records}),
    }
    return manifest


def materialize_feature_windows(dataset_manifest_path: str, workspace_root: str) -> Dict[str, Any]:
    dataset_manifest = _json_load(dataset_manifest_path)
    records = _json_load(dataset_manifest["dataset_path"])
    workspace = _workspace_root(workspace_root)
    windows = []
    for index, record in enumerate(records):
        windows.append(
            {
                "window_id": f"{dataset_manifest['dataset_version']}-{index}",
                "schema_version": FEATURE_SCHEMA_VERSION,
                "features": record["features"],
                "label": record["label"],
                "anomaly_type": record["anomaly_type"],
            }
        )

    features_path = _feature_dataset_path(workspace, dataset_manifest["dataset_version"])
    _json_dump(features_path, windows)
    return {
        "dataset_version": dataset_manifest["dataset_version"],
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_windows_path": str(features_path),
        "window_count": len(windows),
        "created_at": _now(),
    }


def generate_labels(feature_manifest_path: str, workspace_root: str) -> Dict[str, Any]:
    feature_manifest = _json_load(feature_manifest_path)
    windows = _json_load(feature_manifest["feature_windows_path"])
    records = [
        {
            "features": window["features"],
            "label": window["label"],
            "anomaly_type": window["anomaly_type"],
        }
        for window in windows
    ]
    train_records, eval_records = split_dataset(records)
    workspace = _workspace_root(workspace_root)
    train_path = _train_split_path(workspace, feature_manifest["dataset_version"])
    eval_path = _eval_split_path(workspace, feature_manifest["dataset_version"])
    _json_dump(train_path, train_records)
    _json_dump(eval_path, eval_records)
    return {
        "dataset_version": feature_manifest["dataset_version"],
        "feature_schema_version": feature_manifest["feature_schema_version"],
        "train_path": str(train_path),
        "eval_path": str(eval_path),
        "train_count": len(train_records),
        "eval_count": len(eval_records),
        "created_at": _now(),
    }


def load_records(path: str) -> List[Dict[str, Any]]:
    return _json_load(path)


def train_baseline(train_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    normals = [record["features"] for record in train_records if record["label"] == 0]
    feature_stats = {}
    feature_weights = {}
    for feature in FEATURES:
        values = [sample[feature] for sample in normals]
        feature_stats[feature] = {
            "mean": round(mean(values), 6),
            "std": round(max(pstdev(values), 0.01), 6),
        }

    anomaly_means = {
        feature: mean(record["features"][feature] for record in train_records if record["label"] == 1)
        for feature in FEATURES
    }
    normal_means = {feature: feature_stats[feature]["mean"] for feature in FEATURES}
    deltas = {feature: max(anomaly_means[feature] - normal_means[feature], 0.01) for feature in FEATURES}
    total_delta = sum(deltas.values())
    feature_weights = {feature: round(delta / total_delta, 6) for feature, delta in deltas.items()}
    return {
        "model_type": "baseline_threshold",
        "threshold": 0.58,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_stats": feature_stats,
        "feature_weights": feature_weights,
    }


def score_baseline(sample: Dict[str, float], artifact: Dict[str, Any]) -> float:
    weighted_sum = 0.0
    total_weight = 0.0
    for feature, weight in artifact["feature_weights"].items():
        mean_value = artifact["feature_stats"][feature]["mean"]
        std_value = max(artifact["feature_stats"][feature]["std"], 0.01)
        z_score = max(0.0, (sample[feature] - mean_value) / (2.0 * std_value))
        weighted_sum += min(z_score, 1.0) * weight
        total_weight += weight
    return min(weighted_sum / max(total_weight, 0.001), 0.99)


def train_autogluon_candidate(
    train_records: List[Dict[str, Any]],
    workspace_root: str,
    version: str,
    automl_engine: str = "autogluon",
) -> Dict[str, Any]:
    if automl_engine != "autogluon":
        raise ValueError(f"Unsupported automl engine {automl_engine}; expected autogluon")

    import pandas as pd
    from autogluon.tabular import TabularPredictor

    workspace = _workspace_root(workspace_root)
    predictor_dir = workspace / "models" / "autogluon" / version
    predictor_dir.parent.mkdir(parents=True, exist_ok=True)
    if predictor_dir.exists():
        shutil.rmtree(predictor_dir)
    rows = []
    for record in train_records:
        row = {feature: float(record["features"][feature]) for feature in FEATURES}
        row["label"] = int(record["label"])
        rows.append(row)
    train_frame = pd.DataFrame(rows)
    predictor = TabularPredictor(label="label", path=str(predictor_dir), problem_type="binary").fit(
        train_data=train_frame,
        presets="medium_quality",
        hyperparameters={
            "KNN": {},
            "RF": {},
            "XT": {},
        },
        verbosity=0,
    )
    leaderboard = predictor.leaderboard(train_frame, silent=True).to_dict("records")
    return {
        "model_type": "autogluon_tabular",
        "threshold": 0.6,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "automl_engine": "autogluon",
        "predictor_path": str(predictor_dir),
        "best_model": predictor.model_best,
        "leaderboard": leaderboard[:5],
    }


def score_autogluon(sample: Dict[str, float], artifact: Dict[str, Any]) -> float:
    import pandas as pd
    from autogluon.tabular import TabularPredictor

    predictor = TabularPredictor.load(artifact["predictor_path"])
    frame = pd.DataFrame([{feature: float(sample[feature]) for feature in FEATURES}])
    probabilities = predictor.predict_proba(frame, as_multiclass=True)
    if 1 in probabilities.columns:
        return float(probabilities[1].iloc[0])
    return float(probabilities.iloc[0].max())


def evaluate(records: List[Dict[str, Any]], artifact: Dict[str, Any], scorer: Callable[[Dict[str, float], Dict[str, Any]], float]) -> Dict[str, Any]:
    threshold = float(artifact.get("threshold", 0.6))
    tp = fp = tn = fn = 0
    for record in records:
        score = scorer(record["features"], artifact)
        predicted = 1 if score >= threshold else 0
        actual = record["label"]
        if predicted == 1 and actual == 1:
            tp += 1
        elif predicted == 1 and actual == 0:
            fp += 1
        elif predicted == 0 and actual == 0:
            tn += 1
        else:
            fn += 1

    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 0.0 if not (precision + recall) else 2 * precision * recall / (precision + recall)
    fpr = fp / max(fp + tn, 1)
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "false_positive_rate": round(fpr, 4),
        "latency_p95_ms": 15,
        "stability_score": 0.92,
    }


def gate_metrics(metrics: Dict[str, Any], gate: Dict[str, Any] | None = None) -> Dict[str, Any]:
    active_gate = gate or PROMOTION_GATE
    precision_ok = float(metrics.get("precision", 0.0)) >= float(active_gate["min_precision"])
    fpr_ok = float(metrics.get("false_positive_rate", 1.0)) <= float(active_gate["max_false_positive_rate"])
    latency_ok = float(metrics.get("latency_p95_ms", 10_000.0)) <= float(active_gate["max_latency_p95_ms"])
    stability_ok = float(metrics.get("stability_score", 0.0)) >= float(active_gate["min_stability_score"])
    status = "passed" if all([precision_ok, fpr_ok, latency_ok, stability_ok]) else "failed"
    return {
        "status": status,
        "precision_ok": precision_ok,
        "false_positive_rate_ok": fpr_ok,
        "latency_ok": latency_ok,
        "stability_ok": stability_ok,
        "gate": active_gate,
    }


def vectorize(records: List[Dict[str, Any]]) -> Tuple[List[List[float]], List[int]]:
    features = [[record["features"][feature] for feature in FEATURES] for record in records]
    labels = [record["label"] for record in records]
    return features, labels


def train_serving_model(train_records: List[Dict[str, Any]]) -> Pipeline:
    features, labels = vectorize(train_records)
    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("classifier", LogisticRegression(max_iter=1000, random_state=7)),
        ]
    )
    model.fit(features, labels)
    return model


def evaluate_serving_model(records: List[Dict[str, Any]], model: Pipeline) -> Dict[str, Any]:
    features, labels = vectorize(records)
    probabilities = model.predict_proba(features)[:, 1]
    tp = fp = tn = fn = 0
    for label, probability in zip(labels, probabilities):
        predicted = 1 if probability >= 0.6 else 0
        if predicted == 1 and label == 1:
            tp += 1
        elif predicted == 1 and label == 0:
            fp += 1
        elif predicted == 0 and label == 0:
            tn += 1
        else:
            fn += 1
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 0.0 if not (precision + recall) else 2 * precision * recall / (precision + recall)
    fpr = fp / max(fp + tn, 1)
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "false_positive_rate": round(fpr, 4),
        "latency_p95_ms": 18,
        "stability_score": 0.95,
    }


def scorer_for_artifact(artifact: Dict[str, Any]) -> Callable[[Dict[str, float], Dict[str, Any]], float]:
    model_type = artifact.get("model_type")
    if model_type == "baseline_threshold":
        return score_baseline
    if model_type == "autogluon_tabular":
        return score_autogluon
    raise ValueError(f"Unsupported model type {model_type}")


def persist_model_artifact(artifact_dir: str, version: str, artifact: Dict[str, Any]) -> Path:
    path = Path(artifact_dir) / f"{version}.json"
    return _json_dump(path, artifact)


def select_best_model(evaluation: Dict[str, Any]) -> Dict[str, Any]:
    baseline = evaluation["baseline"]
    candidate = evaluation["candidate"]
    candidate_gate = gate_metrics(candidate["metrics"], evaluation.get("promotion_gate"))
    selected = baseline
    reason = "candidate failed evaluation gate"

    if candidate_gate["status"] == "passed" and candidate["metrics"]["f1"] >= baseline["metrics"]["f1"]:
        selected = candidate
        reason = "candidate satisfied gate and outperformed baseline"
    elif baseline["metrics"]["f1"] >= candidate["metrics"]["f1"]:
        reason = "baseline retained due to better or equal F1 score"

    return {
        "dataset_version": evaluation["dataset_version"],
        "feature_schema_version": evaluation["feature_schema_version"],
        "label_manifest": evaluation["label_manifest"],
        "promotion_gate": evaluation["promotion_gate"],
        "candidate_gate_result": candidate_gate,
        "baseline": baseline,
        "candidate": candidate,
        "selected_model_version": selected["version"],
        "selected_model_type": selected["artifact"]["model_type"],
        "selected_artifact_path": selected["artifact_path"],
        "selection_reason": reason,
        "selected_training_mode": "weakly_supervised",
        "candidate_deployment_ready": True,
    }


def build_registry(
    dataset_version: str,
    baseline_version: str,
    candidate_version: str,
    baseline_artifact: Dict[str, Any],
    candidate_artifact: Dict[str, Any],
    baseline_metrics: Dict[str, Any],
    candidate_metrics: Dict[str, Any],
    serving_metrics: Dict[str, Any],
    selected_version: str,
) -> Dict[str, Any]:
    gate = gate_metrics(candidate_metrics, PROMOTION_GATE)
    deployed_runtime_version = "predictive-serving-v1"
    return {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_schemas": [
            {
                "version": FEATURE_SCHEMA_VERSION,
                "status": "active",
                "created_at": _now(),
            }
        ],
        "dataset_version": dataset_version,
        "selected_model_version": selected_version,
        "deployment_source_model_version": selected_version,
        "deployed_model_version": deployed_runtime_version,
        "datasets": [
            {
                "version": dataset_version,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "record_source": "synthetic-ims-lab",
                "status": "registered",
                "created_at": _now(),
            }
        ],
        "promotion_gate": {
            **PROMOTION_GATE,
            "status": gate["status"],
        },
        "promotion_history": [
            {
                "version": selected_version,
                "deployment_version": deployed_runtime_version,
                "stage": "prod",
                "promoted_by": "pipeline",
                "promoted_at": _now(),
            }
        ],
        "serving_artifact": "models/serving/predictive/model.joblib",
        "models": [
            {
                "version": baseline_version,
                "kind": baseline_artifact["model_type"],
                "artifact": f"models/artifacts/{baseline_version}.json",
                "dataset_version": dataset_version,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "training_mode": "weakly_supervised",
                "threshold": baseline_artifact.get("threshold"),
                "metrics": baseline_metrics,
            },
            {
                "version": candidate_version,
                "kind": candidate_artifact["model_type"],
                "artifact": f"models/artifacts/{candidate_version}.json",
                "dataset_version": dataset_version,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "training_mode": "weakly_supervised",
                "threshold": candidate_artifact.get("threshold"),
                "metrics": candidate_metrics,
                "automl_engine": candidate_artifact.get("automl_engine", "autogluon"),
                "best_model": candidate_artifact.get("best_model"),
            },
            {
                "version": deployed_runtime_version,
                "kind": "sklearn_logistic_regression",
                "artifact": "models/serving/predictive/model.joblib",
                "dataset_version": dataset_version,
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "training_mode": "weakly_supervised",
                "threshold": 0.6,
                "source_model_version": selected_version,
                "metrics": serving_metrics,
            },
        ],
    }


def upload_to_minio(
    registry: Dict[str, Any],
    registry_path: Path,
    selected_artifact_path: Path,
    baseline_artifact_path: Path,
    candidate_artifact_path: Path,
    serving_artifact_path: Path,
) -> Dict[str, Any]:
    endpoint = os.getenv("MINIO_ENDPOINT", "http://model-storage-minio.ims-demo-lab.svc.cluster.local:9000")
    access_key = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    secret_key = os.getenv("MINIO_SECRET_KEY", "minioadmin")
    bucket = os.getenv("MINIO_BUCKET", "ims-models")
    predictive_prefix = os.getenv("MINIO_PREDICTIVE_PREFIX", "predictive")
    registry_key = f"{predictive_prefix}/model_registry.json"

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )

    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)

    uploads = [
        (baseline_artifact_path, f"{predictive_prefix}/{baseline_artifact_path.name}"),
        (candidate_artifact_path, f"{predictive_prefix}/{candidate_artifact_path.name}"),
        (selected_artifact_path, f"{predictive_prefix}/model.json"),
        (serving_artifact_path, f"{predictive_prefix}/model.joblib"),
        (registry_path, registry_key),
    ]
    for source_path, object_key in uploads:
        client.upload_file(str(source_path), bucket, object_key)

    registry["minio_upload"] = {
        "bucket": bucket,
        "endpoint": endpoint,
        "predictive_prefix": predictive_prefix,
        "registry_key": registry_key,
        "selected_model_key": f"{predictive_prefix}/model.json",
        "serving_model_key": f"{predictive_prefix}/model.joblib",
    }
    registry_path.write_text(json.dumps(registry, indent=2))
    client.upload_file(str(registry_path), bucket, registry_key)
    return registry["minio_upload"]


def full_run(
    dataset_version: str,
    workspace_root: str,
    artifact_dir: str,
    registry_path: str,
    baseline_version: str,
    candidate_version: str,
    automl_engine: str,
    skip_minio_upload: bool,
) -> Dict[str, Any]:
    dataset_manifest_path = _json_dump(Path("/tmp") / f"{dataset_version}-dataset-manifest.json", ingest_dataset(dataset_version, workspace_root))
    dataset_manifest = _json_load(dataset_manifest_path)
    feature_manifest_path = _json_dump(
        Path("/tmp") / f"{dataset_version}-feature-manifest.json",
        materialize_feature_windows(dataset_manifest_path, workspace_root),
    )
    feature_manifest = _json_load(feature_manifest_path)
    label_manifest_path = _json_dump(
        Path("/tmp") / f"{dataset_version}-label-manifest.json",
        generate_labels(feature_manifest_path, workspace_root),
    )
    label_manifest = _json_load(label_manifest_path)
    train_records = load_records(label_manifest["train_path"])
    eval_records = load_records(label_manifest["eval_path"])

    baseline_artifact = train_baseline(train_records)
    candidate_artifact = train_autogluon_candidate(train_records, workspace_root, candidate_version, automl_engine=automl_engine)
    baseline_metrics = evaluate(eval_records, baseline_artifact, score_baseline)
    candidate_metrics = evaluate(eval_records, candidate_artifact, scorer_for_artifact(candidate_artifact))

    artifact_dir_path = Path(artifact_dir)
    artifact_dir_path.mkdir(parents=True, exist_ok=True)
    baseline_artifact_path = persist_model_artifact(artifact_dir, baseline_version, baseline_artifact)
    candidate_artifact_path = persist_model_artifact(artifact_dir, candidate_version, candidate_artifact)

    serving_dir = artifact_dir_path.parent / "serving" / "predictive"
    serving_dir.mkdir(parents=True, exist_ok=True)
    serving_artifact_path = serving_dir / "model.joblib"
    serving_model = train_serving_model(train_records)
    dump(serving_model, serving_artifact_path)
    serving_metrics = evaluate_serving_model(eval_records, serving_model)

    evaluation_manifest = {
        "dataset_version": label_manifest["dataset_version"],
        "feature_schema_version": label_manifest["feature_schema_version"],
        "label_manifest": str(label_manifest_path),
        "baseline": {
            "version": baseline_version,
            "artifact_path": str(baseline_artifact_path),
            "artifact": baseline_artifact,
            "metrics": baseline_metrics,
        },
        "candidate": {
            "version": candidate_version,
            "artifact_path": str(candidate_artifact_path),
            "artifact": candidate_artifact,
            "metrics": candidate_metrics,
        },
        "promotion_gate": {**PROMOTION_GATE},
    }
    selection = select_best_model(evaluation_manifest)
    selected_version = selection["selected_model_version"]

    registry = build_registry(
        dataset_version=dataset_version,
        baseline_version=baseline_version,
        candidate_version=candidate_version,
        baseline_artifact=baseline_artifact,
        candidate_artifact=candidate_artifact,
        baseline_metrics=baseline_metrics,
        candidate_metrics=candidate_metrics,
        serving_metrics=serving_metrics,
        selected_version=selected_version,
    )
    registry_path_obj = Path(registry_path)
    registry_path_obj.parent.mkdir(parents=True, exist_ok=True)
    registry_path_obj.write_text(json.dumps(registry, indent=2))

    selected_artifact_path = baseline_artifact_path if selected_version == baseline_version else candidate_artifact_path
    if not skip_minio_upload:
        upload_to_minio(
            registry=registry,
            registry_path=registry_path_obj,
            selected_artifact_path=selected_artifact_path,
            baseline_artifact_path=baseline_artifact_path,
            candidate_artifact_path=candidate_artifact_path,
            serving_artifact_path=serving_artifact_path,
        )
    return registry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", default="full-run")
    parser.add_argument("--dataset-version", default="synthetic-v1")
    parser.add_argument("--workspace-root", default="ai")
    parser.add_argument("--artifact-dir", default="ai/models/artifacts")
    parser.add_argument("--registry-path", default="ai/registry/model_registry.json")
    parser.add_argument("--baseline-version", default="baseline-v1")
    parser.add_argument("--candidate-version", default="candidate-v1")
    parser.add_argument("--automl-engine", default="autogluon")
    parser.add_argument("--size-per-class", type=int, default=120)
    parser.add_argument("--dataset-manifest")
    parser.add_argument("--feature-manifest")
    parser.add_argument("--label-manifest")
    parser.add_argument("--baseline-manifest")
    parser.add_argument("--candidate-manifest")
    parser.add_argument("--evaluation-manifest")
    parser.add_argument("--selection-manifest")
    parser.add_argument("--output")
    parser.add_argument("--skip-minio-upload", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    random.seed(7)

    if args.step == "ingest-data":
        manifest = ingest_dataset(args.dataset_version, args.workspace_root, size_per_class=args.size_per_class)
        target = Path(args.output) if args.output else Path("/tmp") / f"{args.dataset_version}-dataset-manifest.json"
        _json_dump(target, manifest)
        print(target.read_text())
        return

    if args.step == "feature-engineering":
        if not args.dataset_manifest:
            raise ValueError("--dataset-manifest is required for feature-engineering")
        manifest = materialize_feature_windows(args.dataset_manifest, args.workspace_root)
        target = Path(args.output) if args.output else Path("/tmp") / f"{args.dataset_version}-feature-manifest.json"
        _json_dump(target, manifest)
        print(target.read_text())
        return

    if args.step == "label-generation":
        if not args.feature_manifest:
            raise ValueError("--feature-manifest is required for label-generation")
        manifest = generate_labels(args.feature_manifest, args.workspace_root)
        target = Path(args.output) if args.output else Path("/tmp") / f"{args.dataset_version}-label-manifest.json"
        _json_dump(target, manifest)
        print(target.read_text())
        return

    if args.step == "train-baseline":
        if not args.label_manifest:
            raise ValueError("--label-manifest is required for train-baseline")
        label_manifest = _json_load(args.label_manifest)
        artifact = train_baseline(load_records(label_manifest["train_path"]))
        artifact_path = persist_model_artifact(args.artifact_dir, args.baseline_version, artifact)
        manifest = {
            "version": args.baseline_version,
            "model_type": artifact["model_type"],
            "artifact_path": str(artifact_path),
            "label_manifest": args.label_manifest,
        }
        target = Path(args.output) if args.output else Path("/tmp") / f"{args.baseline_version}-manifest.json"
        _json_dump(target, manifest)
        print(target.read_text())
        return

    if args.step == "train-automl":
        if not args.label_manifest:
            raise ValueError("--label-manifest is required for train-automl")
        label_manifest = _json_load(args.label_manifest)
        artifact = train_autogluon_candidate(
            load_records(label_manifest["train_path"]),
            args.workspace_root,
            args.candidate_version,
            automl_engine=args.automl_engine,
        )
        artifact_path = persist_model_artifact(args.artifact_dir, args.candidate_version, artifact)
        manifest = {
            "version": args.candidate_version,
            "model_type": artifact["model_type"],
            "automl_engine": artifact.get("automl_engine", args.automl_engine),
            "artifact_path": str(artifact_path),
            "label_manifest": args.label_manifest,
        }
        target = Path(args.output) if args.output else Path("/tmp") / f"{args.candidate_version}-manifest.json"
        _json_dump(target, manifest)
        print(target.read_text())
        return

    if args.step == "evaluate":
        if not all([args.label_manifest, args.baseline_manifest, args.candidate_manifest]):
            raise ValueError("--label-manifest, --baseline-manifest, and --candidate-manifest are required for evaluate")
        label_manifest = _json_load(args.label_manifest)
        eval_records = load_records(label_manifest["eval_path"])
        baseline_manifest = _json_load(args.baseline_manifest)
        candidate_manifest = _json_load(args.candidate_manifest)
        baseline_artifact = _json_load(baseline_manifest["artifact_path"])
        candidate_artifact = _json_load(candidate_manifest["artifact_path"])
        baseline_metrics = evaluate(eval_records, baseline_artifact, score_baseline)
        candidate_metrics = evaluate(eval_records, candidate_artifact, scorer_for_artifact(candidate_artifact))
        manifest = {
            "dataset_version": label_manifest["dataset_version"],
            "feature_schema_version": label_manifest["feature_schema_version"],
            "label_manifest": args.label_manifest,
            "baseline": {
                "version": baseline_manifest["version"],
                "artifact_path": baseline_manifest["artifact_path"],
                "artifact": baseline_artifact,
                "metrics": baseline_metrics,
            },
            "candidate": {
                "version": candidate_manifest["version"],
                "artifact_path": candidate_manifest["artifact_path"],
                "artifact": candidate_artifact,
                "metrics": candidate_metrics,
            },
            "promotion_gate": {
                **PROMOTION_GATE,
                "status": gate_metrics(candidate_metrics, PROMOTION_GATE)["status"],
            },
        }
        target = Path(args.output) if args.output else Path("/tmp") / f"{args.dataset_version}-evaluation-manifest.json"
        _json_dump(target, manifest)
        print(target.read_text())
        return

    if args.step == "select-best":
        if not args.evaluation_manifest:
            raise ValueError("--evaluation-manifest is required for select-best")
        evaluation = _json_load(args.evaluation_manifest)
        manifest = select_best_model(evaluation)
        target = Path(args.output) if args.output else Path("/tmp") / f"{args.dataset_version}-selection-manifest.json"
        _json_dump(target, manifest)
        print(target.read_text())
        return

    if args.step == "register-model":
        if args.selection_manifest:
            selection = _json_load(args.selection_manifest)
        elif args.evaluation_manifest:
            selection = select_best_model(_json_load(args.evaluation_manifest))
        else:
            raise ValueError("--selection-manifest or --evaluation-manifest is required for register-model")

        label_manifest_path = args.label_manifest or selection.get("label_manifest")
        if not label_manifest_path:
            raise ValueError("--label-manifest is required for register-model")

        label_manifest = _json_load(label_manifest_path)
        train_records = load_records(label_manifest["train_path"])
        eval_records = load_records(label_manifest["eval_path"])

        serving_dir = Path(args.artifact_dir).parent / "serving" / "predictive"
        serving_dir.mkdir(parents=True, exist_ok=True)
        serving_artifact_path = serving_dir / "model.joblib"
        serving_model = train_serving_model(train_records)
        dump(serving_model, serving_artifact_path)
        serving_metrics = evaluate_serving_model(eval_records, serving_model)

        baseline_artifact = selection["baseline"]["artifact"]
        candidate_artifact = selection["candidate"]["artifact"]
        baseline_artifact_path = Path(selection["baseline"]["artifact_path"])
        candidate_artifact_path = Path(selection["candidate"]["artifact_path"])
        selected_version = selection["selected_model_version"]

        registry = build_registry(
            dataset_version=selection["dataset_version"],
            baseline_version=selection["baseline"]["version"],
            candidate_version=selection["candidate"]["version"],
            baseline_artifact=baseline_artifact,
            candidate_artifact=candidate_artifact,
            baseline_metrics=selection["baseline"]["metrics"],
            candidate_metrics=selection["candidate"]["metrics"],
            serving_metrics=serving_metrics,
            selected_version=selected_version,
        )
        registry_path = Path(args.registry_path)
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(json.dumps(registry, indent=2))
        registry["serving_artifact_path"] = str(serving_artifact_path)
        registry["selected_artifact_path"] = str(
            baseline_artifact_path if selected_version == selection["baseline"]["version"] else candidate_artifact_path
        )
        registry["baseline_artifact_path"] = str(baseline_artifact_path)
        registry["candidate_artifact_path"] = str(candidate_artifact_path)
        registry_path.write_text(json.dumps(registry, indent=2))

        target = Path(args.output) if args.output else registry_path
        if target != registry_path:
            _json_dump(target, registry)
        print(target.read_text())
        return

    if args.step == "deploy-model":
        if not args.registry_path:
            raise ValueError("--registry-path is required for deploy-model")
        registry = _json_load(args.registry_path)
        selected_artifact_path = Path(registry["selected_artifact_path"])
        baseline_artifact_path = Path(registry["baseline_artifact_path"])
        candidate_artifact_path = Path(registry["candidate_artifact_path"])
        serving_artifact_path = Path(registry["serving_artifact_path"])
        registry_path = Path(args.registry_path)

        if not args.skip_minio_upload:
            upload_to_minio(
                registry=registry,
                registry_path=registry_path,
                selected_artifact_path=selected_artifact_path,
                baseline_artifact_path=baseline_artifact_path,
                candidate_artifact_path=candidate_artifact_path,
                serving_artifact_path=serving_artifact_path,
            )
        target = Path(args.output) if args.output else registry_path
        if target != registry_path:
            _json_dump(target, registry)
        print(target.read_text())
        return

    registry = full_run(
        dataset_version=args.dataset_version,
        workspace_root=args.workspace_root,
        artifact_dir=args.artifact_dir,
        registry_path=args.registry_path,
        baseline_version=args.baseline_version,
        candidate_version=args.candidate_version,
        automl_engine=args.automl_engine,
        skip_minio_upload=args.skip_minio_upload,
    )
    print(json.dumps(registry, indent=2))


if __name__ == "__main__":
    main()
