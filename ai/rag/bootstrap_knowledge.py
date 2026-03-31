import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from shared.rag import DEFAULT_MILVUS_COLLECTIONS, LOCAL_COLLECTION_DIRS, hash_embedding
except ModuleNotFoundError:
    from services.shared.rag import DEFAULT_MILVUS_COLLECTIONS, LOCAL_COLLECTION_DIRS, hash_embedding


def render_document(path: Path, collection_name: str) -> dict[str, object]:
    reference = f"{path.parent.name}/{path.name}"
    if path.suffix == ".json":
        payload = json.loads(path.read_text())
        title = str(payload.get("title") or payload.get("incident_id") or path.stem)
        content = json.dumps(payload, indent=2)
    else:
        content = path.read_text()
        title = path.stem
        for line in content.splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                title = stripped
                break
    return {
        "title": title,
        "reference": reference,
        "content": content,
        "doc_type": collection_name.removeprefix("ims_"),
        "embedding": hash_embedding(content),
    }


def main():
    try:
        from pymilvus import DataType, MilvusClient
    except Exception as exc:
        raise SystemExit(f"pymilvus is required to bootstrap Milvus: {exc}") from exc

    client = MilvusClient(uri=os.getenv("MILVUS_URI", "http://localhost:19530"))
    rag_root = Path(os.getenv("RAG_ROOT_DIR", "ai/rag"))
    bootstrap_summary: dict[str, int] = {}

    for collection_name in DEFAULT_MILVUS_COLLECTIONS:
        directory_name = LOCAL_COLLECTION_DIRS[collection_name]
        source_dir = rag_root / directory_name
        docs = []
        for path in sorted(source_dir.glob("*")):
            if not path.is_file():
                continue
            docs.append({"id": len(docs) + 1, **render_document(path, collection_name)})

        if client.has_collection(collection_name=collection_name):
            client.drop_collection(collection_name=collection_name)

        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
        schema.add_field(field_name="title", datatype=DataType.VARCHAR, max_length=256)
        schema.add_field(field_name="reference", datatype=DataType.VARCHAR, max_length=256)
        schema.add_field(field_name="doc_type", datatype=DataType.VARCHAR, max_length=64)
        schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=16384)
        schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=64)

        index_params = client.prepare_index_params()
        index_params.add_index(field_name="embedding", index_type="AUTOINDEX", metric_type="COSINE")

        client.create_collection(collection_name=collection_name, schema=schema, index_params=index_params)
        if docs:
            client.insert(collection_name=collection_name, data=docs)
        bootstrap_summary[collection_name] = len(docs)

    print(json.dumps({"collections": bootstrap_summary}, indent=2))


if __name__ == "__main__":
    main()
