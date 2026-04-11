#!/usr/bin/env python3
"""Smoke-check the feature-store predictive endpoint served through MLServer."""

from __future__ import annotations

import argparse
import json

from serving_smoke_check import SAMPLES, _health, _infer

DEFAULT_TRITON_ENDPOINT = "http://ani-predictive-fs-predictor.ani-datascience.svc.cluster.local:8080"
DEFAULT_TRITON_MODEL_NAME = "ani-predictive-fs"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default=DEFAULT_TRITON_ENDPOINT)
    parser.add_argument("--model-name", default=DEFAULT_TRITON_MODEL_NAME)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    summary = {
        "endpoint": _health(args.endpoint),
        "samples": [],
    }
    failures: list[str] = []

    if not summary["endpoint"]["ok"]:
        failures.append(f"feature-store health check failed: {summary['endpoint']['body']}")

    if not failures:
        for sample in SAMPLES:
            score = _infer(args.endpoint, args.model_name, sample["features"])
            record = {
                "sample": sample["name"],
                "score": round(score, 6),
            }
            summary["samples"].append(record)

    summary["status"] = "passed" if not failures else "failed"
    summary["failures"] = failures
    print(json.dumps(summary, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
