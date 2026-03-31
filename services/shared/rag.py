import json
import math
import os
from pathlib import Path
from typing import Dict, Iterator, List, Tuple

import requests


DEFAULT_MILVUS_COLLECTIONS = (
    "ims_runbooks",
    "ims_incidents",
    "ims_topology",
    "ims_signal_patterns",
)
LOCAL_COLLECTION_DIRS = {
    "ims_runbooks": "runbooks",
    "ims_incidents": "incidents",
    "ims_topology": "topology",
    "ims_signal_patterns": "signal_patterns",
}


def _rag_root_dir() -> Path:
    explicit_root = os.getenv("RAG_ROOT_DIR", "").strip()
    if explicit_root:
        return Path(explicit_root)
    default_root = Path(__file__).resolve().parents[2] / "ai" / "rag"
    runbook_dir = Path(os.getenv("RUNBOOK_DIR", str(default_root / "runbooks")))
    return runbook_dir.parent if runbook_dir.name == "runbooks" else runbook_dir


def _milvus_collections() -> List[str]:
    raw = os.getenv("MILVUS_COLLECTIONS", ",".join(DEFAULT_MILVUS_COLLECTIONS))
    collections = [item.strip() for item in raw.split(",") if item.strip()]
    return collections or list(DEFAULT_MILVUS_COLLECTIONS)


def _render_local_document(path: Path, collection: str) -> Dict[str, object]:
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
        "collection": collection,
        "doc_type": collection.removeprefix("ims_"),
    }


def _iter_local_documents() -> Iterator[Tuple[str, Dict[str, object]]]:
    root = _rag_root_dir()
    for collection, directory in LOCAL_COLLECTION_DIRS.items():
        base_dir = root / directory
        if not base_dir.exists():
            continue
        for path in sorted(base_dir.glob("*")):
            if path.is_file():
                yield collection, _render_local_document(path, collection)


def hash_embedding(text: str, size: int = 64) -> List[float]:
    vector = [0.0] * size
    tokens = [token.strip(".,:;()[]{}").lower() for token in text.split()]
    for token in tokens:
        if not token:
            continue
        index = hash(token) % size
        vector[index] += 1.0
    length = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / length for value in vector]


def _cosine(left: List[float], right: List[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def local_retrieve(query: str, limit: int = 3) -> List[Dict[str, object]]:
    query_embedding = hash_embedding(query)
    docs = []
    for collection, doc in _iter_local_documents():
        content = str(doc["content"])
        score = _cosine(query_embedding, hash_embedding(content))
        docs.append({**doc, "collection": collection, "score": round(score, 4)})
    docs.sort(key=lambda item: item["score"], reverse=True)
    return docs[:limit]


def milvus_retrieve(query: str, limit: int = 3) -> List[Dict[str, object]]:
    uri = os.getenv("MILVUS_URI", "").strip()
    if not uri:
        return []

    try:
        from pymilvus import MilvusClient
    except Exception:
        return []

    client = MilvusClient(uri=uri)
    docs = []
    for collection in _milvus_collections():
        try:
            results = client.search(
                collection_name=collection,
                data=[hash_embedding(query)],
                output_fields=["title", "reference", "content", "doc_type"],
                limit=limit,
            )
        except Exception:
            continue
        for hit in results[0]:
            entity = hit["entity"]
            docs.append(
                {
                    "title": entity["title"],
                    "reference": entity["reference"],
                    "content": entity["content"],
                    "doc_type": entity.get("doc_type", collection.removeprefix("ims_")),
                    "collection": collection,
                    "score": round(hit["distance"], 4),
                }
            )
    docs.sort(key=lambda item: item["score"], reverse=True)
    return docs[:limit]


def retrieve_context(query: str, limit: int = 3) -> List[Dict[str, object]]:
    docs = milvus_retrieve(query, limit=limit)
    if docs:
        return docs
    return local_retrieve(query, limit=limit)


def build_prompt(incident_context: Dict[str, object], documents: List[Dict[str, object]]) -> str:
    evidence = "\n\n".join(
        f"Collection: {doc.get('collection', 'unknown')}\n"
        f"Type: {doc.get('doc_type', 'unknown')}\n"
        f"Document: {doc['reference']}\n"
        f"Content:\n{doc['content'][:1200]}"
        for doc in documents
    )
    return (
        "You are generating structured root cause analysis for an IMS platform incident.\n"
        f"Incident context:\n{json.dumps(incident_context, indent=2)}\n\n"
        "Use the supplied evidence only.\n"
        "Return JSON with keys: root_cause, confidence, evidence, recommendation.\n\n"
        f"{evidence}"
    )


def generate_with_llm(prompt: str) -> Dict[str, object] | None:
    endpoint = os.getenv("LLM_ENDPOINT", "").rstrip("/")
    model_name = os.getenv("LLM_MODEL", "granite")
    api_key = os.getenv("LLM_API_KEY", "").strip()
    if not endpoint:
        return None

    try:
        response = requests.post(
            f"{endpoint}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}" if api_key else "",
                "Content-Type": "application/json",
            },
            json={
                "model": model_name,
                "messages": [
                    {"role": "system", "content": "Respond only with valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.1,
            },
            timeout=60,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception:
        return None
