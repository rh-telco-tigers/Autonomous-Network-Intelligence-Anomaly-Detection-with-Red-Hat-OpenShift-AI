from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd


PYTHON_ROOT = Path(__file__).resolve().parents[1] / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

import release_runtime  # noqa: E402


class ReleaseRuntimeTests(unittest.TestCase):
    def _write_json(self, path: Path, payload: object) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2))
        return str(path)

    def _build_snapshot(self, root: Path) -> str:
        incidents = []
        approvals = []
        audit_events = []
        feature_windows = []

        feature_sets = [
            (
                "window-1",
                "registration_storm",
                {
                    "register_rate": 6.1,
                    "invite_rate": 0.1,
                    "bye_rate": 0.0,
                    "error_4xx_ratio": 0.02,
                    "error_5xx_ratio": 0.0,
                    "latency_p95": 140.0,
                    "retransmission_count": 10.0,
                    "inter_arrival_mean": 0.7,
                    "payload_variance": 18.0,
                },
            ),
            (
                "window-2",
                "malformed_invite",
                {
                    "register_rate": 0.2,
                    "invite_rate": 2.7,
                    "bye_rate": 0.0,
                    "error_4xx_ratio": 0.41,
                    "error_5xx_ratio": 0.0,
                    "latency_p95": 180.0,
                    "retransmission_count": 2.0,
                    "inter_arrival_mean": 1.1,
                    "payload_variance": 64.0,
                },
            ),
            (
                "window-3",
                "service_degradation",
                {
                    "register_rate": 1.2,
                    "invite_rate": 0.3,
                    "bye_rate": 0.2,
                    "error_4xx_ratio": 0.06,
                    "error_5xx_ratio": 0.16,
                    "latency_p95": 320.0,
                    "retransmission_count": 7.0,
                    "inter_arrival_mean": 2.0,
                    "payload_variance": 28.0,
                },
            ),
            (
                "window-4",
                "normal",
                {
                    "register_rate": 0.4,
                    "invite_rate": 0.2,
                    "bye_rate": 0.1,
                    "error_4xx_ratio": 0.01,
                    "error_5xx_ratio": 0.0,
                    "latency_p95": 22.0,
                    "retransmission_count": 0.0,
                    "inter_arrival_mean": 6.0,
                    "payload_variance": 10.0,
                },
            ),
        ]

        for index, (window_id, anomaly_type, features) in enumerate(feature_sets, start=1):
            incident_id = f"incident-{index}"
            incidents.append(
                {
                    "id": incident_id,
                    "project": "ims-demo",
                    "status": "open",
                    "anomaly_score": 0.91 if anomaly_type != "normal" else 0.03,
                    "anomaly_type": anomaly_type,
                    "model_version": "predictive-v1",
                    "feature_window_id": window_id,
                    "feature_snapshot": {"feature_window_id": window_id, **features},
                    "rca_payload": {
                        "root_cause": f"https://demo.example/ims-demo-lab/{incident_id}",
                        "confidence": 0.87,
                        "recommendation": f"Review {incident_id} in ims-demo-lab.svc.cluster.local",
                        "retrieved_documents": [{"id": f"doc-{index}"}],
                    },
                    "created_at": f"2026-04-01T00:0{index}:00+00:00",
                    "updated_at": f"2026-04-01T00:1{index}:00+00:00",
                }
            )
            approvals.append(
                {
                    "incident_id": incident_id,
                    "action": "notify",
                    "approved_by": "tester",
                    "execute": False,
                    "status": "approved",
                    "output": "simulated",
                    "created_at": f"2026-04-01T00:2{index}:00+00:00",
                }
            )
            audit_events.append(
                {
                    "event_type": "incident_created",
                    "actor": "tester",
                    "incident_id": incident_id,
                    "payload": {"sample": True},
                    "created_at": f"2026-04-01T00:3{index}:00+00:00",
                }
            )
            window_path = root / "bronze" / "snapshot-1" / "feature-windows" / f"{window_id}.json"
            feature_windows.append(
                {
                    "object_key": f"datasets/live-sipp-v1/feature-windows/{anomaly_type}/{window_id}.json",
                    "s3_uri": f"s3://ims-models/datasets/live-sipp-v1/feature-windows/{anomaly_type}/{window_id}.json",
                    "local_path": self._write_json(
                        window_path,
                        {
                            "window_id": window_id,
                            "window_start": f"2026-04-01T00:0{index}:00+00:00",
                            "window_end": f"2026-04-01T00:0{index}:59+00:00",
                            "captured_at": f"2026-04-01T00:0{index}:59+00:00",
                            "scenario_name": anomaly_type,
                            "anomaly_type": anomaly_type,
                            "label": 0 if anomaly_type == "normal" else 1,
                            "label_confidence": 0.95,
                            "schema_version": release_runtime.FEATURE_SCHEMA_VERSION,
                            "features": features,
                        },
                    ),
                    "window_id": window_id,
                    "captured_at": f"2026-04-01T00:0{index}:59+00:00",
                }
            )

        incidents.append(
            {
                "id": "incident-5",
                "project": "ims-demo",
                "status": "resolved",
                "anomaly_score": 0.72,
                "anomaly_type": "malformed_invite",
                "model_version": "predictive-v1",
                "feature_window_id": None,
                "feature_snapshot": {
                    "invite_rate": 3.1,
                    "error_4xx_ratio": 0.51,
                    "latency_p95": 210.0,
                },
                "rca_payload": {
                    "root_cause": "Historical artifact from ims-demo-lab namespace",
                    "confidence": 0.61,
                    "recommendation": "Review svc.cluster.local traces",
                    "retrieved_documents": [],
                },
                "created_at": "2026-04-01T00:20:00+00:00",
                "updated_at": "2026-04-01T00:21:00+00:00",
            }
        )
        audit_events.append(
            {
                "event_type": "incident_resolved",
                "actor": "tester",
                "incident_id": "incident-5",
                "payload": {"sample": True},
                "created_at": "2026-04-01T00:22:00+00:00",
            }
        )

        snapshot_manifest_path = root / "snapshot-manifest.json"
        return self._write_json(
            snapshot_manifest_path,
            {
                "release_version": "release-v1",
                "source_snapshot_id": "snapshot-1",
                "snapshot_cutoff_ts": "2026-04-01T00:59:59+00:00",
                "project": "ims-demo",
                "source_dataset_version": "live-sipp-v1",
                "feature_schema_version": release_runtime.FEATURE_SCHEMA_VERSION,
                "artifacts": {
                    "incidents_path": self._write_json(root / "bronze" / "snapshot-1" / "control-plane" / "incidents.json", incidents),
                    "approvals_path": self._write_json(root / "bronze" / "snapshot-1" / "control-plane" / "approvals.json", approvals),
                    "audit_events_path": self._write_json(root / "bronze" / "snapshot-1" / "control-plane" / "audit-events.json", audit_events),
                    "rca_enrichment_path": self._write_json(root / "bronze" / "snapshot-1" / "control-plane" / "rca-enrichment.json", []),
                    "feature_windows_dir": str(root / "bronze" / "snapshot-1" / "feature-windows"),
                },
                "counts": {
                    "incidents": len(incidents),
                    "approvals": len(approvals),
                    "audit_events": len(audit_events),
                    "feature_window_objects": len(feature_windows),
                },
                "feature_windows": feature_windows,
            },
        )

    def test_snapshot_sources_emits_kafka_mirror_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            published_topics: dict[str, list[tuple[str, dict[str, object]]]] = {}

            incidents = [
                {
                    "id": "incident-1",
                    "project": "ims-demo",
                    "status": "open",
                    "anomaly_type": "registration_storm",
                    "feature_window_id": "window-1",
                }
            ]
            feature_window = {
                "window_id": "window-1",
                "window_start": "2026-04-01T00:00:00+00:00",
                "window_end": "2026-04-01T00:00:59+00:00",
                "captured_at": "2026-04-01T00:00:59+00:00",
                "scenario_name": "registration_storm",
                "anomaly_type": "registration_storm",
                "label": 1,
                "label_confidence": 0.95,
                "schema_version": release_runtime.FEATURE_SCHEMA_VERSION,
                "features": {"register_rate": 6.1},
            }

            def fake_publish(topic: str, events: list[tuple[str, dict[str, object]]]) -> dict[str, object]:
                published_topics[topic] = events
                return {
                    "status": "published",
                    "attempted_records": len(events),
                    "published_records": len(events),
                    "payload_modes": {},
                }

            with patch.dict(os.environ, {"KAFKA_ENABLED": "true"}, clear=False), patch.object(
                release_runtime,
                "export_control_plane_history",
                return_value={"incidents": incidents, "approvals": [], "audit_events": []},
            ), patch.object(
                release_runtime,
                "_list_feature_window_objects",
                return_value=["datasets/live-sipp-v1/feature-windows/registration_storm/window-1.json"],
            ), patch.object(
                release_runtime,
                "_read_json_from_s3",
                return_value=feature_window,
            ), patch.object(
                release_runtime,
                "_publish_kafka_topic_events",
                side_effect=fake_publish,
            ):
                manifest = release_runtime.snapshot_sources(
                    release_version="release-v1",
                    source_dataset_version="live-sipp-v1",
                    project="ims-demo",
                    workspace_root=str(root),
                )

            self.assertTrue(manifest["kafka"]["enabled"])
            self.assertEqual(
                manifest["kafka"]["topics"]["ims-incidents-bronze"]["published_records"],
                1,
            )
            self.assertEqual(
                manifest["kafka"]["topics"]["ims-feature-windows-bronze"]["published_records"],
                1,
            )
            self.assertEqual(
                published_topics["ims-incidents-bronze"][0][1]["event_type"],
                "incident_snapshot_exported",
            )
            self.assertEqual(
                published_topics["ims-feature-windows-bronze"][0][1]["event_type"],
                "feature_window_snapshot_exported",
            )

    def test_normalize_and_validate_release_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshot_manifest = self._build_snapshot(root)

            manifest = release_runtime.normalize_release(
                snapshot_manifest_ref=snapshot_manifest,
                workspace_root=str(root),
                public_record_target=10,
            )
            validation = release_runtime.validate_release(normalized_manifest_ref=json.dumps(manifest))

            training_df = pd.read_parquet(manifest["artifacts"]["training_examples_parquet"])
            balanced_df = pd.read_parquet(manifest["artifacts"]["training_examples_balanced_parquet"])
            incident_df = pd.read_parquet(manifest["artifacts"]["incident_history_parquet"])
            split_manifest = json.loads(Path(manifest["artifacts"]["split_manifest_json"]).read_text())
            quality_report = json.loads(Path(manifest["artifacts"]["quality_report"]).read_text())

            self.assertEqual(validation["validation_results"]["status"], "passed")
            self.assertEqual(len(balanced_df), 10)
            self.assertTrue((balanced_df["training_eligibility_status"] == "eligible").all())
            self.assertNotIn("reconstructed_from_incident_snapshot", set(training_df["linkage_status"]))
            self.assertTrue((incident_df["rca_root_cause_redacted"].fillna("").str.contains("ims-demo-lab")).sum() == 0)
            eligible_ids = set(training_df.loc[training_df["training_eligibility_status"] == "eligible", "record_public_id"])
            split_ids = {item["record_public_id"] for item in split_manifest}
            self.assertTrue(split_ids.issubset(eligible_ids))
            self.assertTrue(Path(manifest["artifacts"]["dataset_card_md"]).exists())
            self.assertTrue(Path(manifest["artifacts"]["schema_json"]).exists())
            self.assertTrue(Path(manifest["artifacts"]["label_dictionary_csv"]).exists())
            self.assertIn("quality_scorecard", quality_report)
            self.assertEqual(quality_report["quality_scorecard"]["metrics"]["authoritative_window_count"], 4)
            self.assertEqual(quality_report["filtered_non_authoritative_incident_row_count"], 1)
            self.assertIn("minimum_authoritative_window_ratio", quality_report["quality_scorecard"]["checks"])

    def test_validate_release_allows_advisory_quality_mode_for_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshot_manifest_path = Path(self._build_snapshot(root))
            snapshot_manifest = json.loads(snapshot_manifest_path.read_text())
            snapshot_manifest["feature_windows"] = []
            snapshot_manifest["counts"]["feature_window_objects"] = 0
            snapshot_manifest_path.write_text(json.dumps(snapshot_manifest, indent=2))

            with patch.dict(
                os.environ,
                {
                    "RELEASE_INCLUDE_NON_AUTHORITATIVE": "true",
                    "RELEASE_QUALITY_ENFORCEMENT": "advisory",
                },
                clear=False,
            ):
                manifest = release_runtime.normalize_release(
                    snapshot_manifest_ref=str(snapshot_manifest_path),
                    workspace_root=str(root),
                    public_record_target=0,
                )
                validation = release_runtime.validate_release(normalized_manifest_ref=json.dumps(manifest))

            self.assertEqual(validation["validation_results"]["status"], "passed")
            self.assertEqual(validation["validation_results"]["quality_enforcement_mode"], "advisory")
            self.assertTrue(validation["validation_results"]["warnings"])
            self.assertTrue(
                any("Join coverage is below the blocking threshold" in warning for warning in validation["validation_results"]["warnings"])
            )
            self.assertTrue(
                any("Quality gate advisory:" in warning for warning in validation["validation_results"]["warnings"])
            )

    def test_publish_release_packages_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            snapshot_manifest = self._build_snapshot(root)
            manifest = release_runtime.normalize_release(
                snapshot_manifest_ref=snapshot_manifest,
                workspace_root=str(root),
                public_record_target=10,
            )
            validation = release_runtime.validate_release(normalized_manifest_ref=json.dumps(manifest))

            def fake_upload(path: Path, prefix: str) -> str:
                return f"s3://ims-models/{prefix}/{path.name}"

            published_topics: dict[str, list[tuple[str, dict[str, object]]]] = {}

            def fake_publish(topic: str, events: list[tuple[str, dict[str, object]]]) -> dict[str, object]:
                published_topics[topic] = events
                return {
                    "status": "published",
                    "attempted_records": len(events),
                    "published_records": len(events),
                    "payload_modes": {},
                }

            with patch.object(release_runtime, "_object_exists_s3", return_value=False), patch.object(
                release_runtime,
                "_upload_file_to_s3",
                side_effect=fake_upload,
            ), patch.object(release_runtime, "_previous_release_manifest_ref", return_value=None), patch.object(
                release_runtime,
                "_publish_kafka_topic_events",
                side_effect=fake_publish,
            ), patch.dict(os.environ, {"KAFKA_ENABLED": "true"}, clear=False):
                published = release_runtime.publish_release(
                    validation_manifest_ref=json.dumps(validation),
                    workspace_root=str(root),
                    release_mode="draft",
                )

            self.assertIn("bundle_archive", published["artifacts"])
            self.assertIn("training_examples_balanced_csv", published["artifacts"])
            self.assertTrue(published["kafka"]["enabled"])
            self.assertEqual(
                published["kafka"]["topics"]["ims-release-artifacts"]["published_records"],
                len(published_topics["ims-release-artifacts"]),
            )
            self.assertIn(
                "release_published",
                {event["event_type"] for _, event in published_topics["ims-release-artifacts"]},
            )
            bundle_path = root / "published" / "release-v1" / "ims_incident_release_bundle.zip"
            self.assertTrue(bundle_path.exists())


if __name__ == "__main__":
    unittest.main()
