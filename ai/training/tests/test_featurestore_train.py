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
