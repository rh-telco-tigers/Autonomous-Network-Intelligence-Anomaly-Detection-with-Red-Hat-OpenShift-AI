import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

MODULE_PATH = Path(__file__).resolve().parents[1] / "eda.py"
SPEC = importlib.util.spec_from_file_location("shared_eda", MODULE_PATH)
assert SPEC and SPEC.loader
eda = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(eda)


class EDAHelperTests(unittest.TestCase):
    def test_normalized_extra_var_handles_json_and_yaml_text(self) -> None:
        extra_vars = eda._activation_extra_vars("critical_signal_guardrail")
        rendered = eda._render_extra_var(extra_vars)

        self.assertEqual(
            eda._normalized_extra_var(json.dumps(extra_vars)),
            eda._normalized_extra_var(rendered),
        )

    def test_ensure_activation_skips_patch_when_extra_vars_are_semantically_equal(self) -> None:
        policy_key = "critical_signal_guardrail"
        definition = eda.POLICY_DEFINITIONS[policy_key]
        extra_vars = eda._activation_extra_vars(policy_key)
        existing = {
            "id": 41,
            "name": definition["name"],
            "description": definition["description"],
            "is_enabled": True,
            "decision_environment_id": 9,
            "rulebook_id": 17,
            "organization_id": 3,
            "restart_policy": "always",
            "log_level": "info",
            "awx_token_id": 23,
            "extra_var": json.dumps(extra_vars),
        }
        calls: list[tuple[str, str]] = []

        def _fake_request(method: str, path: str, **kwargs: object):
            calls.append((method, path))
            if method == "GET" and path == "/api/eda/v1/activations/41/":
                return existing
            raise AssertionError(f"Unexpected request {method} {path} with {kwargs}")

        with (
            mock.patch.object(eda, "_find_named_item", return_value=existing),
            mock.patch.object(eda, "_request", side_effect=_fake_request),
        ):
            result = eda._ensure_activation(
                policy_key=policy_key,
                organization_id=3,
                decision_environment_id=9,
                rulebook_id=17,
                awx_token_id=23,
            )

        self.assertEqual(result["id"], 41)
        self.assertEqual(calls, [("GET", "/api/eda/v1/activations/41/")])

    def test_ensure_activation_restarts_failed_enabled_activation(self) -> None:
        policy_key = "critical_signal_guardrail"
        definition = eda.POLICY_DEFINITIONS[policy_key]
        existing = {
            "id": 41,
            "name": definition["name"],
            "description": definition["description"],
            "is_enabled": True,
            "decision_environment_id": 9,
            "rulebook_id": 17,
            "organization_id": 3,
            "restart_policy": "always",
            "log_level": "info",
            "awx_token_id": 23,
            "extra_var": eda._render_extra_var(eda._activation_extra_vars(policy_key)),
            "status": "failed",
        }
        restarted = existing | {"status": "starting"}

        with (
            mock.patch.object(eda, "_find_named_item", return_value=existing),
            mock.patch.object(eda, "_restart_activation", return_value=restarted) as restart_activation,
        ):
            result = eda._ensure_activation(
                policy_key=policy_key,
                organization_id=3,
                decision_environment_id=9,
                rulebook_id=17,
                awx_token_id=23,
            )

        self.assertEqual(result["status"], "starting")
        restart_activation.assert_called_once_with(41)

    def test_wait_for_activation_stopped_accepts_disabled_failed_activation(self) -> None:
        calls: list[tuple[str, str]] = []

        def _fake_request(method: str, path: str, **kwargs: object):
            calls.append((method, path))
            return {"id": 41, "is_enabled": False, "status": "failed"}

        with mock.patch.object(eda, "_request", side_effect=_fake_request):
            eda._wait_for_activation_stopped(41)

        self.assertEqual(calls, [("GET", "/api/eda/v1/activations/41/")])


if __name__ == "__main__":
    unittest.main()
