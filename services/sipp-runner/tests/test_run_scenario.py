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
            "normal": "normal",
            "registration_storm": "registration_storm",
            "registration_failure": "registration_failure",
            "authentication_failure": "authentication_failure",
            "routing_error": "routing_error",
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


if __name__ == "__main__":
    unittest.main()
