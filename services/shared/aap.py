from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

import requests


class AAPAutomationError(RuntimeError):
    pass


ACTION_DEFINITIONS: Dict[str, Dict[str, str]] = {
    "scale_scscf": {
        "job_template_name": "IMS Scale S-CSCF Path",
        "playbook": "automation/ansible/playbooks/scale-scscf.yaml",
        "description": "Scale the S-CSCF deployment after operator approval.",
        "cases": "registration_storm,call_setup_timeout,server_internal_error",
    },
    "rate_limit_pcscf": {
        "job_template_name": "IMS Rate Limit P-CSCF Ingress",
        "playbook": "automation/ansible/playbooks/rate-limit-pcscf.yaml",
        "description": "Apply the low-risk P-CSCF ingress guardrail through AAP after approval.",
        "cases": "registration_storm,retransmission_spike,network_degradation",
    },
    "quarantine_imsi": {
        "job_template_name": "IMS Quarantine Subscriber or Source",
        "playbook": "automation/ansible/playbooks/quarantine-imsi.yaml",
        "description": "Record a quarantine request for the offending subscriber or traffic source.",
        "cases": "authentication_failure,registration_failure,malformed_sip",
    },
}

TERMINAL_JOB_STATUSES = {"successful", "failed", "error", "canceled"}


def action_supported(action: str) -> bool:
    return _enabled() and action in ACTION_DEFINITIONS


def action_catalog() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for action, definition in ACTION_DEFINITIONS.items():
        items.append(
            {
                "action": action,
                "name": _job_template_name(action),
                "playbook": definition["playbook"],
                "description": definition["description"],
                "cases": [item for item in str(definition.get("cases") or "").split(",") if item],
                "trigger_modes": ["manual_ui"],
            }
        )
    return items


def bootstrap_resources() -> Dict[str, Any]:
    if not _enabled():
        return {"configured": False, "mode": "disabled", "actions": []}

    organization_id = _require_object_id("/api/v2/organizations/", _organization_name(), "organization")
    inventory_id = _ensure_inventory(organization_id)
    project_id = _ensure_project(organization_id)
    _sync_project(project_id)
    kubernetes_credential_id = _ensure_kubernetes_credential(organization_id)
    actions: List[Dict[str, Any]] = []
    for action, definition in ACTION_DEFINITIONS.items():
        template_id = _ensure_job_template(
            organization_id=organization_id,
            inventory_id=inventory_id,
            project_id=project_id,
            credential_id=kubernetes_credential_id,
            action=action,
            playbook=definition["playbook"],
            description=definition["description"],
        )
        actions.append(
            {
                "action": action,
                "name": _job_template_name(action),
                "job_template_id": template_id,
                "playbook": definition["playbook"],
            }
        )
    return {
        "configured": True,
        "mode": "controller-api",
        "organization": _organization_name(),
        "inventory_name": _inventory_name(),
        "inventory_id": inventory_id,
        "project_name": _project_name(),
        "project_id": project_id,
        "kubernetes_credential_name": _kubernetes_credential_name(),
        "kubernetes_credential_id": kubernetes_credential_id,
        "actions": actions,
    }


def launch_action(action: str, extra_vars: Dict[str, Any]) -> Dict[str, Any]:
    definition = ACTION_DEFINITIONS.get(action)
    if not definition:
        raise AAPAutomationError(f"AAP automation is not configured for action '{action}'.")

    organization = _require_object_id("/api/v2/organizations/", _organization_name(), "organization")
    inventory_id = _ensure_inventory(organization)
    project_id = _ensure_project(organization)
    _sync_project(project_id)
    kubernetes_credential_id = _ensure_kubernetes_credential(organization)
    template_id = _ensure_job_template(
        organization_id=organization,
        inventory_id=inventory_id,
        project_id=project_id,
        credential_id=kubernetes_credential_id,
        action=action,
        playbook=definition["playbook"],
        description=definition["description"],
    )
    payload = _request(
        "POST",
        f"/api/v2/job_templates/{template_id}/launch/",
        expected_status=(200, 201, 202),
        json={"extra_vars": extra_vars},
    )
    job_id = int(payload.get("job") or payload.get("id") or 0)
    if job_id <= 0:
        raise AAPAutomationError(f"AAP did not return a job id for action '{action}'.")
    return {
        "job_id": job_id,
        "job_template_id": template_id,
        "job_template_name": _job_template_name(action),
        "action": action,
        "status": str(payload.get("status") or "pending"),
        "job_api_url": _absolute_url(f"/api/v2/jobs/{job_id}/"),
        "job_stdout_url": _absolute_url(f"/api/v2/jobs/{job_id}/stdout/?format=txt_download"),
        "controller_app_url": _controller_app_url(),
    }


