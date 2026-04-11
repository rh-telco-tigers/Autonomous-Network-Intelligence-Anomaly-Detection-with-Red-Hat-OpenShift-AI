from __future__ import annotations

import base64
import functools
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote

import requests


class GiteaAutomationError(RuntimeError):
    pass


def generated_playbook_repo_owner() -> str:
    return os.getenv("AI_PLAYBOOK_GITEA_OWNER", _gitea_username()).strip() or _gitea_username()


def generated_playbook_repo_name() -> str:
    return os.getenv("AI_PLAYBOOK_GITEA_REPO", "ani-ai-generated-playbooks").strip() or "ani-ai-generated-playbooks"


def generated_playbook_main_branch() -> str:
    return os.getenv("AI_PLAYBOOK_GITEA_MAIN_BRANCH", "main").strip() or "main"


def generated_playbook_draft_branch(incident_id: str) -> str:
    return f"{_draft_branch_prefix()}{_incident_key(incident_id)}"


def generated_playbook_path(incident_id: str) -> str:
    return f"playbooks/{_incident_key(incident_id)}/playbook.yaml"


def generated_playbook_repo_scm_url() -> str:
    return (
        os.getenv("AI_PLAYBOOK_GITEA_SCM_URL", "").strip()
        or f"{_app_base_url()}/{generated_playbook_repo_owner()}/{generated_playbook_repo_name()}.git"
    )


def sync_generated_playbook_to_draft(
    incident_id: str,
    playbook_yaml: str,
    *,
    commit_message: str = "",
) -> Dict[str, Any]:
    normalized_playbook = str(playbook_yaml or "").strip()
    if not normalized_playbook:
        raise GiteaAutomationError("AI-generated playbook YAML cannot be empty.")

    repo = _ensure_generated_repo()
    main_branch = generated_playbook_main_branch()
    draft_branch = generated_playbook_draft_branch(incident_id)
    playbook_path = generated_playbook_path(incident_id)

    _ensure_branch(main_branch, str(repo.get("default_branch") or main_branch))
    _ensure_branch(draft_branch, main_branch)

    existing = _get_file(playbook_path, draft_branch)
    if existing and _decode_file_content(existing) == normalized_playbook:
        commit_sha = _content_sha(existing)
        return _sync_summary(
            incident_id=incident_id,
            draft_branch=draft_branch,
            playbook_path=playbook_path,
            commit_sha=commit_sha,
            status="unchanged",
        )

    payload = {
        "branch": draft_branch,
        "content": base64.b64encode(normalized_playbook.encode("utf-8")).decode("utf-8"),
        "message": commit_message or f"Draft AI playbook for incident {_incident_key(incident_id)}",
    }
    method = "POST"
    if existing:
        payload["sha"] = _content_sha(existing)
        method = "PUT"
    result = _request_json(
        method,
        f"/repos/{generated_playbook_repo_owner()}/{generated_playbook_repo_name()}/contents/{quote(playbook_path, safe='')}",
        expected_status=(200, 201),
        json=payload,
    )
    if not isinstance(result, dict):
        raise GiteaAutomationError("Gitea returned an unexpected response while syncing the AI playbook draft.")
    commit = result.get("commit") if isinstance(result.get("commit"), dict) else {}
    commit_sha = str(commit.get("sha") or _content_sha(result.get("content")) or "").strip()
    return _sync_summary(
        incident_id=incident_id,
        draft_branch=draft_branch,
        playbook_path=playbook_path,
        commit_sha=commit_sha,
        status="drafted" if method == "POST" else "updated",
    )


