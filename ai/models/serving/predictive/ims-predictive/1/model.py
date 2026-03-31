import json
from pathlib import Path

import numpy as np
import triton_python_backend_utils as pb_utils


class TritonPythonModel:
    def initialize(self, args):
        version_dir = Path(args["model_repository"]) / args["model_name"] / args["model_version"]
        weights = json.loads((version_dir / "weights.json").read_text())
        self.mean = np.asarray(weights["scaler_mean"], dtype=np.float32)
        self.scale = np.asarray(weights["scaler_scale"], dtype=np.float32)
        self.coefficients = np.asarray(weights["coefficients"], dtype=np.float32)
        self.intercept = float(weights["intercept"])

    def execute(self, requests):
        responses = []
        safe_scale = np.where(self.scale == 0, 1.0, self.scale)
        for request in requests:
            values = pb_utils.get_input_tensor_by_name(request, "predict").as_numpy().astype(np.float32)
            if values.ndim == 1:
                values = values.reshape(1, -1)
            normalized = (values - self.mean) / safe_scale
            logits = normalized @ self.coefficients + self.intercept
            probabilities = 1.0 / (1.0 + np.exp(-logits))
            output = pb_utils.Tensor("anomaly_score", probabilities.astype(np.float32).reshape(-1, 1))
            responses.append(pb_utils.InferenceResponse(output_tensors=[output]))
        return responses
