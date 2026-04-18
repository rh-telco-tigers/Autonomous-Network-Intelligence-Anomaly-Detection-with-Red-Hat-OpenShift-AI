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

    def test_infer_explanation_rewrites_meta_runbook_guidance(self) -> None:
        root_cause = "One server-side control-plane tier is returning 5xx because it is overloaded or blocked on a degraded backend dependency."
        document = {
            "title": "Server internal error RCA",
            "reference": "knowledge/server/server-internal-error.json",
            "content": json.dumps(
                {
                    "summary": "Server-side failures are clustering on one execution tier.",
                    "anomaly_types": ["server_internal_error"],
                    "symptom_profile": {
                        "primary_signals": [
                            "5xx responses increase with rising queue depth and service latency on one server-side control-plane tier.",
                            "A specific revision, pod, node, or dependency path is materially worse than its peers.",
                        ]
                    },
                    "recommended_rca": {
                        "root_cause": root_cause,
                        "explanation": "server_internal_error should focus on the execution tier that is actually returning 5xx.",
                    },
                }
            ),
            "doc_type": "knowledge_article",
            "collection": rca_service_app.RUNBOOK_COLLECTION,
            "category": "server",
            "score": 0.95,
        }

        explanation = rca_service_app.infer_explanation("server_internal_error", root_cause, [document])

        self.assertIn("One server-side control-plane tier is returning 5xx", explanation)
        self.assertIn("5xx responses increase with rising queue depth", explanation)
        self.assertIn("A specific revision, pod, node, or dependency path is materially worse than its peers", explanation)
        self.assertNotIn("server_internal_error should focus", explanation)
        self.assertNotIn("Matched operational guidance came from", explanation)

    def test_infer_explanation_keeps_user_facing_runbook_copy_clean(self) -> None:
        root_cause = "Authentication failures are concentrated on one subscriber cohort."
        document = {
            "title": "Authentication failure RCA",
            "reference": "knowledge/auth/authentication-failure.json",
            "content": json.dumps(
                {
                    "summary": "One subscriber cohort is stuck in repeated auth challenge loops.",
                    "anomaly_types": ["authentication_failure"],
                    "recommended_rca": {
                        "root_cause": root_cause,
                        "explanation": "Authentication failures are concentrated on one subscriber cohort because the auth state or backend lookup path is unstable.",
                    },
                }
            ),
            "doc_type": "knowledge_article",
            "collection": rca_service_app.RUNBOOK_COLLECTION,
            "category": "auth",
            "score": 0.9,
        }

        explanation = rca_service_app.infer_explanation("authentication_failure", root_cause, [document])

        self.assertEqual(
            explanation,
            "Authentication failures are concentrated on one subscriber cohort because the auth state or backend lookup path is unstable.",
        )