def wait_for_job(job_id: int, timeout_seconds: int = 300, poll_interval_seconds: int = 5) -> Dict[str, Any]:
    deadline = time.time() + max(timeout_seconds, 1)
    interval = max(poll_interval_seconds, 1)
    last_payload: Dict[str, Any] | None = None
    while time.time() < deadline:
        payload = _request("GET", f"/api/v2/jobs/{job_id}/")
        last_payload = payload
        status = str(payload.get("status") or "").strip().lower()
        if status in TERMINAL_JOB_STATUSES:
            stdout = _request_stdout(job_id)
            return payload | {"stdout": stdout}
        time.sleep(interval)
    if last_payload is None:
        last_payload = _request("GET", f"/api/v2/jobs/{job_id}/")
    return last_payload | {"stdout": _request_stdout(job_id), "status": str(last_payload.get("status") or "timeout")}


def launch_runner_job(action: str, extra_vars: Dict[str, Any]) -> Dict[str, Any]:
    definition = ACTION_DEFINITIONS.get(action)
    if not definition:
        raise AAPAutomationError(f"AAP runner fallback is not configured for action '{action}'.")
    incident_id = str(extra_vars.get("incident_id") or "incident")
    suffix = incident_id.replace("-", "")[:8] or "demo"
    timestamp = str(int(time.time()))[-8:]
    job_name = f"ims-{action.replace('_', '-')[:20]}-{suffix}-{timestamp}"[:63].rstrip("-")
    namespace = _runner_namespace()
    payload = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": namespace,
            "labels": {
                "app.kubernetes.io/name": "ims-remediation-runner",
                "ims.demo/action": action,
                "ims.demo/incident-id": incident_id,
            },
        },
        "spec": {
            "backoffLimit": 0,
            "ttlSecondsAfterFinished": 1800,
            "template": {
                "metadata": {
                    "labels": {
                        "job-name": job_name,
                        "ims.demo/action": action,
                    }
                },
                "spec": {
                    "serviceAccountName": _runner_service_account(),
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "ansible-runner",
                            "image": _runner_image(),
                            "imagePullPolicy": "IfNotPresent",
                            "env": [
                                {"name": "AAP_GIT_URL", "value": _project_scm_url()},
                                {"name": "AAP_GIT_BRANCH", "value": _project_branch()},
                                {"name": "AAP_PLAYBOOK_PATH", "value": definition["playbook"]},
                                {"name": "EXTRA_VARS_JSON", "value": json.dumps(extra_vars)},
                            ],
                            "command": ["/bin/bash", "-lc"],
                            "args": [
                                "set -euo pipefail\n"
                                "workdir=/tmp/ims-remediation\n"
                                "rm -rf \"$workdir\"\n"
                                "git clone --depth 1 --branch \"$AAP_GIT_BRANCH\" \"$AAP_GIT_URL\" \"$workdir\"\n"
                                "cd \"$workdir\"\n"
                                "printf '%s' \"$EXTRA_VARS_JSON\" >/tmp/extra-vars.json\n"
                                "ansible-playbook \"$AAP_PLAYBOOK_PATH\" -i localhost, -c local -e @/tmp/extra-vars.json\n"
                            ],
                        }
                    ],
                },
            },
        },
    }
    _kubernetes_request(
        "POST",
        f"/apis/batch/v1/namespaces/{namespace}/jobs",
        expected_status=(200, 201),
        json=payload,
    )
    return {
        "job_name": job_name,
        "job_namespace": namespace,
        "status": "created",
        "controller_app_url": _controller_app_url(),
    }


def wait_for_runner_job(job_name: str, namespace: str, timeout_seconds: int = 300, poll_interval_seconds: int = 5) -> Dict[str, Any]:
    deadline = time.time() + max(timeout_seconds, 1)
    interval = max(poll_interval_seconds, 1)
    last_payload: Dict[str, Any] | None = None
    while time.time() < deadline:
        payload = _kubernetes_request("GET", f"/apis/batch/v1/namespaces/{namespace}/jobs/{job_name}")
        last_payload = payload
        status = payload.get("status") or {}
        if int(status.get("succeeded") or 0) > 0:
            return payload | {"status": "successful", "stdout": _runner_job_logs(namespace, job_name)}
        if int(status.get("failed") or 0) > 0:
            return payload | {"status": "failed", "stdout": _runner_job_logs(namespace, job_name)}
        time.sleep(interval)
    if last_payload is None:
        last_payload = _kubernetes_request("GET", f"/apis/batch/v1/namespaces/{namespace}/jobs/{job_name}")
    return last_payload | {"status": "timeout", "stdout": _runner_job_logs(namespace, job_name)}


