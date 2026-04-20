import unittest
from unittest import mock

from fastapi.testclient import TestClient

from ai.featurestore import trustyai_v1_adapter


class TrustyAIV1AdapterTests(unittest.TestCase):
    def test_predict_v1_adapts_v2_probabilities(self) -> None:
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
        self.assertEqual(response.json()["predictions"], [[0.1, 0.9]])
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
        self.assertEqual(response.json()["predictions"], [[0.1, 0.9]])


if __name__ == "__main__":
    unittest.main()
