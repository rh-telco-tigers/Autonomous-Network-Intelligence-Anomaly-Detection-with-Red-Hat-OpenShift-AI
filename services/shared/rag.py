import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Sequence, Tuple

import requests

from shared.debug_trace import interaction_trace_packets, trace_now


DEFAULT_MILVUS_COLLECTIONS = (
    "ims_runbooks",
    "incident_evidence",
    "incident_reasoning",
    "incident_resolution",
    "ims_topology",
    "ims_signal_patterns",
)
LEGACY_MILVUS_COLLECTIONS = ("ims_incidents",)
VECTOR_DIMENSION = 64
MAX_CONTENT_LENGTH = 16384
MAX_EMBEDDING_TEXT_LENGTH = 4096
RUNBOOK_COLLECTION = "ims_runbooks"
KNOWLEDGE_ARTICLE_DOC_TYPE = "knowledge_article"
LOCAL_COLLECTION_DIRS = {
    "ims_runbooks": "runbooks",
    "incident_evidence": "incidents",
    "incident_reasoning": "incidents",
    "incident_resolution": "incidents",
    "ims_topology": "topology",
    "ims_signal_patterns": "signal_patterns",
}
COLLECTION_STAGE_DEFAULTS = {
    "incident_evidence": "evidence",
    "incident_reasoning": "reasoning",
    "incident_resolution": "resolution",
}
COLLECTION_DOC_TYPE_DEFAULTS = {
    "incident_evidence": "incident_evidence",
    "incident_reasoning": "incident_reasoning",
    "incident_resolution": "verified_resolution",
}
MILVUS_OUTPUT_FIELDS = [
    "title",
    "reference",
    "content",
    "doc_type",
    "stage",
    "incident_id",
    "category",
    "knowledge_weight",
]


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


def milvus_client():
    uri = os.getenv("MILVUS_URI", "").strip()
    if not uri:
        return None
    try:
        from pymilvus import MilvusClient
    except Exception:
        return None
    return MilvusClient(uri=uri)


def _stable_document_id(reference: str) -> int:
    digest = hashlib.sha256(reference.encode("utf-8")).hexdigest()
    return int(digest[:15], 16)


def _truncate(value: object, limit: int) -> str:
    return str(value or "")[:limit]


def _coerce_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _slugify(value: object) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return slug or "article"


def _collection_root(collection_name: str) -> Path:
    return _rag_root_dir() / LOCAL_COLLECTION_DIRS[collection_name]


def _collection_reference(path: Path, collection_name: str) -> str:
    try:
        return path.relative_to(_collection_root(collection_name)).as_posix()
    except ValueError:
        return f"{path.parent.name}/{path.name}"


def _collection_category(path: Path, collection_name: str) -> str:
    if collection_name != RUNBOOK_COLLECTION:
        return ""
    try:
        relative = path.relative_to(_collection_root(collection_name))
    except ValueError:
        return ""
    return "" if len(relative.parts) < 2 else relative.parts[0]


def _collection_stage(collection_name: str) -> str:
    return COLLECTION_STAGE_DEFAULTS.get(collection_name, collection_name.removeprefix("ims_"))


def _default_doc_type(collection_name: str) -> str:
    return COLLECTION_DOC_TYPE_DEFAULTS.get(collection_name, collection_name.removeprefix("ims_"))


def _content_to_text(content: str | Dict[str, object]) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, indent=2, sort_keys=True)


def _normalize_category(value: object) -> str:
    return str(value or "").strip().lower()


def hash_embedding(text: str, size: int = VECTOR_DIMENSION) -> List[float]:
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