def promote_generated_playbook(
    incident_id: str,
    *,
    title: str = "",
    body: str = "",
) -> Dict[str, Any]:
    _ensure_generated_repo()
    main_branch = generated_playbook_main_branch()
    draft_branch = generated_playbook_draft_branch(incident_id)
    playbook_path = generated_playbook_path(incident_id)
    _ensure_branch(draft_branch, main_branch)

    pull_request = _find_matching_pull_request(draft_branch, main_branch)
    if pull_request and _pull_request_merged(pull_request):
        return _promotion_summary(
            incident_id=incident_id,
            draft_branch=draft_branch,
            playbook_path=playbook_path,
            pull_request=pull_request,
            status="merged",
        )

    if pull_request is None:
        created = _request_json(
            "POST",
            f"/repos/{generated_playbook_repo_owner()}/{generated_playbook_repo_name()}/pulls",
            expected_status=(200, 201),
            json={
                "base": main_branch,
                "head": draft_branch,
                "title": title or f"Promote AI-generated playbook for incident {_incident_key(incident_id)}",
                "body": body or f"Promote `playbooks/{_incident_key(incident_id)}/playbook.yaml` from `{draft_branch}` to `{main_branch}`.",
            },
        )
        if not isinstance(created, dict):
            raise GiteaAutomationError("Gitea returned an unexpected response while creating the AI playbook pull request.")
        pull_request = created

    if _pull_request_merged(pull_request):
        return _promotion_summary(
            incident_id=incident_id,
            draft_branch=draft_branch,
            playbook_path=playbook_path,
            pull_request=pull_request,
            status="merged",
        )

    pull_request_number = int(pull_request.get("number") or pull_request.get("index") or 0)
    if pull_request_number <= 0:
        raise GiteaAutomationError("Gitea did not return a pull request number for the AI playbook promotion.")

    head_commit = (
        pull_request.get("head")
        if isinstance(pull_request.get("head"), dict)
        else {}
    )
    merge_payload = {
        "Do": "merge",
        "delete_branch_after_merge": False,
        "head_commit_id": str(head_commit.get("sha") or "").strip(),
        "merge_message_field": title or f"Merge AI-generated playbook for incident {_incident_key(incident_id)}",
    }
    response = _request_raw(
        "POST",
        f"/repos/{generated_playbook_repo_owner()}/{generated_playbook_repo_name()}/pulls/{pull_request_number}/merge",
        expected_status=(200, 201, 405, 409),
        json=merge_payload,
    )
    if response.status_code in {405, 409}:
        merged = _wait_for_pull_request_merge(pull_request_number)
        if merged is None:
            message = response.text[:400] if response.text else ""
            raise GiteaAutomationError(
                f"Gitea merge for pull request {pull_request_number} did not reach a merged state after the API returned "
                f"{response.status_code}. {message}".strip()
            )
    else:
        merged = _get_pull_request(pull_request_number)
    if not isinstance(merged, dict):
        raise GiteaAutomationError("Gitea returned an unexpected pull request response after merging the AI playbook.")
    return _promotion_summary(
        incident_id=incident_id,
        draft_branch=draft_branch,
        playbook_path=playbook_path,
        pull_request=merged,
        status="merged",
    )


def _sync_summary(
    *,
    incident_id: str,
    draft_branch: str,
    playbook_path: str,
    commit_sha: str,
    status: str,
) -> Dict[str, Any]:
    return {
        "incident_id": _incident_key(incident_id),
        "repo_owner": generated_playbook_repo_owner(),
        "repo_name": generated_playbook_repo_name(),
        "scm_url": generated_playbook_repo_scm_url(),
        "main_branch": generated_playbook_main_branch(),
        "draft_branch": draft_branch,
        "playbook_path": playbook_path,
        "draft_commit_sha": commit_sha,
        "status": status,
    }


def _promotion_summary(
    *,
    incident_id: str,
    draft_branch: str,
    playbook_path: str,
    pull_request: Dict[str, Any],
    status: str,
) -> Dict[str, Any]:
    head = pull_request.get("head") if isinstance(pull_request.get("head"), dict) else {}
    base = pull_request.get("base") if isinstance(pull_request.get("base"), dict) else {}
    merge_commit_sha = str(pull_request.get("merge_commit_sha") or pull_request.get("merged_commit_sha") or "").strip()
    return {
        "incident_id": _incident_key(incident_id),
        "repo_owner": generated_playbook_repo_owner(),
        "repo_name": generated_playbook_repo_name(),
        "scm_url": generated_playbook_repo_scm_url(),
        "main_branch": str(base.get("ref") or generated_playbook_main_branch()).strip() or generated_playbook_main_branch(),
        "draft_branch": str(head.get("ref") or draft_branch).strip() or draft_branch,
        "playbook_path": playbook_path,
        "draft_commit_sha": str(head.get("sha") or "").strip(),
        "pr_number": int(pull_request.get("number") or pull_request.get("index") or 0),
        "pr_url": _pull_request_url(pull_request),
        "merge_commit_sha": merge_commit_sha,
        "status": status,
    }


