#!/usr/bin/env python3
"""List active and stored incident-release dataset versions for the current cluster."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.client import Config


DEFAULT_SIPP_NAMESPACE = "ani-sipp"
DEFAULT_DATA_NAMESPACE = "ani-data"
DEFAULT_MINIO_SERVICE = "model-storage-minio"
DEFAULT_DATASET_STORE_PREFIX = "pipelines/ani-datascience/datasets"
DEFAULT_BUCKET = "ani-models"
DEFAULT_ACTIVE_LABEL = "app.kubernetes.io/part-of=sipp-backfill-100k"
DEFAULT_BACKFILL_PREFIX = "backfill-sipp-100k"


def _run_oc_json(args: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        ["oc", *args, "-o", "json"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _extract_secret_value(namespace: str, key: str) -> str:
    completed = subprocess.run(
        ["oc", "extract", f"secret/model-storage-credentials", "-n", namespace, "--to=-", f"--keys={key}"],
        check=True,
        capture_output=True,
        text=True,
    )
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        raise RuntimeError(f"Secret key {key} not found in model-storage-credentials/{namespace}")
    return lines[0]


def _extract_secret_value_or_default(namespace: str, key: str, default: str) -> str:
    try:
        return _extract_secret_value(namespace, key)
    except RuntimeError:
        return default


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(host: str, port: int, timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"Timed out waiting for {host}:{port}")


@contextmanager
def _minio_endpoint(endpoint: str, data_namespace: str, service_name: str):
    if ".svc.cluster.local" not in endpoint:
        yield endpoint
        return

    local_port = _pick_free_port()
    process = subprocess.Popen(
        [
            "oc",
            "port-forward",
            "-n",
            data_namespace,
            f"svc/{service_name}",
            f"{local_port}:9000",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=os.environ.copy(),
    )
    try:
        _wait_for_port("127.0.0.1", local_port)
        yield f"http://127.0.0.1:{local_port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()


@dataclass
class JobSummary:
    version: str
    jobs: int = 0
    active: int = 0
    succeeded: int = 0
    failed: int = 0
    latest_created: str = ""


def _collect_active_versions(namespace: str) -> list[JobSummary]:
    payload = _run_oc_json(["get", "jobs", "-n", namespace, "-l", DEFAULT_ACTIVE_LABEL])
    grouped: dict[str, JobSummary] = {}
    for item in payload.get("items", []):
        metadata = item.get("metadata", {})
        labels = metadata.get("labels", {})
        version = str(labels.get("ani.redhat.com/backfill-dataset-version") or "").strip()
        if not version:
            continue
        summary = grouped.setdefault(version, JobSummary(version=version))
        status = item.get("status", {})
        summary.jobs += 1
        summary.active += int(status.get("active") or 0)
        summary.succeeded += int(status.get("succeeded") or 0)
        summary.failed += int(status.get("failed") or 0)
        created = str(metadata.get("creationTimestamp") or "")
        if created and created > summary.latest_created:
            summary.latest_created = created
    return sorted(grouped.values(), key=lambda item: (item.latest_created, item.version), reverse=True)


def _collect_stored_versions(namespace: str, data_namespace: str, service_name: str, prefix: str) -> list[str]:
    endpoint = _extract_secret_value(namespace, "AWS_S3_ENDPOINT")
    access_key = _extract_secret_value(namespace, "AWS_ACCESS_KEY_ID")
    secret_key = _extract_secret_value(namespace, "AWS_SECRET_ACCESS_KEY")
    bucket = _extract_secret_value_or_default(namespace, "AWS_S3_BUCKET", DEFAULT_BUCKET)
    with _minio_endpoint(endpoint, data_namespace, service_name) as resolved_endpoint:
        client = boto3.client(
            "s3",
            endpoint_url=resolved_endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="us-east-1",
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        dataset_root = f"{prefix.strip('/')}/datasets/"
        paginator = client.get_paginator("list_objects_v2")
        versions: list[str] = []
        for page in paginator.paginate(Bucket=bucket or DEFAULT_BUCKET, Prefix=dataset_root, Delimiter="/"):
            for common_prefix in page.get("CommonPrefixes", []):
                key = str(common_prefix.get("Prefix") or "").rstrip("/")
                version = key.removeprefix(dataset_root).strip("/")
                if version:
                    versions.append(version)
    return sorted(set(versions), reverse=True)


def _print_active(summaries: list[JobSummary]) -> None:
    print("Active backfill datasets")
    if not summaries:
        print("  none")
        return
    for item in summaries:
        print(
            f"  {item.version}  jobs={item.jobs} active={item.active} "
            f"succeeded={item.succeeded} failed={item.failed} latest={item.latest_created or '-'}"
        )


def _print_stored(versions: list[str]) -> None:
    print("Stored dataset versions")
    if not versions:
        print("  none")
        return
    for version in versions:
        print(f"  {version}")


def _pick_latest(active: list[JobSummary], stored: list[str], prefer_prefix: str) -> str:
    if prefer_prefix:
        for item in active:
            if item.version.startswith(prefer_prefix):
                return item.version
        for version in stored:
            if version.startswith(prefer_prefix):
                return version
    if active:
        return active[0].version
    if stored:
        return stored[0]
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="List active and stored incident-release dataset versions.")
    parser.add_argument("--sipp-namespace", default=DEFAULT_SIPP_NAMESPACE)
    parser.add_argument("--data-namespace", default=DEFAULT_DATA_NAMESPACE)
    parser.add_argument("--minio-service", default=DEFAULT_MINIO_SERVICE)
    parser.add_argument("--dataset-store-prefix", default=DEFAULT_DATASET_STORE_PREFIX)
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--prefer-prefix", default=DEFAULT_BACKFILL_PREFIX)
    args = parser.parse_args()

    active = _collect_active_versions(args.sipp_namespace)
    stored = _collect_stored_versions(args.sipp_namespace, args.data_namespace, args.minio_service, args.dataset_store_prefix)
    chosen = _pick_latest(active, stored, args.prefer_prefix)
    if args.latest:
        if chosen:
            print(chosen)
            return
        raise SystemExit(1)
    _print_active(active)
    print()
    _print_stored(stored)
    print()
    if chosen:
        print("Example:")
        print("  Backfill datasets are training-only and are not used by Step 3.")
        print(f"  make stop-incident-release INCIDENT_RELEASE_DATASET_VERSION={chosen}")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(exc.stderr or str(exc))
        raise
