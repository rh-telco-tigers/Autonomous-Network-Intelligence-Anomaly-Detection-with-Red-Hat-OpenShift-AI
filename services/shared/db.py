import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def _db_path() -> Path:
    return Path(os.getenv("CONTROL_PLANE_DB_PATH", "/tmp/ims-demo-control-plane.db"))


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


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
            """
        )
        connection.commit()


def create_incident(payload: Dict[str, Any]) -> Dict[str, Any]:
    record = {
        "id": payload["incident_id"],
        "project": payload.get("project", "ims-demo"),
        "status": payload.get("status", "open"),
        "anomaly_score": payload["anomaly_score"],
        "anomaly_type": payload["anomaly_type"],
        "model_version": payload["model_version"],
        "feature_window_id": payload.get("feature_window_id"),
        "feature_snapshot": json.dumps(payload.get("feature_snapshot", {})),
        "rca_payload": None,
        "recommendation": None,
        "created_at": payload.get("created_at", _now()),
        "updated_at": _now(),
    }

    with closing(_connect()) as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO incidents (
              id, project, status, anomaly_score, anomaly_type, model_version,
              feature_window_id, feature_snapshot, rca_payload, recommendation,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"],
                record["project"],
                record["status"],
                record["anomaly_score"],
                record["anomaly_type"],
                record["model_version"],
                record["feature_window_id"],
                record["feature_snapshot"],
                record["rca_payload"],
                record["recommendation"],
                record["created_at"],
                record["updated_at"],
            ),
        )
        connection.commit()
    return get_incident(record["id"])


def get_incident(incident_id: str) -> Dict[str, Any] | None:
    with closing(_connect()) as connection:
        row = connection.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    return _deserialize_incident(row) if row else None


def list_incidents(project: str | None = None) -> List[Dict[str, Any]]:
    with closing(_connect()) as connection:
        if project:
            rows = connection.execute(
                "SELECT * FROM incidents WHERE project = ? ORDER BY created_at DESC",
                (project,),
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM incidents ORDER BY created_at DESC").fetchall()
    return [_deserialize_incident(row) for row in rows]


def attach_rca(incident_id: str, rca_payload: Dict[str, Any]) -> Dict[str, Any] | None:
    with closing(_connect()) as connection:
        connection.execute(
            """
            UPDATE incidents
            SET rca_payload = ?, recommendation = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(rca_payload),
                rca_payload.get("recommendation"),
                _now(),
                incident_id,
            ),
        )
        connection.commit()
    return get_incident(incident_id)


def update_incident_status(incident_id: str, status: str) -> Dict[str, Any] | None:
    with closing(_connect()) as connection:
        connection.execute(
            """
            UPDATE incidents
            SET status = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                _now(),
                incident_id,
            ),
        )
        connection.commit()
    return get_incident(incident_id)


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
        approval_id = cursor.lastrowid
    record["id"] = approval_id
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
                json.dumps(payload),
                _now(),
            ),
        )
        connection.commit()


def list_audit_events(limit: int = 100) -> List[Dict[str, Any]]:
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) | {"payload": json.loads(row["payload"] or "{}")} for row in rows]


def list_approvals(limit: int = 100) -> List[Dict[str, Any]]:
    with closing(_connect()) as connection:
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
    record["feature_snapshot"] = json.loads(record["feature_snapshot"] or "{}")
    record["rca_payload"] = json.loads(record["rca_payload"] or "{}") if record["rca_payload"] else None
    return record