def ensure_milvus_collection(client, collection_name: str) -> bool:
    try:
        if client.has_collection(collection_name=collection_name):
            return True
        from pymilvus import DataType
    except Exception:
        return False

    schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(field_name="id", datatype=DataType.INT64, is_primary=True)
    schema.add_field(field_name="title", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="reference", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="doc_type", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="stage", datatype=DataType.VARCHAR, max_length=32)
    schema.add_field(field_name="incident_id", datatype=DataType.VARCHAR, max_length=96)
    schema.add_field(field_name="parent_id", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="project", datatype=DataType.VARCHAR, max_length=96)
    schema.add_field(field_name="created_at", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="status", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="verified", datatype=DataType.BOOL)
    schema.add_field(field_name="verified_by", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="category", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="suggestion_type", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="resolution_type", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="knowledge_weight", datatype=DataType.FLOAT)
    schema.add_field(field_name="success_score", datatype=DataType.FLOAT)
    schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=MAX_CONTENT_LENGTH)
    schema.add_field(field_name="embedding_text", datatype=DataType.VARCHAR, max_length=MAX_EMBEDDING_TEXT_LENGTH)
    schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=VECTOR_DIMENSION)

    index_params = client.prepare_index_params()
    index_params.add_index(field_name="embedding", index_type="AUTOINDEX", metric_type="COSINE")
    client.create_collection(collection_name=collection_name, schema=schema, index_params=index_params)
    return True


def build_semantic_record(
    collection_name: str,
    reference: str,
    title: str,
    content: str | Dict[str, object],
    *,
    doc_type: str | None = None,
    embedding_text: str | None = None,
    metadata: Dict[str, object] | None = None,
) -> Dict[str, object]:
    metadata = metadata or {}
    content_text = _content_to_text(content)
    embedding_source = (embedding_text or content_text or title).strip()[:MAX_EMBEDDING_TEXT_LENGTH]
    return {
        "id": _stable_document_id(reference),
        "title": _truncate(title, 256),
        "reference": _truncate(reference, 256),
        "doc_type": _truncate(doc_type or _default_doc_type(collection_name), 64),
        "stage": _truncate(metadata.get("stage") or _collection_stage(collection_name), 32),
        "incident_id": _truncate(metadata.get("incident_id"), 96),
        "parent_id": _truncate(metadata.get("parent_id"), 128),
        "project": _truncate(metadata.get("project"), 96),
        "created_at": _truncate(metadata.get("created_at"), 64),
        "status": _truncate(metadata.get("status"), 64),
        "verified": bool(metadata.get("verified", False)),
        "verified_by": _truncate(metadata.get("verified_by"), 128),
        "category": _truncate(metadata.get("category"), 128),
        "suggestion_type": _truncate(metadata.get("suggestion_type"), 128),
        "resolution_type": _truncate(metadata.get("resolution_type"), 64),
        "knowledge_weight": _coerce_float(metadata.get("knowledge_weight"), 1.0),
        "success_score": _coerce_float(metadata.get("success_score"), 0.0),
        "content": content_text[:MAX_CONTENT_LENGTH],
        "embedding_text": embedding_source,
        "embedding": hash_embedding(embedding_source, size=VECTOR_DIMENSION),
    }


def _normalize_retrieved_entity(collection_name: str, entity: Dict[str, object], score: float | None = None) -> Dict[str, object]:
    document = {
        "title": str(entity.get("title") or ""),
        "reference": str(entity.get("reference") or ""),
        "content": str(entity.get("content") or ""),
        "doc_type": str(entity.get("doc_type") or _default_doc_type(collection_name)),
        "collection": collection_name,
        "stage": str(entity.get("stage") or _collection_stage(collection_name)),
        "incident_id": str(entity.get("incident_id") or ""),
        "category": str(entity.get("category") or ""),
        "knowledge_weight": _coerce_float(entity.get("knowledge_weight"), 1.0),
        "score": round(score if score is not None else 0.0, 4),
    }
    return document


def publish_semantic_record(
    collection_name: str,
    reference: str,
    title: str,
    content: str | Dict[str, object],
    *,
    doc_type: str | None = None,
    embedding_text: str | None = None,
    metadata: Dict[str, object] | None = None,
) -> bool:
    client = milvus_client()
    if client is None:
        return False
    if not ensure_milvus_collection(client, collection_name):
        return False

    payload = build_semantic_record(
        collection_name,
        reference,
        title,
        content,
        doc_type=doc_type,
        embedding_text=embedding_text,
        metadata=metadata,
    )
    try:
        client.upsert(collection_name=collection_name, data=[payload])
        return True
    except Exception:
        return False


