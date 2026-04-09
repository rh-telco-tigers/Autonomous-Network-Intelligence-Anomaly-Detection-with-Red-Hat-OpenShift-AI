#!/usr/bin/env python3
"""Compare the current and feature-store-backed serving endpoints with sample feature vectors."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import requests


NUMERIC_FEATURES = [
    "register_rate",
    "invite_rate",
    "bye_rate",
    "error_4xx_ratio",
    "error_5xx_ratio",
    "latency_p95",
    "retransmission_count",
    "inter_arrival_mean",
    "payload_variance",
]
DEFAULT_CURRENT_ENDPOINT = "http://ims-predictive-predictor.ims-datascience.svc.cluster.local:8080"
DEFAULT_CURRENT_MODEL_NAME = "ims-predictive"
DEFAULT_FEATURESTORE_ENDPOINT = "http://ims-predictive-fs-predictor.ims-datascience.svc.cluster.local:8080"
DEFAULT_FEATURESTORE_MODEL_NAME = "ims-predictive-fs"
SAMPLES = [
    {
        "name": "normal",
        "features": {
            "register_rate": 0.13,
            "invite_rate": 0.03,
            "bye_rate": 0.03,
            "error_4xx_ratio": 0.0,
            "error_5xx_ratio": 0.0,
            "latency_p95": 35.0,
            "retransmission_count": 0.0,
            "inter_arrival_mean": 7.5,
            "payload_variance": 40.0,
        },
    },
    {
        "name": "registration_storm",
        "features": {
            "register_rate": 6.0,
            "invite_rate": 0.2,
            "bye_rate": 0.1,
            "error_4xx_ratio": 0.05,
            "error_5xx_ratio": 0.02,
            "latency_p95": 180.0,
            "retransmission_count": 12.0,
            "inter_arrival_mean": 0.8,
            "payload_variance": 60.0,
        },
    },
    {
        "name": "malformed_invite",
        "features": {
            "register_rate": 0.2,
            "invite_rate": 1.6,
            "bye_rate": 0.1,
            "error_4xx_ratio": 0.62,
            "error_5xx_ratio": 0.0,
            "latency_p95": 440.0,
            "retransmission_count": 8.0,
            "inter_arrival_mean": 0.9,
            "payload_variance": 120.0,
        },
    },
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current-endpoint", default=DEFAULT_CURRENT_ENDPOINT)
    parser.add_argument("--current-model-name", default=DEFAULT_CURRENT_MODEL_NAME)
    parser.add_argument("--featurestore-endpoint", default=DEFAULT_FEATURESTORE_ENDPOINT)
    parser.add_argument("--featurestore-model-name", default=DEFAULT_FEATURESTORE_MODEL_NAME)
    parser.add_argument("--max-score-delta", type=float, default=-1.0)
    return parser.parse_args()


def _health(endpoint: str) -> dict[str, Any]:
    target = endpoint.rstrip("/") + "/v2/health/ready"
    try:
        response = requests.get(target, timeout=15)
        return {
            "endpoint": target,
            "ok": response.ok,
            "status_code": response.status_code,
            "body": response.text.strip(),
        }
    except requests.RequestException as exc:
        return {
            "endpoint": target,
            "ok": False,
            "status_code": None,
            "body": str(exc),
        }


def _infer(endpoint: str, model_name: str, features: dict[str, float]) -> float:
    response = requests.post(
        endpoint.rstrip("/") + f"/v2/models/{model_name}/infer",
        json={
            "inputs": [
                {
                    "name": "predict",
                    "shape": [1, len(NUMERIC_FEATURES)],
                    "datatype": "FP32",
                    "data": [[float(features.get(feature, 0.0)) for feature in NUMERIC_FEATURES]],
                }
            ]
        },
        timeout=30,
    )
    response.raise_for_status()
    outputs = response.json().get("outputs", [])
    if not outputs:
        raise ValueError("Predictive response is missing outputs")
    output = outputs[0]
    value: Any = output.get("data", [0.0])
    output_name = str(output.get("name", "")).lower()
    while isinstance(value, list):
        if not value:
            raise ValueError("Predictive response contains an empty output payload")
        if output_name == "predict_proba" and len(value) >= 2 and all(not isinstance(item, list) for item in value):
            return float(value[1])
        value = value[0]
    datatype = str(output.get("datatype", "")).upper()
    scalar = float(value)
    if datatype.startswith("INT") or datatype.startswith("UINT"):
        return 1.0 if scalar >= 1.0 else 0.0
    return scalar


def main() -> None:
    args = _parse_args()
    summary: dict[str, Any] = {
        "current": _health(args.current_endpoint),
        "featurestore": _health(args.featurestore_endpoint),
        "samples": [],
    }
    failures: list[str] = []
    for name, health in (("current", summary["current"]), ("featurestore", summary["featurestore"])):
        if not health["ok"]:
            failures.append(f"{name} health check failed: {health['body']}")

    if not failures:
        for sample in SAMPLES:
            current_score = _infer(args.current_endpoint, args.current_model_name, sample["features"])
            featurestore_score = _infer(args.featurestore_endpoint, args.featurestore_model_name, sample["features"])
            delta = abs(current_score - featurestore_score)
            record = {
                "sample": sample["name"],
                "current_score": round(current_score, 6),
                "featurestore_score": round(featurestore_score, 6),
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
