from __future__ import annotations

import json
from pathlib import Path

from ai.training.stage_backfill_kaggle_dataset import DEFAULT_KEYWORDS, stage_backfill_kaggle_dataset


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def test_stage_backfill_kaggle_dataset_from_local_manifest(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    upload_root = tmp_path / "upload"

    dataset_card = source_root / "dataset_card.md"
    quality_report = source_root / "quality_report.json"
    data_files = {
        "window_features_parquet": source_root / "window_features.parquet",
        "window_context_parquet": source_root / "window_context.parquet",
        "window_labels_parquet": source_root / "window_labels.parquet",
        "incidents_parquet": source_root / "incidents.parquet",
        "rca_summary_parquet": source_root / "rca_summary.parquet",
        "window_features_csv": source_root / "window_features.csv",
        "window_context_csv": source_root / "window_context.csv",
        "window_labels_csv": source_root / "window_labels.csv",
        "incidents_csv": source_root / "incidents.csv",
        "rca_summary_csv": source_root / "rca_summary.csv",
        "offline_source_parquet": source_root / "offline_source.parquet",
        "entity_rows_parquet": source_root / "entity_rows.parquet",
    }

    dataset_card.parent.mkdir(parents=True, exist_ok=True)
    dataset_card.write_text("# Source Card\n\nThis is the original bundle card.\n")
    _write_json(quality_report, {"overall_status": "passed", "incident_linkage_ratio": 0.91})
    for path in data_files.values():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("placeholder")

    manifest = {
        "bundle_version": "ani-backfill-feature-bundle-v1",
        "bundle_contract_version": "ani_feature_bundle_v1",
        "feature_schema_version": "feature_schema_v1",
        "label_taxonomy_version": "ani_incident_taxonomy_v2",
        "source_dataset_versions": ["backfill-sipp-100k"],
        "project": "ani-demo",
        "generated_at": "2026-04-21T00:00:00Z",
        "git_commit": "abc123",
        "row_counts": {"window_features": 10, "incidents": 3},
        "source_counts": {"feature_windows": 10},
        "validation": {"status": "passed"},
        "quality_summary": {"overall_status": "passed", "control_plane_status": "ok"},
        "artifacts": {
            "dataset_card": str(dataset_card),
            "quality_report": str(quality_report),
            "tables": {
                "window_features_parquet": str(data_files["window_features_parquet"]),
                "window_context_parquet": str(data_files["window_context_parquet"]),
                "window_labels_parquet": str(data_files["window_labels_parquet"]),
                "incidents_parquet": str(data_files["incidents_parquet"]),
                "rca_summary_parquet": str(data_files["rca_summary_parquet"]),
            },
            "csv": {
                "window_features_csv": str(data_files["window_features_csv"]),
                "window_context_csv": str(data_files["window_context_csv"]),
                "window_labels_csv": str(data_files["window_labels_csv"]),
                "incidents_csv": str(data_files["incidents_csv"]),
                "rca_summary_csv": str(data_files["rca_summary_csv"]),
            },
            "feature_store": {
                "offline_source_parquet": str(data_files["offline_source_parquet"]),
                "entity_rows_parquet": str(data_files["entity_rows_parquet"]),
            },
        },
    }
    manifest_path = source_root / "manifest.json"
    _write_json(manifest_path, manifest)

    result = stage_backfill_kaggle_dataset(
        bundle_version="ani-backfill-feature-bundle-v1",
        dataset_handle="demo-owner/ims-sipp-backfill",
        title="IMS SIP Backfill Dataset",
        subtitle="Anonymized SIP backfill windows and incident labels for IMS anomaly demos",
        license_name="CC-BY-4.0",
        workspace_root=str(tmp_path / "workspace"),
        upload_dir=str(upload_root),
        manifest_path=str(manifest_path),
        keywords=["sip", "anomaly-detection"],
    )

    assert result["dataset_handle"] == "demo-owner/ims-sipp-backfill"
    assert Path(result["upload_dir"]).exists()
    readme = (upload_root / "README.md").read_text()
    assert readme.startswith("# IMS SIP Backfill Dataset")
    assert "## Release signals" in readme
    assert "## Quick start" in readme
    assert "quality_report.json" in readme
    assert (upload_root / "SOURCE_DATASET_CARD.md").exists()
    assert (upload_root / "kaggle_manifest.json").exists()

    metadata = json.loads((upload_root / "dataset-metadata.json").read_text())
    assert metadata["id"] == "demo-owner/ims-sipp-backfill"
    assert metadata["licenses"] == [{"name": "CC-BY-4.0"}]
    assert metadata["keywords"] == ["sip", "anomaly-detection"]
    assert "dataset_card" not in json.loads((upload_root / "kaggle_manifest.json").read_text())


def test_default_keywords_use_supported_kaggle_tags() -> None:
    assert DEFAULT_KEYWORDS == [
        "tabular",
        "classification",
        "binary classification",
        "multiclass classification",
        "internet",
    ]
