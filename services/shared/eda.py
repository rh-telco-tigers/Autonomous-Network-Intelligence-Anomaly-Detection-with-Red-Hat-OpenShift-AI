from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

import requests

from shared.aap import controller_callback_template_name


class EDAAutomationError(RuntimeError):
    pass


POLICY_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "critical_incident_escalation": {
        "name": "IMS Critical Incident Escalation",
        "rulebook": "rulebooks/critical-incident-escalation.yml",
        "webhook_port": 5000,
        "description": "Escalate critical incidents to Plane after RCA is attached.",
        "event_types": ["rca_attached"],
        "cases": ["authentication_failure", "server_internal_error", "network_degradation"],
        "action_summary": "Transition the incident to ESCALATED and create or sync the Plane ticket.",
        "controller_template_key": "eda_transition_incident_state",
    },
    "critical_signal_guardrail": {
        "name": "IMS Critical Signal Guardrail",
        "rulebook": "rulebooks/critical-signal-guardrail.yml",
        "webhook_port": 5001,
        "description": "Apply the low-risk P-CSCF ingress guardrail after remediations are generated.",
        "event_types": ["remediations_generated"],
        "cases": ["registration_storm", "retransmission_spike", "network_degradation"],
        "action_summary": "Execute the rate_limit_pcscf remediation through the control-plane automation API.",
        "controller_template_key": "eda_execute_incident_action",
    },
}


def enabled() -> bool:
    return os.getenv("EDA_AUTOMATION_ENABLED", "true").strip().lower() == "true"


def policy_catalog() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for policy_key, definition in POLICY_DEFINITIONS.items():
        items.append(
            {
                "policy_key": policy_key,
                "name": str(definition["name"]),
                "rulebook": str(definition["rulebook"]),
                "description": str(definition["description"]),
                "event_types": list(definition.get("event_types", [])),
                "cases": list(definition.get("cases", [])),
                "action_summary": str(definition.get("action_summary") or ""),
                "trigger_modes": ["event_driven"],
            }
        )
    return items


def bootstrap_resources() -> Dict[str, Any]:
    if not enabled():
        return {"configured": False, "mode": "disabled", "policies": []}

    organization_id = _organization_id()
    project_id = _ensure_project(organization_id)
    _sync_project(project_id)
    decision_environment_id = _ensure_decision_environment(organization_id)
    awx_token_id = _ensure_awx_token_id()
    rulebooks = _rulebooks_by_name(project_id)

    policies: List[Dict[str, Any]] = []
    for policy_key, definition in POLICY_DEFINITIONS.items():
        rulebook = rulebooks.get(_rulebook_name(policy_key))
        if not rulebook:
            raise EDAAutomationError(
                f"EDA rulebook '{_rulebook_name(policy_key)}' was not imported from project '{_project_name()}'."
            )
        activation = _ensure_activation(
            policy_key=policy_key,
            organization_id=organization_id,
            decision_environment_id=decision_environment_id,
            rulebook_id=int(rulebook["id"]),
            awx_token_id=awx_token_id,
        )
        policies.append(
            {
                "policy_key": policy_key,
                "name": str(activation.get("name") or definition["name"]),
                "activation_id": int(activation.get("id") or 0),
                "rulebook": _rulebook_name(policy_key),
                "status": str(activation.get("status") or "unknown"),
                "event_stream_urls": _activation_delivery_urls(policy_key, int(activation.get("id") or 0), activation),
            }
        )

    return {
        "configured": True,
        "mode": "eda-api",
        "organization": _organization_name(),
        "project_name": _project_name(),
        "project_id": project_id,
        "decision_environment_name": _decision_environment_name(),
        "decision_environment_id": decision_environment_id,
        "eda_url": _app_url() or _api_url(),
        "policies": policies,
    }