def controller_status() -> Dict[str, Any]:
    if not _enabled():
        return {"configured": False, "mode": "disabled", "live_configured": False, "actions": []}
    try:
        payload = _request("GET", "/api/v2/ping/")
        project_payload = _request("GET", "/api/v2/projects/", params={"name": _project_name(), "page_size": 200})
        template_payload = _request("GET", "/api/v2/job_templates/", params={"page_size": 200})
        credential_payload = _request("GET", "/api/v2/credentials/", params={"name": _kubernetes_credential_name(), "page_size": 200})
        project_exists = any(str(item.get("name") or "") == _project_name() for item in project_payload.get("results", []))
        credential_exists = any(
            str(item.get("name") or "") == _kubernetes_credential_name() for item in credential_payload.get("results", [])
        )
        existing_templates = {
            str(item.get("name") or ""): int(item.get("id") or 0) for item in template_payload.get("results", [])
        }
        actions = []
        for item in action_catalog():
            template_id = existing_templates.get(str(item["name"]))
            actions.append(item | {"template_exists": bool(template_id), "job_template_id": template_id})
        return {
            "configured": True,
            "mode": "controller-api",
            "live_configured": True,
            "version": payload.get("version"),
            "controller_url": _controller_app_url() or _controller_url(),
            "project_name": _project_name(),
            "inventory_name": _inventory_name(),
            "kubernetes_credential_name": _kubernetes_credential_name(),
            "kubernetes_credential_exists": credential_exists,
            "project_exists": project_exists,
            "bootstrapped": project_exists and credential_exists and all(bool(item["template_exists"]) for item in actions),
            "actions": actions,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "configured": True,
            "mode": "controller-api",
            "live_configured": False,
            "error": str(exc),
            "controller_url": _controller_app_url() or _controller_url(),
            "project_name": _project_name(),
            "inventory_name": _inventory_name(),
            "kubernetes_credential_name": _kubernetes_credential_name(),
            "actions": action_catalog(),
        }


def _enabled() -> bool:
    return os.getenv("AAP_AUTOMATION_ENABLED", "true").strip().lower() == "true"


def _controller_url() -> str:
    return (
        os.getenv("AAP_CONTROLLER_URL", "").strip()
        or "http://aap-controller-service.aap.svc.cluster.local"
    ).rstrip("/")


