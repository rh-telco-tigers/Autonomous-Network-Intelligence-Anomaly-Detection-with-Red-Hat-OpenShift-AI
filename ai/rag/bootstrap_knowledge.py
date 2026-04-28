import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from shared.rag import (
        DEFAULT_MILVUS_COLLECTIONS,
        LEGACY_MILVUS_COLLECTIONS,
        LOCAL_COLLECTION_DIRS,
        build_local_seed_records,
        ensure_milvus_collection,
        ensure_milvus_collection_ready,
        milvus_client,
    )
except ModuleNotFoundError:
    from services.shared.rag import (
        DEFAULT_MILVUS_COLLECTIONS,
        LEGACY_MILVUS_COLLECTIONS,
        LOCAL_COLLECTION_DIRS,
        build_local_seed_records,
        ensure_milvus_collection,
        ensure_milvus_collection_ready,
        milvus_client,
    )


def main():
    client = milvus_client()
    if client is None:
        raise SystemExit("pymilvus and MILVUS_URI are required to bootstrap Milvus")
    rag_root = Path(os.getenv("RAG_ROOT_DIR", "ai/rag"))
    recreate_collections = os.getenv("MILVUS_BOOTSTRAP_RECREATE_COLLECTIONS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    bootstrap_summary: dict[str, int] = {}

    for collection_name in LEGACY_MILVUS_COLLECTIONS:
        try:
            if client.has_collection(collection_name=collection_name):
                client.drop_collection(collection_name=collection_name)
        except Exception:
            continue

    for collection_name in DEFAULT_MILVUS_COLLECTIONS:
        directory_name = LOCAL_COLLECTION_DIRS[collection_name]
        source_dir = rag_root / directory_name
        docs = []
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file():
                continue
            docs.extend(build_local_seed_records(path, collection_name))

        if recreate_collections and client.has_collection(collection_name=collection_name):
            client.drop_collection(collection_name=collection_name)
        if not ensure_milvus_collection(client, collection_name):
            raise SystemExit(f"Failed to ensure Milvus collection {collection_name}")
        if docs:
            client.upsert(collection_name=collection_name, data=docs)
        if not ensure_milvus_collection_ready(client, collection_name, load=True, force=True):
            raise SystemExit(f"Failed to load Milvus collection {collection_name}")
        bootstrap_summary[collection_name] = len(docs)

    print(
        json.dumps(
            {
                "collections": bootstrap_summary,
                "dropped_legacy": list(LEGACY_MILVUS_COLLECTIONS),
                "recreated_collections": recreate_collections,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