def publish_document(
    collection_name: str,
    reference: str,
    title: str,
    content: str,
    doc_type: str | None = None,
) -> bool:
    return publish_semantic_record(
        collection_name,
        reference,
        title,
        content,
        doc_type=doc_type,
    )


def _historical_incident_seed(reference: str, title: str, payload: Dict[str, object], collection_name: str) -> Dict[str, object]:
    incident_id = str(payload.get("incident_id") or Path(reference).stem)
    symptoms = [str(item) for item in payload.get("symptoms") or []]
    components = [str(item) for item in payload.get("components") or []]
    root_cause = str(payload.get("root_cause") or "")
    resolution = str(payload.get("resolution") or "")
    if collection_name == "incident_evidence":
        content = {
            "incident_id": incident_id,
            "title": title,
            "stage": "evidence",
            "project": "ims-demo",
            "symptoms": symptoms,
            "components": components,
            "record_status": "historical",
        }
        embedding_text = (
            f"Evidence incident {incident_id}. Title: {title}. "
            f"Symptoms: {'; '.join(symptoms) or 'none'}. Components: {'; '.join(components) or 'unknown'}."
        )
        metadata = {
            "stage": "evidence",
            "incident_id": incident_id,
            "project": "ims-demo",
            "status": "historical",
            "knowledge_weight": 0.7,
        }
        doc_type = "incident_evidence"
    elif collection_name == "incident_reasoning":
        content = {
            "incident_id": incident_id,
            "title": title,
            "stage": "rca",
            "record_status": "historical",
            "root_cause": root_cause,
            "symptoms": symptoms,
            "components": components,
        }
        embedding_text = (
            f"RCA incident {incident_id}. Root cause: {root_cause or 'unknown'}. "
            f"Symptoms: {'; '.join(symptoms) or 'none'}. Components: {'; '.join(components) or 'unknown'}."
        )
        metadata = {
            "stage": "rca",
            "incident_id": incident_id,
            "parent_id": incident_id,
            "project": "ims-demo",
            "status": "historical",
            "category": "historical_rca",
            "knowledge_weight": 0.8,
        }
        doc_type = "incident_reasoning"
    else:
        content = {
            "incident_id": incident_id,
            "title": title,
            "stage": "resolution",
            "verified": True,
            "verified_by": "historical-knowledge",
            "resolution_type": "historical_verified",
            "resolution_summary": resolution,
            "operator_notes": root_cause,
        }
        embedding_text = (
            f"Verified resolution incident {incident_id}. "
            f"Actual fix applied: {resolution or 'unknown'}. Why it worked: {root_cause or 'unknown'}."
        )
        metadata = {
            "stage": "resolution",
            "incident_id": incident_id,
            "parent_id": incident_id,
            "project": "ims-demo",
            "status": "historical",
            "verified": True,
            "verified_by": "historical-knowledge",
            "resolution_type": "historical_verified",
            "knowledge_weight": 1.0,
            "success_score": 1.0,
        }
        doc_type = "verified_resolution"
    return build_semantic_record(
        collection_name,
        reference,
        title,
        content,
        doc_type=doc_type,
        embedding_text=embedding_text,
        metadata=metadata,
    )