def _controller_app_url() -> str:
    return (
        os.getenv("AAP_CONTROLLER_APP_URL", "").strip()
        or "https://aap-controller-aap.apps.ocp.4h2g6.sandbox195.opentlc.com"
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
    verify_ssl = os.getenv("AAP_CONTROLLER_VERIFY_SSL", "").strip().lower()
    if verify_ssl in {"false", "0", "no"}:
        return False
    ca_path = os.getenv("AAP_CONTROLLER_CA_PATH", "").strip()
    if ca_path:
        return ca_path
    return True


def _organization_name() -> str:
    return os.getenv("AAP_ORGANIZATION", "Default").strip() or "Default"


def _inventory_name() -> str:
    return os.getenv("AAP_INVENTORY_NAME", "IMS Incident Local Inventory").strip() or "IMS Incident Local Inventory"


def _inventory_host_name() -> str:
    return os.getenv("AAP_INVENTORY_HOST", "localhost").strip() or "localhost"


def _project_name() -> str:
    return os.getenv("AAP_PROJECT_NAME", "IMS Incident Automation").strip() or "IMS Incident Automation"


def _project_scm_url() -> str:
    return (
        os.getenv("AAP_PROJECT_SCM_URL", "").strip()
        or "http://gitea-http.gitea.svc.cluster.local:3000/gitadmin/IMS-Anomaly-Detection-with-Red-Hat-OpenShift-AI.git"
    )


def _project_branch() -> str:
    return os.getenv("AAP_PROJECT_BRANCH", "main").strip() or "main"


def _kubernetes_credential_name() -> str:
    return (
        os.getenv("AAP_KUBERNETES_CREDENTIAL_NAME", "").strip()
        or "IMS OpenShift API Credential"
    )


def _kubernetes_service_account_namespace() -> str:
    return os.getenv("AAP_KUBERNETES_SERVICE_ACCOUNT_NAMESPACE", "aap").strip() or "aap"


def _kubernetes_service_account_name() -> str:
    return os.getenv("AAP_KUBERNETES_SERVICE_ACCOUNT_NAME", "aap-controller").strip() or "aap-controller"


def _kubernetes_token_expiration_seconds() -> int:
    raw_value = os.getenv("AAP_KUBERNETES_TOKEN_EXPIRATION_SECONDS", "3600").strip()
    try:
        value = int(raw_value)
    except ValueError:
        return 3600
    return value if value > 0 else 3600


def _kubernetes_token_audience() -> str:
    return os.getenv("AAP_KUBERNETES_TOKEN_AUDIENCE", "").strip()


def _runner_namespace() -> str:
    return os.getenv("AAP_RUNNER_NAMESPACE", "aap").strip() or "aap"


def _runner_service_account() -> str:
    return os.getenv("AAP_RUNNER_SERVICE_ACCOUNT", "aap-controller").strip() or "aap-controller"


def _runner_image() -> str:
    return (
        os.getenv("AAP_EXECUTION_ENVIRONMENT_IMAGE", "").strip()
        or "registry.redhat.io/ansible-automation-platform-26/ee-supported-rhel9@sha256:fe0982d489065a2a287fe076873cf1faa5410c0879def91bbc756280e924118d"
    )


def _job_template_name(action: str) -> str:
    definition = ACTION_DEFINITIONS.get(action) or {}
    env_name = f"AAP_JOB_TEMPLATE_{action.upper()}"
    return os.getenv(env_name, definition.get("job_template_name", action)).strip() or action


def _absolute_url(path: str) -> str:
    return f"{_controller_app_url()}{path}"


def _request(
    method: str,
    path: str,
    expected_status: tuple[int, ...] = (200,),
    **kwargs: Any,
) -> Dict[str, Any]:
    url = f"{_controller_url()}{path}"
    response = requests.request(
        method,
        url,
        auth=(_controller_username(), _controller_password()),
        verify=_controller_verify(),
        timeout=float(os.getenv("AAP_CONTROLLER_TIMEOUT_SECONDS", "30")),
        headers={"Content-Type": "application/json", **kwargs.pop("headers", {})},
        **kwargs,
    )
    if response.status_code not in expected_status:
        raise AAPAutomationError(f"AAP request failed for {method} {path}: {response.status_code} {response.text[:400]}")
    if not response.text.strip():
        return {}
    try:
        return response.json()
    except ValueError as exc:  # pragma: no cover - defensive
        raise AAPAutomationError(f"AAP returned non-JSON content for {method} {path}.") from exc


def _request_stdout(job_id: int) -> str:
    response = requests.get(
        f"{_controller_url()}/api/v2/jobs/{job_id}/stdout/?format=txt_download",
        auth=(_controller_username(), _controller_password()),
        verify=_controller_verify(),
        timeout=float(os.getenv("AAP_CONTROLLER_TIMEOUT_SECONDS", "30")),
    )
    if response.status_code >= 400:
        return response.text.strip()[:400]
    return response.text.strip()


def _require_object_id(path: str, name: str, label: str) -> int:
    payload = _request("GET", path, params={"name": name, "page_size": 200})
    for item in payload.get("results", []):
        if str(item.get("name") or "") == name:
            return int(item["id"])
    raise AAPAutomationError(f"AAP {label} '{name}' was not found.")


def _ensure_inventory(organization_id: int) -> int:
    name = _inventory_name()
    payload = _request("GET", "/api/v2/inventories/", params={"name": name, "page_size": 200})
    inventory = next((item for item in payload.get("results", []) if str(item.get("name") or "") == name), None)
    desired = {
        "name": name,
        "organization": organization_id,
        "description": "Local execution inventory for operator-approved IMS remediation playbooks.",
    }
    if inventory is None:
        inventory = _request("POST", "/api/v2/inventories/", expected_status=(200, 201), json=desired)
    _ensure_inventory_host(int(inventory["id"]))
    return int(inventory["id"])


def _ensure_inventory_host(inventory_id: int) -> None:
    host_name = _inventory_host_name()
    payload = _request("GET", f"/api/v2/inventories/{inventory_id}/hosts/", params={"name": host_name, "page_size": 200})
    for item in payload.get("results", []):
        if str(item.get("name") or "") == host_name:
            return
    _request(
        "POST",
        f"/api/v2/inventories/{inventory_id}/hosts/",
        expected_status=(200, 201),
        json={
            "name": host_name,
            "description": "Local execution target inside the AAP execution environment.",
            "variables": "ansible_connection: local\nansible_python_interpreter: /usr/bin/python3\n",
        },
    )


def _ensure_project(organization_id: int) -> int:
    name = _project_name()
    payload = _request("GET", "/api/v2/projects/", params={"name": name, "page_size": 200})
    project = next((item for item in payload.get("results", []) if str(item.get("name") or "") == name), None)
    desired = {
        "name": name,
        "organization": organization_id,
        "description": "IMS incident remediation automation sourced from the cluster Git repository.",
        "scm_type": "git",
        "scm_url": _project_scm_url(),
        "scm_branch": _project_branch(),
        "scm_update_on_launch": True,
        "allow_override": False,
    }
    if project is None:
        project = _request("POST", "/api/v2/projects/", expected_status=(200, 201), json=desired)
        return int(project["id"])
    patch: Dict[str, Any] = {}
    for field in ("scm_url", "scm_branch", "scm_update_on_launch", "allow_override", "description"):
        if project.get(field) != desired[field]:
            patch[field] = desired[field]
    if patch:
        _request("PATCH", f"/api/v2/projects/{project['id']}/", json=patch)
    return int(project["id"])


def _sync_project(project_id: int) -> None:
    payload = _request(
        "POST",
        f"/api/v2/projects/{project_id}/update/",
        expected_status=(200, 202),
        json={},
    )
    update_id = int(payload.get("project_update") or payload.get("id") or 0)
    if update_id <= 0:
        return

    deadline = time.time() + float(os.getenv("AAP_PROJECT_SYNC_TIMEOUT_SECONDS", "120"))
    while time.time() < deadline:
        update = _request("GET", f"/api/v2/project_updates/{update_id}/")
        status = str(update.get("status") or "").strip().lower()
        if status == "successful":
            return
        if status in {"failed", "error", "canceled"}:
            raise AAPAutomationError(
                f"AAP project sync failed for '{_project_name()}': {update.get('result_traceback') or status}"
            )
        time.sleep(4)
    raise AAPAutomationError(f"AAP project sync timed out for '{_project_name()}'.")


def _kubernetes_credential_type_id() -> int:
    payload = _request("GET", "/api/v2/credential_types/", params={"kind": "kubernetes", "page_size": 200})
    for item in payload.get("results", []):
        if str(item.get("kind") or "") == "kubernetes":
            return int(item["id"])
    raise AAPAutomationError("AAP does not expose the built-in Kubernetes credential type.")


def _current_cluster_ca() -> str:
    return Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt").read_text(encoding="utf-8").strip()


def _issue_service_account_token() -> str:
    spec: Dict[str, Any] = {"expirationSeconds": _kubernetes_token_expiration_seconds()}
    audience = _kubernetes_token_audience()
    if audience:
        spec["audiences"] = [audience]
    payload = _kubernetes_request(
        "POST",
        (
            f"/api/v1/namespaces/{_kubernetes_service_account_namespace()}"
            f"/serviceaccounts/{_kubernetes_service_account_name()}/token"
        ),
        expected_status=(200, 201),
        json={
            "apiVersion": "authentication.k8s.io/v1",
            "kind": "TokenRequest",
            "spec": spec,
        },
    )
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    token = str(status.get("token") or "").strip()
    if not token:
        raise AAPAutomationError(
            "Kubernetes did not return a bearer token for "
            f"{_kubernetes_service_account_namespace()}/{_kubernetes_service_account_name()}."
        )
    return token


def _ensure_kubernetes_credential(organization_id: int) -> int:
    name = _kubernetes_credential_name()
    payload = _request("GET", "/api/v2/credentials/", params={"name": name, "page_size": 200})
    credential = next((item for item in payload.get("results", []) if str(item.get("name") or "") == name), None)
    credential_type_id = _kubernetes_credential_type_id()
    desired_inputs = {
        "host": _kubernetes_api_url(),
        "bearer_token": _issue_service_account_token(),
        "verify_ssl": True,
        "ssl_ca_cert": _current_cluster_ca(),
    }
    desired = {
        "name": name,
        "description": (
            "Bearer-token credential for in-cluster automation jobs targeting the "
            f"{_kubernetes_service_account_namespace()}/{_kubernetes_service_account_name()} service account."
        ),
        "organization": organization_id,
        "credential_type": credential_type_id,
        "inputs": desired_inputs,
    }
    if credential is None:
        credential = _request("POST", "/api/v2/credentials/", expected_status=(200, 201), json=desired)
        return int(credential["id"])
    if int(credential.get("credential_type") or 0) not in {0, credential_type_id}:
        raise AAPAutomationError(
            f"AAP credential '{name}' already exists with an unexpected credential type."
        )
    patch: Dict[str, Any] = {"inputs": desired_inputs}
    for field in ("description", "organization"):
        if credential.get(field) != desired[field]:
            patch[field] = desired[field]
    if patch:
        _request("PATCH", f"/api/v2/credentials/{credential['id']}/", json=patch)
    return int(credential["id"])


def _ensure_job_template(
    organization_id: int,
    inventory_id: int,
    project_id: int,
    credential_id: int,
    action: str,
    playbook: str,
    description: str,
) -> int:
    name = _job_template_name(action)
    payload = _request("GET", "/api/v2/job_templates/", params={"name": name, "page_size": 200})
    template = next((item for item in payload.get("results", []) if str(item.get("name") or "") == name), None)
    desired = {
        "name": name,
        "description": description,
        "job_type": "run",
        "inventory": inventory_id,
        "project": project_id,
        "organization": organization_id,
        "playbook": playbook,
        "ask_variables_on_launch": True,
        "verbosity": 1,
    }
    if template is None:
        template = _request("POST", "/api/v2/job_templates/", expected_status=(200, 201), json=desired)
        template_id = int(template["id"])
        _ensure_job_template_credential(template_id, credential_id)
        return template_id
    patch: Dict[str, Any] = {}
    for field in ("description", "inventory", "project", "organization", "playbook", "ask_variables_on_launch", "verbosity"):
        if template.get(field) != desired[field]:
            patch[field] = desired[field]
    if patch:
        _request("PATCH", f"/api/v2/job_templates/{template['id']}/", json=patch)
    template_id = int(template["id"])
    _ensure_job_template_credential(template_id, credential_id)
    return template_id


def _ensure_job_template_credential(template_id: int, credential_id: int) -> None:
    payload = _request("GET", f"/api/v2/job_templates/{template_id}/credentials/", params={"page_size": 200})
    for item in payload.get("results", []):
        if int(item.get("id") or 0) == credential_id:
            return
    _request(
        "POST",
        f"/api/v2/job_templates/{template_id}/credentials/",
        expected_status=(200, 201, 204),
        json={"id": credential_id},
    )


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
        timeout=float(os.getenv("AAP_KUBERNETES_TIMEOUT_SECONDS", "15")),
        **kwargs,
    )
    if response.status_code not in expected_status:
        raise AAPAutomationError(
            f"Kubernetes request failed for {method} {path}: {response.status_code} {response.text[:400]}"
        )
    if not response.text.strip():
        return {}
    return response.json()


