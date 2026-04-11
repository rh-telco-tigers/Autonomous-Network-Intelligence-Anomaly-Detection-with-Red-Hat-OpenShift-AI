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


if __name__ == "__main__":
    unittest.main()