def _build_runbook_bundle_records(path: Path, payload: Dict[str, Any]) -> List[Dict[str, object]]:
    category = _normalize_category(payload.get("category") or path.stem)
    articles = payload.get("articles")
    if not isinstance(articles, list):
        return []

    records: List[Dict[str, object]] = []
    for index, article in enumerate(articles, start=1):
        if not isinstance(article, dict):
            continue
        title = str(article.get("title") or f"{category or 'knowledge'} article {index}").strip()
        summary = str(article.get("summary") or "").strip()
        slug = _slugify(article.get("slug") or title)
        anomaly_types = article.get("anomaly_types") or []
        if not isinstance(anomaly_types, list):
            anomaly_types = []
        anomaly_labels = [str(item).strip() for item in anomaly_types if str(item).strip()]
        article_category = _normalize_category(article.get("category") or category)
        raw_content = article.get("content") or ""
        if isinstance(raw_content, list):
            content = "\n".join(str(item) for item in raw_content).strip()
        else:
            content = str(raw_content).strip()
        body = content if content.startswith("#") else f"# {title}\n\n{content}".strip()
        embedding_parts = [title, summary, body]
        if anomaly_labels:
            embedding_parts.append(f"anomaly_types: {', '.join(anomaly_labels)}")
        reference = f"knowledge/{article_category or 'general'}/{slug}.md"
        records.append(
            build_semantic_record(
                RUNBOOK_COLLECTION,
                reference,
                title,
                body,
                doc_type=str(article.get("doc_type") or KNOWLEDGE_ARTICLE_DOC_TYPE),
                embedding_text="\n\n".join(part for part in embedding_parts if part),
                metadata={
                    "stage": _collection_stage(RUNBOOK_COLLECTION),
                    "status": "seeded",
                    "category": article_category,
                    "knowledge_weight": _coerce_float(article.get("knowledge_weight"), 0.95),
                },
            )
        )
    return records


def build_local_seed_records(path: Path, collection_name: str) -> List[Dict[str, object]]:
    reference = _collection_reference(path, collection_name)
    if path.suffix == ".json":
        payload = json.loads(path.read_text())
        title = str(payload.get("title") or payload.get("incident_id") or path.stem)
        if collection_name in {"incident_evidence", "incident_reasoning", "incident_resolution"}:
            return [_historical_incident_seed(reference, title, payload, collection_name)]
        if collection_name == RUNBOOK_COLLECTION and isinstance(payload, dict) and isinstance(payload.get("articles"), list):
            bundle_records = _build_runbook_bundle_records(path, payload)
            if bundle_records:
                return bundle_records
        content = payload
    else:
        content = path.read_text()
        title = path.stem
        for line in str(content).splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                title = stripped
                break

    metadata = {
        "stage": _collection_stage(collection_name),
        "status": "seeded",
        "knowledge_weight": 0.85,
    }
    category = _collection_category(path, collection_name)
    if category:
        metadata["category"] = category
    return [
        build_semantic_record(
            collection_name,
            reference,
            title,
            content,
            doc_type=_default_doc_type(collection_name),
            embedding_text=f"{title}\n{_content_to_text(content)}",
            metadata=metadata,
        )
    ]


def build_local_seed_record(path: Path, collection_name: str) -> Dict[str, object]:
    return build_local_seed_records(path, collection_name)[0]


def _iter_local_documents() -> Iterator[Tuple[str, Dict[str, object]]]:
    root = _rag_root_dir()
    for collection, directory in LOCAL_COLLECTION_DIRS.items():
        base_dir = root / directory
        if not base_dir.exists():
            continue
        for path in sorted(base_dir.rglob("*")):
            if not path.is_file():
                continue
            for payload in build_local_seed_records(path, collection):
                yield collection, {
                    "title": payload["title"],
                    "reference": payload["reference"],
                    "content": payload["content"],
                    "doc_type": payload["doc_type"],
                    "stage": payload["stage"],
                    "category": payload.get("category", ""),
                    "knowledge_weight": payload["knowledge_weight"],
                    "embedding_text": payload["embedding_text"],
                }


def local_retrieve(
    query: str,
    limit: int = 3,
    *,
    collections: Sequence[str] | None = None,
    category: str | None = None,
) -> List[Dict[str, object]]:
    query_embedding = hash_embedding(query)
    selected_collections = set(collections or [])
    required_category = _normalize_category(category)
    docs = []
    for collection, doc in _iter_local_documents():
        if selected_collections and collection not in selected_collections:
            continue
        if required_category and _normalize_category(doc.get("category")) != required_category:
            continue
        similarity = _cosine(query_embedding, hash_embedding(str(doc["embedding_text"])))
        weighted_score = similarity * max(_coerce_float(doc.get("knowledge_weight"), 1.0), 0.25)
        docs.append(
            {
                **doc,
                "collection": collection,
                "score": round(weighted_score, 4),
            }
        )
    docs.sort(key=lambda item: item["score"], reverse=True)
    return docs[:limit]


