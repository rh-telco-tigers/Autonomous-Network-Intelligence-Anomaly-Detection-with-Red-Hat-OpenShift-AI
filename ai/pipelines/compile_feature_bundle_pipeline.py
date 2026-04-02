"""Compile the feature-bundle pipeline to YAML."""

from pathlib import Path
import shutil

from kfp import compiler

from ims_feature_bundle_pipeline import ims_feature_bundle_pipeline


def main() -> None:
    pipeline_root = Path(__file__).resolve().parent
    generated_path = pipeline_root / "generated" / "ims_feature_bundle_pipeline.yaml"
    asset_path = pipeline_root.parent.parent / "k8s" / "base" / "kfp" / "assets" / "ims_feature_bundle_pipeline.yaml"
    publisher_path = pipeline_root / "publish_feature_bundle_pipeline.py"
    publisher_asset_path = pipeline_root.parent.parent / "k8s" / "base" / "kfp" / "assets" / "publish_feature_bundle_pipeline.py"

    generated_path.parent.mkdir(parents=True, exist_ok=True)
    asset_path.parent.mkdir(parents=True, exist_ok=True)
    publisher_asset_path.parent.mkdir(parents=True, exist_ok=True)
    compiler.Compiler().compile(
        pipeline_func=ims_feature_bundle_pipeline,
        package_path=str(generated_path),
    )
    shutil.copyfile(generated_path, asset_path)
    shutil.copyfile(publisher_path, publisher_asset_path)
    print(generated_path)
    print(asset_path)
    print(publisher_asset_path)


if __name__ == "__main__":
    main()
