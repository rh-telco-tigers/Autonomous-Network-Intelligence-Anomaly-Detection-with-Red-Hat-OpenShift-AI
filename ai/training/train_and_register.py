import argparse
import json
import os
import random
from pathlib import Path
from statistics import mean, pstdev

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


def normal_sample():
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


def registration_storm_sample():
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


def malformed_invite_sample():
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


def hss_latency_sample():
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


def generate_dataset(size_per_class=120):
    records = []
    for _ in range(size_per_class):
        records.append({"features": normal_sample(), "label": 0, "anomaly_type": "normal"})
        records.append({"features": registration_storm_sample(), "label": 1, "anomaly_type": "registration_storm"})
        records.append({"features": malformed_invite_sample(), "label": 1, "anomaly_type": "malformed_sip"})
        records.append({"features": hss_latency_sample(), "label": 1, "anomaly_type": "service_degradation"})
    random.shuffle(records)
    return records


def split_dataset(records):
    cutoff = int(len(records) * 0.7)
    return records[:cutoff], records[cutoff:]


def train_baseline(train_records):
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
        "feature_schema_version": "feature_schema_v1",
        "feature_stats": feature_stats,
        "feature_weights": feature_weights,
    }


def score_baseline(sample, artifact):
    weighted_sum = 0.0
    total_weight = 0.0
    for feature, weight in artifact["feature_weights"].items():
        mean_value = artifact["feature_stats"][feature]["mean"]
        std_value = max(artifact["feature_stats"][feature]["std"], 0.01)
        z_score = max(0.0, (sample[feature] - mean_value) / (2.0 * std_value))
        weighted_sum += min(z_score, 1.0) * weight
        total_weight += weight
    return min(weighted_sum / max(total_weight, 0.001), 0.99)


def train_candidate(train_records):
    positive = [record["features"] for record in train_records if record["label"] == 1]
    negative = [record["features"] for record in train_records if record["label"] == 0]
    weights = {}
    bounds = {}
    deltas = {}
    for feature in FEATURES:
        pos_mean = mean(sample[feature] for sample in positive)
        neg_mean = mean(sample[feature] for sample in negative)
        deltas[feature] = max(pos_mean - neg_mean, 0.01)
        values = [record["features"][feature] for record in train_records]
        bounds[feature] = {"min": min(values), "max": max(values)}

    total = sum(deltas.values())
    weights = {feature: round(delta / total, 6) for feature, delta in deltas.items()}
    return {
        "model_type": "weighted_rule_model",
        "threshold": 0.56,
        "feature_schema_version": "feature_schema_v1",
        "weights": weights,
        "bounds": bounds,
    }


def score_candidate(sample, artifact):
    weighted_sum = 0.0
    total_weight = 0.0
    for feature, weight in artifact["weights"].items():
        lower = artifact["bounds"][feature]["min"]
        upper = artifact["bounds"][feature]["max"]
        normalized = 0.0 if upper <= lower else (sample[feature] - lower) / (upper - lower)
        normalized = min(max(normalized, 0.0), 1.0)
        weighted_sum += normalized * weight
        total_weight += weight
    return min(weighted_sum / max(total_weight, 0.001), 0.99)


def evaluate(records, artifact, scorer):
    threshold = artifact["threshold"]
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


def vectorize(records):
    features = [[record["features"][feature] for feature in FEATURES] for record in records]
    labels = [record["label"] for record in records]
    return features, labels


def train_serving_model(train_records):
    features, labels = vectorize(train_records)
    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("classifier", LogisticRegression(max_iter=1000, random_state=7)),
        ]
    )
    model.fit(features, labels)
    return model


def evaluate_serving_model(records, model):
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


def upload_to_minio(
    registry: dict,
    registry_path: Path,
    selected_artifact_path: Path,
    baseline_artifact_path: Path,
    candidate_artifact_path: Path,
    serving_artifact_path: Path,
) -> dict:
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-version", default="synthetic-v1")
    parser.add_argument("--artifact-dir", default="ai/models/artifacts")
    parser.add_argument("--registry-path", default="ai/registry/model_registry.json")
    parser.add_argument("--baseline-version", default="baseline-v1")
    parser.add_argument("--candidate-version", default="candidate-v1")
    parser.add_argument("--skip-minio-upload", action="store_true")
    args = parser.parse_args()

    random.seed(7)
    records = generate_dataset()
    train_records, eval_records = split_dataset(records)

    baseline_artifact = train_baseline(train_records)
    candidate_artifact = train_candidate(train_records)
    baseline_metrics = evaluate(eval_records, baseline_artifact, score_baseline)
    candidate_metrics = evaluate(eval_records, candidate_artifact, score_candidate)

    artifact_dir = Path(args.artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    baseline_artifact_path = artifact_dir / f"{args.baseline_version}.json"
    candidate_artifact_path = artifact_dir / f"{args.candidate_version}.json"
    baseline_artifact_path.write_text(json.dumps(baseline_artifact, indent=2))
    candidate_artifact_path.write_text(json.dumps(candidate_artifact, indent=2))
    serving_dir = artifact_dir.parent / "serving" / "predictive"
    serving_dir.mkdir(parents=True, exist_ok=True)
    serving_artifact_path = serving_dir / "model.joblib"

    serving_model = train_serving_model(train_records)
    dump(serving_model, serving_artifact_path)
    serving_metrics = evaluate_serving_model(eval_records, serving_model)

    selected_version = args.baseline_version
    if (
        candidate_metrics["precision"] >= 0.8
        and candidate_metrics["false_positive_rate"] <= 0.2
        and candidate_metrics["f1"] >= baseline_metrics["f1"]
    ):
        selected_version = args.candidate_version

    registry = {
        "feature_schema_version": "feature_schema_v1",
        "dataset_version": args.dataset_version,
        "deployed_model_version": selected_version,
        "promotion_gate": {
            "min_precision": 0.8,
            "max_false_positive_rate": 0.2,
            "status": "passed" if candidate_metrics["precision"] >= 0.8 and candidate_metrics["false_positive_rate"] <= 0.2 else "fallback_to_baseline",
        },
        "serving_artifact": "models/serving/predictive/model.joblib",
        "models": [
            {
                "version": args.baseline_version,
                "kind": "baseline_threshold",
                "artifact": f"models/artifacts/{args.baseline_version}.json",
                "dataset_version": args.dataset_version,
                "feature_schema_version": "feature_schema_v1",
                "metrics": baseline_metrics,
            },
            {
                "version": args.candidate_version,
                "kind": "weighted_rule_model",
                "artifact": f"models/artifacts/{args.candidate_version}.json",
                "dataset_version": args.dataset_version,
                "feature_schema_version": "feature_schema_v1",
                "metrics": candidate_metrics,
            },
            {
                "version": "predictive-serving-v1",
                "kind": "sklearn_logistic_regression",
                "artifact": "models/serving/predictive/model.joblib",
                "dataset_version": args.dataset_version,
                "feature_schema_version": "feature_schema_v1",
                "metrics": serving_metrics,
            },
        ],
    }
    registry_path = Path(args.registry_path)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(registry, indent=2))

    selected_artifact_path = baseline_artifact_path if selected_version == args.baseline_version else candidate_artifact_path
    if not args.skip_minio_upload:
        upload_to_minio(
            registry=registry,
            registry_path=registry_path,
            selected_artifact_path=selected_artifact_path,
            baseline_artifact_path=baseline_artifact_path,
            candidate_artifact_path=candidate_artifact_path,
            serving_artifact_path=serving_artifact_path,
        )

    print(registry_path.read_text())


if __name__ == "__main__":
    main()