def _milvus_filter_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def milvus_retrieve(
    query: str,
    limit: int = 3,
    *,
    collections: Sequence[str] | None = None,
    category: str | None = None,
) -> List[Dict[str, object]]:
    client = milvus_client()
    if client is None:
        return []

    docs = []
    selected_collections = list(collections or _milvus_collections())
    required_category = _normalize_category(category)
    for collection in selected_collections:
        search_kwargs: Dict[str, object] = {
            "collection_name": collection,
            "data": [hash_embedding(query)],
            "output_fields": list(MILVUS_OUTPUT_FIELDS),
            "limit": limit,
        }
        if required_category:
            search_kwargs["filter"] = f'category == "{_milvus_filter_literal(required_category)}"'
        try:
            results = client.search(**search_kwargs)
        except Exception:
            if "filter" not in search_kwargs:
                continue
            try:
                fallback_kwargs = dict(search_kwargs)
                fallback_kwargs.pop("filter", None)
                results = client.search(**fallback_kwargs)
            except Exception:
                continue
        for hit in results[0]:
            entity = hit["entity"]
            if required_category and _normalize_category(entity.get("category")) != required_category:
                continue
            knowledge_weight = _coerce_float(entity.get("knowledge_weight"), 1.0)
            weighted_score = float(hit["distance"]) * max(knowledge_weight, 0.25)
            docs.append(_normalize_retrieved_entity(collection, entity, score=weighted_score))
    docs.sort(key=lambda item: item["score"], reverse=True)
    return docs[:limit]


def local_document_by_reference(reference: str, collection_name: str | None = None) -> Dict[str, object] | None:
    selected_collections = {collection_name} if collection_name else None
    for collection, document in _iter_local_documents():
        if selected_collections and collection not in selected_collections:
            continue
        if str(document.get("reference") or "") == reference:
            return {
                "title": str(document.get("title") or ""),
                "reference": str(document.get("reference") or ""),
                "content": str(document.get("content") or ""),
                "doc_type": str(document.get("doc_type") or _default_doc_type(collection)),
                "collection": collection,
                "stage": str(document.get("stage") or _collection_stage(collection)),
                "incident_id": "",
                "category": str(document.get("category") or ""),
                "knowledge_weight": _coerce_float(document.get("knowledge_weight"), 1.0),
                "score": 0.0,
            }
    return None


def milvus_document_by_reference(reference: str, collection_name: str | None = None) -> Dict[str, object] | None:
    client = milvus_client()
    if client is None:
        return None
    selected_collections = [collection_name] if collection_name else _milvus_collections()
    filter_expression = f'reference == "{_milvus_filter_literal(reference)}"'
    for collection in selected_collections:
        try:
            results = client.query(
                collection_name=collection,
                filter=filter_expression,
                output_fields=list(MILVUS_OUTPUT_FIELDS),
            )
        except Exception:
            continue
        if not results:
            continue
        entity = results[0]
        if isinstance(entity, dict):
            return _normalize_retrieved_entity(collection, entity)
    return None


def get_document_by_reference(reference: str, collection_name: str | None = None) -> Dict[str, object] | None:
    document = milvus_document_by_reference(reference, collection_name=collection_name)
    if document:
        return document
    return local_document_by_reference(reference, collection_name=collection_name)


def retrieve_context(
    query: str,
    limit: int = 3,
    *,
    collections: Sequence[str] | None = None,
    category: str | None = None,
) -> List[Dict[str, object]]:
    docs = milvus_retrieve(query, limit=limit, collections=collections, category=category)
    if docs:
        return docs
    return local_retrieve(query, limit=limit, collections=collections, category=category)


def retrieve_knowledge_articles(query: str, category: str | None = None, limit: int = 10) -> List[Dict[str, object]]:
    return retrieve_context(query, limit=limit, collections=[RUNBOOK_COLLECTION], category=category)


