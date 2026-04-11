import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

MODULE_PATH = Path(__file__).resolve().parents[1] / "aap.py"
SPEC = importlib.util.spec_from_file_location("shared_aap", MODULE_PATH)
assert SPEC and SPEC.loader
aap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(aap)


class _FakeTextResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


class AAPHelperTests(unittest.TestCase):
    def test_runner_job_logs_uses_generic_accept_header(self) -> None:
        captured: dict[str, object] = {}

        def _fake_get(url: str, **kwargs: object) -> _FakeTextResponse:
            captured["url"] = url
            captured["headers"] = kwargs.get("headers")
            return _FakeTextResponse("runner log line")

        with (
            mock.patch.object(
                aap,
                "_kubernetes_request",
                return_value={"items": [{"metadata": {"name": "runner-pod"}}]},
            ),
            mock.patch.object(aap.Path, "read_text", return_value="token"),
            mock.patch.object(aap.requests, "get", side_effect=_fake_get),
        ):
            logs = aap._runner_job_logs("aap", "job-123")

        self.assertEqual(logs, "runner log line")
        self.assertEqual(
            captured["url"],
            "https://kubernetes.default.svc:443/api/v1/namespaces/aap/pods/runner-pod/log",
        )
        self.assertEqual(
            captured["headers"],
            {"Authorization": "Bearer token", "Accept": "*/*"},
        )

    def test_launch_repo_playbook_launches_controller_job_from_draft_branch(self) -> None:
        calls: list[tuple[str, str, dict[str, object]]] = []

        def _fake_request(method: str, path: str, **kwargs: object):
            calls.append((method, path, dict(kwargs)))
            if method == "POST" and path == "/api/v2/job_templates/73/launch/":
                return {"job": 812, "status": "pending"}
            return {}

        with (
            mock.patch.object(aap, "_require_object_id", return_value=1),
            mock.patch.object(aap, "_ensure_inventory", return_value=2),
            mock.patch.object(aap, "_ensure_ai_playbook_project", return_value=8),
            mock.patch.object(aap, "_sync_project"),
            mock.patch.object(aap, "_ensure_kubernetes_credential", return_value=5),
            mock.patch.object(aap, "_ensure_job_template", return_value=73) as ensure_template,
            mock.patch.object(aap, "_request", side_effect=_fake_request),
        ):
            launch = aap.launch_repo_playbook("inc-aap-1", {"incident_id": "inc-aap-1", "approved_by": "demo-operator"})

        ensure_template.assert_called_once()
        self.assertEqual(ensure_template.call_args.kwargs["playbook"], "playbooks/inc-aap-1/playbook.yaml")
        self.assertTrue(ensure_template.call_args.kwargs["ask_scm_branch_on_launch"])
        launch_call = next(item for item in calls if item[1] == "/api/v2/job_templates/73/launch/")
        self.assertEqual(launch_call[2]["json"]["scm_branch"], "draft/inc-aap-1")
        self.assertEqual(launch["job_id"], 812)
        self.assertEqual(launch["project_id"], 8)
        self.assertEqual(launch["scm_branch"], "draft/inc-aap-1")


if __name__ == "__main__":
    unittest.main()