def _ensure_generated_repo() -> Dict[str, Any]:
    repo = _get_repo()
    if repo is None:
        created = _request_json(
            "POST",
            "/user/repos",
            expected_status=(200, 201),
            json={
                "name": generated_playbook_repo_name(),
                "auto_init": True,
                "default_branch": generated_playbook_main_branch(),
                "private": False,
                "readme": "Default",
            },
        )
        if not isinstance(created, dict):
            raise GiteaAutomationError("Gitea returned an unexpected response while creating the AI playbook repository.")
        repo = created
    if not isinstance(repo, dict):
        raise GiteaAutomationError("Gitea returned an unexpected repository payload for AI playbooks.")
    return repo


def _get_repo() -> Dict[str, Any] | None:
    response = _request_raw(
        "GET",
        f"/repos/{generated_playbook_repo_owner()}/{generated_playbook_repo_name()}",
        expected_status=(200, 404),
    )
    if response.status_code == 404:
        return None
    payload = _parse_json(response)
    if not isinstance(payload, dict):
        raise GiteaAutomationError("Gitea returned an unexpected repository response.")
    return payload


def _ensure_branch(branch_name: str, old_ref_name: str) -> Dict[str, Any]:
    branch = _get_branch(branch_name)
    if branch is not None:
        return branch
    created = _request_json(
        "POST",
        f"/repos/{generated_playbook_repo_owner()}/{generated_playbook_repo_name()}/branches",
        expected_status=(200, 201),
        json={
            "new_branch_name": branch_name,
            "old_ref_name": old_ref_name,
        },
    )
    if not isinstance(created, dict):
        raise GiteaAutomationError(f"Gitea returned an unexpected response while creating branch '{branch_name}'.")
    return created


def _get_branch(branch_name: str) -> Dict[str, Any] | None:
    response = _request_raw(
        "GET",
        f"/repos/{generated_playbook_repo_owner()}/{generated_playbook_repo_name()}/branches/{quote(branch_name, safe='')}",
        expected_status=(200, 404),
    )
    if response.status_code == 404:
        return None
    payload = _parse_json(response)
    if not isinstance(payload, dict):
        raise GiteaAutomationError(f"Gitea returned an unexpected branch response for '{branch_name}'.")
    return payload


def _get_file(path: str, branch: str) -> Dict[str, Any] | None:
    response = _request_raw(
        "GET",
        f"/repos/{generated_playbook_repo_owner()}/{generated_playbook_repo_name()}/contents/{quote(path, safe='')}",
        expected_status=(200, 404),
        params={"ref": branch},
    )
    if response.status_code == 404:
        return None
    payload = _parse_json(response)
    if not isinstance(payload, dict):
        raise GiteaAutomationError(f"Gitea returned an unexpected file response for '{path}'.")
    return payload


def _find_matching_pull_request(head_branch: str, base_branch: str) -> Dict[str, Any] | None:
    payload = _request_json(
        "GET",
        f"/repos/{generated_playbook_repo_owner()}/{generated_playbook_repo_name()}/pulls",
        expected_status=(200,),
        params={"state": "all", "limit": 100},
    )
    if not isinstance(payload, list):
        raise GiteaAutomationError("Gitea returned an unexpected pull request list for AI playbooks.")
    for item in payload:
        if not isinstance(item, dict):
            continue
        head = item.get("head") if isinstance(item.get("head"), dict) else {}
        base = item.get("base") if isinstance(item.get("base"), dict) else {}
        if str(head.get("ref") or "").strip() == head_branch and str(base.get("ref") or "").strip() == base_branch:
            return item
    return None


def _get_pull_request(pull_request_number: int) -> Dict[str, Any]:
    payload = _request_json(
        "GET",
        f"/repos/{generated_playbook_repo_owner()}/{generated_playbook_repo_name()}/pulls/{pull_request_number}",
        expected_status=(200,),
    )
    if not isinstance(payload, dict):
        raise GiteaAutomationError(f"Gitea returned an unexpected pull request payload for #{pull_request_number}.")
    return payload


def _wait_for_pull_request_merge(pull_request_number: int) -> Dict[str, Any] | None:
    deadline = time.time() + float(os.getenv("AI_PLAYBOOK_GITEA_MERGE_WAIT_SECONDS", "20"))
    poll_interval = max(float(os.getenv("AI_PLAYBOOK_GITEA_MERGE_POLL_SECONDS", "1")), 0.5)
    latest: Dict[str, Any] | None = None
    while time.time() < deadline:
        latest = _get_pull_request(pull_request_number)
        if _pull_request_merged(latest):
            return latest
        time.sleep(poll_interval)
    if latest and _pull_request_merged(latest):
        return latest
    return None