def build_prompt(incident_context: Dict[str, object], documents: List[Dict[str, object]]) -> str:
    evidence = "\n\n".join(
        f"Collection: {doc.get('collection', 'unknown')}\n"
        f"Stage: {doc.get('stage', 'unknown')}\n"
        f"Type: {doc.get('doc_type', 'unknown')}\n"
        f"Document: {doc['reference']}\n"
        f"Content:\n{str(doc['content'])[:1200]}"
        for doc in documents
    )
    return (
        "You are generating structured root cause analysis for an IMS platform incident.\n"
        f"Incident context:\n{json.dumps(incident_context, indent=2)}\n\n"
        "Use the supplied evidence only.\n"
        "Return JSON with keys: root_cause, explanation, confidence, evidence, recommendation.\n"
        "Keep root_cause concise, but make explanation a grounded 2-4 sentence analysis that explains why the evidence supports the diagnosis.\n\n"
        f"{evidence}"
    )


def _llm_chat_completions_url(endpoint: str) -> str:
    base = endpoint.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _coerce_llm_message_content(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "".join(parts).strip()
    return str(content).strip()


def _parse_llm_json_content(content: object) -> Dict[str, object] | None:
    text = _coerce_llm_message_content(content)
    if text.startswith("```"):
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    parsed = json.loads(text)
    return parsed if isinstance(parsed, dict) else None


def generate_with_llm_trace(prompt: str) -> Dict[str, object] | None:
    endpoint = os.getenv("LLM_ENDPOINT", "").rstrip("/")
    model_name = os.getenv("LLM_MODEL", "granite")
    api_key = os.getenv("LLM_API_KEY", "").strip()
    request_timeout_seconds = float(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "10"))
    if not endpoint:
        return None

    request_endpoint = _llm_chat_completions_url(endpoint)
    request_payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "Respond only with valid JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    metadata = {
        "model_name": model_name,
        "timeout_seconds": request_timeout_seconds,
    }
    started_at = trace_now()
    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        response = requests.post(
            request_endpoint,
            headers=headers,
            json=request_payload,
            timeout=request_timeout_seconds,
        )
        finished_at = trace_now()
        response.raise_for_status()
        response_payload = response.json()
        content = response_payload["choices"][0]["message"]["content"]
        raw_content = _coerce_llm_message_content(content)
        parsed = _parse_llm_json_content(content)
        return {
            "parsed": parsed,
            "request_payload": request_payload,
            "response_payload": response_payload,
            "raw_content": raw_content,
            "started_at": started_at,
            "finished_at": finished_at,
            "trace_packets": interaction_trace_packets(
                category="llm",
                service="rca-service",
                target="llm-runtime",
                method="POST",
                endpoint=request_endpoint,
                request_payload=request_payload,
                response_payload={
                    "status_code": response.status_code,
                    "body": response_payload,
                    "raw_content": raw_content,
                    "parsed_json": parsed,
                    "reasoning": ((response_payload.get("choices") or [{}])[0].get("message") or {}).get("reasoning"),
                },
                request_timestamp=started_at,
                response_timestamp=finished_at,
                metadata=metadata,
            ),
        }
    except Exception as exc:
        finished_at = trace_now()
        return {
            "parsed": None,
            "request_payload": request_payload,
            "response_payload": {"error": str(exc)},
            "raw_content": "",
            "started_at": started_at,
            "finished_at": finished_at,
            "trace_packets": interaction_trace_packets(
                category="llm",
                service="rca-service",
                target="llm-runtime",
                method="POST",
                endpoint=request_endpoint,
                request_payload=request_payload,
                response_payload={"error": str(exc)},
                request_timestamp=started_at,
                response_timestamp=finished_at,
                metadata=metadata,
            ),
        }


def generate_with_llm(prompt: str) -> Dict[str, object] | None:
    trace = generate_with_llm_trace(prompt)
    if not trace:
        return None
    parsed = trace.get("parsed")
    return parsed if isinstance(parsed, dict) else None
