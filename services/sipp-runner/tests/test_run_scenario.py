import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "run_scenario.py"
SPEC = importlib.util.spec_from_file_location("sipp_runner_run_scenario", MODULE_PATH)
assert SPEC and SPEC.loader
run_scenario = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_scenario)


class ScenarioAnomalyTypeTests(unittest.TestCase):
    def test_known_categories_preserve_requested_labels(self) -> None:
        expected = {
            "normal": "normal_operation",
            "normal_operation": "normal_operation",
            "registration_storm": "registration_storm",
            "registration_failure": "registration_failure",
            "authentication_failure": "authentication_failure",
            "routing_error": "routing_error",
            "busy_destination": "busy_destination",
            "call_setup_timeout": "call_setup_timeout",
            "call_drop_mid_session": "call_drop_mid_session",
            "server_internal_error": "server_internal_error",
            "network_degradation": "network_degradation",
            "retransmission_spike": "retransmission_spike",
        }
        for scenario_name, anomaly_type in expected.items():
            with self.subTest(scenario_name=scenario_name):
                self.assertEqual(run_scenario._scenario_anomaly_type(scenario_name), anomaly_type)

    def test_malformed_invite_normalizes_to_existing_taxonomy(self) -> None:
        self.assertEqual(run_scenario._scenario_anomaly_type("malformed_invite"), "malformed_sip")

    def test_unknown_scenario_falls_back_to_unknown(self) -> None:
        self.assertEqual(run_scenario._scenario_anomaly_type(""), "unknown")

    def test_contributing_conditions_capture_overlap_signals(self) -> None:
        conditions = run_scenario._derive_contributing_conditions(
            anomaly_type="call_setup_timeout",
            features={
                "register_rate": 0.0,
                "invite_rate": 3.2,
                "bye_rate": 0.0,
                "error_4xx_ratio": 0.0,
                "error_5xx_ratio": 1.0,
                "latency_p95": 320.0,
                "retransmission_count": 6.0,
                "inter_arrival_mean": 0.2,
                "payload_variance": 48.0,
            },
            response_codes=[408, 502, 502],
            auth_challenge_count=0,
            retransmissions=6.0,
        )
        self.assertIn("session_setup_delay", conditions)
        self.assertIn("latency_high", conditions)
        self.assertIn("retry_spike", conditions)

    def test_normal_operation_has_no_contributing_conditions(self) -> None:
        self.assertEqual(
            run_scenario._derive_contributing_conditions(
                anomaly_type="normal_operation",
                features={feature: 0.0 for feature in run_scenario.NUMERIC_FEATURES},
                response_codes=[200],
                auth_challenge_count=0,
                retransmissions=0.0,
            ),
            [],
        )

    def test_score_feature_window_uses_latest_anomaly_service(self) -> None:
        window = {
            "window_id": "live-sipp-v1-registration_failure-1",
            "scenario_name": "registration_failure",
            "anomaly_type": "registration_failure",
            "source": "openims-sipp-lab",
            "feature_source": "sipp-shortmessages",
            "transport": "udp",
            "call_limit": 24,
            "rate": 4,
            "target": "ims-pcscf.ims-demo-lab.svc.cluster.local:5060",
            "scenario_file": "/scenarios/register-failure.xml",
            "contributing_conditions": ["registration_reject", "auth_challenge_loop"],
            "features": {
                "register_rate": 2.4,
                "invite_rate": 0.0,
                "bye_rate": 0.0,
                "error_4xx_ratio": 0.62,
                "error_5xx_ratio": 0.0,
                "latency_p95": 148.0,
                "retransmission_count": 6.0,
                "inter_arrival_mean": 0.3,
                "payload_variance": 18.0,
            },
            "sipp_summary": {
                "transport": "udp",
                "call_limit": 24,
                "rate": 4,
                "target": "ims-pcscf.ims-demo-lab.svc.cluster.local:5060",
                "scenario_file": "/scenarios/register-failure.xml",
                "response_codes": [401, 403, 403],
            },
        }
        response = mock.Mock()
        response.json.return_value = {
            "incident_id": "inc-123",
            "anomaly_type": "registration_failure",
            "model_version": "candidate-fs-v1",
        }

        with (
            mock.patch.dict(
                run_scenario.os.environ,
                {
                    "SIPP_EMIT_CONTROL_PLANE_INCIDENT": "true",
                    "CONTROL_PLANE_PROJECT": "ims-demo",
                    "CONTROL_PLANE_API_KEY": "demo-token",
                },
                clear=False,
            ),
            mock.patch.object(run_scenario.requests, "post", return_value=response) as post,
        ):
            result = run_scenario._score_feature_window(window)

        self.assertEqual(result["incident_id"], "inc-123")
        post.assert_called_once()
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["scenario_name"], "registration_failure")
        self.assertEqual(payload["anomaly_type_hint"], "registration_failure")
        self.assertEqual(payload["features"]["scenario_name"], "registration_failure")
        self.assertEqual(payload["features"]["feature_source"], "sipp-shortmessages")

    def test_score_feature_window_ignores_nominal_result(self) -> None:
        window = {
            "window_id": "live-sipp-v1-normal-1",
            "scenario_name": "normal_operation",
            "anomaly_type": "normal_operation",
            "features": {"register_rate": 0.2},
            "sipp_summary": {},
        }
        response = mock.Mock()
        response.json.return_value = {"incident_id": None, "anomaly_type": "normal_operation"}

        with (
            mock.patch.dict(run_scenario.os.environ, {"SIPP_EMIT_CONTROL_PLANE_INCIDENT": "true"}, clear=False),
            mock.patch.object(run_scenario.requests, "post", return_value=response),
        ):
            result = run_scenario._score_feature_window(window)

        self.assertIsNone(result)

    def test_emit_control_plane_incident_uses_multiclass_payload(self) -> None:
        window = {
            "window_id": "live-sipp-v1-routing_error-1",
            "label": 1,
            "label_confidence": 0.91,
            "anomaly_type": "routing_error",
            "features": {"invite_rate": 1.6},
            "contributing_conditions": ["route_unreachable"],
        }
        response = mock.Mock()
        response.json.return_value = {"id": "inc-routing"}

        with (
            mock.patch.dict(
                run_scenario.os.environ,
                {"SIPP_EMIT_CONTROL_PLANE_INCIDENT": "true"},
                clear=False,
            ),
            mock.patch.object(run_scenario.requests, "post", return_value=response) as post,
        ):
            result = run_scenario._emit_control_plane_incident(window)

        self.assertEqual(result["id"], "inc-routing")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["anomaly_type"], "routing_error")
        self.assertAlmostEqual(payload["predicted_confidence"], 0.91)
        self.assertEqual(payload["top_classes"][0]["anomaly_type"], "routing_error")
        self.assertTrue(payload["is_anomaly"])


