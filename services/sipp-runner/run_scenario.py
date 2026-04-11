import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import boto3
import requests
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

# Support both repo execution (`services/sipp-runner/run_scenario.py`) and
# container execution (`/sipp/run_scenario.py` with `/sipp/shared` copied in).
SCRIPT_ROOT = Path(__file__).resolve().parent
for import_root in [SCRIPT_ROOT, SCRIPT_ROOT.parent]:
    import_root_str = str(import_root)
    if import_root.exists() and import_root_str not in sys.path:
        sys.path.insert(0, import_root_str)

from shared.cluster_env import anomaly_service_url, control_plane_url, dataset_store_bucket, dataset_store_endpoint, dataset_store_prefix
from shared.incident_taxonomy import NORMAL_ANOMALY_TYPE, canonical_anomaly_type, normalize_scenario_name, scenario_definition


FEATURE_SCHEMA_VERSION = "feature_schema_v1"
DEFAULT_DATASET_VERSION = "live-sipp-v1"
DEFAULT_DATASET_STORE_ENDPOINT = dataset_store_endpoint()
DEFAULT_DATASET_STORE_BUCKET = dataset_store_bucket()
DEFAULT_DATASET_STORE_PREFIX = dataset_store_prefix()
DEFAULT_CONTROL_PLANE_URL = control_plane_url()
DEFAULT_ANOMALY_SERVICE_URL = anomaly_service_url()
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
SIPP_TRANSPORT_MAP = {
    "udp": "u1",
    "tcp": "t1",
}
SHORTMSG_DIRECTION_INDEX = 3
SHORTMSG_CALL_ID_INDEX = 4
SHORTMSG_CSEQ_INDEX = 5
SHORTMSG_SUMMARY_INDEX = 6
PAYLOAD_RE = re.compile(r"UDP message (?:sent \((\d+) bytes\)|received \[(\d+)\] bytes)")


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _dataset_store_endpoint() -> str:
    return os.getenv("DATASET_STORE_ENDPOINT", os.getenv("MINIO_ENDPOINT", DEFAULT_DATASET_STORE_ENDPOINT))


def _dataset_store_bucket() -> str:
    return os.getenv("DATASET_STORE_BUCKET", os.getenv("MINIO_BUCKET", DEFAULT_DATASET_STORE_BUCKET))


def _dataset_store_prefix() -> str:
    return os.getenv("DATASET_STORE_PREFIX", DEFAULT_DATASET_STORE_PREFIX).strip("/")


def _dataset_store_access_key() -> str:
    return os.getenv("DATASET_STORE_ACCESS_KEY", os.getenv("MINIO_ACCESS_KEY", "minioadmin"))


def _dataset_store_secret_key() -> str:
    return os.getenv("DATASET_STORE_SECRET_KEY", os.getenv("MINIO_SECRET_KEY", "minioadmin"))


def _dataset_object_key(relative_path: str) -> str:
    normalized_relative = relative_path.lstrip("/")
    prefix = _dataset_store_prefix()
    return f"{prefix}/{normalized_relative}" if prefix else normalized_relative


def _s3_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"


def _dataset_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=_dataset_store_endpoint(),
        aws_access_key_id=_dataset_store_access_key(),
        aws_secret_access_key=_dataset_store_secret_key(),
        region_name="us-east-1",
        config=Config(
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 5, "mode": "standard"},
        ),
    )


def _s3_retry_attempts() -> int:
    return max(int(os.getenv("DATASET_STORE_RETRY_ATTEMPTS", "5")), 1)


def _run_s3_operation(operation):
    last_error: Exception | None = None
    for attempt in range(1, _s3_retry_attempts() + 1):
        try:
            return operation()
        except (BotoCoreError, ClientError) as exc:
            last_error = exc
            if attempt == _s3_retry_attempts():
                break
            time.sleep(min(float(attempt), 5.0))
    if last_error is not None:
        raise last_error


def _ensure_dataset_bucket() -> None:
    client = _dataset_s3_client()
    bucket = _dataset_store_bucket()
    try:
        _run_s3_operation(lambda: client.head_bucket(Bucket=bucket))
    except ClientError as exc:
        error_code = str((exc.response.get("Error") or {}).get("Code") or "")
        if error_code not in {"404", "NoSuchBucket", "NotFound"}:
            raise
        _run_s3_operation(lambda: client.create_bucket(Bucket=bucket))


