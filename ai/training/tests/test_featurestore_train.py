import json

import pytest

from ai.training import featurestore_train as ft


def _make_records(labels, per_class=1):
    records = []
    for label in labels:
        for index in range(per_class):
            records.append(
                {
                    "window_id": f"{label}-{index}",
                    "features": {feature: float(index + 1) for feature in ft.FEATURES},
                    "label": 0 if label == "normal_operation" else 1,
                    "anomaly_type": label,
                }
            )
    return records


def test_resolve_training_records_uses_live_featurestore_data(monkeypatch):
    monkeypatch.setenv("IMS_MIN_REAL_WINDOWS", "1")
    monkeypatch.setenv("IMS_ALLOW_BOOTSTRAP_DATASET", "true")

    live_records = _make_records(ft.CANONICAL_LABELS, per_class=1)

    selected_records, metadata = ft._resolve_training_records(live_records, min_per_class=1)

    assert selected_records == live_records
    assert metadata["source"] == "feast-historical-features"
    assert metadata["live_record_count"] == len(live_records)
    assert metadata["class_counts"]["registration_storm"] == 1


def test_resolve_training_records_falls_back_to_bootstrap_data(monkeypatch):
    monkeypatch.setenv("IMS_MIN_REAL_WINDOWS", "9")
    monkeypatch.setenv("IMS_ALLOW_BOOTSTRAP_DATASET", "true")
    monkeypatch.setenv("IMS_BOOTSTRAP_SIZE_PER_CLASS", "1")

    live_records = _make_records(["normal_operation", "registration_storm"], per_class=1)
    bootstrap_records = _make_records(ft.CANONICAL_LABELS, per_class=1)
    monkeypatch.setattr(ft, "generate_dataset", lambda size_per_class: bootstrap_records)

    selected_records, metadata = ft._resolve_training_records(live_records, min_per_class=1)

    assert selected_records == bootstrap_records
    assert metadata["source"] == "synthetic-ims-lab-multiclass"
    assert metadata["live_record_count"] == len(live_records)
    assert metadata["live_class_counts"]["registration_storm"] == 1
    assert metadata["class_counts"]["network_degradation"] == 1


def test_select_candidate_step_requires_passing_gate():
    evaluation_manifest = {
        "dataset_version": "demo-bundle",
        "feature_schema_version": ft.FEATURE_SCHEMA_VERSION,
        "label_manifest": "/tmp/labels.json",
        "promotion_gate": dict(ft.gate_metrics(
            {
                "macro_f1": 0.7,
                "weighted_f1": 0.8,
                "balanced_accuracy": 0.7,
                "per_class_recall": {label: 0.6 for label in ft.CANONICAL_LABELS},
                "normal_false_alarm_rate": 0.1,
                "calibration": {"multiclass_log_loss": 0.4},
                "latency_p95_ms": 10.0,
                "stability_score": 0.95,
            }
        )["gate"]),
        "candidate": {
            "version": "candidate-fs-v2",
            "artifact_path": "/tmp/candidate.json",
            "model_type": "autogluon_tabular_multiclass",
            "metrics": {
                "macro_f1": 0.7,
                "weighted_f1": 0.8,
                "balanced_accuracy": 0.7,
                "per_class_recall": {label: 0.6 for label in ft.CANONICAL_LABELS},
                "normal_false_alarm_rate": 0.1,
                "calibration": {"multiclass_log_loss": 0.4},
                "latency_p95_ms": 10.0,
                "stability_score": 0.95,
            },
        },
    }

    selected = ft._select_candidate_step(evaluation_manifest)

    assert selected["selected_model_version"] == "candidate-fs-v2"
    assert selected["candidate_deployment_ready"] is True
    assert "promotion gate" in selected["selection_reason"]


def test_select_candidate_step_rejects_failed_gate():
    evaluation_manifest = {
        "dataset_version": "demo-bundle",
        "feature_schema_version": ft.FEATURE_SCHEMA_VERSION,
        "label_manifest": "/tmp/labels.json",
        "promotion_gate": dict(ft.gate_metrics(
            {
                "macro_f1": 0.7,
                "weighted_f1": 0.8,
                "balanced_accuracy": 0.7,
                "per_class_recall": {label: 0.6 for label in ft.CANONICAL_LABELS},
                "normal_false_alarm_rate": 0.1,
                "calibration": {"multiclass_log_loss": 0.4},
                "latency_p95_ms": 10.0,
                "stability_score": 0.95,
            }
        )["gate"]),
        "candidate": {
            "version": "candidate-fs-v2",
            "artifact_path": "/tmp/candidate.json",
            "model_type": "autogluon_tabular_multiclass",
            "metrics": {
                "macro_f1": 0.2,
                "weighted_f1": 0.3,
                "balanced_accuracy": 0.2,
                "per_class_recall": {label: 0.2 for label in ft.CANONICAL_LABELS},
                "normal_false_alarm_rate": 0.6,
                "calibration": {"multiclass_log_loss": 3.5},
                "latency_p95_ms": 80.0,
                "stability_score": 0.2,
            },
        },
    }

    with pytest.raises(ValueError, match="failed the promotion gate"):
        ft._select_candidate_step(evaluation_manifest)


