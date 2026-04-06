import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "incident_taxonomy.py"
SPEC = importlib.util.spec_from_file_location("shared_incident_taxonomy", MODULE_PATH)
assert SPEC and SPEC.loader
incident_taxonomy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(incident_taxonomy)


class IncidentTaxonomyTests(unittest.TestCase):
    def test_canonical_anomaly_types_are_stable_and_complete(self) -> None:
        self.assertEqual(
            incident_taxonomy.canonical_anomaly_types(),
            [
                "normal_operation",
                "registration_storm",
                "registration_failure",
                "authentication_failure",
                "malformed_sip",
                "routing_error",
                "busy_destination",
                "call_setup_timeout",
                "call_drop_mid_session",
                "server_internal_error",
                "network_degradation",
                "retransmission_spike",
            ],
        )

    def test_aliases_resolve_to_canonical_multiclass_labels(self) -> None:
        expected = {
            "normal": "normal_operation",
            "malformed_invite": "malformed_sip",
            "register_storm": "registration_storm",
            "service_degradation": "network_degradation",
            "hss_latency": "network_degradation",
            "hss_overload": "network_degradation",
        }
        for raw_value, canonical in expected.items():
            with self.subTest(raw_value=raw_value):
                self.assertEqual(incident_taxonomy.canonical_anomaly_type(raw_value), canonical)

    def test_every_scenario_definition_emits_canonical_anomaly_type(self) -> None:
        canonical_labels = set(incident_taxonomy.canonical_anomaly_types())
        for scenario_name in incident_taxonomy.console_scenario_names():
            with self.subTest(scenario_name=scenario_name):
                definition = incident_taxonomy.scenario_definition(scenario_name)
                self.assertIn(definition["anomaly_type"], canonical_labels)


if __name__ == "__main__":
    unittest.main()
