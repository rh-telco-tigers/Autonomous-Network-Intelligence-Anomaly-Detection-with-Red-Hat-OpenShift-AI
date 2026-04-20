import unittest
from unittest import mock

from fastapi.testclient import TestClient

from ai.featurestore import trustyai_v1_adapter


class TrustyAIV1AdapterTests(unittest.TestCase):
    def test_predict_v1_adapts_v2_probabilities_to_scalar_class_predictions(self) -> None:
        class _Response:
            ok = True
            status_code = 200
            text = '{"outputs":[{"name":"class_probabilities","data":[[0.1,0.9]]}]}'

            def json(self):
                return {
                    "outputs": [
                        {"name": "class_probabilities", "data": [[0.1, 0.9]]},
                    ]
                }

        client = TestClient(trustyai_v1_adapter.app)
        with mock.patch.object(trustyai_v1_adapter.requests, "post", return_value=_Response()) as post:
            response = client.post(
                "/v1/models/ani-predictive-fs:predict",
                json={"instances": [[12.0, 0.0, 0.0, 0.2, 0.05, 7.5, 15.0, 0.1, 0.0]]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["predictions"], [1])
        self.assertIn("/v2/models/ani-predictive-fs/infer", post.call_args.args[0])

    def test_predict_v1_wraps_flat_probability_vector_for_single_row(self) -> None:
        class _Response:
            ok = True
            status_code = 200
            text = '{"outputs":[{"name":"class_probabilities","data":[0.1,0.9]}]}'

            def json(self):
                return {
                    "outputs": [
                        {"name": "class_probabilities", "data": [0.1, 0.9]},
                    ]
                }

        client = TestClient(trustyai_v1_adapter.app)
        with mock.patch.object(trustyai_v1_adapter.requests, "post", return_value=_Response()):
            response = client.post(
                "/v1/models/ani-predictive-fs:predict",
                json={"instances": [[12.0, 0.0, 0.0, 0.2, 0.05, 7.5, 15.0, 0.1, 0.0]]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["predictions"], [1])

    def test_predict_v1_prefers_scalar_anomaly_score_when_available(self) -> None:
        class _Response:
            ok = True
            status_code = 200
            text = '{"outputs":[{"name":"class_probabilities","data":[[0.1,0.9]]},{"name":"anomaly_score","data":[[0.42]]}]}'

            def json(self):
                return {
                    "outputs": [
                        {"name": "class_probabilities", "data": [[0.1, 0.9]]},
                        {"name": "anomaly_score", "data": [[0.42]]},
                    ]
                }

        client = TestClient(trustyai_v1_adapter.app)
        with mock.patch.object(trustyai_v1_adapter.requests, "post", return_value=_Response()):
            response = client.post(
                "/v1/models/ani-predictive-fs:predict",
                json={"instances": [[12.0, 0.0, 0.0, 0.2, 0.05, 7.5, 15.0, 0.1, 0.0]]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["predictions"], [0.42])

    def test_predict_v1_accepts_root_list_payload(self) -> None:
        class _Response:
            ok = True
            status_code = 200
            text = '{"outputs":[{"name":"class_probabilities","data":[[0.9,0.1]]}]}'

            def json(self):
                return {
                    "outputs": [
                        {"name": "class_probabilities", "data": [[0.9, 0.1]]},
                    ]
                }

        client = TestClient(trustyai_v1_adapter.app)
        with mock.patch.object(trustyai_v1_adapter.requests, "post", return_value=_Response()):
            response = client.post(
                "/v1/models/ani-predictive-fs:predict",
                json=[[12.0, 0.0, 0.0, 0.2, 0.05, 7.5, 15.0, 0.1, 0.0]],
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["predictions"], [0])

    def test_predict_v1_accepts_v2_style_inputs_payload(self) -> None:
        class _Response:
            ok = True
            status_code = 200
            text = '{"outputs":[{"name":"class_probabilities","data":[[0.1,0.9]]}]}'

            def json(self):
                return {
                    "outputs": [
                        {"name": "class_probabilities", "data": [[0.1, 0.9]]},
                    ]
                }

        client = TestClient(trustyai_v1_adapter.app)
        with mock.patch.object(trustyai_v1_adapter.requests, "post", return_value=_Response()):
            response = client.post(
                "/v1/models/ani-predictive-fs:predict",
                json={
                    "inputs": [
                        {
                            "name": "predict",
                            "shape": [1, 9],
                            "datatype": "FP32",
                            "data": [[12.0, 0.0, 0.0, 0.2, 0.05, 7.5, 15.0, 0.1, 0.0]],
                        }
                    ]
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["predictions"], [1])


if __name__ == "__main__":
    unittest.main()
