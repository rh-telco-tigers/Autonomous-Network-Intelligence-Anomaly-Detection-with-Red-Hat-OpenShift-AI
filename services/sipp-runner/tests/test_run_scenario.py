import importlib.util
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
