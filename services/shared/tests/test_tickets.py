import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import requests


SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from shared import tickets, workflow


class _FakeResponse:
    def __init__(self, payload: object, text: str = "ok", status_code: int = 200) -> None:
        self._payload = payload
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def json(self) -> object:
        return self._payload


class PlaneTicketProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        tickets._PLANE_STATE_CACHE.clear()
        self.env = {
            "PLANE_BASE_URL": "https://plane.example.com",
            "PLANE_APP_URL": "https://app.example.com",
            "PLANE_API_KEY": "plane-token",
            "PLANE_WORKSPACE_SLUG": "ims-workspace",
            "PLANE_PROJECT_ID": "ims-project",
        }
        self.states_payload = {
            "results": [
                {"id": "state-backlog", "name": "Backlog", "group": "backlog"},
                {"id": "state-todo", "name": "Todo", "group": "unstarted"},
                {"id": "state-in-progress", "name": "In Progress", "group": "started"},
                {"id": "state-done", "name": "Done", "group": "completed"},
                {"id": "state-cancelled", "name": "Cancelled", "group": "cancelled"},
            ]
        }

    def _incident(self, status: str) -> dict[str, object]:
        return {
            "id": "cbf5c405-3405-4c2b-843f-94f6fde20a80",
            "severity": "Critical",
            "anomaly_type": "registration_storm",
            "status": status,
            "predicted_confidence": 0.97,
            "anomaly_score": 1.0,
            "impact": "Synthetic impact summary.",
        }

    def test_plane_state_for_workflow_uses_supported_labels(self) -> None:
        self.assertEqual(workflow.plane_state_for_workflow(workflow.EXECUTION_FAILED), "In Progress")
        self.assertEqual(workflow.plane_state_for_workflow(workflow.VERIFIED), "Done")
        self.assertEqual(workflow.plane_state_for_workflow(workflow.FALSE_POSITIVE), "Cancelled")

    def test_create_ticket_sets_plane_state_uuid_from_active_workflow(self) -> None:
        incident = self._incident(workflow.ESCALATED)
        workflow_payload = {"incident": incident, "rca_history": [], "remediations": []}
        created_payload = {
            "id": "plane-issue-1",
            "sequence_id": 42,
            "name": "[Critical] IMS Registration Storm (cbf5c405-3405)",
            "state_detail": {"name": "In Progress"},
        }

        with (
            patch.dict(tickets.os.environ, self.env, clear=False),
            patch.object(tickets.requests, "get", return_value=_FakeResponse(self.states_payload)) as get_mock,
            patch.object(tickets.requests, "post", return_value=_FakeResponse(created_payload)) as post_mock,
        ):
            provider = tickets.PlaneTicketProvider()
            result = provider.create_ticket(incident, workflow_payload, source_url="https://demo-ui.example.com/incidents/1")

        self.assertEqual(get_mock.call_count, 1)
        self.assertEqual(post_mock.call_args.kwargs["json"]["state"], "state-in-progress")
        self.assertEqual(result["ticket_status"], "In Progress")

    def test_create_ticket_recovers_existing_plane_issue_after_conflict(self) -> None:
        incident = self._incident(workflow.ESCALATED)
        workflow_payload = {"incident": incident, "rca_history": [], "remediations": []}
        conflict_payload = {
            "error": "Issue with the same external id and external source already exists",
            "id": "plane-issue-1",
        }
        synced_payload = {
            "id": "plane-issue-1",
            "sequence_id": 42,
            "name": "[Critical] IMS Registration Storm (cbf5c405-3405)",
            "state_detail": {"name": "In Progress"},
        }

        with (
            patch.dict(tickets.os.environ, self.env, clear=False),
            patch.object(tickets.requests, "get", return_value=_FakeResponse(self.states_payload)) as get_mock,
            patch.object(
                tickets.requests,
                "post",
                return_value=_FakeResponse(
                    conflict_payload,
                    text='{"error":"Issue with the same external id and external source already exists","id":"plane-issue-1"}',
                    status_code=409,
                ),
            ) as post_mock,
            patch.object(tickets.requests, "patch", return_value=_FakeResponse(synced_payload)) as patch_mock,
        ):
            provider = tickets.PlaneTicketProvider()
            result = provider.create_ticket(incident, workflow_payload)

        self.assertEqual(get_mock.call_count, 1)
        self.assertEqual(post_mock.call_count, 1)
        self.assertEqual(patch_mock.call_count, 1)
        self.assertEqual(result["status"], "synced")
        self.assertEqual(result["external_id"], "plane-issue-1")
        self.assertEqual(result["external_key"], "42")
        self.assertEqual(result["workspace_id"], "ims-workspace")
        self.assertEqual(result["project_id"], "ims-project")

    def test_sync_ticket_marks_verified_workflow_as_done(self) -> None:
        incident = self._incident(workflow.VERIFIED)
        workflow_payload = {"incident": incident, "rca_history": [], "remediations": []}
        existing_ticket = {"external_id": "plane-issue-1", "external_key": "PLANE-42"}
        synced_payload = {
            "id": "plane-issue-1",
            "sequence_id": 42,
            "name": "[Critical] IMS Registration Storm (cbf5c405-3405)",
            "state_detail": {"name": "Done"},
        }

        with (
            patch.dict(tickets.os.environ, self.env, clear=False),
            patch.object(tickets.requests, "get", return_value=_FakeResponse(self.states_payload)) as get_mock,
            patch.object(tickets.requests, "patch", return_value=_FakeResponse(synced_payload)) as patch_mock,
        ):
            provider = tickets.PlaneTicketProvider()
            result = provider.sync_ticket(incident, workflow_payload, existing_ticket)

        self.assertEqual(get_mock.call_count, 1)
        self.assertEqual(patch_mock.call_args.kwargs["json"]["state"], "state-done")
        self.assertEqual(result["ticket_status"], "Done")
        self.assertEqual(result["workspace_id"], "ims-workspace")
        self.assertEqual(result["project_id"], "ims-project")


if __name__ == "__main__":
    unittest.main()
