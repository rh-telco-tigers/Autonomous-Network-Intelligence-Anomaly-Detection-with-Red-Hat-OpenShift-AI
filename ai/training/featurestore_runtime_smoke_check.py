#!/usr/bin/env python3
"""Compare feature-store Triton and MLServer endpoints with shared samples."""

from __future__ import annotations

import argparse
import json

from serving_smoke_check import SAMPLES, _health, _infer

DEFAULT_TRITON_ENDPOINT = "http://ims-predictive-fs-predictor.ims-demo-lab.svc.cluster.local:8080"
DEFAULT_TRITON_MODEL_NAME = "ims-predictive-fs"
DEFAULT_MLSERVER_ENDPOINT = "http://ims-predictive-fs-mlserver-predictor.ims-demo-lab.svc.cluster.local:8080"
DEFAULT_MLSERVER_MODEL_NAME = "ims-predictive-fs-mlserver"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--triton-endpoint", default=DEFAULT_TRITON_ENDPOINT)
    parser.add_argument("--triton-model-name", default=DEFAULT_TRITON_MODEL_NAME)
    parser.add_argument("--mlserver-endpoint", default=DEFAULT_MLSERVER_ENDPOINT)
    parser.add_argument("--mlserver-model-name", default=DEFAULT_MLSERVER_MODEL_NAME)
    parser.add_argument("--max-score-delta", type=float, default=-1.0)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    summary = {
        "triton": _health(args.triton_endpoint),
        "mlserver": _health(args.mlserver_endpoint),
        "samples": [],
    }
    failures: list[str] = []

    for name, health in (("triton", summary["triton"]), ("mlserver", summary["mlserver"])):
        if not health["ok"]:
            failures.append(f"{name} health check failed: {health['body']}")

    if not failures:
        for sample in SAMPLES:
            triton_score = _infer(args.triton_endpoint, args.triton_model_name, sample["features"])
            mlserver_score = _infer(args.mlserver_endpoint, args.mlserver_model_name, sample["features"])
            delta = abs(triton_score - mlserver_score)
            record = {
                "sample": sample["name"],
                "triton_score": round(triton_score, 6),
                "mlserver_score": round(mlserver_score, 6),
                "score_delta": round(delta, 6),
            }
            summary["samples"].append(record)
            if args.max_score_delta >= 0.0 and delta > args.max_score_delta:
                failures.append(
                    f"Sample {sample['name']} exceeded max score delta {args.max_score_delta}: {delta:.6f}"
                )

    summary["status"] = "passed" if not failures else "failed"
    summary["failures"] = failures
    print(json.dumps(summary, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
