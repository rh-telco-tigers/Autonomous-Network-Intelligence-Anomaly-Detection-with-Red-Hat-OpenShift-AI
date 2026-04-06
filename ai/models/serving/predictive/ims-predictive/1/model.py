import json
from pathlib import Path

import numpy as np
import triton_python_backend_utils as pb_utils


class TritonPythonModel:
    def initialize(self, args):
        version_dir = Path(__file__).resolve().parent
        weights = json.loads((version_dir / "weights.json").read_text())
        self.mean = np.asarray(weights["scaler_mean"], dtype=np.float32)
        self.scale = np.asarray(weights["scaler_scale"], dtype=np.float32)
        self.coefficients = np.asarray(weights["coefficients"], dtype=np.float32)
        self.intercepts = np.asarray(weights["intercepts"], dtype=np.float32)
        self.class_labels = list(weights["class_labels"])
        self.normal_index = self.class_labels.index(weights["normal_class_label"])

    def execute(self, requests):
        responses = []
        safe_scale = np.where(self.scale == 0, 1.0, self.scale)
        for request in requests:
            values = pb_utils.get_input_tensor_by_name(request, "predict").as_numpy().astype(np.float32)
            if values.ndim == 1:
                values = values.reshape(1, -1)
            normalized = (values - self.mean) / safe_scale
            logits = normalized @ self.coefficients.T + self.intercepts
            logits = logits - np.max(logits, axis=1, keepdims=True)
            probabilities = np.exp(logits)
            probabilities = probabilities / np.sum(probabilities, axis=1, keepdims=True)
            anomaly_scores = 1.0 - probabilities[:, [self.normal_index]]
            responses.append(
                pb_utils.InferenceResponse(
                    output_tensors=[
                        pb_utils.Tensor("class_probabilities", probabilities.astype(np.float32)),
                        pb_utils.Tensor("anomaly_score", anomaly_scores.astype(np.float32)),
                    ]
                )
            )
        return responses