def _pull_request_merged(pull_request: Dict[str, Any]) -> bool:
    if bool(pull_request.get("merged")):
        return True
    if bool(pull_request.get("has_merged")):
        return True
    return str(pull_request.get("state") or "").strip().lower() == "closed" and bool(pull_request.get("merge_commit_sha"))


def _pull_request_url(pull_request: Dict[str, Any]) -> str:
    for key in ("html_url", "url"):
        value = str(pull_request.get(key) or "").strip()
        if value:
            return value
    number = int(pull_request.get("number") or pull_request.get("index") or 0)
    if number <= 0:
        return ""
    return f"{_app_base_url()}/{generated_playbook_repo_owner()}/{generated_playbook_repo_name()}/pulls/{number}"


def _content_sha(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("sha") or "").strip()


def _decode_file_content(payload: Dict[str, Any]) -> str:
    content = str(payload.get("content") or "").strip()
    encoding = str(payload.get("encoding") or "").strip().lower()
    if not content:
        return ""
    if encoding == "base64":
        return base64.b64decode(content).decode("utf-8").strip()
    return content.strip()


def _incident_key(incident_id: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", str(incident_id or "").strip()).strip("-")
    if not normalized:
        raise GiteaAutomationError("incident_id is required for AI playbook Git operations.")
    return normalized


def _draft_branch_prefix() -> str:
    prefix = os.getenv("AI_PLAYBOOK_GITEA_DRAFT_PREFIX", "draft/").strip() or "draft/"
    return prefix if prefix.endswith("/") else f"{prefix}/"


def _api_base_url() -> str:
    explicit = os.getenv("AI_PLAYBOOK_GITEA_API_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    return f"{_app_base_url()}/api/v1"


def _app_base_url() -> str:
    return (
        os.getenv("AI_PLAYBOOK_GITEA_URL", "").strip()
        or "http://gitea-http.gitea.svc.cluster.local:3000"
    ).rstrip("/")


@functools.lru_cache(maxsize=1)
def _gitea_username() -> str:
    explicit = os.getenv("AI_PLAYBOOK_GITEA_USERNAME", "").strip()
    if explicit:
        return explicit
    try:
        return _read_kubernetes_secret_key("gitea", "gitea-admin-credentials", "username")
    except GiteaAutomationError:
        return "gitadmin"


@functools.lru_cache(maxsize=1)
def _gitea_password() -> str:
    explicit = os.getenv("AI_PLAYBOOK_GITEA_PASSWORD", "").strip()
    if explicit:
        return explicit
    try:
        return _read_kubernetes_secret_key("gitea", "gitea-admin-credentials", "password")
    except GiteaAutomationError:
        return "GiteaAdmin123!"


def _request_json(
    method: str,
    path: str,
    *,
    expected_status: tuple[int, ...] = (200,),
    **kwargs: Any,
) -> Any:
    response = _request_raw(method, path, expected_status=expected_status, **kwargs)
    return _parse_json(response)


def _request_raw(
    method: str,
    path: str,
    *,
    expected_status: tuple[int, ...] = (200,),
    **kwargs: Any,
) -> requests.Response:
    response = requests.request(
        method,
        f"{_api_base_url()}{path}",
        auth=(_gitea_username(), _gitea_password()),
        timeout=float(os.getenv("AI_PLAYBOOK_GITEA_TIMEOUT_SECONDS", "20")),
        headers={"Accept": "application/json", "Content-Type": "application/json", **kwargs.pop("headers", {})},
        **kwargs,
    )
    if response.status_code not in expected_status:
        raise GiteaAutomationError(
            f"Gitea request failed for {method} {path}: {response.status_code} {response.text[:400]}"
        )
    return response


def _parse_json(response: requests.Response) -> Any:
    if not response.text.strip():
        return {}
    try:
        return response.json()
    except ValueError as exc:  # pragma: no cover - defensive
        raise GiteaAutomationError("Gitea returned non-JSON content.") from exc


def _kubernetes_api_url() -> str:
    host = os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc").strip() or "kubernetes.default.svc"
    port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443").strip() or "443"
    return f"https://{host}:{port}"


def _kubernetes_request(
    method: str,
    path: str,
    *,
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
        timeout=float(os.getenv("AI_PLAYBOOK_GITEA_KUBERNETES_TIMEOUT_SECONDS", "15")),
        **kwargs,
    )
    if response.status_code not in expected_status:
        raise GiteaAutomationError(
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
        raise GiteaAutomationError(f"Kubernetes secret {namespace}/{name} does not contain key '{key}'.")
    return base64.b64decode(encoded).decode("utf-8").strip()
