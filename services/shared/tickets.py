from __future__ import annotations

import html
import os
import time
import uuid
from typing import Any, Dict, List

import requests

from shared.workflow import plane_priority_for_severity, plane_state_for_workflow, ticket_creation_exclusion_reason


PLANE_STATE_CACHE_TTL_SECONDS = 60.0
_PLANE_STATE_CACHE: dict[tuple[str, str, str], tuple[float, List[Dict[str, Any]]]] = {}


class TicketProviderError(RuntimeError):
    pass


def _demo_integrations_enabled() -> bool:
    return os.getenv("DEMO_INTEGRATIONS_ENABLED", "true").lower() == "true"


def _strip_html(text: str) -> str:
    return text.replace("<br/>", "\n").replace("<br>", "\n").replace("</p>", "\n").replace("<p>", "").strip()


def _ticket_title(incident: Dict[str, Any]) -> str:
    severity = str(incident.get("severity") or "Medium")
    anomaly_type = str(incident.get("anomaly_type") or "incident").replace("_", " ").title()
    return f"[{severity}] IMS {anomaly_type} ({incident['id'][:12]})"


def _ticket_context_incident(incident: Dict[str, Any], workflow: Dict[str, Any]) -> Dict[str, Any]:
    workflow_incident = workflow.get("incident") if isinstance(workflow, dict) else None
    if isinstance(workflow_incident, dict):
        return workflow_incident
    return incident


def _comment_html(note: str) -> str:
    escaped_lines = [html.escape(line, quote=False) for line in str(note or "").splitlines()]
    body = "<br/>".join(line for line in escaped_lines if line) or html.escape(str(note or "").strip(), quote=False)
    return f"<p>{body}</p>"


def _html_text(value: object) -> str:
    return html.escape(str(value or ""), quote=False)


def _plane_api_headers(api_key: str) -> Dict[str, str]:
    return {"X-API-Key": api_key, "Content-Type": "application/json"}


def _response_json_object(response: requests.Response) -> Dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_plane_label(value: object) -> str:
    return str(value or "").strip().lower()


def _plane_existing_issue_id(response: requests.Response) -> str:
    if response.status_code != 409:
        return ""
    payload = _response_json_object(response)
    if "already exists" not in _normalize_plane_label(payload.get("error")):
        return ""
    return str(payload.get("id") or "")


