import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import boto3
from botocore.config import Config


FEATURE_SCHEMA_VERSION = "feature_schema_v1"
DEFAULT_DATASET_VERSION = "live-sipp-v1"
DEFAULT_DATASET_STORE_ENDPOINT = "http://model-storage-minio.ims-demo-lab.svc.cluster.local:9000"
DEFAULT_DATASET_STORE_BUCKET = "ims-models"
DEFAULT_DATASET_STORE_PREFIX = "pipelines/ims-demo-lab/datasets"
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
        config=Config(s3={"addressing_style": "path"}),
    )


def _ensure_dataset_bucket() -> None:
    client = _dataset_s3_client()
    bucket = _dataset_store_bucket()
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)


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
    if scenario_name == "normal":
        return "normal"
    if scenario_name == "malformed_invite":
        return "malformed_sip"
    return scenario_name


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

    return {
        "window_id": window_id,
        "window_start": datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat(),
        "window_end": datetime.fromtimestamp(end_time, tz=timezone.utc).isoformat(),
        "source": "openims-sipp-lab",
        "feature_source": "sipp-shortmessages",
        "schema_version": FEATURE_SCHEMA_VERSION,
        "dataset_version": args.dataset_version,
        "scenario_name": scenario_name,
        "label": 0 if scenario_name == "normal" else 1,
        "anomaly_type": anomaly_type,
        "label_confidence": 0.95,
        "features": features,
        "labels": {
            "anomaly": scenario_name != "normal",
            "anomaly_type": None if scenario_name == "normal" else anomaly_type,
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
    _dataset_s3_client().put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(window, indent=2).encode("utf-8"),
        ContentType="application/json",
    )
    return _s3_uri(bucket, key)


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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    trace_dir = Path(tempfile.mkdtemp(prefix="ims-sipp-trace-"))
    try:
        sipp_result = _run_sipp(args, trace_dir)
        window = _build_feature_window(args, trace_dir, sipp_result)
        window["sipp_summary"]["status"] = "completed" if sipp_result.returncode == 0 else "completed-with-sipp-errors"
        window_uri = _upload_window(window)
        print(json.dumps({"window_uri": window_uri, "window": window}, indent=2))
    finally:
        shutil.rmtree(trace_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
