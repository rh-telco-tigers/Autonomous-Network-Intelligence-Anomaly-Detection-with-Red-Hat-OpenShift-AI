import importlib.util
import json
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
SPEC = importlib.util.spec_from_file_location("rca_service_app", MODULE_PATH)
assert SPEC and SPEC.loader
rca_service_app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rca_service_app)


class RCASelectionTests(unittest.TestCase):
    def test_retrieve_rca_documents_prioritizes_exact_anomaly_knowledge(self) -> None:
        knowledge_doc = {
            "title": "Registration failure RCA",
            "reference": "knowledge/signaling/registration-failure.json",
            "content": json.dumps(
                {
                    "summary": "A targeted subscriber cohort cannot complete registration.",
                    "anomaly_types": ["registration_failure"],
                    "recommended_rca": {
                        "root_cause": "Registration completion is failing for a narrow cohort."
                    },
                }
            ),
            "doc_type": "knowledge_article",
            "collection": rca_service_app.RUNBOOK_COLLECTION,
            "category": "signaling",
            "score": 0.82,
            "anomaly_types": ["registration_failure"],
        }
        support_doc = {
            "title": "Historical incident",
            "reference": "incident/abc123.json",
            "content": json.dumps({"summary": "Historical evidence record."}),
            "doc_type": "incident_evidence",
            "collection": "incident_evidence",
            "category": "historical_rca",
            "score": 0.91,
        }

        with (
            mock.patch.object(rca_service_app, "retrieve_knowledge_articles", return_value=[knowledge_doc]),
            mock.patch.object(rca_service_app, "retrieve_context", return_value=[support_doc]),
        ):
            documents = rca_service_app._retrieve_rca_documents("incident_id=inc-1", "registration_failure")

        self.assertEqual(documents[0]["reference"], "knowledge/signaling/registration-failure.json")
        self.assertEqual(documents[0]["collection"], rca_service_app.RUNBOOK_COLLECTION)

    def test_summarize_documents_keeps_structured_metadata(self) -> None:
        document = {
            "title": "Authentication failure RCA",
            "reference": "knowledge/auth/authentication-failure.json",
            "content": json.dumps(
                {
                    "summary": "Subscribers are trapped in repeated auth challenge loops.",
                    "anomaly_types": ["authentication_failure"],
                }
            ),
            "doc_type": "knowledge_article",
            "collection": rca_service_app.RUNBOOK_COLLECTION,
            "category": "auth",
            "score": 0.88,
            "match_reasons": ["Exact anomaly match: authentication_failure"],
        }

        summaries = rca_service_app.summarize_documents([document])

        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["anomaly_types"], ["authentication_failure"])
        self.assertEqual(summaries[0]["match_reasons"], ["Exact anomaly match: authentication_failure"])
        self.assertIn("auth challenge loops", summaries[0]["excerpt"])


if __name__ == "__main__":
    unittest.main()
