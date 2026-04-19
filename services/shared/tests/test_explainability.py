import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

MODULE_PATH = Path(__file__).resolve().parents[1] / "explainability.py"
SPEC = importlib.util.spec_from_file_location("shared_explainability", MODULE_PATH)
assert SPEC and SPEC.loader
explainability = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(explainability)


class ExplainabilityFallbackTests(unittest.TestCase):
    def test_build_model_explanation_falls_back_to_heuristic_shape(self) -> None:
        with mock.patch.dict(os.environ, {"ANI_INCIDENT_EXPLAINABILITY_TRUSTYAI_ENABLED": "false"}, clear=False):
            payload = explainability.build_model_explanation(
                {
                    "register_rate": 15.0,
                    "retransmission_count": 9.0,
                    "latency_p95": 180.0,
                    "error_4xx_ratio": 0.12,
                },
                anomaly_type="registration_storm",
                predicted_confidence=0.91,
                model_version="ani-predictive-fs",
            )

        self.assertEqual(payload["provider"]["key"], "local_heuristic")
        self.assertEqual(payload["status"], "fallback")
        self.assertTrue(payload["top_features"])
        self.assertEqual(payload["prediction"]["anomaly_type"], "registration_storm")
        self.assertIn("dominant signals", payload["pattern_insight"].lower())

    def test_legacy_explainability_items_uses_top_features(self) -> None:
        legacy_items = explainability.legacy_explainability_items(
            {
                "top_features": [
                    {
                        "feature": "register_rate",
                        "label": "Register Rate",
                        "impact": 0.45,
                        "tone": "rose",
                    }
                ]
            }
        )
        self.assertEqual(legacy_items[0]["feature"], "register_rate")
        self.assertEqual(legacy_items[0]["weight"], 0.45)


class ExplainabilityTrustyAITests(unittest.TestCase):
    def test_build_model_explanation_marks_trustyai_when_attributions_are_returned(self) -> None:
        class _Response:
            ok = True
            status_code = 200
            text = '{"explanations":[{"feature":"register_rate","impact":0.41},{"feature":"retransmission_count","impact":0.27}]}'

            def json(self):
                return {
                    "explanations": [
                        {"feature": "register_rate", "impact": 0.41},
                        {"feature": "retransmission_count", "impact": 0.27},
                    ]
                }

        with (
            mock.patch.dict(
                os.environ,
                {
                    "ANI_INCIDENT_EXPLAINABILITY_TRUSTYAI_ENABLED": "true",
                    "TRUSTYAI_EXPLAINABILITY_ENDPOINT": "https://trustyai.example.com/explain",
                    "TRUSTYAI_EXPLAINABILITY_VERIFY_TLS": "false",
                },
                clear=False,
            ),
            mock.patch.object(explainability.requests, "post", return_value=_Response()),
        ):
            payload = explainability.build_model_explanation(
                {"register_rate": 15.0, "retransmission_count": 9.0},
                anomaly_type="registration_storm",
                predicted_confidence=0.9,
                model_version="ani-predictive-fs",
            )

        self.assertEqual(payload["provider"]["key"], "trustyai")
        self.assertEqual(payload["status"], "available")
        self.assertEqual(payload["top_features"][0]["feature"], "register_rate")


if __name__ == "__main__":
    unittest.main()