def _runner_job_logs(namespace: str, job_name: str) -> str:
    payload = _kubernetes_request(
        "GET",
        f"/api/v1/namespaces/{namespace}/pods",
        params={"labelSelector": f"job-name={job_name}"},
    )
    items = payload.get("items") or []
    if not items:
        return ""
    pod_name = str(items[0].get("metadata", {}).get("name") or "")
    if not pod_name:
        return ""
    token = Path("/var/run/secrets/kubernetes.io/serviceaccount/token").read_text(encoding="utf-8").strip()
    ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    response = requests.get(
        f"{_kubernetes_api_url()}/api/v1/namespaces/{namespace}/pods/{pod_name}/log",
        headers={"Authorization": f"Bearer {token}", "Accept": "text/plain"},
        verify=ca_path,
        timeout=float(os.getenv("AAP_KUBERNETES_TIMEOUT_SECONDS", "15")),
    )
    if response.status_code >= 400:
        return response.text.strip()[:400]
    return response.text.strip()


def _read_kubernetes_secret_key(namespace: str, name: str, key: str) -> str:
    payload = _kubernetes_request("GET", f"/api/v1/namespaces/{namespace}/secrets/{name}")
    data = payload.get("data") or {}
    encoded = str(data.get(key) or "").strip()
    if not encoded:
        raise AAPAutomationError(f"Kubernetes secret {namespace}/{name} does not contain key '{key}'.")
    return base64.b64decode(encoded).decode("utf-8").strip()