def status() -> Dict[str, Any]:
    if not enabled():
        return {"configured": False, "mode": "disabled", "live_configured": False, "policies": []}
    try:
        _request("GET", "/api/eda/v1/status/")
        organization_id = _organization_id()
        project = _find_named_item("/api/eda/v1/projects/", _project_name())
        decision_environment = _find_named_item("/api/eda/v1/decision-environments/", _decision_environment_name())
        policies = _policy_status()
        return {
            "configured": True,
            "mode": "eda-api",
            "live_configured": True,
            "eda_url": _app_url() or _api_url(),
            "organization": _organization_name(),
            "organization_id": organization_id,
            "project_name": _project_name(),
            "project_exists": project is not None,
            "project_import_state": str((project or {}).get("import_state") or ""),
            "project_import_error": str((project or {}).get("import_error") or ""),
            "decision_environment_name": _decision_environment_name(),
            "decision_environment_exists": decision_environment is not None,
            "bootstrapped": bool(project)
            and bool(decision_environment)
            and all(
                item.get("activation_exists")
                and item.get("enabled")
                and str(item.get("status") or "").lower() not in {"failed", "missing"}
                and bool(item.get("event_stream_urls"))
                for item in policies
            ),
            "policies": policies,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "configured": True,
            "mode": "eda-api",
            "live_configured": False,
            "error": str(exc),
            "eda_url": _app_url() or _api_url(),
            "organization": _organization_name(),
            "project_name": _project_name(),
            "decision_environment_name": _decision_environment_name(),
            "policies": policy_catalog(),
        }


