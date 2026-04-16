import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

MODULE_PATH = Path(__file__).resolve().parents[1] / "model_store.py"
SPEC = importlib.util.spec_from_file_location("shared_model_store", MODULE_PATH)
assert SPEC and SPEC.loader
model_store = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(model_store)


CANONICAL_LABELS = [
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
]


class _FakeResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"unexpected status {self.status_code}")


class ModelStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        model_store._CLASSIFIER_PROFILE_CACHE = None
        model_store._CLASSIFIER_PROFILE_CACHE_EXPIRES_AT = 0.0

    def _registry(self) -> dict[str, object]:
        return {
            "deployed_model_version": "predictive-serving-v1",
            "models": [
                {
                    "version": "predictive-serving-v1",
                    "kind": "triton_python_multiclass_logistic_regression",
                    "artifact": "models/serving/predictive/ani-predictive/1/weights.json",
                    "class_labels": CANONICAL_LABELS,
                }
            ],
        }

    def test_remote_prediction_does_not_require_local_artifact(self) -> None:
        response = _FakeResponse(
            {
                "outputs": [
                    {
                        "name": "class_probabilities",
                        "datatype": "FP32",
                        "shape": [1, len(CANONICAL_LABELS)],
                        "data": [[0.01, 0.92, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.0, 0.01]],
                    },
                    {
                        "name": "anomaly_score",
                        "datatype": "FP32",
                        "shape": [1, 1],
                        "data": [[0.99]],
                    },
                ]
            }
        )

        with (
            mock.patch.object(model_store, "load_registry", return_value=self._registry()),
            mock.patch.object(model_store.requests, "post", return_value=response),
            mock.patch.dict(
                model_store.os.environ,
                {
                    "PREDICTIVE_ENDPOINT": "http://predictive.example.com",
                    "PREDICTIVE_MODEL_NAME": "ani-predictive-fs",
                },
                clear=False,
            ),
        ):
            result = model_store.score_features_detailed({"register_rate": 12.0}, anomaly_type_hint="registration_storm")

        self.assertEqual(result["predicted_anomaly_type"], "registration_storm")
        self.assertEqual(result["scoring_mode"], "remote-kserve:live")
        self.assertEqual(result["model_version"], "ani-predictive-fs")
        self.assertTrue(result["is_anomaly"])

    def test_missing_local_artifact_raises_clear_error_when_remote_unavailable(self) -> None:
        with (
            mock.patch.object(model_store, "load_registry", return_value=self._registry()),
            mock.patch.object(model_store.requests, "post", side_effect=RuntimeError("predictive service unavailable")),
            mock.patch.dict(
                model_store.os.environ,
                {
                    "PREDICTIVE_ENDPOINT": "http://predictive.example.com",
                    "PREDICTIVE_MODEL_NAME": "ani-predictive-fs",
                },
                clear=False,
            ),
        ):
            with self.assertRaises(model_store.ModelUnavailableError) as raised:
                model_store.score_features_detailed({"register_rate": 12.0}, anomaly_type_hint="registration_storm")

        self.assertIn("Remote predictive endpoint http://predictive.example.com", str(raised.exception))
        self.assertIn("weights.json", str(raised.exception))

    def test_control_plane_selected_backfill_profile_uses_backfill_endpoint(self) -> None:
        response = _FakeResponse(
            {
                "outputs": [
                    {
                        "name": "class_probabilities",
                        "datatype": "FP32",
                        "shape": [1, len(CANONICAL_LABELS)],
                        "data": [[0.8, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.1, 0.01]],
                    }
                ]
            }
        )

        with (
            mock.patch.object(model_store, "load_registry", return_value=self._registry()),
            mock.patch.object(
                model_store.requests,
                "get",
                return_value=_FakeResponse(
                    {
                        "requested_profile": "backfill",
                        "active_profile": "backfill",
                        "profiles": [],
                    }
                ),
            ),
            mock.patch.object(model_store.requests, "post", return_value=response) as post_request,
            mock.patch.dict(
                model_store.os.environ,
                {
                    "CONTROL_PLANE_URL": "http://control-plane.example.com",
                    "PREDICTIVE_ENDPOINT_LIVE": "http://predictive-live.example.com",
                    "PREDICTIVE_MODEL_NAME_LIVE": "ani-predictive-fs",
                    "PREDICTIVE_ENDPOINT_BACKFILL": "http://predictive-backfill.example.com",
                    "PREDICTIVE_MODEL_NAME_BACKFILL": "ani-predictive-backfill",
                    "PREDICTIVE_MODEL_VERSION_LABEL_BACKFILL": "ani-predictive-backfill",
                },
                clear=False,
            ),
        ):
            result = model_store.score_features_detailed({"register_rate": 0.1}, anomaly_type_hint="network_degradation")

        self.assertEqual(result["classifier_profile"], "backfill")
        self.assertEqual(result["model_version"], "ani-predictive-backfill")
        self.assertEqual(post_request.call_args.args[0], "http://predictive-backfill.example.com/v2/models/ani-predictive-backfill/infer")

    def test_backfill_profile_does_not_fall_back_to_live_artifact_when_remote_fails(self) -> None:
        with (
            mock.patch.object(model_store, "load_registry", return_value=self._registry()),
            mock.patch.object(
                model_store.requests,
                "get",
                return_value=_FakeResponse(
                    {
                        "requested_profile": "backfill",
                        "active_profile": "backfill",
                        "profiles": [],
                    }
                ),
            ),
            mock.patch.object(model_store.requests, "post", side_effect=RuntimeError("predictive service unavailable")),
            mock.patch.dict(
                model_store.os.environ,
                {
                    "CONTROL_PLANE_URL": "http://control-plane.example.com",
                    "PREDICTIVE_ENDPOINT_LIVE": "http://predictive-live.example.com",
                    "PREDICTIVE_MODEL_NAME_LIVE": "ani-predictive-fs",
                    "PREDICTIVE_ENDPOINT_BACKFILL": "http://predictive-backfill.example.com",
                    "PREDICTIVE_MODEL_NAME_BACKFILL": "ani-predictive-backfill",
                    "PREDICTIVE_MODEL_VERSION_LABEL_BACKFILL": "ani-predictive-backfill",
                },
                clear=False,
            ),
        ):
            with self.assertRaises(model_store.ModelUnavailableError) as raised:
                model_store.score_features_detailed({"register_rate": 0.1}, anomaly_type_hint="network_degradation")

        self.assertIn("classifier profile backfill", str(raised.exception))
        self.assertIn("http://predictive-backfill.example.com", str(raised.exception))

    def test_control_plane_selected_modelcar_profile_uses_modelcar_endpoint(self) -> None:
        response = _FakeResponse(
            {
                "outputs": [
                    {
                        "name": "class_probabilities",
                        "datatype": "FP32",
                        "shape": [1, len(CANONICAL_LABELS)],
                        "data": [[0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.89, 0.01]],
                    }
                ]
            }
        )

        with (
            mock.patch.object(model_store, "load_registry", return_value=self._registry()),
            mock.patch.object(
                model_store.requests,
                "get",
                return_value=_FakeResponse(
                    {
                        "requested_profile": "modelcar",
                        "active_profile": "modelcar",
                        "profiles": [],
                    }
                ),
            ),
            mock.patch.object(model_store.requests, "post", return_value=response) as post_request,
            mock.patch.dict(
                model_store.os.environ,
                {
                    "CONTROL_PLANE_URL": "http://control-plane.example.com",
                    "PREDICTIVE_ENDPOINT_LIVE": "http://predictive-live.example.com",
                    "PREDICTIVE_MODEL_NAME_LIVE": "ani-predictive-fs",
                    "PREDICTIVE_ENDPOINT_BACKFILL": "http://predictive-backfill.example.com",
                    "PREDICTIVE_MODEL_NAME_BACKFILL": "ani-predictive-backfill",
                    "PREDICTIVE_ENDPOINT_MODELCAR": "http://predictive-modelcar.example.com",
                    "PREDICTIVE_MODEL_NAME_MODELCAR": "ani-predictive-backfill-modelcar",
                    "PREDICTIVE_MODEL_VERSION_LABEL_MODELCAR": "ani-predictive-backfill-modelcar",
                },
                clear=False,
            ),
        ):
            result = model_store.score_features_detailed({"register_rate": 0.1}, anomaly_type_hint="network_degradation")

        self.assertEqual(result["classifier_profile"], "modelcar")
        self.assertEqual(result["model_version"], "ani-predictive-backfill-modelcar")
        self.assertEqual(
            post_request.call_args.args[0],
            "http://predictive-modelcar.example.com/v2/models/ani-predictive-backfill-modelcar/infer",
        )


if __name__ == "__main__":
    unittest.main()
