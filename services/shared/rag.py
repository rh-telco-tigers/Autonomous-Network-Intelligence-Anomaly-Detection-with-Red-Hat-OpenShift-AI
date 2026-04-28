import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Sequence, Tuple

import requests

from shared.debug_trace import interaction_trace_packets, trace_now


DEFAULT_MILVUS_COLLECTIONS = (
    "ani_runbooks",
    "incident_evidence",
    "incident_reasoning",
    "incident_resolution",
    "ani_topology",
    "ani_signal_patterns",
)
LEGACY_MILVUS_COLLECTIONS = ("ani_incidents",)
VECTOR_DIMENSION = 64
MAX_CONTENT_LENGTH = 16384
MAX_EMBEDDING_TEXT_LENGTH = 4096
MAX_RETRIEVAL_CANDIDATES = 24
RUNBOOK_COLLECTION = "ani_runbooks"
KNOWLEDGE_ARTICLE_DOC_TYPE = "knowledge_article"
RUNBOOK_SCHEMA_VERSION = "2026-04-06"
MILVUS_REPAIR_COOLDOWN_SECONDS = 30.0
MILVUS_LOAD_TIMEOUT_SECONDS = 20.0
MILVUS_LOAD_POLL_SECONDS = 0.5
TOKEN_PATTERN = re.compile(r"[a-z0-9_]{2,}")
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "if",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}
LOCAL_COLLECTION_DIRS = {
    "ani_runbooks": "runbooks",
    "incident_evidence": "incidents",
    "incident_reasoning": "incidents",
    "incident_resolution": "incidents",
    "ani_topology": "topology",
    "ani_signal_patterns": "signal_patterns",
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
_MILVUS_REPAIR_LOCK = threading.Lock()
_MILVUS_REPAIR_ATTEMPTS: Dict[str, float] = {}
_LOGGER = logging.getLogger(__name__)


def _env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        return max(value, minimum)
    return value


def _env_float(name: str, default: float, *, minimum: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        return max(value, minimum)
    return value


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
    attempts = _env_int("MILVUS_CONNECT_ATTEMPTS", 1, minimum=1)
    backoff_seconds = _env_float("MILVUS_CONNECT_BACKOFF_SECONDS", 0.5, minimum=0.0)
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return MilvusClient(uri=uri)
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts and backoff_seconds:
                time.sleep(backoff_seconds)
    if last_error is not None:
        _LOGGER.debug("Milvus unavailable at %s: %s", uri, last_error)
    return None


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
    return COLLECTION_STAGE_DEFAULTS.get(collection_name, collection_name.removeprefix("ani_"))


def _default_doc_type(collection_name: str) -> str:
    return COLLECTION_DOC_TYPE_DEFAULTS.get(collection_name, collection_name.removeprefix("ani_"))


def _content_to_text(content: str | Dict[str, object]) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, indent=2, sort_keys=True)


def _normalize_category(value: object) -> str:
    return str(value or "").strip().lower()


def _normalize_string_list(value: object) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _structured_content_payload(content: object) -> Dict[str, object] | None:
    if isinstance(content, dict):
        return content
    text = str(content or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _flatten_text_fragments(value: object) -> List[str]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, dict):
        fragments: List[str] = []
        for key, nested in value.items():
            label = str(key or "").replace("_", " ").strip()
            nested_fragments = _flatten_text_fragments(nested)
            if nested_fragments:
                fragments.append(f"{label}: {' '.join(nested_fragments)}" if label else " ".join(nested_fragments))
        return fragments
    if isinstance(value, list):
        fragments: List[str] = []
        for item in value:
            fragments.extend(_flatten_text_fragments(item))
        return fragments
    return [str(value).strip()]


def _clean_article_value(value: object) -> object | None:
    if value in (None, "", [], {}):
        return None
    if isinstance(value, list):
        cleaned = [item for item in value if item not in (None, "", [], {})]
        return cleaned or None
    return value


def _structured_runbook_article(
    article: Dict[str, Any],
    *,
    title: str,
    summary: str,
    category: str,
    anomaly_labels: List[str],
) -> Dict[str, object]:
    raw_content = article.get("content")
    if isinstance(raw_content, dict):
        structured: Dict[str, object] = dict(raw_content)
    else:
        guidance: List[str] = []
        if isinstance(raw_content, list):
            guidance = [str(item).strip() for item in raw_content if str(item).strip()]
        else:
            text = str(raw_content or "").strip()
            if text:
                guidance = [text]
        structured = {"guidance": guidance} if guidance else {}

    content: Dict[str, object] = {
        "schema_version": RUNBOOK_SCHEMA_VERSION,
        "slug": _slugify(article.get("slug") or title),
        "doc_type": str(article.get("doc_type") or KNOWLEDGE_ARTICLE_DOC_TYPE),
        "title": title,
        "summary": summary,
        "category": category,
        "anomaly_types": anomaly_labels,
    }
    for field in (
        "keywords",
        "service_scope",
        "symptom_profile",
        "differential_diagnosis",
        "evidence_to_collect",
        "recommended_rca",
        "operator_actions",
        "safe_actions",
        "escalation_signals",
        "pitfalls",
        "telemetry_queries",
        "reference_incident_pattern",
    ):
        cleaned = _clean_article_value(article.get(field))
        if cleaned is not None:
            content[field] = cleaned
    for key, value in structured.items():
        cleaned = _clean_article_value(value)
        if cleaned is not None and key not in content:
            content[str(key)] = cleaned
    return {key: value for key, value in content.items() if _clean_article_value(value) is not None}


def _runbook_embedding_text(title: str, summary: str, content: Dict[str, object], anomaly_labels: List[str]) -> str:
    parts = [title, summary]
    category = str(content.get("category") or "").strip()
    if category:
        parts.append(f"category: {category}")
    if anomaly_labels:
        parts.append(f"anomaly_types: {', '.join(anomaly_labels)}")
    for field in (
        "keywords",
        "service_scope",
        "symptom_profile",
        "differential_diagnosis",
        "evidence_to_collect",
        "recommended_rca",
        "operator_actions",
        "safe_actions",
        "escalation_signals",
        "pitfalls",
        "telemetry_queries",
        "reference_incident_pattern",
        "guidance",
    ):
        fragments = _flatten_text_fragments(content.get(field))
        if fragments:
            parts.append(f"{field}: {' '.join(fragments)}")
    return "\n\n".join(part for part in parts if part)


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


def _document_payload(document: Dict[str, object]) -> Dict[str, object] | None:
    return _structured_content_payload(document.get("content"))


def _document_anomaly_types(document: Dict[str, object]) -> List[str]:
    payload = _document_payload(document)
    return _normalize_string_list((payload or {}).get("anomaly_types"))


def _document_summary(document: Dict[str, object]) -> str:
    payload = _document_payload(document)
    if not payload:
        return str(document.get("content") or "").strip()
    parts = [str(payload.get("summary") or "").strip()]
    recommended_rca = payload.get("recommended_rca")
    if isinstance(recommended_rca, dict):
        parts.append(str(recommended_rca.get("root_cause") or "").strip())
    primary_signals = _flatten_text_fragments((payload.get("symptom_profile") or {}).get("primary_signals"))
    if primary_signals:
        parts.append(primary_signals[0])
    return " ".join(part for part in parts if part).strip()


def _document_keywords(document: Dict[str, object]) -> List[str]:
    payload = _document_payload(document) or {}
    keywords = _normalize_string_list(payload.get("keywords"))
    keywords.extend(_normalize_string_list(payload.get("service_scope")))
    return keywords


def _document_sections_text(document: Dict[str, object]) -> str:
    payload = _document_payload(document)
    if not payload:
        return str(document.get("content") or "")
    return " ".join(
        fragment
        for field in (
            "summary",
            "keywords",
            "service_scope",
            "symptom_profile",
            "differential_diagnosis",
            "evidence_to_collect",
            "recommended_rca",
            "operator_actions",
            "safe_actions",
            "escalation_signals",
            "pitfalls",
            "telemetry_queries",
            "reference_incident_pattern",
            "guidance",
        )
        for fragment in _flatten_text_fragments(payload.get(field))
    )


def _tokenize(text: object) -> List[str]:
    raw_tokens = TOKEN_PATTERN.findall(str(text or "").lower())
    return [token for token in raw_tokens if token not in STOP_WORDS]


def _token_overlap(query_tokens: Sequence[str], doc_tokens: Sequence[str]) -> float:
    if not query_tokens or not doc_tokens:
        return 0.0
    query_set = set(query_tokens)
    doc_set = set(doc_tokens)
    return min(1.0, len(query_set & doc_set) / max(min(len(query_set), 10), 1))


def _document_lexical_score(document: Dict[str, object], query_tokens: Sequence[str]) -> float:
    if not query_tokens:
        return 0.0
    title_tokens = _tokenize(document.get("title"))
    summary_tokens = _tokenize(_document_summary(document))
    keyword_tokens = _tokenize(" ".join(_document_keywords(document)))
    body_tokens = _tokenize(_document_sections_text(document))[:160]
    return min(
        1.0,
        (_token_overlap(query_tokens, title_tokens) * 0.35)
        + (_token_overlap(query_tokens, summary_tokens) * 0.3)
        + (_token_overlap(query_tokens, keyword_tokens) * 0.2)
        + (_token_overlap(query_tokens, body_tokens) * 0.15),
    )


def _document_structure_score(
    document: Dict[str, object],
    *,
    collection_name: str,
    anomaly_type: str | None,
    category: str | None,
    query_tokens: Sequence[str],
) -> Tuple[float, float, List[str]]:
    reasons: List[str] = []
    payload = _document_payload(document) or {}
    score = 0.0
    penalty = 1.0
    required_category = _normalize_category(category)
    document_category = _normalize_category(document.get("category") or payload.get("category"))
    anomaly_labels = _document_anomaly_types(document)
    normalized_anomaly = str(anomaly_type or "").strip().lower()

    if required_category and document_category == required_category:
        score += 0.18
        reasons.append(f"Category match: {required_category}")
    if collection_name == RUNBOOK_COLLECTION and normalized_anomaly:
        if normalized_anomaly in anomaly_labels:
            score += 0.52
            reasons.append(f"Exact anomaly match: {normalized_anomaly}")
        elif anomaly_labels:
            penalty = 0.42
    shared_hints = sorted(set(_tokenize(" ".join(_document_keywords(document)))) & set(query_tokens))
    if shared_hints:
        score += min(0.16, 0.04 * len(shared_hints))
        reasons.append(f"Shared hints: {', '.join(shared_hints[:4])}")
    if isinstance(payload.get("recommended_rca"), dict):
        score += 0.08
        reasons.append("Structured RCA guidance")
    return min(score, 1.0), penalty, reasons


def _hybrid_document_score(
    document: Dict[str, object],
    *,
    collection_name: str,
    query: str,
    anomaly_type: str | None,
    category: str | None,
) -> Tuple[float, Dict[str, float], List[str]]:
    query_tokens = _tokenize(query)
    vector_score = _cosine(hash_embedding(query), hash_embedding(str(document.get("embedding_text") or document.get("content") or document.get("title") or "")))
    lexical_score = _document_lexical_score(document, query_tokens)
    structure_score, penalty, reasons = _document_structure_score(
        document,
        collection_name=collection_name,
        anomaly_type=anomaly_type,
        category=category,
        query_tokens=query_tokens,
    )
    breakdown = {
        "vector": round(vector_score, 4),
        "lexical": round(lexical_score, 4),
        "structure": round(structure_score, 4),
        "penalty": round(penalty, 4),
    }
    combined = ((vector_score * 0.4) + (lexical_score * 0.36) + (structure_score * 0.24)) * penalty
    weighted = combined * max(_coerce_float(document.get("knowledge_weight"), 1.0), 0.25)
    return weighted, breakdown, reasons


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
    payload = _document_payload(document)
    if payload:
        document["summary"] = str(payload.get("summary") or "")
        document["anomaly_types"] = _normalize_string_list(payload.get("anomaly_types"))
        document["match_reasons"] = []
        document["score_breakdown"] = {}
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
    if not ensure_milvus_collection_ready(
        client,
        collection_name,
        seed_if_empty=False,
        load=False,
        force=True,
    ):
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
            "project": "ani-demo",
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
            "project": "ani-demo",
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
            "project": "ani-demo",
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
            "project": "ani-demo",
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
        if not title or not summary:
            raise ValueError(f"Runbook article {path.name}#{index} is missing a title or summary")
        slug = _slugify(article.get("slug") or title)
        anomaly_types = article.get("anomaly_types") or []
        if not isinstance(anomaly_types, list):
            anomaly_types = []
        anomaly_labels = [str(item).strip() for item in anomaly_types if str(item).strip()]
        if not anomaly_labels:
            raise ValueError(f"Runbook article {path.name}#{index} must declare at least one anomaly type")
        article_category = _normalize_category(article.get("category") or category)
        content = _structured_runbook_article(
            article,
            title=title,
            summary=summary,
            category=article_category or "general",
            anomaly_labels=anomaly_labels,
        )
        reference = f"knowledge/{article_category or 'general'}/{slug}.json"
        records.append(
            build_semantic_record(
                RUNBOOK_COLLECTION,
                reference,
                title,
                content,
                doc_type=str(article.get("doc_type") or KNOWLEDGE_ARTICLE_DOC_TYPE),
                embedding_text=_runbook_embedding_text(title, summary, content, anomaly_labels),
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


def _local_seed_documents(collection_name: str) -> List[Dict[str, object]]:
    directory = LOCAL_COLLECTION_DIRS.get(collection_name)
    if not directory:
        return []
    source_dir = _rag_root_dir() / directory
    if not source_dir.exists():
        return []

    docs: List[Dict[str, object]] = []
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        docs.extend(build_local_seed_records(path, collection_name))
    return docs


def _collection_row_count(client, collection_name: str) -> int | None:
    try:
        stats = client.get_collection_stats(collection_name=collection_name)
    except Exception:
        return None
    if not isinstance(stats, dict):
        return None
    for key in ("row_count", "num_rows"):
        value = stats.get(key)
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _load_state_label(value: object) -> str:
    text = str(value or "").strip().lower()
    if "notload" in text or "not load" in text:
        return "notload"
    if "loading" in text:
        return "loading"
    if "loaded" in text:
        return "loaded"
    return text


def _collection_load_state(client, collection_name: str) -> str:
    try:
        state = client.get_load_state(collection_name=collection_name) or {}
    except Exception:
        return ""
    if isinstance(state, dict):
        return _load_state_label(state.get("state"))
    return _load_state_label(state)


def _ensure_collection_loaded(client, collection_name: str) -> bool:
    state = _collection_load_state(client, collection_name)
    if state == "loaded":
        return True
    try:
        client.load_collection(collection_name=collection_name)
    except Exception:
        return False

    load_timeout = _env_float("MILVUS_LOAD_TIMEOUT_SECONDS", MILVUS_LOAD_TIMEOUT_SECONDS, minimum=1.0)
    deadline = time.monotonic() + load_timeout
    while time.monotonic() < deadline:
        state = _collection_load_state(client, collection_name)
        if state == "loaded":
            return True
        if state not in {"", "loading"}:
            return False
        time.sleep(MILVUS_LOAD_POLL_SECONDS)
    return _collection_load_state(client, collection_name) == "loaded"


def ensure_milvus_collection_ready(
    client,
    collection_name: str,
    *,
    seed_if_empty: bool = False,
    load: bool = True,
    force: bool = False,
) -> bool:
    now = time.monotonic()
    if not force:
        with _MILVUS_REPAIR_LOCK:
            last_attempt = _MILVUS_REPAIR_ATTEMPTS.get(collection_name, 0.0)
            if now - last_attempt < MILVUS_REPAIR_COOLDOWN_SECONDS:
                return True
            _MILVUS_REPAIR_ATTEMPTS[collection_name] = now

    try:
        exists = client.has_collection(collection_name=collection_name)
    except Exception:
        return False

    if not exists and not ensure_milvus_collection(client, collection_name):
        return False

    if seed_if_empty:
        row_count = _collection_row_count(client, collection_name)
        if row_count == 0:
            docs = _local_seed_documents(collection_name)
            if docs:
                try:
                    client.upsert(collection_name=collection_name, data=docs)
                except Exception:
                    return False

    if not load:
        return True
    return _ensure_collection_loaded(client, collection_name)


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
    anomaly_type: str | None = None,
) -> List[Dict[str, object]]:
    selected_collections = set(collections or [])
    required_category = _normalize_category(category)
    docs = []
    for collection, doc in _iter_local_documents():
        if selected_collections and collection not in selected_collections:
            continue
        if required_category and _normalize_category(doc.get("category")) != required_category:
            continue
        weighted_score, breakdown, reasons = _hybrid_document_score(
            doc,
            collection_name=collection,
            query=query,
            anomaly_type=anomaly_type,
            category=required_category,
        )
        docs.append(
            {
                **doc,
                "collection": collection,
                "score": round(weighted_score, 4),
                "summary": _document_summary(doc),
                "anomaly_types": _document_anomaly_types(doc),
                "match_reasons": reasons,
                "score_breakdown": breakdown,
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
    anomaly_type: str | None = None,
) -> List[Dict[str, object]]:
    client = milvus_client()
    if client is None:
        return []

    docs = []
    selected_collections = list(collections or _milvus_collections())
    required_category = _normalize_category(category)
    candidate_limit = min(MAX_RETRIEVAL_CANDIDATES, max(limit * 8, limit))
    for collection in selected_collections:
        results: List[List[Dict[str, object]]] | List[Dict[str, object]] = []
        ensure_milvus_collection_ready(
            client,
            collection,
            seed_if_empty=True,
            load=True,
        )
        search_kwargs: Dict[str, object] = {
            "collection_name": collection,
            "data": [hash_embedding(query)],
            "output_fields": list(MILVUS_OUTPUT_FIELDS),
            "limit": candidate_limit,
        }
        if required_category:
            search_kwargs["filter"] = f'category == "{_milvus_filter_literal(required_category)}"'
        try:
            results = client.search(**search_kwargs)
        except Exception:
            repaired = ensure_milvus_collection_ready(
                client,
                collection,
                seed_if_empty=True,
                load=True,
                force=True,
            )
            if repaired:
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
                else:
                    pass
            elif "filter" not in search_kwargs:
                continue
            else:
                try:
                    fallback_kwargs = dict(search_kwargs)
                    fallback_kwargs.pop("filter", None)
                    results = client.search(**fallback_kwargs)
                except Exception:
                    continue
        if not results:
            continue
        for hit in results[0]:
            entity = hit["entity"]
            if required_category and _normalize_category(entity.get("category")) != required_category:
                continue
            document = _normalize_retrieved_entity(collection, entity)
            weighted_score, breakdown, reasons = _hybrid_document_score(
                {
                    **document,
                    "embedding_text": entity.get("embedding_text") or "",
                },
                collection_name=collection,
                query=query,
                anomaly_type=anomaly_type,
                category=required_category,
            )
            document["score"] = round(weighted_score, 4)
            document["summary"] = _document_summary(document)
            document["anomaly_types"] = _document_anomaly_types(document)
            document["match_reasons"] = reasons
            document["score_breakdown"] = breakdown
            docs.append(document)
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
        ensure_milvus_collection_ready(
            client,
            collection,
            seed_if_empty=True,
            load=True,
        )
        try:
            results = client.query(
                collection_name=collection,
                filter=filter_expression,
                output_fields=list(MILVUS_OUTPUT_FIELDS),
            )
        except Exception:
            repaired = ensure_milvus_collection_ready(
                client,
                collection,
                seed_if_empty=True,
                load=True,
                force=True,
            )
            if not repaired:
                continue
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
    anomaly_type: str | None = None,
) -> List[Dict[str, object]]:
    candidate_limit = min(MAX_RETRIEVAL_CANDIDATES, max(limit * 4, limit))
    candidates = milvus_retrieve(
        query,
        limit=candidate_limit,
        collections=collections,
        category=category,
        anomaly_type=anomaly_type,
    )
    local_candidates = local_retrieve(
        query,
        limit=candidate_limit,
        collections=collections,
        category=category,
        anomaly_type=anomaly_type,
    )
    merged: Dict[Tuple[str, str], Dict[str, object]] = {}
    for document in [*candidates, *local_candidates]:
        key = (str(document.get("collection") or ""), str(document.get("reference") or ""))
        existing = merged.get(key)
        if not existing or float(document.get("score") or 0.0) > float(existing.get("score") or 0.0):
            merged[key] = dict(document)
        elif existing is not None:
            merged_reasons = list(dict.fromkeys([*(existing.get("match_reasons") or []), *(document.get("match_reasons") or [])]))
            existing["match_reasons"] = merged_reasons
    docs = sorted(merged.values(), key=lambda item: float(item.get("score") or 0.0), reverse=True)
    return docs[:limit]


def retrieve_knowledge_articles(
    query: str,
    category: str | None = None,
    *,
    anomaly_type: str | None = None,
    limit: int = 10,
) -> List[Dict[str, object]]:
    return retrieve_context(
        query,
        limit=limit,
        collections=[RUNBOOK_COLLECTION],
        category=category,
        anomaly_type=anomaly_type,
    )


def build_prompt(incident_context: Dict[str, object], documents: List[Dict[str, object]]) -> str:
    def _document_prompt_content(document: Dict[str, object]) -> str:
        payload = _document_payload(document)
        if not payload:
            return str(document.get("content") or "")[:1200]
        summary = str(payload.get("summary") or "")
        recommended_rca = payload.get("recommended_rca") or {}
        primary_signals = _flatten_text_fragments((payload.get("symptom_profile") or {}).get("primary_signals"))[:3]
        operator_actions = _flatten_text_fragments(payload.get("operator_actions"))[:3]
        return "\n".join(
            part
            for part in [
                f"Summary: {summary}" if summary else "",
                f"Anomaly types: {', '.join(_normalize_string_list(payload.get('anomaly_types')))}" if payload.get("anomaly_types") else "",
                f"Primary signals: {' | '.join(primary_signals)}" if primary_signals else "",
                f"Root cause guidance: {str(recommended_rca.get('root_cause') or '').strip()}" if isinstance(recommended_rca, dict) else "",
                f"Recommended response: {str(recommended_rca.get('recommendation') or '').strip()}" if isinstance(recommended_rca, dict) else "",
                f"Operator actions: {' | '.join(operator_actions)}" if operator_actions else "",
            ]
            if part
        )

    evidence = "\n\n".join(
        f"Collection: {doc.get('collection', 'unknown')}\n"
        f"Stage: {doc.get('stage', 'unknown')}\n"
        f"Type: {doc.get('doc_type', 'unknown')}\n"
        f"Document: {doc['reference']}\n"
        f"Match reasons: {', '.join(doc.get('match_reasons') or [])}\n"
        f"Content:\n{_document_prompt_content(doc)}"
        for doc in documents
    )
    return (
        "You are generating structured root cause analysis for an IMS platform incident.\n"
        f"Incident context:\n{json.dumps(incident_context, indent=2)}\n\n"
        "Use the supplied evidence only.\n"
        "Return JSON with keys: root_cause, explanation, confidence, evidence, recommendation.\n"
        "Keep root_cause concise, but make explanation a grounded 2-4 sentence analysis that explains why the evidence supports the diagnosis.\n\n"
        "Write the explanation as the incident diagnosis itself, not as authoring guidance.\n"
        "Do not say things like '<anomaly> should', 'the RCA should', or cite retrieved document titles or collections inside the explanation.\n\n"
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
    host_header = os.getenv("LLM_REQUEST_HOST_HEADER", "").strip()
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
    if host_header:
        metadata["host_header"] = host_header
    started_at = trace_now()
    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        if host_header:
            headers["Host"] = host_header
        response = requests.post(
            request_endpoint,
            headers=headers,
            json=request_payload,
            timeout=request_timeout_seconds,
        )
        finished_at = trace_now()
        response.raise_for_status()
        raw_response_text = response.text or ""
        try:
            response_payload = response.json()
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            response_payload = {
                "status_code": response.status_code,
                "raw_text": raw_response_text,
                "error": str(exc),
            }
            return {
                "parsed": None,
                "request_payload": request_payload,
                "response_payload": response_payload,
                "raw_content": raw_response_text,
                "started_at": started_at,
                "finished_at": finished_at,
                "trace_packets": interaction_trace_packets(
                    category="llm",
                    service="rca-service",
                    target="llm-runtime",
                    method="POST",
                    endpoint=request_endpoint,
                    request_payload=request_payload,
                    response_payload=response_payload,
                    request_timestamp=started_at,
                    response_timestamp=finished_at,
                    metadata=metadata,
                ),
            }

        try:
            content = response_payload["choices"][0]["message"]["content"]
            raw_content = _coerce_llm_message_content(content)
            parsed = _parse_llm_json_content(content)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            error_payload = {
                "status_code": response.status_code,
                "body": response_payload,
                "raw_text": raw_response_text,
                "error": str(exc),
            }
            return {
                "parsed": None,
                "request_payload": request_payload,
                "response_payload": error_payload,
                "raw_content": raw_response_text,
                "started_at": started_at,
                "finished_at": finished_at,
                "trace_packets": interaction_trace_packets(
                    category="llm",
                    service="rca-service",
                    target="llm-runtime",
                    method="POST",
                    endpoint=request_endpoint,
                    request_payload=request_payload,
                    response_payload=error_payload,
                    request_timestamp=started_at,
                    response_timestamp=finished_at,
                    metadata=metadata,
                ),
            }
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