class GuardrailsFallbackTests(unittest.TestCase):
    def _documents(self) -> list[dict[str, object]]:
        return [
            {
                "title": "Registration storm RCA",
                "reference": "knowledge/signaling/registration-storm.json",
                "content": json.dumps(
                    {
                        "summary": "Retry amplification is saturating the ingress tier.",
                        "anomaly_types": ["registration_storm"],
                        "recommended_rca": {
                            "root_cause": "P-CSCF registration saturation is driving retransmission pressure.",
                            "explanation": "Retry amplification is clustered on the ingress tier and historical matches align to a registration storm.",
                        },
                    }
                ),
                "doc_type": "knowledge_article",
                "collection": rca_service_app.RUNBOOK_COLLECTION,
                "category": "signaling",
                "score": 0.95,
            },
            {
                "title": "Historical evidence",
                "reference": "incident/registration-storm-1.json",
                "content": json.dumps({"summary": "4xx ratios and retries rose together."}),
                "doc_type": "incident_evidence",
                "collection": "incident_evidence",
                "category": "historical_rca",
                "score": 0.8,
            },
        ]

    def test_rca_uses_guardrails_blocked_response_instead_of_local_fallback(self) -> None:
        request = rca_service_app.RCARequest(
            incident_id="INC-100",
            context={"anomaly_type": "registration_storm"},
        )
        llm_trace = {
            "parsed": None,
            "raw_content": "Warning: Unsuitable input detected. Input Detections: prompt injection",
            "response_payload": {"raw_text": "Warning: Unsuitable input detected. Input Detections: prompt injection"},
            "trace_packets": [],
        }

        with (
            mock.patch.dict(
                rca_service_app.os.environ,
                {
                    "LLM_ENDPOINT": "http://guardrails-gateway.ani-datascience.svc.cluster.local/rca",
                    "LLM_MODEL": "llama-32-3b-instruct",
                },
                clear=False,
            ),
            mock.patch.object(rca_service_app, "_retrieve_rca_documents", return_value=self._documents()),
            mock.patch.object(rca_service_app, "generate_with_llm_trace", return_value=llm_trace),
            mock.patch.object(rca_service_app, "attach_rca"),
            mock.patch.object(rca_service_app, "record_rca"),
        ):
            response = rca_service_app.rca(request)

        self.assertEqual(response["generation_mode"], "guardrails-blocked")
        self.assertEqual(response["guardrails"]["status"], "block")
        self.assertEqual(response["guardrails"]["reason"], "input_blocked")
        self.assertIn("Guardrails blocked", response["root_cause"])

    def test_rca_surfaces_guardrails_errors_without_bypassing_validation(self) -> None:
        request = rca_service_app.RCARequest(
            incident_id="INC-101",
            context={"anomaly_type": "registration_storm"},
        )
        llm_trace = {
            "parsed": None,
            "raw_content": "",
            "response_payload": {"error": "connection timed out"},
            "trace_packets": [],
        }

        with (
            mock.patch.dict(
                rca_service_app.os.environ,
                {
                    "LLM_ENDPOINT": "http://guardrails-gateway.ani-datascience.svc.cluster.local/rca",
                    "LLM_MODEL": "llama-32-3b-instruct",
                },
                clear=False,
            ),
            mock.patch.object(rca_service_app, "_retrieve_rca_documents", return_value=self._documents()),
            mock.patch.object(rca_service_app, "generate_with_llm_trace", return_value=llm_trace),
            mock.patch.object(rca_service_app, "attach_rca"),
            mock.patch.object(rca_service_app, "record_rca"),
        ):
            response = rca_service_app.rca(request)

        self.assertEqual(response["generation_mode"], "guardrails-error")
        self.assertEqual(response["guardrails"]["status"], "error")
        self.assertIn("Guardrails could not validate", response["root_cause"])

    def test_rca_recovers_valid_payload_from_guarded_gateway_response_body(self) -> None:
        request = rca_service_app.RCARequest(
            incident_id="INC-101B",
            context={"anomaly_type": "registration_storm"},
        )
        llm_trace = {
            "parsed": None,
            "raw_content": json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "root_cause": "Retry amplification is saturating the P-CSCF ingress path.",
                                        "explanation": "Historical matches and current evidence both point to ingress saturation.",
                                        "confidence": 0.84,
                                        "evidence": [
                                            {"type": "doc", "reference": "knowledge/signaling/registration-storm.json", "weight": 0.4},
                                            {"type": "metric", "reference": "retransmission_count", "weight": 0.4},
                                        ],
                                        "recommendation": "Review low-risk ingress guardrails before broader scaling changes.",
                                    }
                                )
                            }
                        }
                    ],
                    "warnings": None,
                    "detections": None,
                }
            ),
            "response_payload": {
                "body": {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "root_cause": "Retry amplification is saturating the P-CSCF ingress path.",
                                        "explanation": "Historical matches and current evidence both point to ingress saturation.",
                                        "confidence": 0.84,
                                        "evidence": [
                                            {"type": "doc", "reference": "knowledge/signaling/registration-storm.json", "weight": 0.4},
                                            {"type": "metric", "reference": "retransmission_count", "weight": 0.4},
                                        ],
                                        "recommendation": "Review low-risk ingress guardrails before broader scaling changes.",
                                    }
                                )
                            }
                        }
                    ],
                    "warnings": None,
                    "detections": None,
                }
            },
            "trace_packets": [],
        }

        with (
            mock.patch.dict(
                rca_service_app.os.environ,
                {
                    "LLM_ENDPOINT": "http://guardrails-orchestrator-service.ani-datascience.svc.cluster.local:8090/rca",
                    "LLM_MODEL": "llama-32-3b-instruct",
                },
                clear=False,
            ),
            mock.patch.object(rca_service_app, "_retrieve_rca_documents", return_value=self._documents()),
            mock.patch.object(rca_service_app, "generate_with_llm_trace", return_value=llm_trace),
            mock.patch.object(rca_service_app, "attach_rca"),
            mock.patch.object(rca_service_app, "record_rca"),
        ):
            response = rca_service_app.rca(request)

        self.assertEqual(response["generation_mode"], "llm-rag")
        self.assertEqual(response["guardrails"]["status"], "allow")
        self.assertEqual(response["rca_state"], "VALIDATED_ALLOW")

    def test_rca_records_allow_state_for_guarded_llm_response(self) -> None:
        request = rca_service_app.RCARequest(
            incident_id="INC-102",
            context={"anomaly_type": "registration_storm", "workflow_revision": 3},
        )
        llm_trace = {
            "parsed": {
                "root_cause": "Retry amplification is saturating the P-CSCF ingress path.",
                "explanation": "Historical matches and current evidence both point to ingress saturation.",
                "confidence": 0.84,
                "evidence": [
                    {"type": "doc", "reference": "knowledge/signaling/registration-storm.json", "weight": 0.4},
                    {"type": "metric", "reference": "retransmission_count", "weight": 0.4},
                ],
                "recommendation": "Review low-risk ingress guardrails before broader scaling changes.",
            },
            "response_payload": {
                "choices": [
                    {
                        "message": {
                            "content": "{}",
                        }
                    }
                ]
            },
            "raw_content": "{}",
            "trace_packets": [],
        }

        with (
            mock.patch.dict(
                rca_service_app.os.environ,
                {
                    "LLM_ENDPOINT": "http://guardrails-orchestrator-service.ani-datascience.svc.cluster.local:8090/rca",
                    "LLM_MODEL": "llama-32-3b-instruct",
                },
                clear=False,
            ),
            mock.patch.object(rca_service_app, "_retrieve_rca_documents", return_value=self._documents()),
            mock.patch.object(rca_service_app, "generate_with_llm_trace", return_value=llm_trace),
            mock.patch.object(rca_service_app, "attach_rca"),
            mock.patch.object(rca_service_app, "record_rca"),
        ):
            response = rca_service_app.rca(request)

        self.assertEqual(response["guardrails"]["status"], "allow")
        self.assertEqual(response["rca_state"], "VALIDATED_ALLOW")
        self.assertEqual(response["source_workflow_revision"], 3)
        self.assertTrue(str(response["rca_request_id"]).startswith("rca-"))
        self.assertTrue(str(response["trace_id"]).startswith("trace-"))

    def test_rca_downgrades_low_confidence_guarded_response_to_review(self) -> None:
        request = rca_service_app.RCARequest(
            incident_id="INC-103",
            context={"anomaly_type": "registration_storm"},
        )
        llm_trace = {
            "parsed": {
                "root_cause": "Retry amplification is saturating the P-CSCF ingress path.",
                "explanation": "Historical matches and current evidence both point to ingress saturation.",
                "confidence": 0.42,
                "evidence": [
                    {"type": "doc", "reference": "knowledge/signaling/registration-storm.json", "weight": 0.4},
                    {"type": "metric", "reference": "retransmission_count", "weight": 0.4},
                ],
                "recommendation": "Review low-risk ingress guardrails before broader scaling changes.",
            },
            "response_payload": {
                "choices": [
                    {
                        "message": {
                            "content": "{}",
                        }
                    }
                ]
            },
            "raw_content": "{}",
            "trace_packets": [],
        }

        with (
            mock.patch.dict(
                rca_service_app.os.environ,
                {
                    "LLM_ENDPOINT": "http://guardrails-orchestrator-service.ani-datascience.svc.cluster.local:8090/rca",
                    "LLM_MODEL": "llama-32-3b-instruct",
                },
                clear=False,
            ),
            mock.patch.object(rca_service_app, "_retrieve_rca_documents", return_value=self._documents()),
            mock.patch.object(rca_service_app, "generate_with_llm_trace", return_value=llm_trace),
            mock.patch.object(rca_service_app, "attach_rca"),
            mock.patch.object(rca_service_app, "record_rca"),
        ):
            response = rca_service_app.rca(request)

        self.assertEqual(response["guardrails"]["status"], "require_review")
        self.assertEqual(response["rca_state"], "VALIDATED_REVIEW")
        self.assertEqual(response["guardrails"]["reason"], "confidence_below_threshold")


if __name__ == "__main__":
    unittest.main()
