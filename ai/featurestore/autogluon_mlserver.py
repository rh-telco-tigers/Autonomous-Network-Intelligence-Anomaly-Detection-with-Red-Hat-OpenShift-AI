from __future__ import annotations

import sys
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd
from autogluon.tabular import TabularPredictor
from mlserver.codecs import NumpyCodec, NumpyRequestCodec
from mlserver.errors import InferenceError
from mlserver.model import MLModel
from mlserver.types import InferenceRequest, InferenceResponse, RequestOutput, ResponseOutput
from mlserver.utils import get_model_uri

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SERVICES_ROOT = REPO_ROOT / "services"
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

from ai.training.train_and_register import FEATURES
from shared.incident_taxonomy import NORMAL_ANOMALY_TYPE, canonical_anomaly_type


OUTPUT_CLASS_PROBABILITIES = "class_probabilities"
OUTPUT_ANOMALY_SCORE = "anomaly_score"
VALID_OUTPUTS = {OUTPUT_CLASS_PROBABILITIES, OUTPUT_ANOMALY_SCORE}


class AutoGluonModel(MLModel):
    async def load(self) -> bool:
        predictor_uri = await get_model_uri(self._settings)
        self._predictor = TabularPredictor.load(predictor_uri)
        class_labels = list(getattr(self._predictor, "class_labels", []) or [])
        self._class_labels = [canonical_anomaly_type(str(label)) for label in class_labels]
        if not self._class_labels:
            probe = self._predictor.predict_proba(
                pd.DataFrame([{feature: 0.0 for feature in FEATURES}]),
                as_multiclass=True,
            )
            self._class_labels = [canonical_anomaly_type(str(label)) for label in probe.columns]
        self.ready = True
        return True

    async def predict(self, payload: InferenceRequest) -> InferenceResponse:
        payload = self._check_request(payload)
        features = self.decode_request(payload, default_codec=NumpyRequestCodec)
        frame = pd.DataFrame(features, columns=FEATURES)
        probabilities = self._predictor.predict_proba(frame, as_multiclass=True)
        outputs = self._build_outputs(payload, probabilities)
        return InferenceResponse(
            model_name=self.name,
            model_version=self.version,
            outputs=outputs,
        )

    def _check_request(self, payload: InferenceRequest) -> InferenceRequest:
        if not payload.outputs:
            payload.outputs = [
                RequestOutput(name=OUTPUT_CLASS_PROBABILITIES),
                RequestOutput(name=OUTPUT_ANOMALY_SCORE),
            ]
            return payload

        for request_output in payload.outputs:
            if request_output.name not in VALID_OUTPUTS:
                raise InferenceError(
                    f"AutoGluonModel only supports {sorted(VALID_OUTPUTS)} outputs "
                    f"({request_output.name} was received)"
                )
        return payload

    def _build_outputs(
        self,
        payload: InferenceRequest,
        probabilities: pd.DataFrame,
    ) -> List[ResponseOutput]:
        probability_rows: list[list[float]] = []
        anomaly_scores: list[list[float]] = []

        for _, row in probabilities.iterrows():
            normalized = {
                canonical_anomaly_type(str(label)): float(row[label])
                for label in probabilities.columns
            }
            probability_rows.append([normalized.get(label, 0.0) for label in self._class_labels])
            anomaly_scores.append([[max(0.0, 1.0 - normalized.get(NORMAL_ANOMALY_TYPE, 0.0))]][0])

        outputs: list[ResponseOutput] = []
        for request_output in payload.outputs or []:
            if request_output.name == OUTPUT_CLASS_PROBABILITIES:
                outputs.append(
                    self.encode(
                        np.asarray(probability_rows, dtype=np.float32),
                        request_output,
                        default_codec=NumpyCodec,
                    )
                )
                continue
            if request_output.name == OUTPUT_ANOMALY_SCORE:
                outputs.append(
                    self.encode(
                        np.asarray(anomaly_scores, dtype=np.float32),
                        request_output,
                        default_codec=NumpyCodec,
                    )
                )
                continue
        return outputs
