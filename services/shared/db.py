import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from shared.incident_taxonomy import NORMAL_ANOMALY_TYPE
from shared.guardrails import guardrail_status
from shared.workflow import NEW, RCA_GENERATED, REMEDIATION_SUGGESTED, normalize_workflow_state, severity_from_prediction, severity_from_score


def _db_path() -> Path:
    return Path(os.getenv("CONTROL_PLANE_DB_PATH", "/tmp/ani-demo-control-plane.db"))


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _table_columns(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def _ensure_columns(connection: sqlite3.Connection, table_name: str, columns: Dict[str, str]) -> None:
    existing = _table_columns(connection, table_name)
    for name, definition in columns.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")


def _json_dumps(value: object) -> str:
    return json.dumps(value)


def _json_loads(value: object, fallback: object) -> object:
    if not value:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def init_db() -> None:
    with closing(_connect()) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS incidents (
              id TEXT PRIMARY KEY,
              project TEXT NOT NULL,
              status TEXT NOT NULL,
              anomaly_score REAL NOT NULL,
              anomaly_type TEXT NOT NULL,
              model_version TEXT NOT NULL,
              feature_window_id TEXT,
              feature_snapshot TEXT,
              rca_payload TEXT,
              recommendation TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS approvals (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              incident_id TEXT NOT NULL,
              action TEXT NOT NULL,
              approved_by TEXT NOT NULL,
              execute INTEGER NOT NULL,
              status TEXT NOT NULL,
              output TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audit_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              event_type TEXT NOT NULL,
              actor TEXT NOT NULL,
              incident_id TEXT,
              payload TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS incident_rca (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              incident_id TEXT NOT NULL,
              version INTEGER NOT NULL,
              based_on_revision INTEGER NOT NULL,
              root_cause TEXT NOT NULL,
              category TEXT,
              confidence REAL NOT NULL,
              explanation TEXT,
              model_name TEXT,
              prompt_version TEXT,
              retrieval_refs TEXT,
              payload TEXT,
              created_at TEXT NOT NULL,
              request_id TEXT,
              trace_id TEXT,
              lifecycle_state TEXT,
              guardrail_status TEXT,
              guardrail_reason TEXT,
              is_active INTEGER DEFAULT 1,
              superseded_at TEXT
            );

            CREATE TABLE IF NOT EXISTS incident_remediation (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              incident_id TEXT NOT NULL,
              rca_id INTEGER,
              based_on_revision INTEGER NOT NULL,
              suggestion_rank INTEGER NOT NULL,
              title TEXT NOT NULL,
              suggestion_type TEXT NOT NULL,
              description TEXT NOT NULL,
              risk_level TEXT,
              confidence REAL,
              automation_level TEXT,
              requires_approval INTEGER NOT NULL,
              playbook_ref TEXT,
              action_ref TEXT,
              preconditions_json TEXT,
              expected_outcome TEXT,
              rank_score REAL,
              factors_json TEXT,
              metadata_json TEXT,
              playbook_yaml TEXT,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS incident_actions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              incident_id TEXT NOT NULL,
              remediation_id INTEGER,
              action_mode TEXT NOT NULL,
              source_of_action TEXT NOT NULL,
              approved_revision INTEGER NOT NULL,
              triggered_by TEXT NOT NULL,
              execution_status TEXT NOT NULL,
              notes TEXT,
              started_at TEXT,
              finished_at TEXT,
              result_summary TEXT,
              result_json TEXT
            );

            CREATE TABLE IF NOT EXISTS incident_verification (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              incident_id TEXT NOT NULL,
              action_id INTEGER,
              verified_by TEXT NOT NULL,
              verification_status TEXT NOT NULL,
              notes TEXT,
              custom_resolution TEXT,
              metric_based INTEGER NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS incident_tickets (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              incident_id TEXT NOT NULL,
              provider TEXT NOT NULL,
              external_key TEXT,
              external_id TEXT,
              workspace_id TEXT,
              project_id TEXT,
              status TEXT,
              url TEXT,
              title TEXT,
              last_synced_at TEXT,
              sync_state TEXT,
              last_synced_revision INTEGER,
              metadata_json TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(incident_id, provider)
            );

            CREATE TABLE IF NOT EXISTS ticket_sync_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ticket_id INTEGER NOT NULL,
              direction TEXT NOT NULL,
              event_type TEXT NOT NULL,
              delivery_id TEXT,
              payload_hash TEXT,
              status TEXT NOT NULL,
              payload TEXT,
              created_at TEXT NOT NULL,
              UNIQUE(ticket_id, delivery_id)
            );

            CREATE TABLE IF NOT EXISTS ticket_comments_index (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ticket_id INTEGER NOT NULL,
              external_comment_id TEXT NOT NULL UNIQUE,
              author TEXT,
              body TEXT,
              comment_type TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ticket_resolution_extracts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              incident_id TEXT NOT NULL,
              ticket_id INTEGER,
              source_comment_id TEXT,
              summary TEXT NOT NULL,
              verified INTEGER NOT NULL,
              verification_quality TEXT,
              knowledge_weight REAL,
              usage_count INTEGER NOT NULL DEFAULT 0,
              success_rate REAL NOT NULL DEFAULT 0,
              last_validated_at TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_settings (
              key TEXT PRIMARY KEY,
              value_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )
        connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_incidents_project_created_at
              ON incidents(project, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_approvals_incident_created_at
              ON approvals(incident_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_events_incident_created_at
              ON audit_events(incident_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_incident_rca_incident_created_at
              ON incident_rca(incident_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_incident_remediation_incident_revision_rank
              ON incident_remediation(incident_id, based_on_revision DESC, suggestion_rank ASC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_incident_actions_incident_finished_at
              ON incident_actions(incident_id, finished_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_incident_verification_incident_created_at
              ON incident_verification(incident_id, created_at DESC);
            """
        )
        _ensure_columns(
            connection,
            "incidents",
            {
                "severity": "TEXT",
                "source_system": "TEXT",
                "workflow_state": "TEXT",
                "workflow_revision": "INTEGER DEFAULT 1",
                "current_rca_id": "INTEGER",
                "current_ticket_id": "INTEGER",
                "duplicate_of_incident_id": "TEXT",
                "predicted_confidence": "REAL",
                "class_probabilities_json": "TEXT",
                "top_classes_json": "TEXT",
                "is_anomaly": "INTEGER",
                "model_explanation_json": "TEXT",
            },
        )
        _ensure_columns(
            connection,
            "incident_remediation",
            {
                "metadata_json": "TEXT",
                "playbook_yaml": "TEXT",
            },
        )
        _ensure_columns(
            connection,
            "incident_rca",
            {
                "request_id": "TEXT",
                "trace_id": "TEXT",
                "lifecycle_state": "TEXT",
                "guardrail_status": "TEXT",
                "guardrail_reason": "TEXT",
                "is_active": "INTEGER DEFAULT 1",
                "superseded_at": "TEXT",
            },
        )
        connection.execute("UPDATE incident_rca SET is_active = COALESCE(is_active, 1)")
        connection.execute(
            """
            UPDATE incidents
            SET workflow_state = COALESCE(workflow_state, status),
                workflow_revision = COALESCE(workflow_revision, 1),
                severity = COALESCE(severity, 'Medium'),
                source_system = COALESCE(source_system, 'anomaly-service'),
                is_anomaly = COALESCE(is_anomaly, CASE WHEN anomaly_type = 'normal_operation' THEN 0 ELSE 1 END)
            """
        )
        connection.commit()


def get_app_setting_record(key: str) -> Dict[str, Any] | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT key, value_json, updated_at FROM app_settings WHERE key = ?",
            (key,),
        ).fetchone()
    if not row:
        return None
    return {
        "key": str(row["key"]),
        "value": _json_loads(row["value_json"], None),
        "updated_at": str(row["updated_at"]),
    }


def get_app_setting(key: str, default: Any = None) -> Any:
    record = get_app_setting_record(key)
    if record is None:
        return default
    return record.get("value", default)


def set_app_setting(key: str, value: Any) -> Dict[str, Any]:
    record = {
        "key": key,
        "value": value,
        "updated_at": _now(),
    }
    with closing(_connect()) as connection:
        connection.execute(
            """
            INSERT INTO app_settings (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              value_json = excluded.value_json,
              updated_at = excluded.updated_at
            """,
            (
                record["key"],
                _json_dumps(record["value"]),
                record["updated_at"],
            ),
        )
        connection.commit()
    return record


def _incident_row(connection: sqlite3.Connection, incident_id: str) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()


def _rca_row(connection: sqlite3.Connection, incident_id: str, rca_id: int) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM incident_rca WHERE incident_id = ? AND id = ?",
        (incident_id, rca_id),
    ).fetchone()


def create_incident(payload: Dict[str, Any]) -> Dict[str, Any]:
    workflow_state = normalize_workflow_state(str(payload.get("status") or payload.get("workflow_state") or NEW))
    predicted_confidence = float(payload.get("predicted_confidence") or 0.0)
    anomaly_type = str(payload.get("anomaly_type") or NORMAL_ANOMALY_TYPE)
    record = {
        "id": payload["incident_id"],
        "project": payload.get("project", "ani-demo"),
        "status": workflow_state,
        "severity": payload.get("severity") or severity_from_prediction(anomaly_type, predicted_confidence)
        or severity_from_score(float(payload["anomaly_score"])),
        "source_system": payload.get("source_system", "anomaly-service"),
        "anomaly_score": payload["anomaly_score"],
        "anomaly_type": anomaly_type,
        "predicted_confidence": predicted_confidence,
        "class_probabilities_json": _json_dumps(payload.get("class_probabilities", {})),
        "top_classes_json": _json_dumps(payload.get("top_classes", [])),
        "is_anomaly": bool(payload.get("is_anomaly", anomaly_type != "normal_operation")),
        "model_version": payload["model_version"],
        "feature_window_id": payload.get("feature_window_id"),
        "feature_snapshot": _json_dumps(payload.get("feature_snapshot", {})),
        "model_explanation_json": _json_dumps(payload["model_explanation"]) if payload.get("model_explanation") else None,
        "rca_payload": None,
        "recommendation": payload.get("recommendation"),
        "created_at": payload.get("created_at") or _now(),
        "updated_at": _now(),
        "workflow_revision": int(payload.get("workflow_revision") or 1),
        "duplicate_of_incident_id": payload.get("duplicate_of_incident_id"),
    }
    with closing(_connect()) as connection:
        connection.execute(
            """
            INSERT INTO incidents (
              id, project, status, severity, source_system, anomaly_score, anomaly_type, model_version,
              predicted_confidence, class_probabilities_json, top_classes_json, is_anomaly,
              feature_window_id, feature_snapshot, model_explanation_json, rca_payload, recommendation, created_at, updated_at,
              workflow_state, workflow_revision, duplicate_of_incident_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              project = excluded.project,
              status = excluded.status,
              severity = excluded.severity,
              source_system = excluded.source_system,
              anomaly_score = excluded.anomaly_score,
              anomaly_type = excluded.anomaly_type,
              predicted_confidence = excluded.predicted_confidence,
              class_probabilities_json = excluded.class_probabilities_json,
              top_classes_json = excluded.top_classes_json,
              is_anomaly = excluded.is_anomaly,
              model_version = excluded.model_version,
              feature_window_id = excluded.feature_window_id,
              feature_snapshot = excluded.feature_snapshot,
              model_explanation_json = COALESCE(excluded.model_explanation_json, incidents.model_explanation_json),
              recommendation = COALESCE(incidents.recommendation, excluded.recommendation),
              updated_at = excluded.updated_at,
              workflow_state = excluded.workflow_state,
              workflow_revision = COALESCE(incidents.workflow_revision, excluded.workflow_revision),
              duplicate_of_incident_id = COALESCE(excluded.duplicate_of_incident_id, incidents.duplicate_of_incident_id)
            """,
            (
                record["id"],
                record["project"],
                record["status"],
                record["severity"],
                record["source_system"],
                record["anomaly_score"],
                record["anomaly_type"],
                record["model_version"],
                record["predicted_confidence"],
                record["class_probabilities_json"],
                record["top_classes_json"],
                int(record["is_anomaly"]),
                record["feature_window_id"],
                record["feature_snapshot"],
                record["model_explanation_json"],
                record["rca_payload"],
                record["recommendation"],
                record["created_at"],
                record["updated_at"],
                record["status"],
                record["workflow_revision"],
                record["duplicate_of_incident_id"],
            ),
        )
        connection.commit()
    return get_incident(record["id"])


def get_incident(incident_id: str) -> Dict[str, Any] | None:
    with closing(_connect()) as connection:
        row = _incident_row(connection, incident_id)
    return _deserialize_incident(row) if row else None


def list_incidents(project: str | None = None, limit: int | None = None) -> List[Dict[str, Any]]:
    with closing(_connect()) as connection:
        params: list[object] = []
        if project:
            query = "SELECT * FROM incidents WHERE project = ? ORDER BY created_at DESC"
            params.append(project)
        else:
            query = "SELECT * FROM incidents ORDER BY created_at DESC"
        if limit is not None:
            query = f"{query} LIMIT ?"
            params.append(max(0, int(limit)))
        rows = connection.execute(query, tuple(params)).fetchall()
    return [_deserialize_incident(row) for row in rows]


def attach_rca(incident_id: str, rca_payload: Dict[str, Any]) -> Dict[str, Any] | None:
    with closing(_connect()) as connection:
        incident = _incident_row(connection, incident_id)
        if not incident:
            return None
        request_id = str(rca_payload.get("rca_request_id") or "").strip()
        if request_id:
            existing = connection.execute(
                "SELECT id FROM incident_rca WHERE incident_id = ? AND request_id = ?",
                (incident_id, request_id),
            ).fetchone()
            if existing:
                return get_incident(incident_id)
        current_revision = int(incident["workflow_revision"] or 1)
        try:
            source_revision = int(rca_payload.get("source_workflow_revision") or current_revision)
        except (TypeError, ValueError):
            source_revision = current_revision
        if source_revision < current_revision:
            return get_incident(incident_id)
        next_revision = current_revision + 1
        version = int(
            connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM incident_rca WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()[0]
        )
        guardrails = rca_payload.get("guardrails") if isinstance(rca_payload.get("guardrails"), dict) else {}
        lifecycle_state = str(rca_payload.get("rca_state") or "").strip()
        current_guardrail_status = guardrail_status(rca_payload)
        guardrail_reason = str(guardrails.get("reason") or "").strip()
        trace_id = str(rca_payload.get("trace_id") or "").strip()
        retrieval_refs = [
            str(item.get("reference") or item.get("title") or "")
            for item in rca_payload.get("retrieved_documents", [])
            if isinstance(item, dict)
        ]
        connection.execute(
            """
            UPDATE incident_rca
            SET is_active = 0,
                superseded_at = ?
            WHERE incident_id = ? AND COALESCE(is_active, 1) = 1
            """,
            (_now(), incident_id),
        )
        cursor = connection.execute(
            """
            INSERT INTO incident_rca (
              incident_id, version, based_on_revision, root_cause, category, confidence, explanation,
              model_name, prompt_version, retrieval_refs, payload, created_at,
              request_id, trace_id, lifecycle_state, guardrail_status, guardrail_reason, is_active, superseded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                version,
                next_revision,
                str(rca_payload.get("root_cause") or ""),
                str(incident["anomaly_type"]),
                float(rca_payload.get("confidence") or 0.0),
                str(rca_payload.get("explanation") or rca_payload.get("root_cause") or ""),
                str(rca_payload.get("llm_model") or incident["model_version"]),
                str(rca_payload.get("generation_mode") or "local-rag"),
                _json_dumps(retrieval_refs),
                _json_dumps(rca_payload),
                _now(),
                request_id,
                trace_id,
                lifecycle_state,
                current_guardrail_status,
                guardrail_reason,
                1,
                None,
            ),
        )
        rca_id = int(cursor.lastrowid)
        connection.execute(
            """
            UPDATE incidents
            SET rca_payload = ?, recommendation = ?, current_rca_id = ?, workflow_revision = ?,
                status = ?, workflow_state = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                _json_dumps(rca_payload),
                rca_payload.get("recommendation"),
                rca_id,
                next_revision,
                RCA_GENERATED,
                RCA_GENERATED,
                _now(),
                incident_id,
            ),
        )
        connection.commit()
    return get_incident(incident_id)


def list_incident_rca(incident_id: str) -> List[Dict[str, Any]]:
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM incident_rca WHERE incident_id = ? ORDER BY COALESCE(is_active, 1) DESC, created_at DESC, id DESC",
            (incident_id,),
        ).fetchall()
    return [_deserialize_rca(row) for row in rows]


def get_incident_rca(incident_id: str, rca_id: int) -> Dict[str, Any] | None:
    with closing(_connect()) as connection:
        row = _rca_row(connection, incident_id, rca_id)
    return _deserialize_rca(row) if row else None


def replace_remediations(incident_id: str, rca_id: int | None, suggestions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    with closing(_connect()) as connection:
        incident = _incident_row(connection, incident_id)
        if not incident:
            return []
        current_revision = int(incident["workflow_revision"] or 1)
        next_revision = current_revision + 1
        connection.execute(
            """
            UPDATE incident_remediation
            SET status = 'superseded'
            WHERE incident_id = ? AND status IN ('available', 'approved', 'executing', 'executed')
            """,
            (incident_id,),
        )
        for suggestion in suggestions:
            connection.execute(
                """
                INSERT INTO incident_remediation (
                  incident_id, rca_id, based_on_revision, suggestion_rank, title, suggestion_type, description,
                  risk_level, confidence, automation_level, requires_approval, playbook_ref, action_ref,
                  preconditions_json, expected_outcome, rank_score, factors_json, metadata_json, playbook_yaml, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    incident_id,
                    rca_id,
                    next_revision,
                    int(suggestion.get("suggestion_rank") or 1),
                    str(suggestion.get("title") or "Untitled remediation"),
                    str(suggestion.get("suggestion_type") or "manual"),
                    str(suggestion.get("description") or ""),
                    str(suggestion.get("risk_level") or "medium"),
                    float(suggestion.get("confidence") or 0.0),
                    str(suggestion.get("automation_level") or "manual"),
                    int(bool(suggestion.get("requires_approval", True))),
                    str(suggestion.get("playbook_ref") or ""),
                    str(suggestion.get("action_ref") or ""),
                    _json_dumps(suggestion.get("preconditions") or []),
                    str(suggestion.get("expected_outcome") or ""),
                    float(suggestion.get("rank_score") or 0.0),
                    _json_dumps(
                        {
                            "historical_success_rate": suggestion.get("historical_success_rate"),
                            "retrieval_similarity": suggestion.get("retrieval_similarity"),
                            "rca_confidence": suggestion.get("rca_confidence"),
                            "policy_bonus": suggestion.get("policy_bonus"),
                            "risk_penalty": suggestion.get("risk_penalty"),
                            "execution_cost_penalty": suggestion.get("execution_cost_penalty"),
                        }
                    ),
                    _json_dumps(suggestion.get("metadata") or {}),
                    str(suggestion.get("playbook_yaml") or ""),
                    "available",
                    _now(),
                ),
            )
        recommendation = str(suggestions[0].get("description") or suggestions[0].get("title")) if suggestions else None
        connection.execute(
            """
            UPDATE incidents
            SET recommendation = ?, workflow_revision = ?, status = ?, workflow_state = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                recommendation,
                next_revision,
                REMEDIATION_SUGGESTED,
                REMEDIATION_SUGGESTED,
                _now(),
                incident_id,
            ),
        )
        connection.commit()
    return list_incident_remediations(incident_id)


def list_incident_remediations(incident_id: str) -> List[Dict[str, Any]]:
    with closing(_connect()) as connection:
        rows = connection.execute(
            """
            SELECT * FROM incident_remediation
            WHERE incident_id = ?
            ORDER BY based_on_revision DESC, suggestion_rank ASC, id DESC
            """,
            (incident_id,),
        ).fetchall()
    return [_deserialize_remediation(row) for row in rows]


def list_incident_remediations_for_incidents(incident_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    normalized_ids = [str(item).strip() for item in incident_ids if str(item).strip()]
    if not normalized_ids:
        return {}
    placeholders = ", ".join("?" for _ in normalized_ids)
    query = f"""
        SELECT * FROM incident_remediation
        WHERE incident_id IN ({placeholders})
        ORDER BY incident_id ASC, based_on_revision DESC, suggestion_rank ASC, id DESC
    """
    with closing(_connect()) as connection:
        rows = connection.execute(query, tuple(normalized_ids)).fetchall()
    grouped: Dict[str, List[Dict[str, Any]]] = {incident_id: [] for incident_id in normalized_ids}
    for row in rows:
        incident_id = str(row["incident_id"] or "")
        grouped.setdefault(incident_id, []).append(_deserialize_remediation(row))
    return grouped


def get_incident_remediation(incident_id: str, remediation_id: int) -> Dict[str, Any] | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM incident_remediation WHERE incident_id = ? AND id = ?",
            (incident_id, remediation_id),
        ).fetchone()
    return _deserialize_remediation(row) if row else None


def set_incident_remediation_status(incident_id: str, remediation_id: int, status: str) -> Dict[str, Any] | None:
    with closing(_connect()) as connection:
        connection.execute(
            "UPDATE incident_remediation SET status = ? WHERE incident_id = ? AND id = ?",
            (status, incident_id, remediation_id),
        )
        connection.commit()
    return get_incident_remediation(incident_id, remediation_id)


def update_incident_remediation(
    incident_id: str,
    remediation_id: int,
    *,
    based_on_revision: int | None = None,
    title: str | None = None,
    suggestion_type: str | None = None,
    description: str | None = None,
    risk_level: str | None = None,
    confidence: float | None = None,
    automation_level: str | None = None,
    requires_approval: bool | None = None,
    playbook_ref: str | None = None,
    action_ref: str | None = None,
    preconditions: List[str] | None = None,
    expected_outcome: str | None = None,
    rank_score: float | None = None,
    status: str | None = None,
    metadata: Dict[str, Any] | None = None,
    playbook_yaml: str | None = None,
) -> Dict[str, Any] | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM incident_remediation WHERE incident_id = ? AND id = ?",
            (incident_id, remediation_id),
        ).fetchone()
        if not row:
            return None
        current = _deserialize_remediation(row)
        connection.execute(
            """
            UPDATE incident_remediation
            SET based_on_revision = ?, title = ?, suggestion_type = ?, description = ?, risk_level = ?, confidence = ?,
                automation_level = ?, requires_approval = ?, playbook_ref = ?, action_ref = ?, preconditions_json = ?,
                expected_outcome = ?, rank_score = ?, status = ?, metadata_json = ?, playbook_yaml = ?
            WHERE incident_id = ? AND id = ?
            """,
            (
                int(based_on_revision if based_on_revision is not None else current.get("based_on_revision") or 1),
                str(title if title is not None else current.get("title") or "Untitled remediation"),
                str(suggestion_type if suggestion_type is not None else current.get("suggestion_type") or "manual"),
                str(description if description is not None else current.get("description") or ""),
                str(risk_level if risk_level is not None else current.get("risk_level") or "medium"),
                float(confidence if confidence is not None else current.get("confidence") or 0.0),
                str(automation_level if automation_level is not None else current.get("automation_level") or "manual"),
                int(requires_approval if requires_approval is not None else bool(current.get("requires_approval"))),
                str(playbook_ref if playbook_ref is not None else current.get("playbook_ref") or ""),
                str(action_ref if action_ref is not None else current.get("action_ref") or ""),
                _json_dumps(preconditions if preconditions is not None else current.get("preconditions") or []),
                str(expected_outcome if expected_outcome is not None else current.get("expected_outcome") or ""),
                float(rank_score if rank_score is not None else current.get("rank_score") or 0.0),
                str(status if status is not None else current.get("status") or "available"),
                _json_dumps(metadata if metadata is not None else current.get("metadata") or {}),
                str(playbook_yaml if playbook_yaml is not None else current.get("playbook_yaml") or ""),
                incident_id,
                remediation_id,
            ),
        )
        connection.commit()
    return get_incident_remediation(incident_id, remediation_id)


def remediation_success_rates() -> Dict[str, float]:
    with closing(_connect()) as connection:
        rows = connection.execute(
            """
            SELECT
              COALESCE(r.action_ref, '') AS action_ref,
              SUM(CASE WHEN v.verification_status = 'verified' THEN 1 ELSE 0 END) AS success_count,
              SUM(CASE WHEN v.id IS NOT NULL THEN 1 ELSE 0 END) AS total_count
            FROM incident_remediation r
            LEFT JOIN incident_actions a ON a.remediation_id = r.id
            LEFT JOIN incident_verification v ON v.action_id = a.id
            GROUP BY COALESCE(r.action_ref, '')
            """
        ).fetchall()
    results: Dict[str, float] = {}
    for row in rows:
        action_ref = str(row["action_ref"] or "").strip()
        if not action_ref:
            continue
        total = int(row["total_count"] or 0)
        success = int(row["success_count"] or 0)
        if total > 0:
            results[action_ref] = round(success / total, 4)
    return results


def transition_incident_state(incident_id: str, state: str) -> Dict[str, Any] | None:
    normalized = normalize_workflow_state(state)
    with closing(_connect()) as connection:
        connection.execute(
            """
            UPDATE incidents
            SET status = ?, workflow_state = ?, updated_at = ?
            WHERE id = ?
            """,
            (normalized, normalized, _now(), incident_id),
        )
        connection.commit()
    return get_incident(incident_id)


def update_incident_status(incident_id: str, status: str) -> Dict[str, Any] | None:
    return transition_incident_state(incident_id, status)


def record_incident_action(
    incident_id: str,
    remediation_id: int | None,
    action_mode: str,
    source_of_action: str,
    approved_revision: int,
    triggered_by: str,
    execution_status: str,
    notes: str = "",
    started_at: str | None = None,
    finished_at: str | None = None,
    result_summary: str = "",
    result_json: Dict[str, object] | None = None,
) -> Dict[str, Any]:
    with closing(_connect()) as connection:
        cursor = connection.execute(
            """
            INSERT INTO incident_actions (
              incident_id, remediation_id, action_mode, source_of_action, approved_revision, triggered_by,
              execution_status, notes, started_at, finished_at, result_summary, result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                remediation_id,
                action_mode,
                source_of_action,
                approved_revision,
                triggered_by,
                execution_status,
                notes,
                started_at,
                finished_at,
                result_summary,
                _json_dumps(result_json or {}),
            ),
        )
        action_id = int(cursor.lastrowid)
        if remediation_id is not None:
            connection.execute(
                "UPDATE incident_remediation SET status = ? WHERE id = ? AND incident_id = ?",
                (execution_status, remediation_id, incident_id),
            )
        connection.commit()
    return get_incident_action(incident_id, action_id) or {}


def update_incident_action(
    incident_id: str,
    action_id: int,
    execution_status: str,
    finished_at: str | None = None,
    result_summary: str | None = None,
    result_json: Dict[str, object] | None = None,
) -> Dict[str, Any] | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT remediation_id, result_json FROM incident_actions WHERE incident_id = ? AND id = ?",
            (incident_id, action_id),
        ).fetchone()
        if not row:
            return None
        merged_result_json = _json_loads(row["result_json"], {}) | (result_json or {})
        connection.execute(
            """
            UPDATE incident_actions
            SET execution_status = ?,
                finished_at = COALESCE(?, finished_at),
                result_summary = COALESCE(?, result_summary),
                result_json = ?
            WHERE incident_id = ? AND id = ?
            """,
            (
                execution_status,
                finished_at,
                result_summary,
                _json_dumps(merged_result_json),
                incident_id,
                action_id,
            ),
        )
        remediation_id = row["remediation_id"]
        if remediation_id is not None:
            connection.execute(
                "UPDATE incident_remediation SET status = ? WHERE incident_id = ? AND id = ?",
                (execution_status, incident_id, remediation_id),
            )
        connection.commit()
    return get_incident_action(incident_id, action_id)


def get_incident_action(incident_id: str, action_id: int) -> Dict[str, Any] | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM incident_actions WHERE incident_id = ? AND id = ?",
            (incident_id, action_id),
        ).fetchone()
    return _deserialize_action(row) if row else None


def list_incident_actions(incident_id: str) -> List[Dict[str, Any]]:
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM incident_actions WHERE incident_id = ? ORDER BY id DESC",
            (incident_id,),
        ).fetchall()
    return [_deserialize_action(row) for row in rows]


def record_verification(
    incident_id: str,
    action_id: int | None,
    verified_by: str,
    verification_status: str,
    notes: str = "",
    custom_resolution: str = "",
    metric_based: bool = False,
) -> Dict[str, Any]:
    with closing(_connect()) as connection:
        cursor = connection.execute(
            """
            INSERT INTO incident_verification (
              incident_id, action_id, verified_by, verification_status, notes, custom_resolution, metric_based, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                action_id,
                verified_by,
                verification_status,
                notes,
                custom_resolution,
                int(metric_based),
                _now(),
            ),
        )
        verification_id = int(cursor.lastrowid)
        connection.commit()
    return get_incident_verification(incident_id, verification_id) or {}


def get_incident_verification(incident_id: str, verification_id: int) -> Dict[str, Any] | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM incident_verification WHERE incident_id = ? AND id = ?",
            (incident_id, verification_id),
        ).fetchone()
    return _deserialize_verification(row) if row else None


def list_incident_verifications(incident_id: str) -> List[Dict[str, Any]]:
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM incident_verification WHERE incident_id = ? ORDER BY id DESC",
            (incident_id,),
        ).fetchall()
    return [_deserialize_verification(row) for row in rows]


def upsert_incident_ticket(
    incident_id: str,
    provider: str,
    external_key: str = "",
    external_id: str = "",
    workspace_id: str = "",
    project_id: str = "",
    status: str = "",
    url: str = "",
    title: str = "",
    sync_state: str = "",
    last_synced_revision: int | None = None,
    metadata: Dict[str, object] | None = None,
) -> Dict[str, Any]:
    now = _now()
    with closing(_connect()) as connection:
        connection.execute(
            """
            INSERT INTO incident_tickets (
              incident_id, provider, external_key, external_id, workspace_id, project_id, status, url, title,
              last_synced_at, sync_state, last_synced_revision, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(incident_id, provider) DO UPDATE SET
              external_key = excluded.external_key,
              external_id = excluded.external_id,
              workspace_id = excluded.workspace_id,
              project_id = excluded.project_id,
              status = excluded.status,
              url = excluded.url,
              title = excluded.title,
              last_synced_at = excluded.last_synced_at,
              sync_state = excluded.sync_state,
              last_synced_revision = excluded.last_synced_revision,
              metadata_json = excluded.metadata_json,
              updated_at = excluded.updated_at
            """,
            (
                incident_id,
                provider,
                external_key,
                external_id,
                workspace_id,
                project_id,
                status,
                url,
                title,
                now,
                sync_state,
                last_synced_revision,
                _json_dumps(metadata or {}),
                now,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM incident_tickets WHERE incident_id = ? AND provider = ?",
            (incident_id, provider),
        ).fetchone()
        if row:
            connection.execute(
                "UPDATE incidents SET current_ticket_id = ?, updated_at = ? WHERE id = ?",
                (int(row["id"]), now, incident_id),
            )
        connection.commit()
    return get_incident_ticket(incident_id, int(row["id"])) if row else {}


def get_incident_ticket(incident_id: str, ticket_id: int) -> Dict[str, Any] | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM incident_tickets WHERE incident_id = ? AND id = ?",
            (incident_id, ticket_id),
        ).fetchone()
    return _deserialize_ticket(row) if row else None


def get_ticket_by_provider_external_id(provider: str, external_id: str) -> Dict[str, Any] | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM incident_tickets WHERE provider = ? AND external_id = ?",
            (provider, external_id),
        ).fetchone()
    return _deserialize_ticket(row) if row else None


def list_incident_tickets(incident_id: str) -> List[Dict[str, Any]]:
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM incident_tickets WHERE incident_id = ? ORDER BY updated_at DESC, id DESC",
            (incident_id,),
        ).fetchall()
    return [_deserialize_ticket(row) for row in rows]


def record_ticket_sync_event(
    ticket_id: int,
    direction: str,
    event_type: str,
    delivery_id: str | None,
    payload_hash: str,
    status: str,
    payload: Dict[str, object] | None = None,
) -> Dict[str, Any] | None:
    with closing(_connect()) as connection:
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO ticket_sync_events (
              ticket_id, direction, event_type, delivery_id, payload_hash, status, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket_id,
                direction,
                event_type,
                delivery_id,
                payload_hash,
                status,
                _json_dumps(payload or {}),
                _now(),
            ),
        )
        if cursor.rowcount == 0:
            return None
        event_id = int(cursor.lastrowid)
        connection.commit()
        row = connection.execute(
            "SELECT * FROM ticket_sync_events WHERE id = ?",
            (event_id,),
        ).fetchone()
    return _deserialize_ticket_sync(row) if row else None


def list_ticket_sync_events(ticket_id: int) -> List[Dict[str, Any]]:
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM ticket_sync_events WHERE ticket_id = ? ORDER BY id DESC",
            (ticket_id,),
        ).fetchall()
    return [_deserialize_ticket_sync(row) for row in rows]


def upsert_ticket_comment(
    ticket_id: int,
    external_comment_id: str,
    author: str,
    body: str,
    comment_type: str,
    created_at: str | None = None,
) -> Dict[str, Any]:
    now = _now()
    with closing(_connect()) as connection:
        connection.execute(
            """
            INSERT INTO ticket_comments_index (
              ticket_id, external_comment_id, author, body, comment_type, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(external_comment_id) DO UPDATE SET
              author = excluded.author,
              body = excluded.body,
              comment_type = excluded.comment_type,
              updated_at = excluded.updated_at
            """,
            (
                ticket_id,
                external_comment_id,
                author,
                body,
                comment_type,
                created_at or now,
                now,
            ),
        )
        connection.commit()
        row = connection.execute(
            "SELECT * FROM ticket_comments_index WHERE external_comment_id = ?",
            (external_comment_id,),
        ).fetchone()
    return _deserialize_ticket_comment(row) if row else {}


def list_ticket_comments(ticket_id: int) -> List[Dict[str, Any]]:
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM ticket_comments_index WHERE ticket_id = ? ORDER BY updated_at DESC, id DESC",
            (ticket_id,),
        ).fetchall()
    return [_deserialize_ticket_comment(row) for row in rows]


def create_ticket_resolution_extract(
    incident_id: str,
    ticket_id: int | None,
    source_comment_id: str | None,
    summary: str,
    verified: bool,
    verification_quality: str,
    knowledge_weight: float,
    success_rate: float,
    last_validated_at: str | None = None,
) -> Dict[str, Any]:
    with closing(_connect()) as connection:
        cursor = connection.execute(
            """
            INSERT INTO ticket_resolution_extracts (
              incident_id, ticket_id, source_comment_id, summary, verified, verification_quality,
              knowledge_weight, usage_count, success_rate, last_validated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                ticket_id,
                source_comment_id,
                summary,
                int(verified),
                verification_quality,
                knowledge_weight,
                0,
                success_rate,
                last_validated_at,
                _now(),
            ),
        )
        extract_id = int(cursor.lastrowid)
        connection.commit()
        row = connection.execute(
            "SELECT * FROM ticket_resolution_extracts WHERE id = ?",
            (extract_id,),
        ).fetchone()
    return _deserialize_resolution_extract(row) if row else {}


def list_ticket_resolution_extracts(incident_id: str) -> List[Dict[str, Any]]:
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM ticket_resolution_extracts WHERE incident_id = ? ORDER BY id DESC",
            (incident_id,),
        ).fetchall()
    return [_deserialize_resolution_extract(row) for row in rows]


def record_approval(
    incident_id: str,
    action: str,
    approved_by: str,
    execute: bool,
    status: str,
    output: str,
) -> Dict[str, Any]:
    record = {
        "incident_id": incident_id,
        "action": action,
        "approved_by": approved_by,
        "execute": int(execute),
        "status": status,
        "output": output,
        "created_at": _now(),
    }
    with closing(_connect()) as connection:
        cursor = connection.execute(
            """
            INSERT INTO approvals (incident_id, action, approved_by, execute, status, output, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["incident_id"],
                record["action"],
                record["approved_by"],
                record["execute"],
                record["status"],
                record["output"],
                record["created_at"],
            ),
        )
        connection.commit()
        approval_id = int(cursor.lastrowid)
    record["id"] = approval_id
    record["execute"] = bool(record["execute"])
    return record


def update_approval(
    approval_id: int,
    status: str,
    output: str | None = None,
) -> Dict[str, Any] | None:
    with closing(_connect()) as connection:
        row = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if not row:
            return None
        connection.execute(
            "UPDATE approvals SET status = ?, output = COALESCE(?, output) WHERE id = ?",
            (status, output, approval_id),
        )
        connection.commit()
        updated = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    if not updated:
        return None
    record = dict(updated)
    record["execute"] = bool(record["execute"])
    return record


def record_audit(event_type: str, actor: str, payload: Dict[str, Any], incident_id: str | None = None) -> None:
    with closing(_connect()) as connection:
        connection.execute(
            """
            INSERT INTO audit_events (event_type, actor, incident_id, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_type,
                actor,
                incident_id,
                _json_dumps(payload),
                _now(),
            ),
        )
        connection.commit()


def list_audit_events(
    limit: int = 100,
    incident_id: str | None = None,
    event_type: str | None = None,
) -> List[Dict[str, Any]]:
    with closing(_connect()) as connection:
        if incident_id and event_type:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE incident_id = ? AND event_type = ? ORDER BY created_at DESC LIMIT ?",
                (incident_id, event_type, limit),
            ).fetchall()
        elif incident_id:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE incident_id = ? ORDER BY created_at DESC LIMIT ?",
                (incident_id, limit),
            ).fetchall()
        elif event_type:
            rows = connection.execute(
                "SELECT * FROM audit_events WHERE event_type = ? ORDER BY created_at DESC LIMIT ?",
                (event_type, limit),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(row) | {"payload": _json_loads(row["payload"], {})} for row in rows]


def list_approvals(limit: int = 100, incident_id: str | None = None) -> List[Dict[str, Any]]:
    with closing(_connect()) as connection:
        if incident_id:
            rows = connection.execute(
                "SELECT * FROM approvals WHERE incident_id = ? ORDER BY created_at DESC LIMIT ?",
                (incident_id, limit),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM approvals ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    results = []
    for row in rows:
        record = dict(row)
        record["execute"] = bool(record["execute"])
        results.append(record)
    return results


def _deserialize_incident(row: sqlite3.Row) -> Dict[str, Any]:
    record = dict(row)
    state = normalize_workflow_state(str(record.get("workflow_state") or record.get("status") or NEW))
    record["status"] = state
    record["workflow_state"] = state
    record["workflow_revision"] = int(record.get("workflow_revision") or 1)
    record["feature_snapshot"] = _json_loads(record.get("feature_snapshot"), {})
    record["model_explanation"] = _json_loads(record.get("model_explanation_json"), None)
    record["rca_payload"] = _json_loads(record.get("rca_payload"), None)
    record["class_probabilities"] = _json_loads(record.get("class_probabilities_json"), {})
    record["top_classes"] = _json_loads(record.get("top_classes_json"), [])
    record["current_rca_id"] = int(record["current_rca_id"]) if record.get("current_rca_id") else None
    record["current_ticket_id"] = int(record["current_ticket_id"]) if record.get("current_ticket_id") else None
    record["predicted_confidence"] = float(record.get("predicted_confidence") or 0.0)
    record["is_anomaly"] = bool(
        record.get("is_anomaly")
        if record.get("is_anomaly") is not None
        else str(record.get("anomaly_type") or NORMAL_ANOMALY_TYPE) != NORMAL_ANOMALY_TYPE
    )
    record["severity"] = str(
        record.get("severity")
        or severity_from_prediction(str(record.get("anomaly_type") or ""), float(record.get("predicted_confidence") or 0.0))
        or severity_from_score(float(record.get("anomaly_score") or 0.0))
    )
    record["source_system"] = str(record.get("source_system") or "anomaly-service")
    return record


def _deserialize_rca(row: sqlite3.Row) -> Dict[str, Any]:
    record = dict(row)
    record["based_on_revision"] = int(record.get("based_on_revision") or 1)
    record["confidence"] = float(record.get("confidence") or 0.0)
    record["retrieval_refs"] = _json_loads(record.get("retrieval_refs"), [])
    record["payload"] = _json_loads(record.get("payload"), {})
    record["is_active"] = bool(record.get("is_active"))
    return record


def _deserialize_remediation(row: sqlite3.Row) -> Dict[str, Any]:
    record = dict(row)
    record["based_on_revision"] = int(record.get("based_on_revision") or 1)
    record["suggestion_rank"] = int(record.get("suggestion_rank") or 1)
    record["confidence"] = float(record.get("confidence") or 0.0)
    record["requires_approval"] = bool(record.get("requires_approval"))
    record["rank_score"] = float(record.get("rank_score") or 0.0)
    record["preconditions"] = _json_loads(record.get("preconditions_json"), [])
    record["factors"] = _json_loads(record.get("factors_json"), {})
    metadata = _json_loads(record.get("metadata_json"), {})
    record["metadata"] = metadata if isinstance(metadata, dict) else {}
    record["ai_generated"] = bool(record["metadata"].get("ai_generated"))
    record["generation_kind"] = str(record["metadata"].get("generation_kind") or "")
    record["generation_provider"] = str(record["metadata"].get("generation_provider") or "")
    record["generation_status"] = str(record["metadata"].get("generation_status") or "")
    record["generation_error"] = str(record["metadata"].get("generation_error") or "")
    return record


def _deserialize_action(row: sqlite3.Row) -> Dict[str, Any]:
    record = dict(row)
    record["approved_revision"] = int(record.get("approved_revision") or 1)
    record["result_json"] = _json_loads(record.get("result_json"), {})
    return record


def _deserialize_verification(row: sqlite3.Row) -> Dict[str, Any]:
    record = dict(row)
    record["metric_based"] = bool(record.get("metric_based"))
    return record


def _deserialize_ticket(row: sqlite3.Row) -> Dict[str, Any]:
    record = dict(row)
    record["last_synced_revision"] = int(record["last_synced_revision"]) if record.get("last_synced_revision") is not None else None
    record["metadata"] = _json_loads(record.get("metadata_json"), {})
    return record


def _deserialize_ticket_sync(row: sqlite3.Row) -> Dict[str, Any]:
    record = dict(row)
    record["payload"] = _json_loads(record.get("payload"), {})
    return record


def _deserialize_ticket_comment(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


def _deserialize_resolution_extract(row: sqlite3.Row) -> Dict[str, Any]:
    record = dict(row)
    record["verified"] = bool(record.get("verified"))
    record["knowledge_weight"] = float(record.get("knowledge_weight") or 0.0)
    record["usage_count"] = int(record.get("usage_count") or 0)
    record["success_rate"] = float(record.get("success_rate") or 0.0)
    return record
