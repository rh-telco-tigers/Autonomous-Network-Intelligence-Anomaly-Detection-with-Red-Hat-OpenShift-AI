"""Copy canonical feature-store repo files into Kustomize assets."""

from pathlib import Path
import shutil


FILES = (
    "entities.py",
    "feature_views.py",
    "feature_services.py",
)


def main() -> None:
    source_dir = Path(__file__).resolve().parent / "feature_repo"
    asset_dir = Path(__file__).resolve().parents[2] / "k8s" / "base" / "feature-store" / "assets"

    asset_dir.mkdir(parents=True, exist_ok=True)
    for file_name in FILES:
        source_path = source_dir / file_name
        destination_path = asset_dir / file_name
        shutil.copyfile(source_path, destination_path)
        print(destination_path)


if __name__ == "__main__":
    main()
