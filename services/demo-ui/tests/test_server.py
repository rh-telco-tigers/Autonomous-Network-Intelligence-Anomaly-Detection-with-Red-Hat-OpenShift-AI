import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "server.py"
SPEC = importlib.util.spec_from_file_location("demo_ui_server", MODULE_PATH)
assert SPEC and SPEC.loader
server = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(server)


class DemoUiServerTests(unittest.TestCase):
    def test_upstream_url_rewrites_api_prefix(self) -> None:
        with mock.patch.dict(os.environ, {"CONTROL_PLANE_PROXY_URL": "http://control-plane.test.svc.cluster.local:8080"}):
            self.assertEqual(
                server._upstream_url("/api/console/state?project=ims-demo"),
                "http://control-plane.test.svc.cluster.local:8080/console/state?project=ims-demo",
            )

    def test_upstream_url_supports_api_root(self) -> None:
        with mock.patch.dict(os.environ, {"CONTROL_PLANE_PROXY_URL": "http://control-plane.test.svc.cluster.local:8080"}):
            self.assertEqual(
                server._upstream_url("/api"),
                "http://control-plane.test.svc.cluster.local:8080/",
            )

    def test_upstream_url_rejects_non_api_paths(self) -> None:
        with self.assertRaises(ValueError):
            server._upstream_url("/console/state")

    def test_ssl_context_created_only_for_https_when_skip_enabled(self) -> None:
        with mock.patch.dict(os.environ, {"CONTROL_PLANE_SKIP_TLS_VERIFY": "true"}):
            self.assertIsNotNone(server._ssl_context_for("https://example.com"))
            self.assertIsNone(server._ssl_context_for("http://example.com"))

    def test_ssl_context_disabled_by_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(server._ssl_context_for("https://example.com"))


if __name__ == "__main__":
    unittest.main()
