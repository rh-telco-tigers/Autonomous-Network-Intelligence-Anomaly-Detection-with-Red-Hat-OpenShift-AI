import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

if "prometheus_client" not in sys.modules:
    prometheus_client = types.ModuleType("prometheus_client")

    class _NoopMetric:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def labels(self, *_args: object, **_kwargs: object) -> "_NoopMetric":
            return self

        def inc(self, *_args: object, **_kwargs: object) -> None:
            return None

        def set(self, *_args: object, **_kwargs: object) -> None:
            return None

        def observe(self, *_args: object, **_kwargs: object) -> None:
            return None

    prometheus_client.CONTENT_TYPE_LATEST = "text/plain"
    prometheus_client.Counter = _NoopMetric
    prometheus_client.Gauge = _NoopMetric
    prometheus_client.Histogram = _NoopMetric
    prometheus_client.generate_latest = lambda: b""
    sys.modules["prometheus_client"] = prometheus_client

MODULE_PATH = Path(__file__).resolve().parents[1] / "app.py"
SPEC = importlib.util.spec_from_file_location("anomaly_service_app", MODULE_PATH)
assert SPEC and SPEC.loader
anomaly_service_app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(anomaly_service_app)


class ScoreExplainabilityTests(unittest.TestCase):
    def test_score_attaches_model_explanation_to_response_and_incident(self) -> None:
        request = anomaly_service_app.ScoreRequest(
            project="ani-demo",
            features={"register_rate": 15.0, "retransmission_count": 9.0},
            feature_window_id="fw-1",
            scenario_name="registration_storm",
        )

        explanation = {
            "provider": {"key": "trustyai", "label": "TrustyAI Explainability", "family": "Explainability"},
            "status": "available",
            "top_features": [{"feature": "register_rate", "impact": 0.41}],
        }

        with (
            mock.patch.object(anomaly_service_app, "ensure_project_access"),
            mock.patch.object(
                anomaly_service_app,
                "score_features_detailed",
                return_value={
                    "anomaly_score": 0.94,
                    "is_anomaly": True,
                    "predicted_anomaly_type": "registration_storm",
                    "predicted_confidence": 0.91,
                    "class_probabilities": {"registration_storm": 0.91, "normal_operation": 0.09},
                    "top_classes": [{"anomaly_type": "registration_storm", "probability": 0.91}],
                    "model_version": "ani-predictive-fs",
                    "scoring_mode": "remote-kserve:live",
                    "debug_trace": [],
                },
            ),
            mock.patch.object(
                anomaly_service_app,
                "current_predictive_profile",
                return_value={"profile_key": "live", "model_name": "ani-predictive-fs", "endpoint": "http://predictor"},
            ),
            mock.patch.object(anomaly_service_app, "build_model_explanation", return_value=explanation) as build_explanation,
            mock.patch.object(anomaly_service_app, "create_incident") as create_incident,
        ):
            response = anomaly_service_app.score(request, auth=None)

        self.assertEqual(response["model_explanation"]["provider"]["key"], "trustyai")
        self.assertEqual(create_incident.call_args.args[0]["model_explanation"], explanation)
        build_explanation.assert_called_once()


if __name__ == "__main__":
    unittest.main()
