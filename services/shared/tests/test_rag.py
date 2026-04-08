import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

MODULE_PATH = Path(__file__).resolve().parents[1] / "rag.py"
SPEC = importlib.util.spec_from_file_location("shared_rag", MODULE_PATH)
assert SPEC and SPEC.loader
rag = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rag)


class KnowledgeBundleTests(unittest.TestCase):
    def _write_bundle(self, root: Path, filename: str, payload: dict) -> Path:
        runbooks_dir = root / "runbooks"
        runbooks_dir.mkdir(parents=True, exist_ok=True)
        path = runbooks_dir / filename
        path.write_text(json.dumps(payload, indent=2))
        return path

    def test_bundle_file_expands_to_category_scoped_seed_records(self) -> None:
        payload = {
            "category": "signaling",
            "articles": [
                {
                    "slug": "scale-pcscf",
                    "title": "Scale P-CSCF workers",
                    "summary": "Relieve REGISTER pressure.",
                    "anomaly_types": ["registration_storm"],
                    "content": ["When to use: REGISTER surge.", "Action: scale the edge."],
                },
                {
                    "slug": "rebalance-edge",
                    "title": "Rebalance the SIP edge",
                    "summary": "Distribute hot shards.",
                    "anomaly_types": ["registration_failure"],
                    "content": ["When to use: one shard is hot.", "Action: rebalance traffic."],
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = self._write_bundle(root, "signaling.json", payload)
            with patch.dict(rag.os.environ, {"RAG_ROOT_DIR": str(root)}, clear=False):
                records = rag.build_local_seed_records(bundle, rag.RUNBOOK_COLLECTION)

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["reference"], "knowledge/signaling/scale-pcscf.json")
        self.assertEqual(records[1]["reference"], "knowledge/signaling/rebalance-edge.json")
        self.assertEqual(records[0]["category"], "signaling")
        self.assertEqual(records[0]["doc_type"], rag.KNOWLEDGE_ARTICLE_DOC_TYPE)
        first_content = json.loads(records[0]["content"])
        self.assertEqual(first_content["title"], "Scale P-CSCF workers")
        self.assertEqual(first_content["anomaly_types"], ["registration_storm"])
        self.assertEqual(first_content["guidance"][0], "When to use: REGISTER surge.")

    def test_local_retrieve_filters_to_requested_knowledge_category(self) -> None:
        signaling_payload = {
            "category": "signaling",
            "articles": [
                {
                    "slug": "scale-pcscf",
                    "title": "Scale P-CSCF workers",
                    "summary": "Relieve REGISTER pressure.",
                    "anomaly_types": ["registration_storm"],
                    "content": ["REGISTER retries are increasing.", "Scale the P-CSCF path."],
                }
            ],
        }
        auth_payload = {
            "category": "auth",
            "articles": [
                {
                    "slug": "validate-hss-vectors",
                    "title": "Validate HSS vectors",
                    "summary": "Fix auth loops.",
                    "anomaly_types": ["authentication_failure"],
                    "content": ["401 loops point to auth vectors.", "Verify HSS responses."],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_bundle(root, "signaling.json", signaling_payload)
            self._write_bundle(root, "auth.json", auth_payload)
            with patch.dict(rag.os.environ, {"RAG_ROOT_DIR": str(root)}, clear=False):
                results = rag.local_retrieve(
                    "registration retries on the pcscf path",
                    limit=5,
                    collections=[rag.RUNBOOK_COLLECTION],
                    category="signaling",
                )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["category"], "signaling")
        self.assertEqual(results[0]["reference"], "knowledge/signaling/scale-pcscf.json")

    def test_local_retrieve_prefers_exact_anomaly_anchor_article(self) -> None:
        payload = {
            "category": "signaling",
            "articles": [
                {
                    "slug": "registration-storm-anchor",
                    "title": "Registration storm RCA",
                    "summary": "Retry amplification on the edge causes a storm.",
                    "anomaly_types": ["registration_storm"],
                    "keywords": ["retry amplification", "pcscf", "register"],
                    "recommended_rca": {
                        "root_cause": "REGISTER retry amplification is saturating the edge."
                    },
                    "content": ["Edge retry loops dominate transaction load."],
                },
                {
                    "slug": "registration-failure-anchor",
                    "title": "Registration failure RCA",
                    "summary": "A subscriber cohort cannot complete registration successfully.",
                    "anomaly_types": ["registration_failure"],
                    "keywords": ["subscriber cohort", "reject codes", "challenge state"],
                    "recommended_rca": {
                        "root_cause": "Registration completion is failing for a targeted cohort."
                    },
                    "content": ["The same subscribers fail repeatedly without a raw surge."],
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_bundle(root, "signaling.json", payload)
            with patch.dict(rag.os.environ, {"RAG_ROOT_DIR": str(root)}, clear=False):
                results = rag.local_retrieve(
                    "reject codes are concentrated on one subscriber cohort and challenge state looks broken",
                    limit=2,
                    collections=[rag.RUNBOOK_COLLECTION],
                    category="signaling",
                    anomaly_type="registration_failure",
                )

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["reference"], "knowledge/signaling/registration-failure-anchor.json")
        self.assertIn("registration_failure", results[0]["anomaly_types"])
        self.assertTrue(any("Exact anomaly match" in reason for reason in results[0]["match_reasons"]))

    def test_bundle_file_requires_summary_and_anomaly_types(self) -> None:
        payload = {
            "category": "server",
            "articles": [
                {
                    "slug": "broken-article",
                    "title": "Broken article",
                    "content": ["Missing required schema fields."],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = self._write_bundle(root, "server.json", payload)
            with patch.dict(rag.os.environ, {"RAG_ROOT_DIR": str(root)}, clear=False):
                with self.assertRaises(ValueError):
                    rag.build_local_seed_records(bundle, rag.RUNBOOK_COLLECTION)

    def test_build_prompt_bans_meta_authoring_language_in_explanation(self) -> None:
        document = {
            "reference": "knowledge/server/server-internal-error.json",
            "collection": rag.RUNBOOK_COLLECTION,
            "stage": "runbooks",
            "doc_type": rag.KNOWLEDGE_ARTICLE_DOC_TYPE,
            "match_reasons": ["Exact anomaly match: server_internal_error"],
            "content": json.dumps(
                {
                    "summary": "5xx responses cluster on one server tier.",
                    "anomaly_types": ["server_internal_error"],
                    "symptom_profile": {
                        "primary_signals": [
                            "Queue depth and latency rise together on one service cohort.",
                        ]
                    },
                    "recommended_rca": {
                        "root_cause": "One server tier is overloaded.",
                        "recommendation": "Scale or isolate the failing tier.",
                    },
                    "operator_actions": [
                        {"action": "Compare the hottest pods with healthy peers."},
                    ],
                }
            ),
        }

        prompt = rag.build_prompt({"incident_id": "inc-1", "anomaly_type": "server_internal_error"}, [document])

        self.assertIn("Write the explanation as the incident diagnosis itself", prompt)
        self.assertIn("the RCA should", prompt)
        self.assertIn("cite retrieved document titles or collections", prompt)


if __name__ == "__main__":
    unittest.main()