class BulkBackfillTests(unittest.TestCase):
    def _args(self, **overrides):
        values = {
            "dataset_version": "backfill-sipp-100k-v1",
            "scenario_name": "registration_failure",
            "repeat_count": 3,
            "repeat_sleep_seconds": 0.0,
            "progress_every": 0,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_positive_repeat_count_rejects_zero(self) -> None:
        with self.assertRaises(ValueError):
            run_scenario._positive_repeat_count(0)

    def test_run_repeated_returns_bulk_summary(self) -> None:
        args = self._args(repeat_count=3)
        results = [
            {"window_uri": "s3://bucket/window-1.json", "window": {"sipp_summary": {"return_code": 0}}, "incident": None},
            {"window_uri": "s3://bucket/window-2.json", "window": {"sipp_summary": {"return_code": 1}}, "incident": {"id": "inc-2"}},
            {"window_uri": "s3://bucket/window-3.json", "window": {"sipp_summary": {"return_code": 0}}, "incident": {"id": "inc-3"}},
        ]
        with mock.patch.object(run_scenario, "_run_once", side_effect=results):
            summary = run_scenario._run_repeated(args)

        self.assertEqual(summary["dataset_version"], "backfill-sipp-100k-v1")
        self.assertEqual(summary["scenario_name"], "registration_failure")
        self.assertEqual(summary["repeat_count"], 3)
        self.assertEqual(summary["windows_created"], 3)
        self.assertEqual(summary["control_plane_incidents_emitted"], 2)
        self.assertEqual(summary["completed_with_sipp_errors"], 1)
        self.assertEqual(summary["first_window_uri"], "s3://bucket/window-1.json")
        self.assertEqual(summary["last_window_uri"], "s3://bucket/window-3.json")

    def test_run_repeated_sleeps_between_iterations(self) -> None:
        args = self._args(repeat_count=3, repeat_sleep_seconds=0.5)
        result = {"window_uri": "s3://bucket/window.json", "window": {"sipp_summary": {"return_code": 0}}, "incident": None}
        with (
            mock.patch.object(run_scenario, "_run_once", return_value=result),
            mock.patch.object(run_scenario.time, "sleep") as sleep,
        ):
            run_scenario._run_repeated(args)

        self.assertEqual(sleep.call_count, 2)
        sleep.assert_has_calls([mock.call(0.5), mock.call(0.5)])


if __name__ == "__main__":
    unittest.main()
