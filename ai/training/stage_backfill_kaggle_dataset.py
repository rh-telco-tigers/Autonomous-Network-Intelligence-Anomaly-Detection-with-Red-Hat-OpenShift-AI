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
    "anomaly-detection",
    "telecommunications",
    "networking",
    "sip",
    "openshift-ai",
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
    uploaded_files: list[str],
    source_dataset_card: str,
) -> str:
    quality_summary = manifest.get("quality_summary", {})
    row_counts = manifest.get("row_counts", {})
    source_dataset_versions = manifest.get("source_dataset_versions", [])
    file_inventory = "\n".join(
        f"- `{file_name}`: {FILE_DESCRIPTIONS.get(file_name, 'Included dataset artifact.')}"
        for file_name in uploaded_files
    )
    return (
        f"# {dataset_title}\n\n"
        f"This Kaggle package contains the published IMS SIP backfill feature bundle exported from the OpenShift AI demo environment.\n\n"
        f"## Dataset handle\n\n"
        f"- `{dataset_handle}`\n\n"
        f"## Provenance\n\n"
        f"- bundle_version: `{manifest.get('bundle_version', '')}`\n"
        f"- feature_schema_version: `{manifest.get('feature_schema_version', '')}`\n"
        f"- label_taxonomy_version: `{manifest.get('label_taxonomy_version', '')}`\n"
        f"- git_commit: `{manifest.get('git_commit', '')}`\n"
        f"- source_dataset_versions: `{', '.join(source_dataset_versions)}`\n\n"
        f"## Included files\n\n"
        f"{file_inventory}\n\n"
        f"## Row counts\n\n"
        f"- window_features: `{row_counts.get('window_features', 0)}`\n"
        f"- window_context: `{row_counts.get('window_context', 0)}`\n"
        f"- window_labels: `{row_counts.get('window_labels', 0)}`\n"
        f"- incidents: `{row_counts.get('incidents', 0)}`\n"
        f"- rca_summary: `{row_counts.get('rca_summary', 0)}`\n"
        f"- entity_rows: `{row_counts.get('entity_rows', 0)}`\n\n"
        f"## Quality summary\n\n"
        f"- overall_status: `{quality_summary.get('overall_status', 'unknown')}`\n"
        f"- control_plane_status: `{quality_summary.get('control_plane_status', 'unknown')}`\n"
        f"- incident_linkage_ratio: `{quality_summary.get('incident_linkage_ratio', 0)}`\n"
        f"- rca_attachment_ratio: `{quality_summary.get('rca_attachment_ratio', 0)}`\n\n"
        f"## Intended use\n\n"
        f"- offline anomaly detection experiments\n"
        f"- feature-store training benchmarks\n"
        f"- reproducible demo and workshop datasets\n\n"
        f"## Notes\n\n"
        f"- This upload intentionally excludes cluster-internal URLs and object-store paths from the public manifest.\n"
        f"- Use the parquet artifacts for training pipelines and the CSV artifacts for quick inspection.\n\n"
        f"## Original dataset card\n\n"
        f"{source_dataset_card.strip()}\n"
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

    source_dataset_card = (staging_dir / "SOURCE_DATASET_CARD.md").read_text()
    public_manifest = _public_manifest(manifest, dataset_handle, sorted(downloaded_files))
    (staging_dir / "kaggle_manifest.json").write_text(json.dumps(public_manifest, indent=2))
    downloaded_files.append("kaggle_manifest.json")

    readme_text = _readme_text(
        dataset_title=title,
        dataset_handle=dataset_handle,
        manifest=manifest,
        uploaded_files=sorted(downloaded_files),
        source_dataset_card=source_dataset_card,
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
        or "Anonymized SIP backfill windows and incident labels for IMS anomaly demos"
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
