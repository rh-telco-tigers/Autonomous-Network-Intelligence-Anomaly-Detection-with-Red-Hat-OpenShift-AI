import json

from ai.training import model_registry_client as mrc


def _payload():
    return {
        "model_name": "ani-anomaly-featurestore-backfill",
        "model_version_name": "ani-anomaly-featurestore-backfill-v1",
        "artifact_uri": "s3://ani-models/predictive-featurestore/ani-predictive-backfill/candidate-backfill-fs-v1/",
        "model_format_name": "autogluon",
        "model_format_version": "1",
        "bundle_version": "ani-backfill-feature-bundle-v1",
        "feature_schema_version": "feature_schema_v1",
        "feature_service_name": "ani_anomaly_scoring_v1",
        "pipeline_name": "ani-featurestore-train-and-register",
        "pipeline_run_id": "run-123",
        "deployment_readiness_status": "needs-review",
        "registry_endpoint": "http://default-modelregistry.rhoai-model-registries.svc.cluster.local:8080",
        "generated_at": "2026-04-13T00:00:00+00:00",
        "metrics": {"macro_f1": 0.91, "latency_p95_ms": 1.2},
        "metadata": {
            "description": "Feature-store-trained ANI anomaly model for bundle ani-backfill-feature-bundle-v1",
            "serving_model_name": "ani-predictive-backfill",
        },
    }


def test_publish_model_version_registers_via_plural_rest_endpoints(tmp_path, monkeypatch):
    calls = []

    def fake_registry_request_json(endpoint, path, *, method="GET", payload=None):
        calls.append((method, path, payload))
        assert endpoint == "http://default-modelregistry.rhoai-model-registries.svc.cluster.local:8080"
        if method == "GET" and path == "registered_models":
            return {"items": []}
        if method == "POST" and path == "registered_models":
            return {"id": "registered-model-1", "name": "ani-anomaly-featurestore-backfill"}
        if method == "GET" and path == "registered_models/registered-model-1/versions?name=ani-anomaly-featurestore-backfill-v1":
            return {"items": []}
        if method == "POST" and path == "registered_models/registered-model-1/versions":
            return {"id": "model-version-1", "name": "ani-anomaly-featurestore-backfill-v1"}
        if method == "GET" and path == "model_versions/model-version-1/artifacts?name=ani-anomaly-featurestore-backfill":
            return {"items": []}
        if method == "GET" and path == "model_versions/model-version-1/artifacts":
            return {"items": []}
        if method == "POST" and path == "model_versions/model-version-1/artifacts":
            return {
                "id": "model-artifact-1",
                "name": "ani-anomaly-featurestore-backfill",
                "uri": "s3://ani-models/predictive-featurestore/ani-predictive-backfill/candidate-backfill-fs-v1/",
            }
        raise AssertionError(f"Unexpected registry request: {(method, path, payload)}")

    monkeypatch.setattr(mrc, "_registry_request_json", fake_registry_request_json)

    result = mrc.publish_model_version(_payload(), tmp_path / "registration.json")
    output = json.loads((tmp_path / "registration.json").read_text())

    assert result["registration_result"]["status"] == "registered"
    assert result["registration_result"]["registered_model_created"] is True
    assert result["registration_result"]["model_version_created"] is True
    assert result["registration_result"]["model_artifact_action"] == "created"
    assert output["registration_result"]["result"]["model_artifact"]["id"] == "model-artifact-1"
    assert [call[:2] for call in calls] == [
        ("GET", "registered_models"),
        ("POST", "registered_models"),
        ("GET", "registered_models/registered-model-1/versions?name=ani-anomaly-featurestore-backfill-v1"),
        ("POST", "registered_models/registered-model-1/versions"),
        ("GET", "model_versions/model-version-1/artifacts?name=ani-anomaly-featurestore-backfill"),
        ("GET", "model_versions/model-version-1/artifacts"),
        ("POST", "model_versions/model-version-1/artifacts"),
    ]


def test_publish_model_version_reuses_existing_registry_artifact(tmp_path, monkeypatch):
    def fake_registry_request_json(endpoint, path, *, method="GET", payload=None):
        if method == "GET" and path == "registered_models":
            return {"items": [{"id": "registered-model-1", "name": "ani-anomaly-featurestore-backfill"}]}
        if method == "GET" and path == "registered_models/registered-model-1/versions?name=ani-anomaly-featurestore-backfill-v1":
            return {"items": [{"id": "model-version-1", "name": "ani-anomaly-featurestore-backfill-v1"}]}
        if method == "GET" and path == "model_versions/model-version-1/artifacts?name=ani-anomaly-featurestore-backfill":
            return {
                "items": [
                    {
                        "id": "model-artifact-1",
                        "name": "ani-anomaly-featurestore-backfill",
                        "uri": "s3://ani-models/predictive-featurestore/ani-predictive-backfill/candidate-backfill-fs-v1/",
                    }
                ]
            }
        raise AssertionError(f"Unexpected registry request: {(method, path, payload)}")

    monkeypatch.setattr(mrc, "_registry_request_json", fake_registry_request_json)

    result = mrc.publish_model_version(_payload(), tmp_path / "registration.json")

    assert result["registration_result"]["status"] == "registered"
    assert result["registration_result"]["registered_model_created"] is False
    assert result["registration_result"]["model_version_created"] is False
    assert result["registration_result"]["model_artifact_action"] == "existing"


def test_publish_model_version_updates_existing_registry_artifact_uri(tmp_path, monkeypatch):
    calls = []

    def fake_registry_request_json(endpoint, path, *, method="GET", payload=None):
        calls.append((method, path, payload))
        if method == "GET" and path == "registered_models":
            return {"items": [{"id": "registered-model-1", "name": "ani-anomaly-featurestore-backfill"}]}
        if method == "GET" and path == "registered_models/registered-model-1/versions?name=ani-anomaly-featurestore-backfill-v1":
            return {"items": [{"id": "model-version-1", "name": "ani-anomaly-featurestore-backfill-v1"}]}
        if method == "GET" and path == "model_versions/model-version-1/artifacts?name=ani-anomaly-featurestore-backfill":
            return {
                "items": [
                    {
                        "id": "model-artifact-1",
                        "name": "ani-anomaly-featurestore-backfill",
                        "uri": "s3://ani-models/predictive-featurestore/ani-predictive-backfill/old/",
                    }
                ]
            }
        if method == "PATCH" and path == "model_artifacts/model-artifact-1":
            return {
                "id": "model-artifact-1",
                "name": "ani-anomaly-featurestore-backfill",
                "uri": payload["uri"],
            }
        raise AssertionError(f"Unexpected registry request: {(method, path, payload)}")

    monkeypatch.setattr(mrc, "_registry_request_json", fake_registry_request_json)

    result = mrc.publish_model_version(_payload(), tmp_path / "registration.json")

    assert result["registration_result"]["status"] == "registered"
    assert result["registration_result"]["model_artifact_action"] == "updated"
    assert ("PATCH", "model_artifacts/model-artifact-1", {
        "uri": "s3://ani-models/predictive-featurestore/ani-predictive-backfill/candidate-backfill-fs-v1/",
        "artifactType": "model-artifact",
        "modelFormatName": "autogluon",
        "modelFormatVersion": "1",
    }) in calls
