import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

MODULE_PATH = Path(__file__).resolve().parents[1] / "db.py"
SPEC = importlib.util.spec_from_file_location("shared_db", MODULE_PATH)
assert SPEC and SPEC.loader
db = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(db)


class IncidentRcaPersistenceTests(unittest.TestCase):
    def test_attach_rca_is_idempotent_per_request_id_and_tracks_active_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "control-plane.db"
            with mock.patch.dict(os.environ, {"CONTROL_PLANE_DB_PATH": str(db_path)}, clear=False):
                db.init_db()
                db.create_incident(
                    {
                        "incident_id": "inc-db-1",
                        "project": "ani-demo",
                        "anomaly_score": 0.94,
                        "anomaly_type": "registration_storm",
                        "model_version": "predictive-v1",
                    }
                )

                base_payload = {
                    "root_cause": "Retry amplification is saturating ingress.",
                    "explanation": "Historical matches point to ingress saturation.",
                    "confidence": 0.81,
                    "evidence": [
                        {"type": "metric", "reference": "retransmission_count", "weight": 0.4},
                        {"type": "doc", "reference": "knowledge/signaling/registration-storm.json", "weight": 0.4},
                    ],
                    "recommendation": "Review ingress guardrails.",
                    "rca_request_id": "rca-1",
                    "trace_id": "trace-1",
                    "rca_state": "VALIDATED_ALLOW",
                    "guardrails": {"status": "allow", "reason": "validated"},
                }

                db.attach_rca("inc-db-1", dict(base_payload))
                db.attach_rca("inc-db-1", dict(base_payload))

                history = db.list_incident_rca("inc-db-1")
                self.assertEqual(len(history), 1)
                self.assertTrue(history[0]["is_active"])
                self.assertEqual(history[0]["request_id"], "rca-1")

                next_payload = dict(base_payload) | {
                    "rca_request_id": "rca-2",
                    "trace_id": "trace-2",
                    "root_cause": "A newer RCA result superseded the first one.",
                    "source_workflow_revision": 2,
                }
                db.attach_rca("inc-db-1", next_payload)

                history = db.list_incident_rca("inc-db-1")
                self.assertEqual(len(history), 2)
                active = [item for item in history if item["is_active"]]
                inactive = [item for item in history if not item["is_active"]]
                self.assertEqual(len(active), 1)
                self.assertEqual(active[0]["request_id"], "rca-2")
                self.assertEqual(len(inactive), 1)
                self.assertEqual(inactive[0]["request_id"], "rca-1")


class IncidentExplainabilityPersistenceTests(unittest.TestCase):
    def test_create_incident_persists_model_explanation_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "control-plane.db"
            with mock.patch.dict(os.environ, {"CONTROL_PLANE_DB_PATH": str(db_path)}, clear=False):
                db.init_db()
                incident = db.create_incident(
                    {
                        "incident_id": "inc-explain-1",
                        "project": "ani-demo",
                        "anomaly_score": 0.88,
                        "anomaly_type": "registration_storm",
                        "predicted_confidence": 0.91,
                        "model_version": "ani-predictive-fs",
                        "feature_snapshot": {"register_rate": 15.0, "retransmission_count": 9.0},
                        "model_explanation": {
                            "provider": {
                                "key": "trustyai",
                                "label": "TrustyAI Explainability",
                                "family": "Explainability",
                            },
                            "schema_version": "ani.explainability.v1",
                            "status": "available",
                            "pattern_insight": "Register Rate and Retransmission Count dominate the prediction.",
                            "explanation_confidence": "high",
                            "top_features": [
                                {
                                    "feature": "register_rate",
                                    "label": "Register Rate",
                                    "impact": 0.45,
                                    "raw_impact": 0.45,
                                    "direction": "increase",
                                    "display_value": "15.0",
                                    "tone": "rose",
                                }
                            ],
                        },
                    }
                )

                self.assertIsInstance(incident["model_explanation"], dict)
                self.assertEqual(incident["model_explanation"]["provider"]["key"], "trustyai")
                self.assertEqual(incident["model_explanation"]["top_features"][0]["feature"], "register_rate")


class IncidentListTests(unittest.TestCase):
    def test_list_incidents_applies_recent_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "control-plane.db"
            with mock.patch.dict(os.environ, {"CONTROL_PLANE_DB_PATH": str(db_path)}, clear=False):
                db.init_db()
                for index in range(3):
                    db.create_incident(
                        {
                            "incident_id": f"inc-list-{index}",
                            "project": "ani-demo",
                            "anomaly_score": 0.70 + index / 100,
                            "anomaly_type": "registration_storm",
                            "model_version": "predictive-v1",
                            "created_at": f"2026-01-01T00:00:0{index}+00:00",
                        }
                    )

                incidents = db.list_incidents(project="ani-demo", limit=2)

                self.assertEqual([incident["id"] for incident in incidents], ["inc-list-2", "inc-list-1"])


if __name__ == "__main__":
    unittest.main()