def publish_event(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not enabled():
        return []

    results: List[Dict[str, Any]] = []
    for policy in _policy_status():
        if not policy.get("enabled"):
            continue
        for url in policy.get("event_stream_urls", []):
            if not url:
                continue
            response = requests.post(
                str(url),
                json=event,
                verify=_event_verify(),
                timeout=float(os.getenv("EDA_EVENT_TIMEOUT_SECONDS", "10")),
            )
            if response.status_code >= 400:
                raise EDAAutomationError(
                    f"EDA event delivery failed for {policy.get('name')}: {response.status_code} {response.text[:300]}"
                )
            results.append(
                {
                    "policy_key": str(policy.get("policy_key") or ""),
                    "name": str(policy.get("name") or ""),
                    "url": str(url),
                    "status_code": response.status_code,
                }
            )
    return results


def _request(
    method: str,
    path: str,
    expected_status: tuple[int, ...] = (200,),
    **kwargs: Any,
) -> Dict[str, Any]:
    response = requests.request(
        method,
        f"{_api_url()}{path}",
        auth=(_username(), _password()),
        verify=_api_verify(),
        timeout=float(os.getenv("EDA_API_TIMEOUT_SECONDS", "30")),
        headers={"Content-Type": "application/json", **kwargs.pop("headers", {})},
        **kwargs,
    )
    if response.status_code not in expected_status:
        raise EDAAutomationError(f"EDA request failed for {method} {path}: {response.status_code} {response.text[:400]}")
    if not response.text.strip():
        return {}
    try:
        return response.json()
    except ValueError as exc:  # pragma: no cover - defensive
        raise EDAAutomationError(f"EDA returned non-JSON content for {method} {path}.") from exc


def _api_url() -> str:
    return (
        os.getenv("EDA_API_URL", "").strip()
        or "http://aap-eda-api.aap.svc.cluster.local:8000"
    ).rstrip("/")


def _app_url() -> str:
    return (
        os.getenv("EDA_APP_URL", "").strip()
        or "https://aap-eda-aap.apps.ocp.4h2g6.sandbox195.opentlc.com"
    ).rstrip("/")


def _username() -> str:
    return os.getenv("EDA_USERNAME", "admin").strip() or "admin"


def _password() -> str:
    explicit = os.getenv("EDA_PASSWORD", "").strip()
    if explicit:
        return explicit
    namespace = os.getenv("EDA_PASSWORD_SECRET_NAMESPACE", "aap").strip() or "aap"
    name = os.getenv("EDA_PASSWORD_SECRET_NAME", "aap-eda-admin-password").strip() or "aap-eda-admin-password"
    key = os.getenv("EDA_PASSWORD_SECRET_KEY", "password").strip() or "password"
    return _read_kubernetes_secret_key(namespace, name, key)


def _api_verify() -> bool | str:
    verify_ssl = os.getenv("EDA_VERIFY_SSL", "").strip().lower()
    if verify_ssl in {"false", "0", "no"}:
        return False
    ca_path = os.getenv("EDA_CA_PATH", "").strip()
    if ca_path:
        return ca_path
    return True


def _event_verify() -> bool | str:
    verify_ssl = os.getenv("EDA_EVENT_VERIFY_SSL", "").strip().lower()
    if verify_ssl in {"false", "0", "no"}:
        return False
    return _api_verify()


def _organization_name() -> str:
    return os.getenv("EDA_ORGANIZATION", "Default").strip() or "Default"


def _organization_id() -> int:
    explicit = os.getenv("EDA_ORGANIZATION_ID", "").strip()
    if explicit.isdigit():
        return int(explicit)
    payload = _request("GET", "/api/eda/v1/organizations/", params={"name": _organization_name(), "page_size": 200})
    for item in payload.get("results", []):
        if str(item.get("name") or "") == _organization_name():
            return int(item["id"])
    raise EDAAutomationError(f"EDA organization '{_organization_name()}' was not found.")


def _project_name() -> str:
    return os.getenv("EDA_PROJECT_NAME", "IMS Incident Event Policies").strip() or "IMS Incident Event Policies"


def _project_url() -> str:
    return (
        os.getenv("EDA_PROJECT_URL", "").strip()
        or "http://gitea-http.gitea.svc.cluster.local:3000/gitadmin/IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI.git"
    )


def _project_branch() -> str:
    return os.getenv("EDA_PROJECT_BRANCH", "main").strip() or "main"


def _decision_environment_name() -> str:
    return os.getenv("EDA_DECISION_ENVIRONMENT_NAME", "IMS Incident Decisions").strip() or "IMS Incident Decisions"


def _decision_environment_image() -> str:
    return (
        os.getenv("EDA_DECISION_ENVIRONMENT_IMAGE", "").strip()
        or "registry.redhat.io/ansible-automation-platform-26/de-minimal-rhel9:latest"
    )


def _control_plane_url() -> str:
    return (
        os.getenv("EDA_CONTROL_PLANE_URL", "").strip()
        or "http://control-plane.ims-demo-lab.svc.cluster.local:8080"
    ).rstrip("/")


def _control_plane_api_key() -> str:
    return os.getenv("EDA_CONTROL_PLANE_API_KEY", "eda-token").strip() or "eda-token"


def _automation_actor() -> str:
    return os.getenv("EDA_AUTOMATION_ACTOR", "eda-automation").strip() or "eda-automation"


def _event_source_url(policy_key: str) -> str:
    return os.getenv(f"EDA_POLICY_{policy_key.upper()}_SOURCE_URL", f"eda://{policy_key}").strip() or f"eda://{policy_key}"


def _controller_url() -> str:
    return (
        os.getenv("AAP_CONTROLLER_URL", "").strip()
        or "http://aap-controller-service.aap.svc.cluster.local"
    ).rstrip("/")


def _controller_username() -> str:
    return os.getenv("AAP_CONTROLLER_USERNAME", "admin").strip() or "admin"


def _controller_password() -> str:
    explicit = os.getenv("AAP_CONTROLLER_PASSWORD", "").strip()
    if explicit:
        return explicit
    namespace = os.getenv("AAP_CONTROLLER_PASSWORD_SECRET_NAMESPACE", "aap").strip() or "aap"
    name = os.getenv("AAP_CONTROLLER_PASSWORD_SECRET_NAME", "aap-controller-admin-password").strip() or "aap-controller-admin-password"
    key = os.getenv("AAP_CONTROLLER_PASSWORD_SECRET_KEY", "password").strip() or "password"
    return _read_kubernetes_secret_key(namespace, name, key)


def _controller_verify() -> bool | str:
    if _controller_url().startswith("http://"):
        return False
    verify_ssl = os.getenv("AAP_CONTROLLER_VERIFY_SSL", "").strip().lower()
    if verify_ssl in {"false", "0", "no"}:
        return False
    ca_path = os.getenv("AAP_CONTROLLER_CA_PATH", "").strip()
    if ca_path:
        return ca_path
    return True


def _controller_organization_name() -> str:
    return os.getenv("AAP_ORGANIZATION", "Default").strip() or "Default"


def _controller_token_name() -> str:
    return os.getenv("EDA_CONTROLLER_TOKEN_NAME", "IMS EDA Controller Token").strip() or "IMS EDA Controller Token"


def _project_payload(organization_id: int) -> Dict[str, Any]:
    return {
        "name": _project_name(),
        "description": "Event-driven incident policies sourced from the cluster Git repository.",
        "organization_id": organization_id,
        "url": _project_url(),
        "verify_ssl": False if _project_url().startswith("http://") else True,
        "scm_type": "git",
        "scm_branch": _project_branch(),
    }


def _ensure_project(organization_id: int) -> int:
    existing = _find_named_item("/api/eda/v1/projects/", _project_name())
    desired = _project_payload(organization_id)
    if existing is None:
        project = _request("POST", "/api/eda/v1/projects/", expected_status=(200, 201), json=desired)
        return int(project["id"])
    patch: Dict[str, Any] = {}
    for field in ("description", "organization_id", "url", "verify_ssl", "scm_type", "scm_branch"):
        if existing.get(field) != desired[field]:
            patch[field] = desired[field]
    if patch:
        _request("PATCH", f"/api/eda/v1/projects/{existing['id']}/", expected_status=(200,), json=patch)
    return int(existing["id"])


def _sync_project(project_id: int) -> None:
    _request("POST", f"/api/eda/v1/projects/{project_id}/sync/", expected_status=(200, 201, 202, 409))
    deadline = time.time() + float(os.getenv("EDA_PROJECT_SYNC_TIMEOUT_SECONDS", "120"))
    last_state = "unknown"
    last_error = ""
    while time.time() < deadline:
        project = _request("GET", f"/api/eda/v1/projects/{project_id}/")
        if _rulebooks_by_name(project_id):
            return
        import_state = str(project.get("import_state") or "").strip().lower()
        import_error = str(project.get("import_error") or "").strip()
        last_state = import_state or "unknown"
        last_error = import_error
        if import_state in {"completed", "successful", "ready"}:
            if import_error:
                raise EDAAutomationError(
                    f"EDA project import completed with errors for '{_project_name()}': {import_error}"
                )
            return
        if import_state in {"failed", "error"}:
            raise EDAAutomationError(
                f"EDA project import failed for '{_project_name()}': {import_error or import_state}"
            )
        time.sleep(4)
    if last_error:
        raise EDAAutomationError(
            f"EDA project sync timed out for '{_project_name()}' while waiting for rulebooks: {last_error}"
        )
    raise EDAAutomationError(
        f"EDA project sync timed out for '{_project_name()}' with state '{last_state}'."
    )


def _ensure_decision_environment(organization_id: int) -> int:
    existing = _find_named_item("/api/eda/v1/decision-environments/", _decision_environment_name())
    desired = {
        "name": _decision_environment_name(),
        "description": "Decision environment for IMS incident event-driven policies.",
        "image_url": _decision_environment_image(),
        "organization_id": organization_id,
    }
    if existing is None:
        environment = _request("POST", "/api/eda/v1/decision-environments/", expected_status=(200, 201), json=desired)
        return int(environment["id"])
    patch: Dict[str, Any] = {}
    for field in ("description", "image_url", "organization_id"):
        if existing.get(field) != desired[field]:
            patch[field] = desired[field]
    if patch:
        _request("PATCH", f"/api/eda/v1/decision-environments/{existing['id']}/", expected_status=(200,), json=patch)
    return int(existing["id"])


def _rulebooks_by_name(project_id: int) -> Dict[str, Dict[str, Any]]:
    payload = _request("GET", "/api/eda/v1/rulebooks/", params={"page_size": 200})
    matches: Dict[str, Dict[str, Any]] = {}
    for item in payload.get("results", []):
        if not isinstance(item, dict):
            continue
        if int(item.get("project_id") or 0) != project_id:
            continue
        name = str(item.get("name") or "")
        if name:
            matches[name] = item
    return matches


def _rulebook_name(policy_key: str) -> str:
    return str(POLICY_DEFINITIONS[policy_key]["rulebook"]).rsplit("/", 1)[-1]


def _ensure_activation(
    policy_key: str,
    organization_id: int,
    decision_environment_id: int,
    rulebook_id: int,
    awx_token_id: int,
) -> Dict[str, Any]:
    definition = POLICY_DEFINITIONS[policy_key]
    controller_template_key = str(definition.get("controller_template_key") or "").strip()
    if not controller_template_key:
        raise EDAAutomationError(f"EDA policy '{policy_key}' does not declare a controller callback template.")
    desired = {
        "name": definition["name"],
        "description": definition["description"],
        "is_enabled": True,
        "decision_environment_id": decision_environment_id,
        "rulebook_id": rulebook_id,
        "organization_id": organization_id,
        "restart_policy": "always",
        "log_level": "info",
        "awx_token_id": awx_token_id,
        "extra_var": json.dumps(
            {
                "control_plane_url": _control_plane_url(),
                "control_plane_api_key": _control_plane_api_key(),
                "approved_by": _automation_actor(),
                "source_url": _event_source_url(policy_key),
                "policy_key": policy_key,
                "policy_name": definition["name"],
                "controller_job_template_name": controller_callback_template_name(controller_template_key),
                "controller_organization_name": _controller_organization_name(),
            }
        ),
    }
    existing = _find_named_item("/api/eda/v1/activations/", definition["name"])
    if existing is None:
        return _request("POST", "/api/eda/v1/activations/", expected_status=(200, 201), json=desired)

    patch: Dict[str, Any] = {}
    decision_environment = existing.get("decision_environment") if isinstance(existing.get("decision_environment"), dict) else {}
    rulebook = existing.get("rulebook") if isinstance(existing.get("rulebook"), dict) else {}
    organization = existing.get("organization") if isinstance(existing.get("organization"), dict) else {}
    comparisons = {
        "description": existing.get("description"),
        "decision_environment_id": decision_environment.get("id"),
        "rulebook_id": rulebook.get("id"),
        "organization_id": organization.get("id"),
        "restart_policy": existing.get("restart_policy"),
        "log_level": existing.get("log_level"),
        "awx_token_id": existing.get("awx_token_id"),
        "extra_var": existing.get("extra_var"),
    }
    for field, current in comparisons.items():
        if current != desired[field]:
            patch[field] = desired[field]
    if patch:
        activation_id = int(existing["id"])
        if bool(existing.get("is_enabled")):
            _request(
                "POST",
                f"/api/eda/v1/activations/{activation_id}/disable/",
                expected_status=(200, 201, 202, 204, 409),
            )
            _wait_for_activation_stopped(activation_id)
        try:
            _request("PATCH", f"/api/eda/v1/activations/{activation_id}/", expected_status=(200,), json=patch)
            existing = _request("GET", f"/api/eda/v1/activations/{activation_id}/")
        except EDAAutomationError as exc:
            if ": 400 " not in str(exc) and ": 409 " not in str(exc):
                raise
            return _replace_activation(activation_id, str(definition["name"]), desired)
    if desired["is_enabled"] and not bool(existing.get("is_enabled")):
        _request("POST", f"/api/eda/v1/activations/{existing['id']}/enable/", expected_status=(200, 201, 202))
    return _request("GET", f"/api/eda/v1/activations/{existing['id']}/")


def _policy_status() -> List[Dict[str, Any]]:
    payload = _request("GET", "/api/eda/v1/activations/", params={"page_size": 200})
    activations = {
        str(item.get("name") or ""): item
        for item in payload.get("results", [])
        if isinstance(item, dict)
    }
    policies: List[Dict[str, Any]] = []
    for item in policy_catalog():
        activation = activations.get(str(item["name"]))
        activation_id = int(activation.get("id") or 0) if activation else 0
        policies.append(
            item
            | {
                "activation_exists": activation is not None,
                "activation_id": activation_id or None,
                "enabled": bool(activation.get("is_enabled")) if activation else False,
                "status": str(activation.get("status") or ("ready" if activation else "missing")),
                "event_stream_urls": _activation_delivery_urls(str(item["policy_key"]), activation_id, activation),
            }
        )
    return policies


def _controller_request(
    method: str,
    path: str,
    expected_status: tuple[int, ...] = (200,),
    **kwargs: Any,
) -> Dict[str, Any]:
    response = requests.request(
        method,
        f"{_controller_url()}{path}",
        auth=(_controller_username(), _controller_password()),
        verify=_controller_verify(),
        timeout=float(os.getenv("AAP_CONTROLLER_TIMEOUT_SECONDS", "30")),
        headers={"Content-Type": "application/json", **kwargs.pop("headers", {})},
        **kwargs,
    )
    if response.status_code not in expected_status:
        raise EDAAutomationError(
            f"Controller request failed for {method} {path}: {response.status_code} {response.text[:400]}"
        )
    if not response.text.strip():
        return {}
    try:
        return response.json()
    except ValueError as exc:  # pragma: no cover - defensive
        raise EDAAutomationError(f"Controller returned non-JSON content for {method} {path}.") from exc


def _ensure_awx_token_id() -> int:
    name = _controller_token_name()
    payload = _request("GET", "/api/eda/v1/users/me/awx-tokens/", params={"page_size": 200})
    for item in payload.get("results", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "") == name:
            return int(item["id"])

    controller_user = _controller_request("GET", "/api/v2/me/")
    results = controller_user.get("results") if isinstance(controller_user.get("results"), list) else []
    if not results:
        raise EDAAutomationError("AAP controller did not return the current user needed to create an EDA controller token.")
    controller_user_id = int(results[0]["id"])

    existing_tokens = _controller_request(
        "GET",
        f"/api/v2/users/{controller_user_id}/personal_tokens/",
        params={"page_size": 200},
    )
    for item in existing_tokens.get("results", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("description") or "") == name:
            _controller_request("DELETE", f"/api/v2/tokens/{item['id']}/", expected_status=(204,))

    created_token = _controller_request(
        "POST",
        f"/api/v2/users/{controller_user_id}/personal_tokens/",
        expected_status=(200, 201),
        json={"description": name, "application": None, "scope": "write"},
    )
    token_value = str(created_token.get("token") or "").strip()
    if not token_value:
        raise EDAAutomationError("AAP controller did not return a token value for Event-Driven Ansible.")

    created_awx_token = _request(
        "POST",
        "/api/eda/v1/users/me/awx-tokens/",
        expected_status=(200, 201),
        json={
            "name": name,
            "description": "Controller token used by EDA run_job_template actions for IMS incident automation.",
            "token": token_value,
        },
    )
    return int(created_awx_token["id"])

def _wait_for_activation_stopped(activation_id: int) -> None:
    deadline = time.time() + float(os.getenv("EDA_ACTIVATION_STOP_TIMEOUT_SECONDS", "90"))
    while time.time() < deadline:
        activation = _request("GET", f"/api/eda/v1/activations/{activation_id}/")
        if not bool(activation.get("is_enabled")) and str(activation.get("status") or "").lower() in {"stopped", "disabled"}:
            return
        time.sleep(3)
    raise EDAAutomationError(
        f"EDA activation {activation_id} did not stop after disable within the configured timeout."
    )


def _replace_activation(activation_id: int, activation_name: str, desired: Dict[str, Any]) -> Dict[str, Any]:
    _request("DELETE", f"/api/eda/v1/activations/{activation_id}/", expected_status=(200, 202, 204))
    deadline = time.time() + float(os.getenv("EDA_ACTIVATION_RECREATE_TIMEOUT_SECONDS", "90"))
    while time.time() < deadline:
        if _find_named_item("/api/eda/v1/activations/", activation_name) is None:
            return _request("POST", "/api/eda/v1/activations/", expected_status=(200, 201), json=desired)
        time.sleep(3)
    raise EDAAutomationError(f"EDA activation '{activation_name}' was not deleted before recreate timed out.")


def _activation_delivery_urls(
    policy_key: str,
    activation_id: int,
    activation: Dict[str, Any] | None = None,
) -> List[str]:
    event_streams = activation.get("event_streams") if isinstance(activation, dict) else None
    service_name = activation.get("k8s_service_name") if isinstance(activation, dict) else None
    if activation_id > 0 and (
        activation is None or not isinstance(event_streams, list) or not str(service_name or "").strip()
    ):
        activation = _request("GET", f"/api/eda/v1/activations/{activation_id}/")
    if not isinstance(activation, dict):
        return []
    event_streams = activation.get("event_streams") if isinstance(activation.get("event_streams"), list) else []
    urls = [str(item.get("url") or "").strip() for item in event_streams if str(item.get("url") or "").strip()]
    service_name = str(activation.get("k8s_service_name") or "").strip()
    webhook_port = int(POLICY_DEFINITIONS.get(policy_key, {}).get("webhook_port") or 0)
    namespace = os.getenv("EDA_SERVICE_NAMESPACE", "aap").strip() or "aap"
    if service_name and webhook_port > 0:
        urls.append(f"http://{service_name}.{namespace}.svc.cluster.local:{webhook_port}")
    # Preserve order while removing duplicates.
    return list(dict.fromkeys(urls))


def _find_named_item(path: str, name: str) -> Dict[str, Any] | None:
    payload = _request("GET", path, params={"name": name, "page_size": 200})
    for item in payload.get("results", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("name") or "") == name:
            return item
    return None


def _kubernetes_api_url() -> str:
    host = os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc").strip() or "kubernetes.default.svc"
    port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443").strip() or "443"
    return f"https://{host}:{port}"


def _kubernetes_request(
    method: str,
    path: str,
    expected_status: tuple[int, ...] = (200,),
    **kwargs: Any,
) -> Dict[str, Any]:
    token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text(encoding="utf-8").strip()
    ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    response = requests.request(
        method,
        f"{_kubernetes_api_url()}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json", **kwargs.pop("headers", {})},
        verify=ca_path,
        timeout=float(os.getenv("EDA_KUBERNETES_TIMEOUT_SECONDS", "15")),
        **kwargs,
    )
    if response.status_code not in expected_status:
        raise EDAAutomationError(
            f"Kubernetes request failed for {method} {path}: {response.status_code} {response.text[:400]}"
        )
    if not response.text.strip():
        return {}
    return response.json()


def _read_kubernetes_secret_key(namespace: str, name: str, key: str) -> str:
    payload = _kubernetes_request("GET", f"/api/v1/namespaces/{namespace}/secrets/{name}")
    data = payload.get("data") or {}
    encoded = str(data.get(key) or "").strip()
    if not encoded:
        raise EDAAutomationError(f"Kubernetes secret {namespace}/{name} does not contain key '{key}'.")
    return base64.b64decode(encoded).decode("utf-8").strip()
