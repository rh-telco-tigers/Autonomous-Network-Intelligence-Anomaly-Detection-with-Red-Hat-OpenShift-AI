from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from release_runtime import normalize_release, publish_release, snapshot_sources, validate_release

DEFAULT_APPROVAL_LIMIT = 1000
DEFAULT_AUDIT_LIMIT = 1000


def _write_output(path: str | None, payload: dict[str, object], fallback_name: str) -> None:
    target = Path(path) if path else Path("/tmp") / fallback_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2))
    print(target.read_text())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incident release workflow runtime")
    parser.add_argument("--step", required=True)
    parser.add_argument("--release-version")
    parser.add_argument("--source-dataset-version", default="live-sipp-v1")
    parser.add_argument("--project", default="ani-demo")
    parser.add_argument("--workspace-root", default="/tmp/ani-incident-release")
    parser.add_argument("--public-record-target", type=int, default=10_000)
    parser.add_argument("--release-mode", default="draft")
    parser.add_argument("--previous-release-version")
    parser.add_argument("--source-snapshot-id")
    parser.add_argument("--snapshot-manifest")
    parser.add_argument("--normalized-manifest")
    parser.add_argument("--validation-manifest")
    parser.add_argument("--approval-limit", type=int, default=DEFAULT_APPROVAL_LIMIT)
    parser.add_argument("--audit-limit", type=int, default=DEFAULT_AUDIT_LIMIT)
    parser.add_argument("--output")
    return parser.parse_args()


def _configure_validation_mode(release_mode: str) -> None:
    normalized_mode = (release_mode or "").strip().lower()
    os.environ["RELEASE_QUALITY_ENFORCEMENT"] = "advisory" if normalized_mode.startswith("draft") else "strict"


def main() -> None:
    args = parse_args()
    release_version = args.release_version or f"{args.source_dataset_version}-draft"

    if args.step == "run-release":
        snapshot_manifest = snapshot_sources(
            release_version=release_version,
            source_dataset_version=args.source_dataset_version,
            project=args.project,
            workspace_root=args.workspace_root,
            source_snapshot_id=args.source_snapshot_id,
            approval_limit=args.approval_limit,
            audit_limit=args.audit_limit,
        )
        normalized_manifest = normalize_release(
            snapshot_manifest_ref=json.dumps(snapshot_manifest),
            workspace_root=args.workspace_root,
            public_record_target=args.public_record_target,
        )
        _configure_validation_mode(args.release_mode)
        validation_manifest = validate_release(normalized_manifest_ref=json.dumps(normalized_manifest))
        payload = publish_release(
            validation_manifest_ref=json.dumps(validation_manifest),
            workspace_root=args.workspace_root,
            release_mode=args.release_mode,
            previous_release_version=args.previous_release_version,
        )
        _write_output(args.output, payload, f"{release_version}-release-manifest.json")
        return

    if args.step == "snapshot-sources":
        payload = snapshot_sources(
            release_version=release_version,
            source_dataset_version=args.source_dataset_version,
            project=args.project,
            workspace_root=args.workspace_root,
            source_snapshot_id=args.source_snapshot_id,
            approval_limit=args.approval_limit,
            audit_limit=args.audit_limit,
        )
        _write_output(args.output, payload, f"{release_version}-snapshot-manifest.json")
        return

    if args.step == "normalize-release":
        if not args.snapshot_manifest:
            raise ValueError("--snapshot-manifest is required for normalize-release")
        payload = normalize_release(
            snapshot_manifest_ref=args.snapshot_manifest,
            workspace_root=args.workspace_root,
            public_record_target=args.public_record_target,
        )
        _write_output(args.output, payload, f"{release_version}-normalized-manifest.json")
        return

    if args.step == "validate-release":
        if not args.normalized_manifest:
            raise ValueError("--normalized-manifest is required for validate-release")
        _configure_validation_mode(args.release_mode)
        payload = validate_release(normalized_manifest_ref=args.normalized_manifest)
        _write_output(args.output, payload, f"{release_version}-validation-manifest.json")
        return

    if args.step == "publish-release":
        if not args.validation_manifest:
            raise ValueError("--validation-manifest is required for publish-release")
        payload = publish_release(
            validation_manifest_ref=args.validation_manifest,
            workspace_root=args.workspace_root,
            release_mode=args.release_mode,
            previous_release_version=args.previous_release_version,
        )
        _write_output(args.output, payload, f"{release_version}-release-manifest.json")
        return

    raise ValueError(f"Unsupported step {args.step}")


if __name__ == "__main__":
    main()
