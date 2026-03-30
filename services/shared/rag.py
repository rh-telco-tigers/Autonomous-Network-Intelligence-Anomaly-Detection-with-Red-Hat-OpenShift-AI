import json
import math
import os
from pathlib import Path
from typing import Dict, List

import requests


def _runbook_dir() -> Path:
    return Path(os.getenv("RUNBOOK_DIR", "/app/ai/rag/runbooks"))


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
    for path in sorted(_runbook_dir().glob("*.md")):
        text = path.read_text()
        score = _cosine(query_embedding, hash_embedding(text))
        docs.append(
            {
                "title": path.stem,
                "reference": path.name,
                "content": text,
                "score": round(score, 4),
            }
        )
    docs.sort(key=lambda item: item["score"], reverse=True)
    return docs[:limit]


def milvus_retrieve(query: str, limit: int = 3) -> List[Dict[str, object]]:
    uri = os.getenv("MILVUS_URI", "").strip()
    collection = os.getenv("MILVUS_COLLECTION", "ims_runbooks")
    if not uri:
        return []

    try:
        from pymilvus import MilvusClient
    except Exception:
        return []

    client = MilvusClient(uri=uri)
    try:
        results = client.search(
            collection_name=collection,
            data=[hash_embedding(query)],
            output_fields=["title", "reference", "content"],
            limit=limit,
        )
    except Exception:
        return []
    docs = []
    for hit in results[0]:
        entity = hit["entity"]
        docs.append(
            {
                "title": entity["title"],
                "reference": entity["reference"],
                "content": entity["content"],
                "score": round(hit["distance"], 4),
            }
        )
    return docs


def retrieve_context(query: str, limit: int = 3) -> List[Dict[str, object]]:
    docs = milvus_retrieve(query, limit=limit)
    if docs:
        return docs
    return local_retrieve(query, limit=limit)


def build_prompt(incident_context: Dict[str, object], documents: List[Dict[str, object]]) -> str:
    evidence = "\n\n".join(
        f"Document: {doc['reference']}\nContent:\n{doc['content'][:1200]}"
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
