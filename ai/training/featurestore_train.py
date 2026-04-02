#!/usr/bin/env python3
"""Feature-store-backed training and publishing path for the IMS anomaly platform."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import boto3
from botocore.config import Config
import pandas as pd

from ai.training.build_feature_bundle import build_bundle, localize_bundle_manifest, resolve_bundle_manifest
from ai.training.model_registry_client import (
    DEFAULT_MODEL_REGISTRY_ENDPOINT,
    build_model_registry_payload,
    publish_model_version,
)
from ai.training.train_and_register import (
    FEATURES,
    FEATURE_SCHEMA_VERSION,
    TRITON_CONFIG_TEMPLATE,
    TRITON_MODEL_TEMPLATE,
    _download_file_reference,
    _json_dump,
    _json_load,
    _now,
    _prepare_artifact_for_storage,
    _write_json_reference,
    evaluate,
    evaluate_serving_model,
    gate_metrics,
    persist_model_artifact,
    score_baseline,
    scorer_for_artifact,
    select_best_model,
    split_dataset,
    train_autogluon_candidate,
    train_baseline,
    train_serving_model,
)

DEFAULT_WORKSPACE_ROOT = "/tmp/ims-featurestore"
DEFAULT_FEATURE_REPO_PATH = "/workspace/ai/featurestore/feature_repo"
DEFAULT_FEATURE_SERVICE_NAME = "ims_anomaly_scoring_v1"
DEFAULT_MODEL_NAME = "ims-anomaly-featurestore"
DEFAULT_SERVING_MODEL_NAME = "ims-predictive-fs"
DEFAULT_SERVING_RUNTIME_NAME = "nvidia-triton-runtime"
DEFAULT_SERVING_SERVICE_ACCOUNT = "model-storage-sa"
DEFAULT_SERVING_PREFIX = "predictive-featurestore"
DEFAULT_SERVING_ALIAS = "current"
DEFAULT_PIPELINE_NAME = "ims-featurestore-train-and-register"
DEFAULT_MODEL_FORMAT_NAME = "triton"
DEFAULT_MODEL_FORMAT_VERSION = "2"
DEFAULT_FEATURESTORE_MODE = "local"
DEFAULT_MANAGED_FEATURESTORE_PROJECT = "ims_anomaly_featurestore"
DEFAULT_MANAGED_FEATURESTORE_REGISTRY_PATH = "feast-ims-featurestore-registry.ims-demo-lab.svc.cluster.local:443"
DEFAULT_MANAGED_FEATURESTORE_ONLINE_STORE_PATH = "https://feast-ims-featurestore-online.ims-demo-lab.svc.cluster.local:443"
DEFAULT_MANAGED_FEATURESTORE_CA_CERT_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/service-ca.crt"
DEFAULT_MANAGED_FEATURESTORE_AUTH_TYPE = "no_auth"
DEFAULT_MANAGED_FEATURESTORE_ENTITY_KEY_SERIALIZATION_VERSION = "3"


def _feature_repo_path(path: str) -> Path:
    return Path(path or DEFAULT_FEATURE_REPO_PATH)


def _feature_store_mode() -> str:
    return (os.getenv("IMS_FEATURESTORE_MODE", DEFAULT_FEATURESTORE_MODE).strip().lower() or DEFAULT_FEATURESTORE_MODE)


def _use_managed_feature_store() -> bool:
    return _feature_store_mode() == "remote"


def _managed_feature_store_yaml() -> str:
    project = os.getenv("IMS_FEATURESTORE_PROJECT", DEFAULT_MANAGED_FEATURESTORE_PROJECT).strip()
    registry_path = os.getenv("IMS_FEATURESTORE_REGISTRY_PATH", DEFAULT_MANAGED_FEATURESTORE_REGISTRY_PATH).strip()
    online_store_path = os.getenv(
        "IMS_FEATURESTORE_ONLINE_STORE_PATH",
        DEFAULT_MANAGED_FEATURESTORE_ONLINE_STORE_PATH,
    ).strip()
    cert_path = os.getenv("IMS_FEATURESTORE_CA_CERT_PATH", DEFAULT_MANAGED_FEATURESTORE_CA_CERT_PATH).strip()
    auth_type = os.getenv("IMS_FEATURESTORE_AUTH_TYPE", DEFAULT_MANAGED_FEATURESTORE_AUTH_TYPE).strip()
    entity_key_serialization_version = os.getenv(
        "IMS_FEATURESTORE_ENTITY_KEY_SERIALIZATION_VERSION",
        DEFAULT_MANAGED_FEATURESTORE_ENTITY_KEY_SERIALIZATION_VERSION,
    ).strip()
    if not registry_path or not online_store_path:
        raise ValueError("Managed feature store mode requires registry and online store paths")

    lines = [
        f"project: {project or DEFAULT_MANAGED_FEATURESTORE_PROJECT}",
        "provider: local",
        "online_store:",
        f"  path: {online_store_path}",
        "  type: remote",
    ]
    if cert_path:
        lines.append(f"  cert: {cert_path}")
    lines.extend(
        [
            "registry:",
            f"  path: {registry_path}",
            "  registry_type: remote",
        ]
    )
    if cert_path:
        lines.append(f"  cert: {cert_path}")
    lines.extend(
        [
            "auth:",
            f"  type: {auth_type or DEFAULT_MANAGED_FEATURESTORE_AUTH_TYPE}",
            f"entity_key_serialization_version: {entity_key_serialization_version or DEFAULT_MANAGED_FEATURESTORE_ENTITY_KEY_SERIALIZATION_VERSION}",
        ]
    )
    return "\n".join(lines) + "\n"


def _prepare_feature_repo(feature_repo_path: str, workspace_root: str) -> Path:
    source_path = _feature_repo_path(feature_repo_path)
    if not source_path.exists():
        raise FileNotFoundError(f"Feature repo path {source_path} does not exist")
    workspace_feature_repo = Path(workspace_root) / "feature_repo"
    if workspace_feature_repo.exists():
        shutil.rmtree(workspace_feature_repo)
    workspace_feature_repo.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_path, workspace_feature_repo)
    if _use_managed_feature_store():
        (workspace_feature_repo / "feature_store.yaml").write_text(_managed_feature_store_yaml())
    return workspace_feature_repo


def _feature_store_artifacts(localized_manifest: Dict[str, Any]) -> Dict[str, Any]:
    if _use_managed_feature_store():
        return localized_manifest["artifacts"]
    return localized_manifest["localized_artifacts"]


def _feature_store_env(localized_manifest: Dict[str, Any]) -> Dict[str, str]:
    artifacts = _feature_store_artifacts(localized_manifest)
    env = {
        "IMS_FEATURESTORE_OFFLINE_SOURCE_PATH": artifacts["feature_store"]["offline_source_parquet"],
        "IMS_FEATURESTORE_LABEL_SOURCE_PATH": artifacts["tables"]["window_labels_parquet"],
    }
    for name in (
        "FEAST_S3_ENDPOINT_URL",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_DEFAULT_REGION",
        "AWS_REGION",
        "AWS_S3_ENDPOINT",
    ):
        value = os.getenv(name, "").strip()
        if value:
            env[name] = value
    return env


def _normalize_localized_bundle(localized_manifest: Dict[str, Any]) -> None:
    for path in (
        localized_manifest["localized_artifacts"]["feature_store"]["offline_source_parquet"],
        localized_manifest["localized_artifacts"]["feature_store"]["entity_rows_parquet"],
        localized_manifest["localized_artifacts"]["tables"]["window_features_parquet"],
        localized_manifest["localized_artifacts"]["tables"]["window_context_parquet"],
        localized_manifest["localized_artifacts"]["tables"]["window_labels_parquet"],
    ):
        frame = pd.read_parquet(path)
        changed = False
        for column in ("event_timestamp", "created_timestamp", "created_at", "updated_at", "approval_created_at"):
            if column in frame.columns and not pd.api.types.is_datetime64_any_dtype(frame[column]):
                frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce")
                changed = True
        if changed:
            frame.to_parquet(path, index=False)


def _resolve_bundle(bundle_version: str, workspace_root: str) -> Dict[str, Any]:
    resolved = resolve_bundle_manifest(bundle_version, workspace_root)
    manifest = _json_load(resolved["bundle_manifest_path"])
    return {
        **resolved,
        "bundle_contract_version": manifest.get("bundle_contract_version"),
        "feature_schema_version": manifest.get("feature_schema_version"),
        "label_taxonomy_version": manifest.get("label_taxonomy_version"),
        "row_counts": manifest.get("row_counts", {}),
        "resolved_at": _now(),
    }


def _parse_source_dataset_versions(raw_value: str) -> list[str]:
    stripped = raw_value.strip()
    if not stripped:
        raise ValueError("At least one source dataset version is required")
    if stripped.startswith("["):
        parsed = json.loads(stripped)
        if not isinstance(parsed, list) or not all(isinstance(item, str) and item.strip() for item in parsed):
            raise ValueError("--source-dataset-versions-json must be a JSON array of strings")
        return [item.strip() for item in parsed]
    return [item.strip() for item in stripped.split(",") if item.strip()]


def _build_bundle_step(
    bundle_version: str,
    source_dataset_versions_json: str,
    workspace_root: str,
    project: str,
) -> Dict[str, Any]:
    return build_bundle(
        bundle_version=bundle_version,
        source_dataset_versions=_parse_source_dataset_versions(source_dataset_versions_json),
        workspace_root=workspace_root,
        project=project,
    )


def _validate_bundle(bundle_manifest_path: str, workspace_root: str) -> Dict[str, Any]:
    localized_manifest = localize_bundle_manifest(bundle_manifest_path, workspace_root)
    _normalize_localized_bundle(localized_manifest)
    features_frame = pd.read_parquet(localized_manifest["localized_artifacts"]["feature_store"]["offline_source_parquet"])
    labels_frame = pd.read_parquet(localized_manifest["localized_artifacts"]["tables"]["window_labels_parquet"])
    incidents_frame = pd.read_parquet(localized_manifest["localized_artifacts"]["tables"]["incidents_parquet"])
    rca_frame = pd.read_parquet(localized_manifest["localized_artifacts"]["tables"]["rca_summary_parquet"])

    missing_feature_columns = sorted(
        ({"window_id", "event_timestamp", "created_timestamp", *FEATURES}) - set(features_frame.columns)
    )
    missing_label_columns = sorted(
        ({"window_id", "label", "anomaly_type", "incident_id", "approval_status", "rca_status"}) - set(labels_frame.columns)
    )
    missing_incident_columns = sorted(
        ({"incident_id", "window_id", "approval_status", "rca_status", "anomaly_score"}) - set(incidents_frame.columns)
    )
    missing_rca_columns = sorted(
        ({"incident_id", "window_id", "root_cause", "confidence", "recommendation"}) - set(rca_frame.columns)
    )
    if missing_feature_columns:
        raise ValueError(f"Bundle is missing feature columns: {missing_feature_columns}")
    if missing_label_columns:
        raise ValueError(f"Bundle is missing label columns: {missing_label_columns}")
    if missing_incident_columns:
        raise ValueError(f"Bundle is missing incident columns: {missing_incident_columns}")
    if missing_rca_columns:
        raise ValueError(f"Bundle is missing RCA columns: {missing_rca_columns}")
    if not localized_manifest.get("label_taxonomy_version"):
        raise ValueError("Bundle manifest is missing label_taxonomy_version")
    if not localized_manifest.get("git_commit"):
        raise ValueError("Bundle manifest is missing git_commit")

    return {
        "bundle_version": localized_manifest["bundle_version"],
        "bundle_contract_version": localized_manifest["bundle_contract_version"],
        "feature_schema_version": localized_manifest["feature_schema_version"],
        "label_taxonomy_version": localized_manifest["label_taxonomy_version"],
        "git_commit": localized_manifest["git_commit"],
        "source_snapshot_id": localized_manifest["source_snapshot_id"],
        "source_dataset_versions": localized_manifest["source_dataset_versions"],
        "bundle_manifest_path": bundle_manifest_path,
        "row_counts": localized_manifest["row_counts"],
        "localized_artifacts": localized_manifest["localized_artifacts"],
        "validation": {
            "status": "passed",
            "feature_rows": int(len(features_frame.index)),
            "label_rows": int(len(labels_frame.index)),
            "incident_rows": int(len(incidents_frame.index)),
            "rca_rows": int(len(rca_frame.index)),
        },
        "validated_at": _now(),
    }


def _run_feast_apply(repo_path: Path, env: Dict[str, str]) -> None:
    command = [os.getenv("IMS_FEATURESTORE_FEAST_BIN", "feast"), "apply"]
    merged_env = os.environ.copy()
    merged_env.update(env)
    subprocess.run(command, cwd=repo_path, env=merged_env, check=True)


def _sync_feature_store(bundle_manifest_path: str, workspace_root: str, feature_repo_path: str) -> Dict[str, Any]:
    localized_manifest = localize_bundle_manifest(bundle_manifest_path, workspace_root)
    _normalize_localized_bundle(localized_manifest)
    repo_path = _prepare_feature_repo(feature_repo_path, workspace_root)
    env = _feature_store_env(localized_manifest)
    _run_feast_apply(repo_path, env)
    return {
        "bundle_version": localized_manifest["bundle_version"],
        "bundle_manifest_path": bundle_manifest_path,
        "feature_repo_path": str(repo_path),
        "feature_store_env": env,
        "status": "applied",
        "applied_at": _now(),
    }


def _load_feature_store(repo_path: Path, env: Dict[str, str]):
    merged_env = os.environ.copy()
    merged_env.update(env)
    os.environ.update(merged_env)
    from feast import FeatureStore

    return FeatureStore(repo_path=str(repo_path))


def _retrieve_training_dataset(
    bundle_manifest_path: str,
    workspace_root: str,
    feature_repo_path: str,
    feature_service_name: str,
) -> Dict[str, Any]:
    localized_manifest = localize_bundle_manifest(bundle_manifest_path, workspace_root)
    _normalize_localized_bundle(localized_manifest)
    env = _feature_store_env(localized_manifest)
    repo_path = _prepare_feature_repo(feature_repo_path, workspace_root)
    if not _use_managed_feature_store():
        _run_feast_apply(repo_path, env)
    store = _load_feature_store(repo_path, env)

    entity_rows = pd.read_parquet(localized_manifest["localized_artifacts"]["feature_store"]["entity_rows_parquet"])
    entity_rows["event_timestamp"] = pd.to_datetime(entity_rows["event_timestamp"], utc=True)
    labels_frame = pd.read_parquet(localized_manifest["localized_artifacts"]["tables"]["window_labels_parquet"])[
        ["window_id", "label", "anomaly_type"]
    ].drop_duplicates(subset=["window_id"])
    feature_service = store.get_feature_service(feature_service_name)
    training_frame = store.get_historical_features(
        entity_df=entity_rows,
        features=feature_service,
    ).to_df()
    training_frame = training_frame.merge(labels_frame, on="window_id", how="inner")

    records: List[Dict[str, Any]] = []
    for row in training_frame.to_dict(orient="records"):
        records.append(
            {
                "window_id": str(row["window_id"]),
                "features": {feature: float(row.get(feature, 0.0) or 0.0) for feature in FEATURES},
                "label": int(row["label"]),
                "anomaly_type": str(row["anomaly_type"]),
            }
        )

    train_records, eval_records = split_dataset(records)
    workspace = Path(workspace_root)
    bundle_version = localized_manifest["bundle_version"]
    train_path = workspace / "featurestore" / bundle_version / "training" / f"{feature_service_name}-train.json"
    eval_path = workspace / "featurestore" / bundle_version / "training" / f"{feature_service_name}-eval.json"
    train_reference = _write_json_reference(
        train_records,
        f"featurestore/{bundle_version}/training/{feature_service_name}-train.json",
        train_path,
    )
    eval_reference = _write_json_reference(
        eval_records,
        f"featurestore/{bundle_version}/training/{feature_service_name}-eval.json",
        eval_path,
    )

    return {
        "bundle_version": bundle_version,
        "bundle_manifest_path": bundle_manifest_path,
        "feature_repo_path": str(repo_path),
        "feature_service_name": feature_service_name,
        "train_path": train_reference,
        "eval_path": eval_reference,
        "train_count": len(train_records),
        "eval_count": len(eval_records),
        "created_at": _now(),
    }


def _load_training_manifest(path: str) -> Dict[str, Any]:
    return _json_load(path)


def _training_records(training_manifest: Dict[str, Any], records_key: str, path_key: str) -> List[Dict[str, Any]]:
    records = training_manifest.get(records_key)
    if records is not None:
        return records
    return _json_load(training_manifest[path_key])


def _train_baseline_step(training_manifest_path: str, baseline_version: str, artifact_dir: str) -> Dict[str, Any]:
    training_manifest = _load_training_manifest(training_manifest_path)
    train_records = _training_records(training_manifest, "train_records", "train_path")
    artifact = train_baseline(train_records)
    artifact_path = persist_model_artifact(artifact_dir, baseline_version, artifact)
    return {
        "bundle_version": training_manifest["bundle_version"],
        "feature_service_name": training_manifest["feature_service_name"],
        "training_manifest": training_manifest_path,
        "version": baseline_version,
        "model_type": artifact["model_type"],
        "artifact_path": artifact_path,
        "created_at": _now(),
    }


def _train_automl_step(
    training_manifest_path: str,
    candidate_version: str,
    workspace_root: str,
    artifact_dir: str,
    automl_engine: str,
) -> Dict[str, Any]:
    training_manifest = _load_training_manifest(training_manifest_path)
    train_records = _training_records(training_manifest, "train_records", "train_path")
    artifact = train_autogluon_candidate(
        train_records,
        workspace_root=workspace_root,
        version=candidate_version,
        automl_engine=automl_engine,
    )
    artifact = _prepare_artifact_for_storage(artifact, candidate_version)
    artifact_path = persist_model_artifact(artifact_dir, candidate_version, artifact)
    return {
        "bundle_version": training_manifest["bundle_version"],
        "feature_service_name": training_manifest["feature_service_name"],
        "training_manifest": training_manifest_path,
        "version": candidate_version,
        "model_type": artifact["model_type"],
        "artifact_path": artifact_path,
        "created_at": _now(),
    }


def _evaluate_step(training_manifest_path: str, baseline_manifest_path: str, candidate_manifest_path: str) -> Dict[str, Any]:
    training_manifest = _load_training_manifest(training_manifest_path)
    eval_records = _training_records(training_manifest, "eval_records", "eval_path")
    baseline_manifest = _json_load(baseline_manifest_path)
    candidate_manifest = _json_load(candidate_manifest_path)
    baseline_artifact = _json_load(baseline_manifest["artifact_path"])
    candidate_artifact = _json_load(candidate_manifest["artifact_path"])
    baseline_metrics = evaluate(eval_records, baseline_artifact, score_baseline)
    candidate_metrics = evaluate(eval_records, candidate_artifact, scorer_for_artifact(candidate_artifact))
    return {
        "dataset_version": training_manifest["bundle_version"],
        "bundle_version": training_manifest["bundle_version"],
        "feature_service_name": training_manifest["feature_service_name"],
        "training_manifest": training_manifest_path,
        "label_manifest": training_manifest_path,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "promotion_gate": {**gate_metrics(candidate_metrics)["gate"]},
        "baseline": {
            "version": baseline_manifest["version"],
            "artifact_path": baseline_manifest["artifact_path"],
            "metrics": baseline_metrics,
            "model_type": baseline_manifest["model_type"],
        },
        "candidate": {
            "version": candidate_manifest["version"],
            "artifact_path": candidate_manifest["artifact_path"],
            "metrics": candidate_metrics,
            "model_type": candidate_manifest["model_type"],
        },
        "created_at": _now(),
    }


def _export_triton_repository(
    serving_root: Path,
    serving_model_name: str,
    model,
    source_model_version: str,
) -> Dict[str, Path]:
    repository_root = serving_root / serving_model_name
    if repository_root.exists():
        shutil.rmtree(repository_root)
    version_root = repository_root / "1"
    version_root.mkdir(parents=True, exist_ok=True)

    scaler = model.named_steps["scaler"]
    classifier = model.named_steps["classifier"]
    weights_path = version_root / "weights.json"
    _json_dump(
        weights_path,
        {
            "model_type": "triton_python_logistic_regression",
            "source_model_version": source_model_version,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "feature_names": FEATURES,
            "scaler_mean": [round(float(value), 10) for value in scaler.mean_.tolist()],
            "scaler_scale": [round(float(value), 10) for value in scaler.scale_.tolist()],
            "coefficients": [round(float(value), 10) for value in classifier.coef_[0].tolist()],
            "intercept": round(float(classifier.intercept_[0]), 10),
            "threshold": 0.6,
        },
    )
    (version_root / "model.py").write_text(TRITON_MODEL_TEMPLATE)
    (repository_root / "config.pbtxt").write_text(
        TRITON_CONFIG_TEMPLATE.format(
            model_name=serving_model_name,
            feature_count=len(FEATURES),
            model_version="1",
        )
    )
    return {
        "repository_root": repository_root,
        "version_root": version_root,
        "weights_path": weights_path,
        "config_path": repository_root / "config.pbtxt",
    }


def _upload_serving_bundle(
    serving_repository_root: Path,
    *,
    serving_prefix: str,
    serving_model_name: str,
    model_version_name: str,
    serving_alias: str,
    selected_artifact_path: str | Path,
    metadata_path: Path,
) -> Dict[str, Any]:
    endpoint = os.getenv("MINIO_ENDPOINT", "http://model-storage-minio.ims-demo-lab.svc.cluster.local:9000")
    access_key = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
    secret_key = os.getenv("MINIO_SECRET_KEY", "minioadmin")
    bucket = os.getenv("MINIO_BUCKET", "ims-models")
    artifact_root = f"{serving_prefix.rstrip('/')}/{serving_model_name}/{model_version_name}".strip("/")
    alias_root = f"{serving_prefix.rstrip('/')}/{serving_model_name}/{serving_alias}".strip("/")

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)

    selected_source = Path(str(selected_artifact_path))
    if str(selected_artifact_path).startswith("s3://"):
        selected_source = _download_file_reference(
            str(selected_artifact_path),
            Path(tempfile.mkdtemp(prefix="ims-featurestore-selected-")) / Path(str(selected_artifact_path)).name,
        )

    for root in (artifact_root, alias_root):
        client.upload_file(str(selected_source), bucket, f"{root}/selected-model.json")
        client.upload_file(str(metadata_path), bucket, f"{root}/serving-metadata.json")
    for file_path in serving_repository_root.rglob("*"):
        if not file_path.is_file():
            continue
        relative_path = file_path.relative_to(serving_repository_root)
        for root in (artifact_root, alias_root):
            client.upload_file(str(file_path), bucket, f"{root}/{relative_path.as_posix()}")

    storage_uri = f"s3://{bucket}/{artifact_root}/"
    alias_storage_uri = f"s3://{bucket}/{alias_root}/"
    return {
        "bucket": bucket,
        "endpoint": endpoint,
        "serving_prefix": artifact_root,
        "serving_alias_prefix": alias_root,
        "storage_uri": storage_uri,
        "alias_storage_uri": alias_storage_uri,
        "weights_uri": f"{storage_uri}{serving_model_name}/1/weights.json",
        "alias_weights_uri": f"{alias_storage_uri}{serving_model_name}/1/weights.json",
    }


def _export_serving_artifact_step(
    training_manifest_path: str,
    selection_manifest_path: str,
    artifact_dir: str,
    serving_model_name: str,
    serving_runtime_name: str,
    serving_prefix: str,
    serving_alias: str,
) -> Dict[str, Any]:
    training_manifest = _load_training_manifest(training_manifest_path)
    selection_manifest = _json_load(selection_manifest_path)
    train_records = _training_records(training_manifest, "train_records", "train_path")
    eval_records = _training_records(training_manifest, "eval_records", "eval_path")
    selected_version = selection_manifest["selected_model_version"]
    selected_artifact_path = selection_manifest["selected_artifact_path"]

    serving_model = train_serving_model(train_records)
    serving_metrics = evaluate_serving_model(eval_records, serving_model)

    artifact_dir_path = Path(artifact_dir)
    artifact_dir_path.mkdir(parents=True, exist_ok=True)
    serving_root = artifact_dir_path.parent / "serving" / serving_model_name
    serving_root.mkdir(parents=True, exist_ok=True)
    triton_export = _export_triton_repository(
        serving_root=serving_root,
        serving_model_name=serving_model_name,
        model=serving_model,
        source_model_version=selected_version,
    )
    metadata_path = artifact_dir_path.parent / "serving" / f"{serving_model_name}-metadata.json"
    serving_metadata = {
        "bundle_version": training_manifest["bundle_version"],
        "feature_service_name": training_manifest["feature_service_name"],
        "selected_model_version": selected_version,
        "selected_artifact_path": selected_artifact_path,
        "serving_model_name": serving_model_name,
        "serving_runtime_name": serving_runtime_name,
        "serving_metrics": serving_metrics,
        "created_at": _now(),
    }
    _json_dump(metadata_path, serving_metadata)
    upload = _upload_serving_bundle(
        serving_repository_root=serving_root,
        serving_prefix=serving_prefix,
        serving_model_name=serving_model_name,
        model_version_name=selected_version,
        serving_alias=serving_alias,
        selected_artifact_path=selected_artifact_path,
        metadata_path=metadata_path,
    )
    deployment_readiness = (
        "ready"
        if gate_metrics(serving_metrics)["status"] == "passed"
        else "needs-review"
    )
    return {
        **serving_metadata,
        "training_manifest": training_manifest_path,
        "selection_manifest": selection_manifest_path,
        "serving_repository_path": str(serving_root),
        "serving_weights_path": str(triton_export["weights_path"]),
        "serving_storage_uri": upload["storage_uri"],
        "serving_alias_storage_uri": upload["alias_storage_uri"],
        "serving_weights_uri": upload["weights_uri"],
        "serving_alias_weights_uri": upload["alias_weights_uri"],
        "deployment_readiness_status": deployment_readiness,
        "minio_upload": upload,
    }


def _register_model_version_step(
    export_manifest_path: str,
    model_name: str,
    model_version_name: str | None,
    feature_service_name: str,
    pipeline_name: str,
) -> Dict[str, Any]:
    export_manifest = _json_load(export_manifest_path)
    resolved_version = model_version_name or f"{export_manifest['bundle_version']}-{export_manifest['selected_model_version']}"
    payload = build_model_registry_payload(
        model_name=model_name,
        model_version_name=resolved_version,
        artifact_uri=export_manifest["serving_storage_uri"],
        bundle_version=export_manifest["bundle_version"],
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_service_name=feature_service_name or export_manifest["feature_service_name"],
        model_format_name=DEFAULT_MODEL_FORMAT_NAME,
        model_format_version=DEFAULT_MODEL_FORMAT_VERSION,
        pipeline_name=pipeline_name,
        metrics=export_manifest["serving_metrics"],
        deployment_readiness_status=export_manifest.get("deployment_readiness_status", "needs-review"),
        metadata={
            "serving_model_name": export_manifest["serving_model_name"],
            "serving_runtime_name": export_manifest["serving_runtime_name"],
            "selected_model_version": export_manifest["selected_model_version"],
            "serving_alias_storage_uri": export_manifest.get("serving_alias_storage_uri", ""),
            "description": f"Feature-store-trained IMS anomaly model for bundle {export_manifest['bundle_version']}",
        },
    )
    temp_output = Path(DEFAULT_WORKSPACE_ROOT) / "model-registry" / f"{resolved_version}.json"
    published = publish_model_version(payload, temp_output)
    return {
        "bundle_version": export_manifest["bundle_version"],
        "model_name": model_name,
        "model_version_name": resolved_version,
        "feature_service_name": feature_service_name or export_manifest["feature_service_name"],
        "model_registry_endpoint": payload["registry_endpoint"],
        "registration_record_path": str(temp_output),
        "registration_payload": payload,
        "registration_result": published["registration_result"],
        "created_at": _now(),
    }


def _deployment_yaml(
    *,
    serving_model_name: str,
    serving_runtime_name: str,
    service_account_name: str,
    storage_uri: str,
    bundle_version: str,
    feature_service_name: str,
    selected_model_version: str,
) -> str:
    return "\n".join(
        [
            "apiVersion: serving.kserve.io/v1beta1",
            "kind: InferenceService",
            "metadata:",
            f"  name: {serving_model_name}",
            "  namespace: ims-demo-lab",
            "  labels:",
            '    opendatahub.io/dashboard: "true"',
            "  annotations:",
            f"    ims.redhat.com/source-bundle-version: {bundle_version}",
            f"    ims.redhat.com/feature-service-name: {feature_service_name}",
            f"    ims.redhat.com/source-model-version: {selected_model_version}",
            "    serving.kserve.io/deploymentMode: RawDeployment",
            "    serving.kserve.io/enable-prometheus-scraping: \"true\"",
            "spec:",
            "  predictor:",
            f"    serviceAccountName: {service_account_name}",
            "    minReplicas: 1",
            "    maxReplicas: 1",
            "    model:",
            f"      runtime: {serving_runtime_name}",
            "      modelFormat:",
            "        name: triton",
            "      protocolVersion: v2",
            f"      storageUri: {storage_uri}",
            "      resources:",
            "        requests:",
            "          cpu: 100m",
            "          memory: 1Gi",
            "        limits:",
            "          memory: 2Gi",
            "",
        ]
    )


def _publish_deployment_manifest_step(
    export_manifest_path: str,
    model_registry_manifest_path: str,
    service_account_name: str,
) -> Dict[str, Any]:
    export_manifest = _json_load(export_manifest_path)
    model_registry_manifest = _json_load(model_registry_manifest_path)
    deployment_root = Path(DEFAULT_WORKSPACE_ROOT) / "deployment"
    deployment_root.mkdir(parents=True, exist_ok=True)
    deployment_path = deployment_root / f"{export_manifest['serving_model_name']}.yaml"
    deployment_yaml = _deployment_yaml(
        serving_model_name=export_manifest["serving_model_name"],
        serving_runtime_name=export_manifest["serving_runtime_name"],
        service_account_name=service_account_name,
        storage_uri=export_manifest.get("serving_alias_storage_uri", export_manifest["serving_storage_uri"]),
        bundle_version=export_manifest["bundle_version"],
        feature_service_name=export_manifest["feature_service_name"],
        selected_model_version=export_manifest["selected_model_version"],
    )
    deployment_path.write_text(deployment_yaml)
    compatibility_manifest_path = deployment_root / f"{export_manifest['serving_model_name']}-compatibility.json"
    compatibility_manifest = {
        "serving_model_name": export_manifest["serving_model_name"],
        "serving_runtime_name": export_manifest["serving_runtime_name"],
        "bundle_version": export_manifest["bundle_version"],
        "selected_model_version": export_manifest["selected_model_version"],
        "versioned_storage_uri": export_manifest["serving_storage_uri"],
        "stable_storage_uri": export_manifest.get("serving_alias_storage_uri", export_manifest["serving_storage_uri"]),
        "model_registry_endpoint": model_registry_manifest.get("model_registry_endpoint", DEFAULT_MODEL_REGISTRY_ENDPOINT),
        "registration_result": model_registry_manifest["registration_result"],
        "created_at": _now(),
    }
    _json_dump(compatibility_manifest_path, compatibility_manifest)
    return {
        "bundle_version": export_manifest["bundle_version"],
        "serving_model_name": export_manifest["serving_model_name"],
        "deployment_manifest_path": str(deployment_path),
        "compatibility_manifest_path": str(compatibility_manifest_path),
        "deployment_yaml": deployment_yaml,
        "registration_record_path": model_registry_manifest_path,
        "registration_result": model_registry_manifest["registration_result"],
        "created_at": _now(),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True)
    parser.add_argument("--workspace-root", default=DEFAULT_WORKSPACE_ROOT)
    parser.add_argument("--bundle-version")
    parser.add_argument("--bundle-manifest")
    parser.add_argument("--source-dataset-versions-json")
    parser.add_argument("--project", default="ims-demo")
    parser.add_argument("--training-manifest")
    parser.add_argument("--baseline-manifest")
    parser.add_argument("--candidate-manifest")
    parser.add_argument("--evaluation-manifest")
    parser.add_argument("--selection-manifest")
    parser.add_argument("--export-manifest")
    parser.add_argument("--model-registry-manifest")
    parser.add_argument("--feature-repo-path", default=DEFAULT_FEATURE_REPO_PATH)
    parser.add_argument("--feature-service-name", default=DEFAULT_FEATURE_SERVICE_NAME)
    parser.add_argument("--artifact-dir", default=f"{DEFAULT_WORKSPACE_ROOT}/models/artifacts")
    parser.add_argument("--baseline-version", default="baseline-fs-v1")
    parser.add_argument("--candidate-version", default="candidate-fs-v1")
    parser.add_argument("--automl-engine", default="autogluon")
    parser.add_argument("--model-name", default=DEFAULT_MODEL_NAME)
    parser.add_argument("--model-version-name")
    parser.add_argument("--serving-model-name", default=DEFAULT_SERVING_MODEL_NAME)
    parser.add_argument("--serving-runtime-name", default=DEFAULT_SERVING_RUNTIME_NAME)
    parser.add_argument("--serving-prefix", default=DEFAULT_SERVING_PREFIX)
    parser.add_argument("--serving-alias", default=DEFAULT_SERVING_ALIAS)
    parser.add_argument("--service-account-name", default=DEFAULT_SERVING_SERVICE_ACCOUNT)
    parser.add_argument("--pipeline-name", default=DEFAULT_PIPELINE_NAME)
    parser.add_argument("--output")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    output_is_text = False

    if args.step == "build-bundle":
        if not args.bundle_version:
            raise ValueError("--bundle-version is required")
        if not args.source_dataset_versions_json:
            raise ValueError("--source-dataset-versions-json is required")
        manifest = _build_bundle_step(
            args.bundle_version,
            args.source_dataset_versions_json,
            args.workspace_root,
            args.project,
        )
        output_is_text = True
    elif args.step == "resolve-bundle":
        if not args.bundle_version:
            raise ValueError("--bundle-version is required")
        manifest = _resolve_bundle(args.bundle_version, args.workspace_root)
        output_is_text = True
    elif args.step == "validate-bundle":
        if not args.bundle_manifest:
            raise ValueError("--bundle-manifest is required")
        manifest = _validate_bundle(args.bundle_manifest, args.workspace_root)
    elif args.step == "sync-feature-store-definitions":
        if not args.bundle_manifest:
            raise ValueError("--bundle-manifest is required")
        manifest = _sync_feature_store(args.bundle_manifest, args.workspace_root, args.feature_repo_path)
    elif args.step == "retrieve-training-dataset":
        if not args.bundle_manifest:
            raise ValueError("--bundle-manifest is required")
        manifest = _retrieve_training_dataset(
            args.bundle_manifest,
            args.workspace_root,
            args.feature_repo_path,
            args.feature_service_name,
        )
    elif args.step == "train-baseline":
        if not args.training_manifest:
            raise ValueError("--training-manifest is required")
        manifest = _train_baseline_step(args.training_manifest, args.baseline_version, args.artifact_dir)
    elif args.step == "train-automl":
        if not args.training_manifest:
            raise ValueError("--training-manifest is required")
        manifest = _train_automl_step(
            args.training_manifest,
            args.candidate_version,
            args.workspace_root,
            args.artifact_dir,
            args.automl_engine,
        )
    elif args.step == "evaluate":
        if not all([args.training_manifest, args.baseline_manifest, args.candidate_manifest]):
            raise ValueError("--training-manifest, --baseline-manifest, and --candidate-manifest are required")
        manifest = _evaluate_step(args.training_manifest, args.baseline_manifest, args.candidate_manifest)
    elif args.step == "select-best":
        if not args.evaluation_manifest:
            raise ValueError("--evaluation-manifest is required")
        manifest = select_best_model(_json_load(args.evaluation_manifest))
    elif args.step == "export-serving-artifact":
        if not all([args.training_manifest, args.selection_manifest]):
            raise ValueError("--training-manifest and --selection-manifest are required")
        manifest = _export_serving_artifact_step(
            args.training_manifest,
            args.selection_manifest,
            args.artifact_dir,
            args.serving_model_name,
            args.serving_runtime_name,
            args.serving_prefix,
            args.serving_alias,
        )
    elif args.step == "register-model-version":
        if not args.export_manifest:
            raise ValueError("--export-manifest is required")
        manifest = _register_model_version_step(
            args.export_manifest,
            args.model_name,
            args.model_version_name,
            args.feature_service_name,
            args.pipeline_name,
        )
    elif args.step == "publish-deployment-manifest":
        if not all([args.export_manifest, args.model_registry_manifest]):
            raise ValueError("--export-manifest and --model-registry-manifest are required")
        manifest = _publish_deployment_manifest_step(
            args.export_manifest,
            args.model_registry_manifest,
            args.service_account_name,
        )
    else:
        raise ValueError(f"Unsupported step {args.step}")

    target = Path(args.output) if args.output else Path(DEFAULT_WORKSPACE_ROOT) / f"{args.step}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if output_is_text:
        text_output = (
            manifest["artifacts"]["manifest"]
            if args.step == "build-bundle"
            else manifest["bundle_manifest_path"]
        )
        target.write_text(str(text_output))
    else:
        _json_dump(target, manifest)
    print(target.read_text())


if __name__ == "__main__":
    main()
