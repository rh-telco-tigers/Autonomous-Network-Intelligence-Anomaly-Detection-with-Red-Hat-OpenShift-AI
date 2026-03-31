import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from shared.rag import hash_embedding
except ModuleNotFoundError:
    from services.shared.rag import hash_embedding


def main():
    try:
        from pymilvus import DataType, MilvusClient
    except Exception as exc:
        raise SystemExit(f"pymilvus is required to bootstrap Milvus: {exc}") from exc

    collection_name = "ims_runbooks"
    runbook_dir = Path("ai/rag/runbooks")
    docs = []
    for path in sorted(runbook_dir.glob("*.md")):
        docs.append(
            {
                "id": len(docs) + 1,
                "title": path.stem,
                "reference": path.name,
                "content": path.read_text(),
                "embedding": hash_embedding(path.read_text()),
            }
        )

    client = MilvusClient(uri=os.getenv("MILVUS_URI", "http://localhost:19530"))
    if client.has_collection(collection_name=collection_name):
        client.drop_collection(collection_name=collection_name)

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
    schema.add_field(field_name="title", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="reference", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=8192)
    schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=64)

    index_params = client.prepare_index_params()
    index_params.add_index(field_name="embedding", index_type="AUTOINDEX", metric_type="COSINE")

    client.create_collection(collection_name=collection_name, schema=schema, index_params=index_params)
    client.insert(collection_name=collection_name, data=docs)
    print(json.dumps({"collection": collection_name, "documents": len(docs)}, indent=2))


if __name__ == "__main__":
    main()
