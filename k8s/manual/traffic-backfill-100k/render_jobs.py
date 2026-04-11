#!/usr/bin/env python3
"""Render the manual SIPp backfill Jobs for a chosen dataset version."""

from __future__ import annotations

import argparse
import re
import sys


DATASET_PLACEHOLDER = "backfill-sipp-100k-v1"
DATASET_LABEL_KEY = "ani.redhat.com/backfill-dataset-version"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rewrite the manual backfill Jobs so each trigger creates fresh Jobs for one dataset version."
    )
    parser.add_argument("--dataset-version", required=True)
    return parser.parse_args()


def _validate_dataset_version(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,61}[A-Za-z0-9])?", value):
        raise SystemExit(
            "--dataset-version must be 1-63 characters and match Kubernetes label value rules "
            "(letters, digits, '.', '_' or '-', starting and ending with a letter or digit)."
        )
    return value


def _inject_dataset_label(rendered: str, dataset_version: str) -> str:
    rendered = re.sub(
        r"(?m)^    app\.kubernetes\.io/part-of: sipp-backfill-100k$",
        "    app.kubernetes.io/part-of: sipp-backfill-100k\n"
        f"    {DATASET_LABEL_KEY}: {dataset_version}",
        rendered,
    )
    return re.sub(
        r"(?m)^        app\.kubernetes\.io/part-of: sipp-backfill-100k$",
        "        app.kubernetes.io/part-of: sipp-backfill-100k\n"
        f"        {DATASET_LABEL_KEY}: {dataset_version}",
        rendered,
    )


def _rewrite_generate_name(rendered: str) -> str:
    name_pattern = re.compile(r"(?m)^  name: (sipp-backfill-[a-z0-9-]+)$")
    return name_pattern.sub(r"  generateName: \1-", rendered)


def main() -> None:
    args = _parse_args()
    dataset_version = _validate_dataset_version(args.dataset_version)
    rendered = sys.stdin.read()
    if not rendered.strip():
        raise SystemExit("No manifest data received on stdin.")
    if DATASET_PLACEHOLDER not in rendered:
        raise SystemExit(f"Expected dataset placeholder {DATASET_PLACEHOLDER!r} was not found.")

    rendered = rendered.replace(DATASET_PLACEHOLDER, dataset_version)
    rendered = _inject_dataset_label(rendered, dataset_version)
    rendered = _rewrite_generate_name(rendered)
    sys.stdout.write(rendered)


if __name__ == "__main__":
    main()
