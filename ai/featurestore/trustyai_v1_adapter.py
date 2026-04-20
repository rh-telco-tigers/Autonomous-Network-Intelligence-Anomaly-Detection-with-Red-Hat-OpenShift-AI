from __future__ import annotations

import os
from typing import Any, Iterable, List, Mapping, Sequence

import requests
from fastapi import FastAPI, HTTPException

from ai.training.train_and_register import FEATURES


app = FastAPI(title="trustyai-v1-adapter", version="0.1.0")


def _upstream_url() -> str:
    return str(os.getenv("TRUSTYAI_ADAPTER_UPSTREAM_URL", "http://127.0.0.1:8080")).strip().rstrip("/")


def _timeout_seconds() -> float:
    raw = str(os.getenv("TRUSTYAI_ADAPTER_TIMEOUT_SECONDS", "15")).strip()
    try:
        return max(1.0, min(float(raw), 30.0))
    except ValueError:
        return 15.0


def _coerce_row(row: object) -> List[float]:
    if isinstance(row, Mapping):
        return [float(row.get(feature, 0.0) or 0.0) for feature in FEATURES]
    if isinstance(row, Iterable) and not isinstance(row, (str, bytes, bytearray)):
        return [float(value or 0.0) for value in row]
    raise ValueError("Each instance must be an object or a numeric sequence")


def _instances_from_payload(payload: Mapping[str, Any]) -> List[List[float]]:
    raw_instances = payload.get("instances")
    if raw_instances is None:
        raw_instances = payload.get("inputs")
    if not isinstance(raw_instances, list) or not raw_instances:
        raise ValueError("Request body must include a non-empty 'instances' list")
    return [_coerce_row(row) for row in raw_instances]


def _v2_infer_payload(instances: List[List[float]]) -> Mapping[str, Any]:
    width = max((len(row) for row in instances), default=len(FEATURES))
    normalized = [row + ([0.0] * max(0, width - len(row))) for row in instances]
    return {
        "inputs": [
            {
                "name": "predict",
                "shape": [len(normalized), width],
                "datatype": "FP32",
                "data": normalized,
            }
        ]
    }


def _normalize_rows(data: Sequence[Any]) -> List[List[float]]:
    if not isinstance(data, Sequence) or not data:
        raise ValueError("Upstream prediction data was empty")
    if data and not isinstance(data[0], list):
        return [[float(value or 0.0) for value in data]]
    rows: List[List[float]] = []
    for row in data:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes, bytearray)):
            raise ValueError("Upstream prediction rows were malformed")
        rows.append([float(value or 0.0) for value in row])
    return rows


def _scalar_predictions_from_v2_response(payload: Mapping[str, Any]) -> List[Any]:
    outputs = payload.get("outputs")
    if not isinstance(outputs, list):
        raise ValueError("Upstream v2 response did not include outputs")

    outputs_by_name = {
        str(output.get("name") or "").lower(): output
        for output in outputs
        if isinstance(output, Mapping)
    }
    anomaly_score = outputs_by_name.get("anomaly_score")
    if isinstance(anomaly_score, Mapping):
        data = anomaly_score.get("data")
        if isinstance(data, list) and data:
            rows = _normalize_rows(data)
            if rows and len(rows[0]) == 1:
                return [float(row[0]) for row in rows]

    probabilities = outputs_by_name.get("class_probabilities") or outputs_by_name.get("predict_proba")
    if not isinstance(probabilities, Mapping):
        raise ValueError("Upstream v2 response did not include class probabilities")

    data = probabilities.get("data")
    if not isinstance(data, list) or not data:
        raise ValueError("Upstream class probabilities were empty")
    rows = _normalize_rows(data)
    return [max(range(len(row)), key=row.__getitem__) for row in rows]


@app.get("/healthz")
def healthz() -> Mapping[str, str]:
    return {"status": "ok"}


@app.post("/v1/models/{model_name}:predict")
def predict_v1(model_name: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    try:
        instances = _instances_from_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    request_payload = _v2_infer_payload(instances)
    endpoint = f"{_upstream_url()}/v2/models/{model_name}/infer"
    try:
        response = requests.post(endpoint, json=request_payload, timeout=_timeout_seconds())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Upstream predictive request failed: {exc}") from exc

    body = response.text.strip()
    try:
        response_payload = response.json() if body else {}
    except ValueError:
        response_payload = {"detail": body} if body else {}

    if not response.ok:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Upstream predictive service returned an error",
                "status_code": response.status_code,
                "body": response_payload,
            },
        )

    try:
        predictions = _scalar_predictions_from_v2_response(
            response_payload if isinstance(response_payload, Mapping) else {}
        )
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "model_name": model_name,
        "predictions": predictions,
    }
