#!/usr/bin/env python3
"""Stage a public Kaggle upload directory from the published backfill bundle."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

DEFAULT_DATASET_STORE_ENDPOINT = "http://model-storage-minio.ani-data.svc.cluster.local:9000"
DEFAULT_DATASET_STORE_BUCKET = "ani-models"
DEFAULT_DATASET_STORE_PREFIX = "pipelines/ani-datascience/datasets"
DEFAULT_WORKSPACE_ROOT = ".tmp/kaggle-backfill-workspace"
DEFAULT_UPLOAD_ROOT = ".tmp/kaggle-backfill-upload"
DEFAULT_LICENSE = "CC-BY-4.0"
DEFAULT_KEYWORDS = [
    "tabular",
    "classification",
    "binary classification",
    "multiclass classification",
    "internet",
]

FILE_DESCRIPTIONS = {
    "window_features.parquet": "Feature-engineered training rows in parquet format.",
    "window_features.csv": "Feature-engineered training rows in CSV format.",
    "window_context.parquet": "Window context and scenario metadata in parquet format.",
    "window_context.csv": "Window context and scenario metadata in CSV format.",
    "window_labels.parquet": "Window labels and incident linkage in parquet format.",
    "window_labels.csv": "Window labels and incident linkage in CSV format.",
    "incidents.parquet": "Incident-level training labels and workflow status in parquet format.",
    "incidents.csv": "Incident-level training labels and workflow status in CSV format.",
    "rca_summary.parquet": "RCA summaries aligned to incident records in parquet format.",
    "rca_summary.csv": "RCA summaries aligned to incident records in CSV format.",
    "offline_source.parquet": "Feature-store offline source parquet for downstream model training.",
    "entity_rows.parquet": "Feature-store entity rows parquet for offline joins.",
    "quality_report.json": "Quality checks and coverage metrics for the exported bundle.",
    "kaggle_manifest.json": "Public-facing manifest for this Kaggle publication.",
    "README.md": "Dataset overview, provenance, and usage notes.",
    "SOURCE_DATASET_CARD.md": "Original bundle dataset card captured from the source export.",
    "FILES.md": "Inventory of files included in the Kaggle upload.",
    "dataset-metadata.json": "Kaggle dataset metadata used for upload.",
}


def _format_count(value: Any) -> str:
    return f"{int(value or 0):,}"


def _format_percent(numerator: float, denominator: float) -> str:
    if not denominator:
        return "0.0%"
    return f"{(numerator / denominator) * 100:.1f}%"


def _quality_report_insights(quality_report: dict[str, Any]) -> dict[str, Any]:
    row_count = int(quality_report.get("row_count") or 0)
    incident_count = int(quality_report.get("incident_count") or 0)
    rca_count = int(quality_report.get("rca_count") or 0)
    label_distribution = quality_report.get("label_distribution") or {}
    normal_count = int(label_distribution.get("0") or 0)
    anomaly_count = int(label_distribution.get("1") or 0)
    anomaly_distribution = quality_report.get("anomaly_type_distribution") or {}
    anomaly_types = [name for name in anomaly_distribution if name != "normal_operation"]
    return {
        "row_count": row_count,
        "incident_count": incident_count,
        "rca_count": rca_count,
        "normal_count": normal_count,
        "anomaly_count": anomaly_count,
        "normal_ratio": _format_percent(normal_count, row_count),
        "anomaly_ratio": _format_percent(anomaly_count, row_count),
        "scenario_count": len(anomaly_distribution),
        "anomaly_type_count": len(anomaly_types),
        "feature_count": len(quality_report.get("numeric_feature_columns") or []),
        "control_plane_status": str((quality_report.get("source_status") or {}).get("control_plane") or "unknown"),
        "generated_at": str(quality_report.get("generated_at") or ""),
    }


def _distribution_markdown(distribution: dict[str, Any]) -> str:
    if not distribution:
        return "- none"
    total = float(sum(int(value or 0) for value in distribution.values()))
    lines = []
    for name, count in sorted(distribution.items(), key=lambda item: (-int(item[1] or 0), str(item[0]))):
        lines.append(f"- `{name}`: {_format_count(count)} rows ({_format_percent(int(count or 0), total)})")
    return "\n".join(lines)


def _table_rows(rows: list[tuple[str, str]]) -> str:
    header = "| Signal | Value |\n| --- | --- |\n"
    body = "\n".join(f"| {label} | {value} |" for label, value in rows)
    return header + body


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle-version", required=True)
    parser.add_argument("--dataset-handle", help="Kaggle dataset handle in the form owner/slug")
    parser.add_argument("--dataset-owner")
    parser.add_argument("--dataset-slug")
    parser.add_argument("--title")
    parser.add_argument("--subtitle")
    parser.add_argument("--license-name", default=DEFAULT_LICENSE)
    parser.add_argument("--workspace-root", default=DEFAULT_WORKSPACE_ROOT)
    parser.add_argument("--upload-dir")
    parser.add_argument("--manifest-path")
    parser.add_argument("--keywords", nargs="*", default=DEFAULT_KEYWORDS)
    return parser.parse_args()


def _dataset_store_endpoint() -> str:
    return os.getenv("DATASET_STORE_ENDPOINT", DEFAULT_DATASET_STORE_ENDPOINT).strip()


def _dataset_store_bucket() -> str:
    return os.getenv("DATASET_STORE_BUCKET", DEFAULT_DATASET_STORE_BUCKET).strip()


def _dataset_store_prefix() -> str:
    return os.getenv("DATASET_STORE_PREFIX", DEFAULT_DATASET_STORE_PREFIX).strip("/")


def _dataset_store_access_key() -> str:
    return os.getenv("DATASET_STORE_ACCESS_KEY", "minioadmin").strip()


def _dataset_store_secret_key() -> str:
    return os.getenv("DATASET_STORE_SECRET_KEY", "minioadmin").strip()


def _dataset_s3_client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=_dataset_store_endpoint(),
        aws_access_key_id=_dataset_store_access_key(),
        aws_secret_access_key=_dataset_store_secret_key(),
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )


def _dataset_object_key(relative_path: str) -> str:
    prefix = _dataset_store_prefix()
    normalized_relative = relative_path.lstrip("/")
    return f"{prefix}/{normalized_relative}" if prefix else normalized_relative


def _s3_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key.lstrip('/')}"


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    stripped = uri.removeprefix("s3://")
    bucket, _, key = stripped.partition("/")
    return bucket, key


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    return normalized[:50] or "ims-sipp-backfill"


def _title_from_slug(slug: str) -> str:
    return " ".join(part.capitalize() for part in slug.split("-") if part)


def _normalize_title(title: str) -> str:
    normalized = title.strip()
    if len(normalized) < 6:
        normalized = f"{normalized} dataset".strip()
    return normalized[:50]


def _normalize_subtitle(subtitle: str) -> str:
    normalized = " ".join(subtitle.split())
    if len(normalized) < 20:
        normalized = normalized + " " * (20 - len(normalized))
    return normalized[:80]


def _resolve_dataset_handle(args: argparse.Namespace) -> str:
    if args.dataset_handle:
        handle = args.dataset_handle.strip()
    else:
        owner = str(args.dataset_owner or "").strip()
        slug = str(args.dataset_slug or "").strip()
        if not owner:
            raise ValueError("Provide --dataset-handle or --dataset-owner")
        if not slug:
            slug = _slugify(f"ims-sipp-backfill-{args.bundle_version}")
        handle = f"{owner}/{slug}"
    owner, _, slug = handle.partition("/")
    if not owner or not slug:
        raise ValueError("Dataset handle must be in the form owner/slug")
    return f"{owner}/{_slugify(slug)}"


def _bundle_manifest_reference(bundle_version: str, workspace_root: str, explicit_manifest: str | None) -> str:
    if explicit_manifest:
        return explicit_manifest
    local_candidate = Path(workspace_root) / "feature-bundles" / bundle_version / "manifest.json"
    if local_candidate.exists():
        return str(local_candidate)
    bucket = _dataset_store_bucket()
    key = _dataset_object_key(f"feature-bundles/{bundle_version}/manifest.json")
    return _s3_uri(bucket, key)


def _read_json(reference: str) -> dict[str, Any]:
    if reference.startswith("s3://"):
        bucket, key = _parse_s3_uri(reference)
        response = _dataset_s3_client().get_object(Bucket=bucket, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    return json.loads(Path(reference).read_text())


def _download_file(reference: str, target_path: Path) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if reference.startswith("s3://"):
        bucket, key = _parse_s3_uri(reference)
        _dataset_s3_client().download_file(bucket, key, str(target_path))
    else:
        shutil.copy2(reference, target_path)
    return target_path


def _artifact_sources(manifest: dict[str, Any]) -> dict[str, str]:
    return {
        "SOURCE_DATASET_CARD.md": str(manifest["artifacts"]["dataset_card"]),
        "quality_report.json": str(manifest["artifacts"]["quality_report"]),
        "window_features.parquet": str(manifest["artifacts"]["tables"]["window_features_parquet"]),
        "window_context.parquet": str(manifest["artifacts"]["tables"]["window_context_parquet"]),
        "window_labels.parquet": str(manifest["artifacts"]["tables"]["window_labels_parquet"]),
        "incidents.parquet": str(manifest["artifacts"]["tables"]["incidents_parquet"]),
        "rca_summary.parquet": str(manifest["artifacts"]["tables"]["rca_summary_parquet"]),
        "window_features.csv": str(manifest["artifacts"]["csv"]["window_features_csv"]),
        "window_context.csv": str(manifest["artifacts"]["csv"]["window_context_csv"]),
        "window_labels.csv": str(manifest["artifacts"]["csv"]["window_labels_csv"]),
        "incidents.csv": str(manifest["artifacts"]["csv"]["incidents_csv"]),
        "rca_summary.csv": str(manifest["artifacts"]["csv"]["rca_summary_csv"]),
        "offline_source.parquet": str(manifest["artifacts"]["feature_store"]["offline_source_parquet"]),
        "entity_rows.parquet": str(manifest["artifacts"]["feature_store"]["entity_rows_parquet"]),
    }


def _public_manifest(manifest: dict[str, Any], dataset_handle: str, uploaded_files: list[str]) -> dict[str, Any]:
    return {
        "dataset_handle": dataset_handle,
        "bundle_version": manifest.get("bundle_version"),
        "bundle_contract_version": manifest.get("bundle_contract_version"),
        "feature_schema_version": manifest.get("feature_schema_version"),
        "label_taxonomy_version": manifest.get("label_taxonomy_version"),
        "source_dataset_versions": manifest.get("source_dataset_versions", []),
        "project": manifest.get("project"),
        "generated_at": manifest.get("generated_at"),
        "git_commit": manifest.get("git_commit"),
        "row_counts": manifest.get("row_counts", {}),
        "source_counts": manifest.get("source_counts", {}),
        "validation": manifest.get("validation", {}),
        "quality_summary": manifest.get("quality_summary", {}),
        "included_files": uploaded_files,
    }


def _files_markdown(uploaded_files: list[str]) -> str:
    lines = ["# File Inventory", ""]
    for file_name in uploaded_files:
        description = FILE_DESCRIPTIONS.get(file_name, "Included dataset artifact.")
        lines.append(f"- `{file_name}`: {description}")
    lines.append("")
    return "\n".join(lines)


def _readme_text(
    *,
    dataset_title: str,
    dataset_handle: str,
    manifest: dict[str, Any],
    quality_report: dict[str, Any],
    uploaded_files: list[str],
) -> str:
    row_counts = manifest.get("row_counts", {})
    source_dataset_versions = manifest.get("source_dataset_versions", [])
    quality = _quality_report_insights(quality_report)
    file_inventory = "\n".join(
        f"- `{file_name}`: {FILE_DESCRIPTIONS.get(file_name, 'Included dataset artifact.')}"
        for file_name in uploaded_files
        if file_name
    )
    release_signals = _table_rows(
        [
            ("Dataset handle", f"`{dataset_handle}`"),
            ("Bundle version", f"`{manifest.get('bundle_version', '')}`"),
            ("Source dataset versions", ", ".join(f"`{value}`" for value in source_dataset_versions) or "`unknown`"),
            ("Generated at", f"`{quality['generated_at'] or manifest.get('generated_at', '')}`"),
            ("Feature schema", f"`{manifest.get('feature_schema_version', '')}`"),
            ("Label taxonomy", f"`{manifest.get('label_taxonomy_version', '')}`"),
            ("Git commit", f"`{manifest.get('git_commit', '')}`"),
            ("Window-level examples", _format_count(quality["row_count"])),
            ("Normal vs anomaly mix", f"{_format_count(quality['normal_count'])} normal ({quality['normal_ratio']}), {_format_count(quality['anomaly_count'])} anomaly ({quality['anomaly_ratio']})"),
            ("Scenario classes", f"{quality['scenario_count']} total, {quality['anomaly_type_count']} anomalous"),
            ("Serving-aligned numeric features", _format_count(quality["feature_count"])),
            ("Control-plane reachability during bundle build", f"`{quality['control_plane_status']}`"),
        ]
    )
    row_count_table = _table_rows(
        [
            ("window_features.parquet", _format_count(row_counts.get("window_features", 0))),
            ("window_context.parquet", _format_count(row_counts.get("window_context", 0))),
            ("window_labels.parquet", _format_count(row_counts.get("window_labels", 0))),
            ("incidents.parquet", _format_count(row_counts.get("incidents", 0))),
            ("rca_summary.parquet", _format_count(row_counts.get("rca_summary", 0))),
            ("entity_rows.parquet", _format_count(row_counts.get("entity_rows", 0))),
        ]
    )
    anomaly_distribution = _distribution_markdown(quality_report.get("anomaly_type_distribution") or {})
    feature_list = "\n".join(
        f"- `{feature}`" for feature in (quality_report.get("numeric_feature_columns") or [])
    ) or "- none"
    limitations = [
        "- `incidents.parquet` and `rca_summary.parquet` may legitimately contain zero rows in bundles produced from traffic-only backfill windows. Use this release primarily for window-level anomaly classification unless those tables are populated.",
        "- This package intentionally strips cluster-internal object-store URIs and service URLs from the public manifest.",
        "- The release is a deterministic snapshot, not a live continuously updating feed.",
    ]
    return (
        f"# {dataset_title}\n\n"
        "This release packages the IMS SIP backfill feature bundle as a reproducible Kaggle dataset for tabular anomaly-detection work. "
        "It is designed for offline model training, feature-store benchmarking, and demo reproducibility rather than raw packet replay.\n\n"
        "## Why this release is strong\n\n"
        "- public-safe packaging with explicit manifests, a quality report, and a preserved source dataset card\n"
        "- both Parquet and CSV exports so the data is useful for production training and quick inspection\n"
        "- serving-aligned numeric feature columns that match the current anomaly scoring contract\n"
        "- deterministic schema and provenance fields for reproducible offline experiments\n\n"
        "## Release signals\n\n"
        f"{release_signals}\n\n"
        "## Recommended use cases\n\n"
        "- binary anomaly detection using `window_features.parquet` joined with `window_labels.parquet`\n"
        "- multiclass scenario classification across the published anomaly types\n"
        "- benchmark comparisons between tabular model families on a fixed, documented snapshot\n"
        "- feature-store offline training flows using `offline_source.parquet` and `entity_rows.parquet`\n\n"
        "## Row counts\n\n"
        f"{row_count_table}\n\n"
        "## Scenario distribution\n\n"
        f"{anomaly_distribution}\n\n"
        "## Numeric feature columns\n\n"
        f"{feature_list}\n\n"
        "## Quick start\n\n"
        "```python\n"
        "import pandas as pd\n\n"
        "features = pd.read_parquet('window_features.parquet')\n"
        "labels = pd.read_parquet('window_labels.parquet')[['window_id', 'label', 'anomaly_type']]\n"
        "training_frame = features.merge(labels, on='window_id', how='left')\n"
        "print(training_frame.head())\n"
        "```\n\n"
        "## Included artifacts\n\n"
        f"{file_inventory}\n\n"
        "## Quality and provenance files\n\n"
        "- `quality_report.json`: authoritative release statistics and source-status details\n"
        "- `kaggle_manifest.json`: public-facing manifest with provenance and included-file inventory\n"
        "- `SOURCE_DATASET_CARD.md`: original internal bundle card preserved alongside this release\n"
        "- `FILES.md`: concise file-by-file inventory for the upload directory\n\n"
        "## Limitations and interpretation notes\n\n"
        f"{chr(10).join(limitations)}\n\n"
        "## License\n\n"
        "- `CC-BY-4.0`\n"
    )


def _resource_entries(uploaded_files: list[str]) -> list[dict[str, str]]:
    resources: list[dict[str, str]] = []
    for file_name in uploaded_files:
        if file_name == "dataset-metadata.json":
            continue
        resources.append(
            {
                "path": file_name,
                "description": FILE_DESCRIPTIONS.get(file_name, "Included dataset artifact."),
            }
        )
    return resources


def _dataset_metadata(
    *,
    dataset_handle: str,
    title: str,
    subtitle: str,
    readme_text: str,
    uploaded_files: list[str],
    license_name: str,
    keywords: list[str],
    source_dataset_versions: list[str],
) -> dict[str, Any]:
    return {
        "title": title,
        "subtitle": subtitle,
        "description": readme_text,
        "id": dataset_handle,
        "licenses": [{"name": license_name}],
        "keywords": keywords,
        "expectedUpdateFrequency": "never",
        "userSpecifiedSources": (
            "Generated from the IMS anomaly detection backfill bundle "
            f"covering dataset versions: {', '.join(source_dataset_versions)}"
        ),
        "resources": _resource_entries(uploaded_files),
    }


def stage_backfill_kaggle_dataset(
    *,
    bundle_version: str,
    dataset_handle: str,
    title: str,
    subtitle: str,
    license_name: str,
    workspace_root: str,
    upload_dir: str | None,
    manifest_path: str | None,
    keywords: list[str],
) -> dict[str, Any]:
    manifest_reference = _bundle_manifest_reference(bundle_version, workspace_root, manifest_path)
    manifest = _read_json(manifest_reference)

    owner, _, slug = dataset_handle.partition("/")
    if not upload_dir:
        upload_dir = str(Path(DEFAULT_UPLOAD_ROOT) / owner / slug)
    staging_dir = Path(upload_dir)
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    artifact_sources = _artifact_sources(manifest)
    downloaded_files: list[str] = []
    for file_name, source in artifact_sources.items():
        _download_file(source, staging_dir / file_name)
        downloaded_files.append(file_name)

    quality_report = json.loads((staging_dir / "quality_report.json").read_text())
    public_manifest = _public_manifest(manifest, dataset_handle, sorted(downloaded_files))
    (staging_dir / "kaggle_manifest.json").write_text(json.dumps(public_manifest, indent=2))
    downloaded_files.append("kaggle_manifest.json")

    readme_text = _readme_text(
        dataset_title=title,
        dataset_handle=dataset_handle,
        manifest=manifest,
        quality_report=quality_report,
        uploaded_files=sorted(downloaded_files),
    )
    (staging_dir / "README.md").write_text(readme_text)
    downloaded_files.append("README.md")

    files_md = _files_markdown(sorted(downloaded_files))
    (staging_dir / "FILES.md").write_text(files_md)
    downloaded_files.append("FILES.md")

    metadata = _dataset_metadata(
        dataset_handle=dataset_handle,
        title=title,
        subtitle=subtitle,
        readme_text=readme_text,
        uploaded_files=sorted(downloaded_files),
        license_name=license_name,
        keywords=keywords,
        source_dataset_versions=[str(value) for value in manifest.get("source_dataset_versions", [])],
    )
    (staging_dir / "dataset-metadata.json").write_text(json.dumps(metadata, indent=2))
    downloaded_files.append("dataset-metadata.json")

    result = {
        "bundle_version": bundle_version,
        "dataset_handle": dataset_handle,
        "upload_dir": str(staging_dir),
        "uploaded_files": sorted(downloaded_files),
        "manifest_reference": manifest_reference,
    }
    return result


def main() -> None:
    args = _parse_args()
    dataset_handle = _resolve_dataset_handle(args)
    _, _, dataset_slug = dataset_handle.partition("/")
    title = _normalize_title(args.title or _title_from_slug(dataset_slug))
    subtitle = _normalize_subtitle(
        args.subtitle
        or "Window-level SIP anomaly features for IMS detection benchmarks"
    )
    result = stage_backfill_kaggle_dataset(
        bundle_version=args.bundle_version,
        dataset_handle=dataset_handle,
        title=title,
        subtitle=subtitle,
        license_name=args.license_name,
        workspace_root=args.workspace_root,
        upload_dir=args.upload_dir,
        manifest_path=args.manifest_path,
        keywords=[keyword for keyword in args.keywords if str(keyword).strip()],
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