def test_export_serving_artifact_uses_autogluon_bundle(tmp_path, monkeypatch):
    train_records = _make_records(ft.CANONICAL_LABELS, per_class=1)
    eval_records = _make_records(ft.CANONICAL_LABELS, per_class=1)
    train_path = tmp_path / "train.json"
    eval_path = tmp_path / "eval.json"
    train_path.write_text(json.dumps(train_records))
    eval_path.write_text(json.dumps(eval_records))

    predictor_dir = tmp_path / "predictor"
    predictor_dir.mkdir()
    (predictor_dir / "metadata.json").write_text(json.dumps({"kind": "autogluon"}))

    training_manifest_path = tmp_path / "training-manifest.json"
    training_manifest_path.write_text(
        json.dumps(
            {
                "bundle_version": "ani-feature-bundle-v1",
                "feature_service_name": "ani_anomaly_scoring_v1",
                "train_path": str(train_path),
                "eval_path": str(eval_path),
                "label_taxonomy_version": "ani_incident_taxonomy_v2",
            }
        )
    )

    candidate_artifact_path = tmp_path / "candidate.json"
    candidate_artifact_path.write_text(
        json.dumps(
            {
                "model_type": "autogluon_tabular_multiclass",
                "predictor_path": str(predictor_dir),
                "class_labels": ft.CANONICAL_LABELS,
                "normal_class_label": "normal_operation",
            }
        )
    )

    selection_manifest_path = tmp_path / "selection-manifest.json"
    selection_manifest_path.write_text(
        json.dumps(
            {
                "selected_model_version": "candidate-fs-v2",
                "selected_model_type": "autogluon_tabular_multiclass",
                "selected_artifact_path": str(candidate_artifact_path),
                "candidate_deployment_ready": True,
            }
        )
    )

    monkeypatch.setattr(
        ft,
        "evaluate",
        lambda records, artifact, scorer: {
            "macro_f1": 0.9,
            "weighted_f1": 0.91,
            "balanced_accuracy": 0.9,
            "per_class_recall": {label: 0.85 for label in ft.CANONICAL_LABELS},
            "normal_false_alarm_rate": 0.05,
            "calibration": {"multiclass_log_loss": 0.2},
            "latency_p95_ms": 5.0,
            "stability_score": 0.96,
        },
    )
    monkeypatch.setattr(
        ft,
        "_upload_serving_bundle",
        lambda *args, **kwargs: {
            "storage_uri": "s3://ani-models/predictive-featurestore/ani-predictive-fs/candidate-fs-v2/",
            "alias_storage_uri": "s3://ani-models/predictive-featurestore/ani-predictive-fs/current/",
            "weights_uri": "",
            "alias_weights_uri": "",
        },
    )

    manifest = ft._export_serving_artifact_step(
        str(training_manifest_path),
        str(selection_manifest_path),
        str(tmp_path / "artifacts"),
        "ani-predictive-fs",
        "ani-autogluon-mlserver-runtime",
        "autogluon",
        "1",
        "predictive-featurestore",
        "current",
        "v2",
    )

    model_settings_path = tmp_path / "serving" / "ani-predictive-fs" / "ani-predictive-fs" / "model-settings.json"
    predictor_copy = tmp_path / "serving" / "ani-predictive-fs" / "ani-predictive-fs" / "predictor" / "metadata.json"
    model_settings = json.loads(model_settings_path.read_text())

    assert manifest["serving_backend"] == "mlserver-autogluon"
    assert manifest["serving_runtime_name"] == "ani-autogluon-mlserver-runtime"
    assert model_settings["implementation"] == ft.DEFAULT_AUTOGLUON_MLSERVER_IMPLEMENTATION
    assert model_settings["parameters"]["uri"] == "./predictor"
    assert predictor_copy.exists()