def _env_flag(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _control_plane_headers() -> dict[str, str]:
    api_key = os.getenv("CONTROL_PLANE_API_KEY", os.getenv("API_KEY", "")).strip()
    return {"x-api-key": api_key} if api_key else {}


def _control_plane_url(path: str) -> str:
    base_url = os.getenv("CONTROL_PLANE_URL", DEFAULT_CONTROL_PLANE_URL).rstrip("/")
    return f"{base_url}/{path.lstrip('/')}"


def _anomaly_service_headers() -> dict[str, str]:
    api_key = os.getenv("ANOMALY_SERVICE_API_KEY", os.getenv("CONTROL_PLANE_API_KEY", os.getenv("API_KEY", ""))).strip()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def _anomaly_service_url(path: str) -> str:
    base_url = os.getenv("ANOMALY_SERVICE_URL", DEFAULT_ANOMALY_SERVICE_URL).rstrip("/")
    return f"{base_url}/{path.lstrip('/')}"


def _score_feature_window(window: dict[str, Any]) -> dict[str, Any] | None:
    if not _env_flag("SIPP_EMIT_CONTROL_PLANE_INCIDENT", False):
        return None

    sipp_summary = dict(window.get("sipp_summary") or {})
    features = {
        **dict(window.get("features") or {}),
        "scenario_name": str(window.get("scenario_name") or ""),
        "feature_source": str(window.get("feature_source") or ""),
        "source": str(window.get("source") or ""),
        "transport": str(window.get("transport") or sipp_summary.get("transport") or ""),
        "call_limit": window.get("call_limit") or sipp_summary.get("call_limit"),
        "rate": window.get("rate") or sipp_summary.get("rate"),
        "target": window.get("target") or sipp_summary.get("target"),
        "scenario_file": window.get("scenario_file") or sipp_summary.get("scenario_file"),
        "contributing_conditions": list(window.get("contributing_conditions") or []),
        "response_codes": list(sipp_summary.get("response_codes") or []),
    }
    timeout_seconds = max(float(os.getenv("ANOMALY_SERVICE_TIMEOUT_SECONDS", "15")), 1.0)
    response = requests.post(
        _anomaly_service_url("/score"),
        json={
            "features": features,
            "project": os.getenv("CONTROL_PLANE_PROJECT", "ani-demo").strip() or "ani-demo",
            "feature_window_id": str(window.get("window_id") or ""),
            "scenario_name": str(window.get("scenario_name") or ""),
            "anomaly_type_hint": str(window.get("anomaly_type") or NORMAL_ANOMALY_TYPE),
        },
        headers=_anomaly_service_headers(),
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if str(payload.get("incident_id") or "").strip():
        return payload
    return None


def _emit_control_plane_incident(window: dict[str, Any]) -> dict[str, Any] | None:
    if not _env_flag("SIPP_EMIT_CONTROL_PLANE_INCIDENT", False):
        return None
    if int(window.get("label", 0)) == 0 and not _env_flag("SIPP_EMIT_NORMAL_INCIDENT", False):
        return None

    predicted_confidence = float(window.get("label_confidence") or 0.95)
    anomaly_type = str(window.get("anomaly_type") or NORMAL_ANOMALY_TYPE)
    if anomaly_type == NORMAL_ANOMALY_TYPE:
        class_probabilities = {NORMAL_ANOMALY_TYPE: 1.0}
    else:
        class_probabilities = {
            NORMAL_ANOMALY_TYPE: round(max(0.0, 1.0 - predicted_confidence), 6),
            anomaly_type: round(predicted_confidence, 6),
        }
    payload = {
        "incident_id": str(uuid.uuid4()),
        "project": os.getenv("CONTROL_PLANE_PROJECT", "ani-demo").strip() or "ani-demo",
        "anomaly_score": float(os.getenv("CONTROL_PLANE_INCIDENT_SCORE", "0.99" if int(window.get("label", 0)) else "0.05")),
        "anomaly_type": anomaly_type,
        "predicted_confidence": predicted_confidence,
        "class_probabilities": class_probabilities,
        "top_classes": [
            {"anomaly_type": label, "probability": probability}
            for label, probability in sorted(class_probabilities.items(), key=lambda item: (-item[1], item[0]))
        ][:3],
        "is_anomaly": anomaly_type != NORMAL_ANOMALY_TYPE,
        "model_version": os.getenv("CONTROL_PLANE_INCIDENT_MODEL_VERSION", "sipp-scenario-labeler-v1").strip()
        or "sipp-scenario-labeler-v1",
        "feature_window_id": str(window.get("window_id") or ""),
        "feature_snapshot": {
            **dict(window.get("features") or {}),
            "contributing_conditions": list(window.get("contributing_conditions") or []),
        },
        "created_at": str(window.get("captured_at") or _now()),
        "status": os.getenv("CONTROL_PLANE_INCIDENT_STATUS", "resolved").strip() or "resolved",
    }
    timeout_seconds = max(float(os.getenv("CONTROL_PLANE_TIMEOUT_SECONDS", "15")), 1.0)
    response = requests.post(
        _control_plane_url("/incidents"),
        json=payload,
        headers=_control_plane_headers(),
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    return response.json()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    sorted_values = sorted(values)
    rank = (len(sorted_values) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = rank - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _scenario_anomaly_type(scenario_name: str) -> str:
    normalized_name = normalize_scenario_name(scenario_name)
    if not str(scenario_name or "").strip():
        return "unknown"
    return canonical_anomaly_type(normalized_name)


def _normalize_condition_name(value: object) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    return text.strip("_")


def _append_condition(conditions: list[str], value: object) -> None:
    normalized = _normalize_condition_name(value)
    if normalized and normalized not in conditions:
        conditions.append(normalized)


def _is_normal_anomaly_type(anomaly_type: str) -> bool:
    return anomaly_type == NORMAL_ANOMALY_TYPE


def _derive_contributing_conditions(
    *,
    anomaly_type: str,
    features: dict[str, float],
    response_codes: list[int],
    auth_challenge_count: int,
    retransmissions: float,
) -> list[str]:
    conditions: list[str] = []
    for value in scenario_definition(anomaly_type).get("base_conditions", []):
        _append_condition(conditions, value)

    if _is_normal_anomaly_type(anomaly_type):
        return conditions

    error_4xx_ratio = float(features.get("error_4xx_ratio", 0.0) or 0.0)
    error_5xx_ratio = float(features.get("error_5xx_ratio", 0.0) or 0.0)
    latency_p95 = float(features.get("latency_p95", 0.0) or 0.0)
    payload_variance = float(features.get("payload_variance", 0.0) or 0.0)
    register_rate = float(features.get("register_rate", 0.0) or 0.0)

    if error_4xx_ratio >= 0.35:
        _append_condition(conditions, "4xx_burst")
    if error_5xx_ratio >= 0.35:
        _append_condition(conditions, "5xx_burst")
    if latency_p95 >= 250.0:
        _append_condition(conditions, "latency_high")
    if retransmissions >= 1.0:
        _append_condition(conditions, "retry_spike")
    if auth_challenge_count > 0 or 401 in response_codes:
        _append_condition(conditions, "auth_challenge_loop")
    if payload_variance >= 50.0:
        _append_condition(conditions, "payload_anomaly")
    if register_rate >= 5.0:
        _append_condition(conditions, "traffic_surge")
    if 404 in response_codes or 483 in response_codes:
        _append_condition(conditions, "route_unreachable")
    if 408 in response_codes or 480 in response_codes:
        _append_condition(conditions, "session_setup_delay")
    if 486 in response_codes:
        _append_condition(conditions, "destination_busy")
    if any(code >= 500 for code in response_codes):
        _append_condition(conditions, "dependency_instability")

    return conditions


def _cseq_method(cseq: str) -> str:
    parts = cseq.split()
    return parts[-1].upper() if parts else ""


def _parse_stats_csv(trace_dir: Path) -> dict[str, str]:
    stats_files = sorted(trace_dir.glob("*.csv"))
    if not stats_files:
        return {}
    with stats_files[0].open(newline="") as handle:
        reader = csv.reader(handle, delimiter=";")
        rows = list(reader)
    if len(rows) < 2:
        return {}
    header = rows[0]
    last_row = rows[-1]
    width = min(len(header), len(last_row))
    return {header[index]: last_row[index] for index in range(width)}


def _parse_payload_sizes(trace_dir: Path) -> list[int]:
    message_files = sorted(trace_dir.glob("*_messages.log"))
    sizes: list[int] = []
    for path in message_files:
        for line in path.read_text().splitlines():
            match = PAYLOAD_RE.search(line)
            if match:
                size = match.group(1) or match.group(2)
                sizes.append(int(size))
    return sizes


def _parse_shortmessages(trace_dir: Path) -> dict[str, Any]:
    short_files = sorted(trace_dir.glob("*_shortmessages.log"))
    if not short_files:
        raise FileNotFoundError("SIPp did not produce shortmessages log output")

    sent_timestamps: list[float] = []
    response_codes: list[int] = []
    transactions: dict[tuple[str, str], dict[str, Any]] = {}
    latencies_ms: list[float] = []
    method_counts = {"REGISTER": 0, "INVITE": 0, "BYE": 0}
    first_seen: float | None = None
    last_seen: float | None = None
    effective_response_codes: list[int] = []
    auth_challenge_count = 0

    for path in short_files:
        for raw_line in path.read_text().splitlines():
            parts = raw_line.split("\t")
            if len(parts) <= SHORTMSG_SUMMARY_INDEX:
                continue
            timestamp = float(parts[2])
            direction = parts[SHORTMSG_DIRECTION_INDEX]
            call_id = parts[SHORTMSG_CALL_ID_INDEX]
            cseq = parts[SHORTMSG_CSEQ_INDEX]
            summary = parts[SHORTMSG_SUMMARY_INDEX]
            first_seen = timestamp if first_seen is None else min(first_seen, timestamp)
            last_seen = timestamp if last_seen is None else max(last_seen, timestamp)

            if direction == "S":
                method = summary.split(" ", 1)[0].upper()
                if method in method_counts:
                    key = (call_id, cseq)
                    transaction = transactions.get(key)
                    if transaction is None:
                        method_counts[method] += 1
                        sent_timestamps.append(timestamp)
                        transactions[key] = {
                            "method": method,
                            "first_send": timestamp,
                            "last_send": timestamp,
                        }
                    else:
                        transaction["last_send"] = timestamp
            elif direction == "R" and summary.startswith("SIP/2.0 "):
                code = int(summary.split()[1])
                response_codes.append(code)
                transaction = transactions.get((call_id, cseq))
                method = _cseq_method(cseq)
                is_expected_auth_challenge = code == 401 and method == "REGISTER"
                if is_expected_auth_challenge:
                    auth_challenge_count += 1
                else:
                    effective_response_codes.append(code)
                if transaction:
                    latencies_ms.append(max(timestamp - float(transaction["first_send"]), 0.0) * 1000.0)

    duration_seconds = 0.0
    if first_seen is not None and last_seen is not None:
        duration_seconds = max(last_seen - first_seen, 1.0)

    inter_arrivals = [
        later - earlier for earlier, later in zip(sent_timestamps, sent_timestamps[1:])
    ]
    total_responses = max(len(effective_response_codes), 1)
    return {
        "duration_seconds": duration_seconds,
        "method_counts": method_counts,
        "response_codes": response_codes,
        "latencies_ms": latencies_ms,
        "inter_arrival_mean": mean(inter_arrivals) if inter_arrivals else duration_seconds / max(len(sent_timestamps), 1),
        "first_seen": first_seen,
        "last_seen": last_seen,
        "error_4xx_ratio": sum(1 for code in effective_response_codes if 400 <= code < 500) / total_responses,
        "error_5xx_ratio": sum(1 for code in effective_response_codes if code >= 500) / total_responses,
        "event_count": len(sent_timestamps) + len(response_codes),
        "auth_challenge_count": auth_challenge_count,
    }


def _build_feature_window(args: argparse.Namespace, trace_dir: Path, sipp_result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    parsed = _parse_shortmessages(trace_dir)
    payload_sizes = _parse_payload_sizes(trace_dir)
    stats = _parse_stats_csv(trace_dir)
    duration_seconds = max(parsed["duration_seconds"], 1.0)
    scenario_name = args.scenario_name
    anomaly_type = _scenario_anomaly_type(scenario_name)
    start_time = parsed["first_seen"] or datetime.now(tz=timezone.utc).timestamp()
    end_time = parsed["last_seen"] or start_time
    retransmissions = float(stats.get("Retransmissions(C)", "0") or 0.0)

    window_id = f"{args.dataset_version}-{scenario_name}-{int(start_time)}-{uuid.uuid4().hex[:8]}"
    payload_variance = float(max(payload_sizes) - min(payload_sizes)) if payload_sizes else 0.0
    features = {
        "register_rate": round(parsed["method_counts"].get("REGISTER", 0) / duration_seconds, 3),
        "invite_rate": round(parsed["method_counts"].get("INVITE", 0) / duration_seconds, 3),
        "bye_rate": round(parsed["method_counts"].get("BYE", 0) / duration_seconds, 3),
        "error_4xx_ratio": round(parsed["error_4xx_ratio"], 4),
        "error_5xx_ratio": round(parsed["error_5xx_ratio"], 4),
        "latency_p95": round(_percentile(parsed["latencies_ms"], 0.95), 2),
        "retransmission_count": round(retransmissions, 3),
        "inter_arrival_mean": round(parsed["inter_arrival_mean"], 4),
        "payload_variance": round(payload_variance, 3),
    }
    contributing_conditions = _derive_contributing_conditions(
        anomaly_type=anomaly_type,
        features=features,
        response_codes=list(parsed["response_codes"]),
        auth_challenge_count=int(parsed["auth_challenge_count"]),
        retransmissions=retransmissions,
    )
    is_normal = _is_normal_anomaly_type(anomaly_type)

    return {
        "window_id": window_id,
        "window_start": datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat(),
        "window_end": datetime.fromtimestamp(end_time, tz=timezone.utc).isoformat(),
        "source": "openani-sipp-lab",
        "feature_source": "sipp-shortmessages",
        "schema_version": FEATURE_SCHEMA_VERSION,
        "dataset_version": args.dataset_version,
        "scenario_name": scenario_name,
        "transport": args.transport,
        "call_limit": args.call_limit,
        "rate": args.rate,
        "target": f"{args.target_host}:{args.target_port}",
        "scenario_file": args.scenario_file,
        "label": 0 if is_normal else 1,
        "anomaly_type": anomaly_type,
        "label_confidence": 0.95,
        "contributing_conditions": contributing_conditions,
        "features": features,
        "labels": {
            "anomaly": not is_normal,
            "anomaly_type": None if is_normal else anomaly_type,
            "contributing_conditions": contributing_conditions,
        },
        "sipp_summary": {
            "target": f"{args.target_host}:{args.target_port}",
            "scenario_file": args.scenario_file,
            "call_limit": args.call_limit,
            "rate": args.rate,
            "event_count": parsed["event_count"],
            "response_codes": parsed["response_codes"],
            "auth_challenge_count": parsed["auth_challenge_count"],
            "return_code": sipp_result.returncode,
            "stdout_tail": "\n".join((sipp_result.stdout or "").splitlines()[-20:]),
            "stderr_tail": "\n".join((sipp_result.stderr or "").splitlines()[-20:]),
        },
        "captured_at": _now(),
    }


def _upload_window(window: dict[str, Any]) -> str:
    _ensure_dataset_bucket()
    bucket = _dataset_store_bucket()
    relative_path = (
        f"datasets/{window['dataset_version']}/feature-windows/"
        f"{window['scenario_name']}/{window['window_id']}.json"
    )
    key = _dataset_object_key(relative_path)
    _run_s3_operation(
        lambda: _dataset_s3_client().put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(window, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
    )
    return _s3_uri(bucket, key)


def _positive_repeat_count(value: int) -> int:
    count = int(value)
    if count < 1:
        raise ValueError("--repeat-count must be at least 1")
    return count


def _run_sipp(args: argparse.Namespace, trace_dir: Path) -> subprocess.CompletedProcess[str]:
    command = [
        "sipp",
        f"{args.target_host}:{args.target_port}",
        "-sf",
        args.scenario_file,
        "-t",
        SIPP_TRANSPORT_MAP[args.transport],
        "-m",
        str(args.call_limit),
        "-r",
        str(args.rate),
        "-trace_msg",
        "-trace_shortmsg",
        "-trace_stat",
        "-trace_err",
        "-fd",
        "1",
    ]
    return subprocess.run(
        command,
        cwd=trace_dir,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_once(args: argparse.Namespace) -> dict[str, Any]:
    trace_dir = Path(tempfile.mkdtemp(prefix="ani-sipp-trace-"))
    try:
        sipp_result = _run_sipp(args, trace_dir)
        window = _build_feature_window(args, trace_dir, sipp_result)
        window["sipp_summary"]["status"] = "completed" if sipp_result.returncode == 0 else "completed-with-sipp-errors"
        window_uri = _upload_window(window)
        incident = None
        try:
            incident = _score_feature_window(window)
            if incident is None and _env_flag("SIPP_FALLBACK_TO_SCENARIO_LABELER", False):
                incident = _emit_control_plane_incident(window)
        except requests.RequestException:
            if _env_flag("SIPP_FALLBACK_TO_SCENARIO_LABELER", False):
                incident = _emit_control_plane_incident(window)
            elif _env_flag("CONTROL_PLANE_INCIDENT_REQUIRED", False):
                raise
        return {"window_uri": window_uri, "window": window, "incident": incident}
    finally:
        shutil.rmtree(trace_dir, ignore_errors=True)


def _progress_payload(args: argparse.Namespace, completed_runs: int, total_runs: int) -> dict[str, Any]:
    return {
        "dataset_version": args.dataset_version,
        "scenario_name": args.scenario_name,
        "completed_runs": completed_runs,
        "total_runs": total_runs,
    }


def _bulk_summary(args: argparse.Namespace, results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "dataset_version": args.dataset_version,
        "scenario_name": args.scenario_name,
        "repeat_count": len(results),
        "windows_created": len(results),
        "control_plane_incidents_emitted": sum(1 for result in results if result.get("incident")),
        "completed_with_sipp_errors": sum(
            1
            for result in results
            if int(((result.get("window") or {}).get("sipp_summary") or {}).get("return_code", 0) or 0) != 0
        ),
        "first_window_uri": results[0].get("window_uri") if results else None,
        "last_window_uri": results[-1].get("window_uri") if results else None,
    }


def _run_repeated(args: argparse.Namespace) -> dict[str, Any]:
    repeat_count = _positive_repeat_count(args.repeat_count)
    if repeat_count == 1:
        return _run_once(args)

    progress_every = max(int(args.progress_every), 0)
    repeat_sleep_seconds = max(float(args.repeat_sleep_seconds), 0.0)
    results: list[dict[str, Any]] = []
    for iteration in range(repeat_count):
        results.append(_run_once(args))
        completed_runs = iteration + 1
        if progress_every and completed_runs < repeat_count and completed_runs % progress_every == 0:
            print(json.dumps(_progress_payload(args, completed_runs, repeat_count)))
        if repeat_sleep_seconds and completed_runs < repeat_count:
            time.sleep(repeat_sleep_seconds)
    return _bulk_summary(args, results)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", type=int, default=5060)
    parser.add_argument("--scenario-file", required=True)
    parser.add_argument("--scenario-name", required=True)
    parser.add_argument("--call-limit", type=int, required=True)
    parser.add_argument("--rate", type=int, required=True)
    parser.add_argument("--transport", choices=sorted(SIPP_TRANSPORT_MAP.keys()), default="udp")
    parser.add_argument("--dataset-version", default=os.getenv("DATASET_VERSION", DEFAULT_DATASET_VERSION))
    parser.add_argument("--repeat-count", type=int, default=1)
    parser.add_argument("--repeat-sleep-seconds", type=float, default=0.0)
    parser.add_argument("--progress-every", type=int, default=0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(json.dumps(_run_repeated(args), indent=2))


if __name__ == "__main__":
    main()
