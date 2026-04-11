import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

MODULE_PATH = Path(__file__).resolve().parents[1] / "gitea.py"
SPEC = importlib.util.spec_from_file_location("shared_gitea", MODULE_PATH)
assert SPEC and SPEC.loader
gitea = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gitea)


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = "" if payload is None else json.dumps(payload)

    def json(self):
        return self._payload


class GiteaHelperTests(unittest.TestCase):
    def test_sync_generated_playbook_to_draft_creates_repo_branch_and_file(self) -> None:
        requests_seen: list[tuple[str, str, object]] = []

        def _fake_request(method: str, url: str, **kwargs: object) -> _FakeResponse:
            requests_seen.append((method, url, kwargs.get("json")))
            if url.endswith("/api/v1/repos/gitadmin/ani-ai-generated-playbooks") and method == "GET":
                return _FakeResponse({"message": "not found"}, status_code=404)
            if url.endswith("/api/v1/user/repos") and method == "POST":
                return _FakeResponse({"name": "ani-ai-generated-playbooks", "default_branch": "main"})
            if url.endswith("/api/v1/repos/gitadmin/ani-ai-generated-playbooks/branches/main") and method == "GET":
                return _FakeResponse({"name": "main"})
            if url.endswith("/api/v1/repos/gitadmin/ani-ai-generated-playbooks/branches/draft%2Finc-sync-1") and method == "GET":
                return _FakeResponse({"message": "not found"}, status_code=404)
            if url.endswith("/api/v1/repos/gitadmin/ani-ai-generated-playbooks/branches") and method == "POST":
                return _FakeResponse({"name": "draft/inc-sync-1"})
            if url.endswith("/api/v1/repos/gitadmin/ani-ai-generated-playbooks/contents/playbooks%2Finc-sync-1%2Fplaybook.yaml") and method == "GET":
                return _FakeResponse({"message": "not found"}, status_code=404)
            if url.endswith("/api/v1/repos/gitadmin/ani-ai-generated-playbooks/contents/playbooks%2Finc-sync-1%2Fplaybook.yaml") and method == "POST":
                return _FakeResponse({"commit": {"sha": "draftsha123"}, "content": {"sha": "filesha123"}})
            raise AssertionError(f"Unexpected request: {method} {url}")

        with (
            mock.patch.object(gitea.requests, "request", side_effect=_fake_request),
            mock.patch.object(gitea, "_gitea_username", return_value="gitadmin"),
            mock.patch.object(gitea, "_gitea_password", return_value="secret"),
        ):
            result = gitea.sync_generated_playbook_to_draft(
                "inc-sync-1",
                "---\n- hosts: localhost\n  gather_facts: false\n  tasks: []\n",
            )

        self.assertEqual(result["repo_owner"], "gitadmin")
        self.assertEqual(result["repo_name"], "ani-ai-generated-playbooks")
        self.assertEqual(result["draft_branch"], "draft/inc-sync-1")
        self.assertEqual(result["playbook_path"], "playbooks/inc-sync-1/playbook.yaml")
        self.assertEqual(result["draft_commit_sha"], "draftsha123")
        self.assertEqual(result["status"], "drafted")
        create_file_request = next(item for item in requests_seen if item[0] == "POST" and str(item[1]).endswith("/contents/playbooks%2Finc-sync-1%2Fplaybook.yaml"))
        self.assertEqual(create_file_request[2]["branch"], "draft/inc-sync-1")

    def test_promote_generated_playbook_creates_and_merges_pull_request(self) -> None:
        def _fake_request(method: str, url: str, **kwargs: object) -> _FakeResponse:
            if url.endswith("/api/v1/repos/gitadmin/ani-ai-generated-playbooks") and method == "GET":
                return _FakeResponse({"name": "ani-ai-generated-playbooks", "default_branch": "main"})
            if url.endswith("/api/v1/repos/gitadmin/ani-ai-generated-playbooks/branches/draft%2Finc-promote-1") and method == "GET":
                return _FakeResponse({"name": "draft/inc-promote-1"})
            if url.endswith("/api/v1/repos/gitadmin/ani-ai-generated-playbooks/pulls") and method == "GET":
                return _FakeResponse([])
            if url.endswith("/api/v1/repos/gitadmin/ani-ai-generated-playbooks/pulls") and method == "POST":
                return _FakeResponse(
                    {
                        "number": 12,
                        "html_url": "https://gitea.example/gitadmin/ani-ai-generated-playbooks/pulls/12",
                        "state": "open",
                        "head": {"ref": "draft/inc-promote-1", "sha": "draftsha456"},
                        "base": {"ref": "main"},
                    }
                )
            if url.endswith("/api/v1/repos/gitadmin/ani-ai-generated-playbooks/pulls/12/merge") and method == "POST":
                return _FakeResponse({"message": "merged"})
            if url.endswith("/api/v1/repos/gitadmin/ani-ai-generated-playbooks/pulls/12") and method == "GET":
                return _FakeResponse(
                    {
                        "number": 12,
                        "html_url": "https://gitea.example/gitadmin/ani-ai-generated-playbooks/pulls/12",
                        "state": "closed",
                        "merged": True,
                        "merge_commit_sha": "merge789",
                        "head": {"ref": "draft/inc-promote-1", "sha": "draftsha456"},
                        "base": {"ref": "main"},
                    }
                )
            raise AssertionError(f"Unexpected request: {method} {url}")

        with (
            mock.patch.object(gitea.requests, "request", side_effect=_fake_request),
            mock.patch.object(gitea, "_gitea_username", return_value="gitadmin"),
            mock.patch.object(gitea, "_gitea_password", return_value="secret"),
        ):
            result = gitea.promote_generated_playbook("inc-promote-1")

        self.assertEqual(result["status"], "merged")
        self.assertEqual(result["pr_number"], 12)
        self.assertEqual(result["draft_branch"], "draft/inc-promote-1")
        self.assertEqual(result["main_branch"], "main")
        self.assertEqual(result["merge_commit_sha"], "merge789")


if __name__ == "__main__":
    unittest.main()
