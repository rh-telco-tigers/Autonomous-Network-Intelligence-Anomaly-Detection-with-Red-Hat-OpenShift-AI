import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


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
        self.assertEqual(records[0]["reference"], "knowledge/signaling/scale-pcscf.md")
        self.assertEqual(records[1]["reference"], "knowledge/signaling/rebalance-edge.md")
        self.assertEqual(records[0]["category"], "signaling")
        self.assertEqual(records[0]["doc_type"], rag.KNOWLEDGE_ARTICLE_DOC_TYPE)

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
        self.assertEqual(results[0]["reference"], "knowledge/signaling/scale-pcscf.md")


if __name__ == "__main__":
    unittest.main()
