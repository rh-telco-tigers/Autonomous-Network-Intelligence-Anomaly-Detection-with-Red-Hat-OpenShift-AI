import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest import mock


SERVICES_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICES_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICES_ROOT))

MODULE_PATH = Path(__file__).resolve().parents[1] / "classifier_profiles.py"
SPEC = importlib.util.spec_from_file_location("shared_classifier_profiles", MODULE_PATH)
assert SPEC and SPEC.loader
classifier_profiles = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(classifier_profiles)


class ClassifierProfileCatalogTests(unittest.TestCase):
    def test_catalog_uses_explicit_explainability_endpoint(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "PREDICTIVE_ENDPOINT_LIVE": "http://ani-predictive-fs-predictor.ani-datascience.svc.cluster.local:8080",
                "PREDICTIVE_EXPLAINABILITY_ENDPOINT_LIVE": "http://ani-predictive-fs-explainer.ani-datascience.svc.cluster.local:8080",
            },
            clear=False,
        ):
            catalog = classifier_profiles.classifier_profile_catalog()

        self.assertEqual(
            catalog["live"]["explainability_endpoint"],
            "http://ani-predictive-fs-explainer.ani-datascience.svc.cluster.local:8080",
        )

    def test_catalog_derives_explainability_endpoint_from_predictor_service(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "PREDICTIVE_ENDPOINT_LIVE": "http://ani-predictive-fs-predictor.ani-datascience.svc.cluster.local:8080",
                "PREDICTIVE_EXPLAINABILITY_ENDPOINT_LIVE": "",
                "PREDICTIVE_EXPLAINABILITY_ENDPOINT": "",
            },
            clear=False,
        ):
            catalog = classifier_profiles.classifier_profile_catalog()

        self.assertEqual(
            catalog["live"]["explainability_endpoint"],
            "http://ani-predictive-fs-explainer.ani-datascience.svc.cluster.local:8080",
        )


if __name__ == "__main__":
    unittest.main()
