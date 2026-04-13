import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[3] / "k8s" / "manual" / "demo-triggers" / "render_backfill_serving_resources.py"
)
SPEC = importlib.util.spec_from_file_location("render_backfill_serving_resources", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


def test_resolve_registered_model_artifact_uri_uses_registry_entries(monkeypatch):
    responses = {
        "registered_models": {
            "items": [
                {
                    "id": "registered-model-1",
                    "name": "ani-anomaly-featurestore-backfill",
                }
            ]
        },
        "registered_models/registered-model-1/versions?name=ani-anomaly-featurestore-backfill-v1": {
            "items": [
                {
                    "id": "model-version-1",
                    "name": "ani-anomaly-featurestore-backfill-v1",
                }
            ]
        },
        "model_versions/model-version-1/artifacts?name=ani-anomaly-featurestore-backfill": {
            "items": [
                {
                    "uri": "s3://ani-models/predictive-featurestore/ani-predictive-backfill/registry-resolved/",
                }
            ]
        },
    }

    monkeypatch.setattr(
        renderer,
        "_registry_request",
        lambda **kwargs: responses[kwargs["path"]],
    )

    artifact_uri = renderer._resolve_registered_model_artifact_uri(
        model_name="ani-anomaly-featurestore-backfill",
        model_version_name="ani-anomaly-featurestore-backfill-v1",
        endpoint="http://default-modelregistry.rhoai-model-registries.svc.cluster.local:8080",
        registry_namespace="rhoai-model-registries",
        registry_service="default-modelregistry",
    )

    assert artifact_uri == "s3://ani-models/predictive-featurestore/ani-predictive-backfill/registry-resolved/"


def test_render_manifest_replaces_backfill_registry_placeholders(tmp_path):
    template_path = tmp_path / "backfill-template.yaml"
    template_path.write_text(
        "\n".join(
            [
                "metadata:",
                "  name: __BACKFILL_SERVING_MODEL_NAME__",
                "  namespace: __DATASCIENCE_NAMESPACE__",
                "annotations:",
                "  ani.redhat.com/model-registry-model: \"__BACKFILL_MODEL_NAME__\"",
                "  ani.redhat.com/model-registry-version: \"__BACKFILL_MODEL_VERSION_NAME__\"",
                "  ani.redhat.com/model-registry-endpoint: \"__BACKFILL_MODEL_REGISTRY_ENDPOINT__\"",
                "spec:",
                "  predictor:",
                "    model:",
                "      runtime: __BACKFILL_SERVING_RUNTIME_NAME__",
                "      storageUri: __BACKFILL_STORAGE_URI__",
            ]
        )
    )

    rendered = renderer._render_manifest(
        template_path,
        {
            "__DATASCIENCE_NAMESPACE__": "ani-datascience",
            "__BACKFILL_MODEL_NAME__": "ani-anomaly-featurestore-backfill",
            "__BACKFILL_MODEL_VERSION_NAME__": "ani-anomaly-featurestore-backfill-v1",
            "__BACKFILL_SERVING_MODEL_NAME__": "ani-predictive-backfill",
            "__BACKFILL_SERVING_RUNTIME_NAME__": "ani-autogluon-mlserver-runtime",
            "__BACKFILL_MODEL_REGISTRY_ENDPOINT__": "http://default-modelregistry.rhoai-model-registries.svc.cluster.local:8080",
            "__BACKFILL_STORAGE_URI__": "s3://ani-models/predictive-featurestore/ani-predictive-backfill/registry-resolved/",
        },
    )

    assert "__BACKFILL_" not in rendered
    assert "name: ani-predictive-backfill" in rendered
    assert "namespace: ani-datascience" in rendered
    assert "runtime: ani-autogluon-mlserver-runtime" in rendered
    assert "storageUri: s3://ani-models/predictive-featurestore/ani-predictive-backfill/registry-resolved/" in rendered
