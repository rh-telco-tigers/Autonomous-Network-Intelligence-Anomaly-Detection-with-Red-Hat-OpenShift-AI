#!/usr/bin/env python3
"""Render the backfill serving resources from the model registry record."""

from __future__ import annotations

import argparse
import json
import ssl
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


DEFAULT_MODEL_REGISTRY_NAMESPACE = "rhoai-model-registries"
DEFAULT_MODEL_REGISTRY_SERVICE = "default-modelregistry"
DEFAULT_MODEL_REGISTRY_ENDPOINT = "http://default-modelregistry.rhoai-model-registries.svc.cluster.local:8080"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve the backfill model artifact from the model registry and render the serving manifest.",
    )
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--model-version-name", required=True)
    parser.add_argument("--serving-model-name", required=True)
    parser.add_argument("--serving-runtime-name", required=True)
    parser.add_argument("--model-registry-endpoint", default=DEFAULT_MODEL_REGISTRY_ENDPOINT)
    parser.add_argument("--model-registry-namespace", default=DEFAULT_MODEL_REGISTRY_NAMESPACE)
    parser.add_argument("--model-registry-service", default=DEFAULT_MODEL_REGISTRY_SERVICE)
    parser.add_argument("--template", required=True)
    return parser.parse_args()


def _run_oc(args: list[str]) -> str:
    result = subprocess.run(
        ["oc", *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"oc {' '.join(args)} failed"
        raise SystemExit(message)
    return result.stdout


def _oc_json(args: list[str]) -> dict[str, Any]:
    payload = _run_oc(args).strip()
    return json.loads(payload) if payload else {}


def _registry_request_via_service_proxy(
    *,
    registry_namespace: str,
    registry_service: str,
    registry_port: int,
    path: str,
) -> dict[str, Any]:
    proxy_path = (
        f"/api/v1/namespaces/{registry_namespace}/services/http:{registry_service}:{registry_port}/proxy"
        f"/api/model_registry/v1alpha3/{path.lstrip('/')}"
    )
    return _oc_json(["get", "--raw", proxy_path])


def _registry_request_direct(endpoint: str, path: str) -> dict[str, Any]:
    request = Request(
        f"{endpoint.rstrip('/')}/api/model_registry/v1alpha3/{path.lstrip('/')}",
        headers={"Accept": "application/json"},
    )
    context = ssl.create_default_context() if endpoint.startswith("https://") else None
    try:
        with urlopen(request, timeout=10, context=context) as response:
            payload = response.read().decode()
    except (HTTPError, URLError, OSError, ValueError) as exc:
        raise SystemExit(f"Unable to reach model registry endpoint {endpoint}: {exc}") from exc
    return json.loads(payload) if payload else {}


def _registry_request(
    *,
    endpoint: str,
    registry_namespace: str,
    registry_service: str,
    path: str,
) -> dict[str, Any]:
    parsed = urlparse(endpoint)
    if parsed.hostname and parsed.hostname.endswith(".svc.cluster.local"):
        port = parsed.port or 8080
        return _registry_request_via_service_proxy(
            registry_namespace=registry_namespace,
            registry_service=registry_service,
            registry_port=port,
            path=path,
        )
    return _registry_request_direct(endpoint, path)


def _resolve_registered_model_artifact_uri(
    *,
    model_name: str,
    model_version_name: str,
    endpoint: str,
    registry_namespace: str,
    registry_service: str,
) -> str:
    registered_models = _registry_request(
        endpoint=endpoint,
        registry_namespace=registry_namespace,
        registry_service=registry_service,
        path="registered_models",
    ).get("items", [])
    registered_model = next((item for item in registered_models if str(item.get("name") or "") == model_name), None)
    if not registered_model:
        raise SystemExit(f"Registered model {model_name!r} was not found in {endpoint}")

    registered_model_id = str(registered_model.get("id") or "").strip()
    if not registered_model_id:
        raise SystemExit(f"Registered model {model_name!r} is missing an ID")

    versions = _registry_request(
        endpoint=endpoint,
        registry_namespace=registry_namespace,
        registry_service=registry_service,
        path=f"registered_models/{quote(registered_model_id, safe='')}/versions?name={quote(model_version_name, safe='')}",
    ).get("items", [])
    model_version = next((item for item in versions if str(item.get("name") or "") == model_version_name), None)
    if not model_version:
        raise SystemExit(f"Model version {model_version_name!r} was not found for registered model {model_name!r}")

    model_version_id = str(model_version.get("id") or "").strip()
    if not model_version_id:
        raise SystemExit(f"Model version {model_version_name!r} is missing an ID")

    artifacts = _registry_request(
        endpoint=endpoint,
        registry_namespace=registry_namespace,
        registry_service=registry_service,
        path=f"model_versions/{quote(model_version_id, safe='')}/artifacts?name={quote(model_name, safe='')}",
    ).get("items", [])
    if not artifacts:
        artifacts = _registry_request(
            endpoint=endpoint,
            registry_namespace=registry_namespace,
            registry_service=registry_service,
            path=f"model_versions/{quote(model_version_id, safe='')}/artifacts",
        ).get("items", [])
    artifact = next((item for item in artifacts if str(item.get("uri") or "").strip()), None)
    if not artifact:
        raise SystemExit(f"No model artifact URI was found for {model_name!r} version {model_version_name!r}")

    artifact_uri = str(artifact.get("uri") or "").strip()
    if not artifact_uri:
        raise SystemExit(f"Model artifact URI for {model_name!r} version {model_version_name!r} is empty")
    return artifact_uri


def _render_manifest(template_path: Path, replacements: dict[str, str]) -> str:
    rendered = template_path.read_text()
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    missing = sorted({token for token in rendered.split() if token.startswith("__") and token.endswith("__")})
    if missing:
        raise SystemExit(f"Unresolved placeholders remain in rendered manifest: {', '.join(missing)}")
    return rendered


def main() -> int:
    args = _parse_args()
    template_path = Path(args.template)
    if not template_path.exists():
        raise SystemExit(f"Template {template_path} was not found")

    artifact_uri = _resolve_registered_model_artifact_uri(
        model_name=args.model_name,
        model_version_name=args.model_version_name,
        endpoint=args.model_registry_endpoint,
        registry_namespace=args.model_registry_namespace,
        registry_service=args.model_registry_service,
    )
    rendered = _render_manifest(
        template_path,
        {
            "__DATASCIENCE_NAMESPACE__": args.namespace,
            "__BACKFILL_MODEL_NAME__": args.model_name,
            "__BACKFILL_MODEL_VERSION_NAME__": args.model_version_name,
            "__BACKFILL_SERVING_MODEL_NAME__": args.serving_model_name,
            "__BACKFILL_SERVING_RUNTIME_NAME__": args.serving_runtime_name,
            "__BACKFILL_MODEL_REGISTRY_ENDPOINT__": args.model_registry_endpoint.rstrip("/"),
            "__BACKFILL_STORAGE_URI__": artifact_uri,
        },
    )
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