def _fetch_plane_project_states(base_url: str, workspace_slug: str, project_id: str, api_key: str) -> List[Dict[str, Any]]:
    cache_key = (base_url, workspace_slug, project_id)
    now = time.time()
    cached = _PLANE_STATE_CACHE.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    response = requests.get(
        f"{base_url}/api/v1/workspaces/{workspace_slug}/projects/{project_id}/states/",
        headers=_plane_api_headers(api_key),
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results") if isinstance(payload, dict) else payload
    states = [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []
    _PLANE_STATE_CACHE[cache_key] = (now + PLANE_STATE_CACHE_TTL_SECONDS, states)
    return states


def _resolve_plane_state(states: List[Dict[str, Any]], desired_name: str) -> Dict[str, Any] | None:
    normalized_desired = _normalize_plane_label(desired_name)
    if not normalized_desired:
        return None

    for state in states:
        if _normalize_plane_label(state.get("name")) == normalized_desired:
            return state

    aliases = {
        "todo": (("Todo", "Backlog"), ("unstarted", "backlog")),
        "in progress": (("In Progress",), ("started",)),
        "done": (("Done",), ("completed",)),
        "cancelled": (("Cancelled", "Canceled"), ("cancelled",)),
        "blocked": (("In Progress", "Todo", "Backlog"), ("started", "unstarted", "backlog")),
    }
    candidate_names, candidate_groups = aliases.get(normalized_desired, ((desired_name,), ()))
    for candidate_name in candidate_names:
        normalized_candidate = _normalize_plane_label(candidate_name)
        for state in states:
            if _normalize_plane_label(state.get("name")) == normalized_candidate:
                return state
    for candidate_group in candidate_groups:
        normalized_group = _normalize_plane_label(candidate_group)
        for state in states:
            if _normalize_plane_label(state.get("group")) == normalized_group:
                return state
    return None


def _rca_payload(record: Dict[str, Any]) -> Dict[str, Any]:
    payload = record.get("payload") or {}
    return payload if isinstance(payload, dict) else {}


def _current_rca(workflow: Dict[str, Any]) -> Dict[str, Any]:
    incident = workflow.get("incident") or {}
    current_rca_id = incident.get("current_rca_id")
    for record in workflow.get("rca_history") or []:
        if current_rca_id and record.get("id") == current_rca_id:
            return record
    history = workflow.get("rca_history") or []
    return history[0] if history else {}


def _current_remediations(workflow: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        item
        for item in workflow.get("remediations") or []
        if str(item.get("status") or "").lower() in {"available", "approved", "executing", "executed"}
    ][:3]


def build_ticket_description_html(incident: Dict[str, Any], workflow: Dict[str, Any], incident_url: str = "") -> str:
    ticket_incident = _ticket_context_incident(incident, workflow)
    rca = _current_rca(workflow)
    rca_payload = _rca_payload(rca)
    remediations = _current_remediations(workflow)
    recommendation_items = "".join(
        f"<li><strong>{_html_text(item.get('title'))}</strong>: {_html_text(item.get('description'))}</li>"
        for item in remediations
    ) or "<li>No remediation suggestions generated yet.</li>"
    evidence = ticket_incident.get("evidence_sources") or []
    evidence_items = "".join(
        f"<li><strong>{_html_text(item.get('title'))}</strong>: {_html_text(item.get('detail'))}</li>"
        for item in evidence[:3]
    ) or "<li>No evidence attached yet.</li>"
    explanation = (
        str(rca.get("explanation") or "")
        or str(rca_payload.get("explanation") or "")
        or str(ticket_incident.get("narrative") or "")
        or "RCA narrative has not been attached yet."
    )
    recommendation = (
        str(rca_payload.get("recommendation") or "")
        or str(ticket_incident.get("recommendation") or "")
        or "Recommendation has not been attached yet."
    )
    source_label = str(rca_payload.get("generation_source_label") or "RCA source unavailable")
    incident_link = (
        f'<p><strong>Incident workspace:</strong> <a href="{html.escape(incident_url, quote=True)}">{_html_text(incident_url)}</a></p>'
        if incident_url
        else ""
    )
    return (
        f"<h3>{_html_text(_ticket_title(ticket_incident))}</h3>"
        f"{incident_link}"
        f"<p><strong>Workflow state:</strong> {_html_text(ticket_incident.get('status'))}</p>"
        f"<p><strong>Predicted confidence:</strong> {_html_text(ticket_incident.get('predicted_confidence'))}</p>"
        f"<p><strong>Anomaly score:</strong> {_html_text(ticket_incident.get('anomaly_score'))}</p>"
        f"<p><strong>Severity:</strong> {_html_text(ticket_incident.get('severity'))}</p>"
        f"<p><strong>Impact:</strong> {_html_text(ticket_incident.get('impact') or ticket_incident.get('subtitle') or '')}</p>"
        f"<h4>RCA</h4>"
        f"<p><strong>Root cause:</strong> {_html_text(rca.get('root_cause') or ticket_incident.get('narrative') or 'Pending RCA')}</p>"
        f"<p><strong>Analysis:</strong> {_html_text(explanation)}</p>"
        f"<p><strong>Confidence:</strong> {_html_text(rca.get('confidence') or 0)}</p>"
        f"<p><strong>Source:</strong> {_html_text(source_label)}</p>"
        f"<p><strong>Recommended action:</strong> {_html_text(recommendation)}</p>"
        f"<h4>Evidence</h4><ul>{evidence_items}</ul>"
        f"<h4>Suggested remediations</h4><ol>{recommendation_items}</ol>"
    )


class TicketProvider:
    provider_name = "ticket"

    def status(self) -> Dict[str, object]:
        raise NotImplementedError

    def create_ticket(
        self,
        incident: Dict[str, Any],
        workflow: Dict[str, Any],
        note: str = "",
        force: bool = False,
        source_url: str = "",
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def sync_ticket(
        self,
        incident: Dict[str, Any],
        workflow: Dict[str, Any],
        ticket: Dict[str, Any],
        note: str = "",
        source_url: str = "",
    ) -> Dict[str, Any]:
        raise NotImplementedError


class PlaneTicketProvider(TicketProvider):
    provider_name = "plane"

    def __init__(self) -> None:
        self.base_url = os.getenv("PLANE_BASE_URL", "").rstrip("/")
        self.api_key = os.getenv("PLANE_API_KEY", "").strip()
        self.workspace_slug = os.getenv("PLANE_WORKSPACE_SLUG", "").strip()
        self.project_id = os.getenv("PLANE_PROJECT_ID", "").strip()
        self.app_url = os.getenv("PLANE_APP_URL", "").rstrip("/") or self.base_url

    def _live_configured(self) -> bool:
        return all([self.base_url, self.api_key, self.workspace_slug, self.project_id])

    def _headers(self) -> Dict[str, str]:
        return _plane_api_headers(self.api_key)

    def _target_state(self, incident: Dict[str, Any]) -> Dict[str, str]:
        desired_name = plane_state_for_workflow(str(incident.get("status") or ""))
        if not self._live_configured():
            return {"id": "", "name": desired_name}
        resolved = _resolve_plane_state(
            _fetch_plane_project_states(self.base_url, self.workspace_slug, self.project_id, self.api_key),
            desired_name,
        )
        if not resolved:
            return {"id": "", "name": desired_name}
        return {
            "id": str(resolved.get("id") or ""),
            "name": str(resolved.get("name") or desired_name),
        }

    def status(self) -> Dict[str, object]:
        live = self._live_configured()
        return {
            "configured": live or _demo_integrations_enabled(),
            "mode": "api" if live else "demo-relay",
            "live_configured": live,
            "app_url": self.app_url or None,
            "base_url": self.base_url or None,
            "workspace_slug": self.workspace_slug or None,
            "project_id": self.project_id or None,
        }

    def create_ticket(
        self,
        incident: Dict[str, Any],
        workflow: Dict[str, Any],
        note: str = "",
        force: bool = False,
        source_url: str = "",
    ) -> Dict[str, Any]:
        exclusion = ticket_creation_exclusion_reason(incident)
        if exclusion and not force:
            return {"status": "skipped", "provider": "plane", "reason": exclusion}
        plane_state = self._target_state(incident)

        if not self._live_configured():
            if not _demo_integrations_enabled():
                return {"status": "skipped", "provider": "plane", "reason": "Plane credentials not configured"}
            external_id = f"plane-{uuid.uuid4().hex[:12]}"
            external_key = f"PLANE-{uuid.uuid4().hex[:6].upper()}"
            comment_id = f"plane-comment-{uuid.uuid4().hex[:12]}" if note.strip() else ""
            return {
                "status": "created",
                "provider": "plane",
                "mode": "demo-relay",
                "external_id": external_id,
                "external_key": external_key,
                "url": "",
                "workspace_id": self.workspace_slug or "demo-workspace",
                "project_id": self.project_id or "demo-project",
                "title": _ticket_title(incident),
                "ticket_status": str(plane_state.get("name") or plane_state_for_workflow(str(incident.get("status") or ""))),
                "source_url": source_url,
                "comment": {
                    "external_comment_id": comment_id,
                    "author": "IMS Platform",
                    "body": note.strip(),
                    "comment_type": "operator_update",
                }
                if note.strip()
                else None,
            }

        create_payload = {
            "name": _ticket_title(incident),
            "description_html": build_ticket_description_html(incident, workflow, incident_url=source_url),
            "priority": plane_priority_for_severity(str(incident.get("severity") or "medium")),
            "external_source": "ani-demo",
            "external_id": str(incident.get("id") or ""),
        }
        plane_state_id = str(plane_state.get("id") or "")
        if plane_state_id:
            create_payload["state"] = plane_state_id
        response = requests.post(
            f"{self.base_url}/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/work-items/",
            headers=self._headers(),
            json=create_payload,
            timeout=15,
        )
        existing_issue_id = _plane_existing_issue_id(response)
        if existing_issue_id:
            return self.sync_ticket(
                incident,
                workflow,
                {
                    "provider": self.provider_name,
                    "external_id": existing_issue_id,
                    "external_key": existing_issue_id,
                },
                note=note,
                source_url=source_url,
            )
        response.raise_for_status()
        payload = _response_json_object(response)
        external_id = str(payload.get("id") or "")
        payload_state = payload.get("state_detail") if isinstance(payload, dict) else {}
        ticket_status = str((payload_state or {}).get("name") or plane_state.get("name") or "")
        sequence = payload.get("sequence_id")
        comment_payload: Dict[str, Any] | None = None
        if note.strip() and external_id:
            comment_response = requests.post(
                f"{self.base_url}/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/work-items/{external_id}/comments/",
                headers=self._headers(),
                json={
                    "comment_html": _comment_html(note.strip()),
                    "external_source": "ani-demo",
                    "external_id": f"{incident.get('id')}-create-{uuid.uuid4().hex[:8]}",
                },
                timeout=15,
            )
            comment_response.raise_for_status()
            comment_payload = comment_response.json() if comment_response.text.strip() else {}
        return {
            "status": "created",
            "provider": "plane",
            "mode": "api",
            "external_id": external_id,
            "external_key": str(sequence or external_id),
            "url": f"{self.app_url}/{self.workspace_slug}/projects/{self.project_id}/issues/{external_id}",
            "workspace_id": self.workspace_slug,
            "project_id": self.project_id,
            "title": str(payload.get("name") or _ticket_title(incident)),
            "ticket_status": ticket_status,
            "source_url": source_url,
            "raw": payload,
            "comment": {
                "external_comment_id": str(comment_payload.get("id") or ""),
                "author": "IMS Platform",
                "body": note.strip(),
                "comment_type": "operator_update",
                "raw": comment_payload,
            }
            if note.strip() and comment_payload is not None
            else None,
        }

    def sync_ticket(
        self,
        incident: Dict[str, Any],
        workflow: Dict[str, Any],
        ticket: Dict[str, Any],
        note: str = "",
        source_url: str = "",
    ) -> Dict[str, Any]:
        external_id = str(ticket.get("external_id") or "")
        if not external_id:
            raise TicketProviderError("Plane ticket is missing an external id")
        plane_state = self._target_state(incident)

        if not self._live_configured():
            if not _demo_integrations_enabled():
                return {"status": "skipped", "provider": "plane", "reason": "Plane credentials not configured"}
            comment_id = f"plane-comment-{uuid.uuid4().hex[:12]}" if note.strip() else ""
            return {
                "status": "synced",
                "provider": "plane",
                "mode": "demo-relay",
                "external_id": external_id,
                "external_key": ticket.get("external_key") or external_id,
                "url": "",
                "workspace_id": self.workspace_slug or "demo-workspace",
                "project_id": self.project_id or "demo-project",
                "title": _ticket_title(incident),
                "ticket_status": str(plane_state.get("name") or plane_state_for_workflow(str(incident.get("status") or ""))),
                "source_url": source_url,
                "comment": {
                    "external_comment_id": comment_id,
                    "author": "IMS Platform",
                    "body": note.strip(),
                    "comment_type": "operator_update",
                }
                if note.strip()
                else None,
            }

        description_html = build_ticket_description_html(incident, workflow, incident_url=source_url)
        patch_payload = {
            "name": _ticket_title(incident),
            "description_html": description_html,
            "priority": plane_priority_for_severity(str(incident.get("severity") or "medium")),
        }
        plane_state_id = str(plane_state.get("id") or "")
        if plane_state_id:
            patch_payload["state"] = plane_state_id
        response = requests.patch(
            f"{self.base_url}/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/work-items/{external_id}/",
            headers=self._headers(),
            json=patch_payload,
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        payload_state = payload.get("state_detail") if isinstance(payload, dict) else {}
        ticket_status = str((payload_state or {}).get("name") or plane_state.get("name") or "")
        comment_payload: Dict[str, Any] | None = None
        if note.strip():
            comment_response = requests.post(
                f"{self.base_url}/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/work-items/{external_id}/comments/",
                headers=self._headers(),
                json={
                    "comment_html": _comment_html(note.strip()),
                    "external_source": "ani-demo",
                    "external_id": f"{incident.get('id')}-sync-{uuid.uuid4().hex[:8]}",
                },
                timeout=15,
            )
            comment_response.raise_for_status()
            comment_payload = comment_response.json() if comment_response.text.strip() else {}
        return {
            "status": "synced",
            "provider": "plane",
            "mode": "api",
            "external_id": external_id,
            "external_key": str(payload.get("sequence_id") or ticket.get("external_key") or external_id),
            "url": f"{self.app_url}/{self.workspace_slug}/projects/{self.project_id}/issues/{external_id}",
            "workspace_id": self.workspace_slug,
            "project_id": self.project_id,
            "title": str(payload.get("name") or _ticket_title(incident)),
            "ticket_status": ticket_status,
            "source_url": source_url,
            "raw": payload,
            "comment": {
                "external_comment_id": str(comment_payload.get("id") or ""),
                "author": "IMS Platform",
                "body": note.strip(),
                "comment_type": "operator_update",
                "raw": comment_payload,
            }
            if note.strip() and comment_payload is not None
            else None,
        }


class JiraTicketProvider(TicketProvider):
    provider_name = "jira"

    def __init__(self) -> None:
        self.base_url = os.getenv("JIRA_BASE_URL", "").rstrip("/")
        self.email = os.getenv("JIRA_EMAIL", "")
        self.api_token = os.getenv("JIRA_API_TOKEN", "")
        self.project_key = os.getenv("JIRA_PROJECT_KEY", "")

    def _live_configured(self) -> bool:
        return all([self.base_url, self.email, self.api_token, self.project_key])

    def status(self) -> Dict[str, object]:
        live = self._live_configured()
        return {
            "configured": live or _demo_integrations_enabled(),
            "mode": "rest-api" if live else "demo-relay",
            "live_configured": live,
            "project_key": self.project_key or None,
        }

    def create_ticket(
        self,
        incident: Dict[str, Any],
        workflow: Dict[str, Any],
        note: str = "",
        force: bool = False,
        source_url: str = "",
    ) -> Dict[str, Any]:
        if not self._live_configured():
            if not _demo_integrations_enabled():
                return {"status": "skipped", "provider": "jira", "reason": "Jira credentials not configured"}
            issue_key = f"DEMO-{uuid.uuid4().hex[:6].upper()}"
            return {
                "status": "created",
                "provider": "jira",
                "mode": "demo-relay",
                "external_id": issue_key,
                "external_key": issue_key,
                "url": "",
                "workspace_id": "",
                "project_id": self.project_key or "DEMO",
                "title": _ticket_title(incident),
                "source_url": source_url,
                "comment": {
                    "external_comment_id": f"jira-comment-{uuid.uuid4().hex[:12]}",
                    "author": "IMS Platform",
                    "body": note.strip(),
                    "comment_type": "operator_update",
                }
                if note.strip()
                else None,
            }

        response = requests.post(
            f"{self.base_url}/rest/api/3/issue",
            auth=(self.email, self.api_token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={
                "fields": {
                    "project": {"key": self.project_key},
                    "summary": _ticket_title(incident),
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": _strip_html(build_ticket_description_html(incident, workflow))}],
                            }
                        ],
                    },
                    "issuetype": {"name": "Task"},
                }
            },
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        issue_key = str(payload.get("key") or "")
        return {
            "status": "created",
            "provider": "jira",
            "mode": "rest-api",
            "external_id": issue_key,
            "external_key": issue_key,
            "url": f"{self.base_url}/browse/{issue_key}" if issue_key else "",
            "workspace_id": "",
            "project_id": self.project_key,
            "title": _ticket_title(incident),
            "raw": payload,
        }

    def sync_ticket(
        self,
        incident: Dict[str, Any],
        workflow: Dict[str, Any],
        ticket: Dict[str, Any],
        note: str = "",
        source_url: str = "",
    ) -> Dict[str, Any]:
        external_key = str(ticket.get("external_key") or ticket.get("external_id") or "")
        if not external_key:
            raise TicketProviderError("Jira ticket is missing an external key")
        if not self._live_configured():
            if not _demo_integrations_enabled():
                return {"status": "skipped", "provider": "jira", "reason": "Jira credentials not configured"}
            return {
                "status": "synced",
                "provider": "jira",
                "mode": "demo-relay",
                "external_id": external_key,
                "external_key": external_key,
                "url": ticket.get("url") or "",
                "title": _ticket_title(incident),
                "source_url": source_url,
            }
        response = requests.put(
            f"{self.base_url}/rest/api/3/issue/{external_key}",
            auth=(self.email, self.api_token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={
                "fields": {
                    "summary": _ticket_title(incident),
                }
            },
            timeout=15,
        )
        response.raise_for_status()
        return {
            "status": "synced",
            "provider": "jira",
            "mode": "rest-api",
            "external_id": external_key,
            "external_key": external_key,
            "url": ticket.get("url") or f"{self.base_url}/browse/{external_key}",
            "title": _ticket_title(incident),
            "note": note.strip() or None,
        }


def ticketing_status() -> Dict[str, Dict[str, object]]:
    return {
        "plane": PlaneTicketProvider().status(),
        "jira": JiraTicketProvider().status(),
    }


def get_ticket_provider(provider_name: str) -> TicketProvider:
    normalized = str(provider_name or "").strip().lower()
    if normalized == "plane":
        return PlaneTicketProvider()
    if normalized == "jira":
        return JiraTicketProvider()
    raise TicketProviderError(f"Unsupported ticket provider {provider_name}")
