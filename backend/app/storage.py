"""Small transactional store for accounts, sessions and owned dataset metadata."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from backend.app.config import DATA_DIR, DATASET_REGISTRY_PATH

DB_PATH = Path(os.environ.get("AIS_AUTH_DB", str(DATA_DIR / "private" / "platform.sqlite3")))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        with con:
            yield con
    finally:
        con.close()


def initialize() -> None:
    with connection() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user','superadmin')),
                enabled INTEGER NOT NULL DEFAULT 1, must_change_password INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, password_changed_at TEXT NOT NULL,
                last_login_at TEXT, last_access_at TEXT, login_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
                csrf_token TEXT NOT NULL, active_dataset_id TEXT,
                created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                expires_at REAL NOT NULL, ip TEXT NOT NULL, user_agent TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
            CREATE TABLE IF NOT EXISTS datasets (
                id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL REFERENCES users(id),
                record TEXT NOT NULL, last_used_at TEXT
            );
            CREATE INDEX IF NOT EXISTS datasets_owner ON datasets(owner_id);
            CREATE TABLE IF NOT EXISTS login_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(id),
                username TEXT NOT NULL, success INTEGER NOT NULL,
                created_at TEXT NOT NULL, ip TEXT NOT NULL, user_agent TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS login_events_time ON login_events(created_at);
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER REFERENCES users(id),
                action TEXT NOT NULL, target_id INTEGER, details TEXT NOT NULL,
                created_at TEXT NOT NULL, ip TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS access_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
                path TEXT NOT NULL, created_at TEXT NOT NULL, ip TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                owner_id INTEGER NOT NULL REFERENCES users(id), kind TEXT NOT NULL,
                state TEXT NOT NULL, PRIMARY KEY(owner_id, kind)
            );
        """)


def has_users() -> bool:
    with connection() as con:
        return con.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def public_user(row) -> dict:
    fields = ("id", "username", "display_name", "role", "enabled", "must_change_password",
              "created_at", "password_changed_at", "last_login_at", "last_access_at", "login_count")
    return {key: row[key] for key in fields}


def audit(con, actor_id: int, action: str, target_id: int | None, ip: str, **details) -> None:
    con.execute("INSERT INTO audit_events(actor_id,action,target_id,details,created_at,ip) VALUES(?,?,?,?,?,?)",
                (actor_id, action, target_id, json.dumps(details, ensure_ascii=False), now(), ip))


def migrate_legacy(con, owner_id: int) -> int:
    """Adopt existing files without moving or rewriting the legacy registry."""
    if not DATASET_REGISTRY_PATH.exists():
        return 0
    registry = json.loads(DATASET_REGISTRY_PATH.read_text(encoding="utf-8"))
    count = 0
    for dataset_id, record in registry.get("datasets", {}).items():
        con.execute("INSERT OR IGNORE INTO datasets(id,owner_id,record) VALUES(?,?,?)",
                    (dataset_id, owner_id, json.dumps(record, ensure_ascii=False)))
        count += 1
    return count


def owned_registry(owner_id: int, active_id: str | None) -> dict:
    with connection() as con:
        rows = con.execute("SELECT id,record,last_used_at FROM datasets WHERE owner_id=?", (owner_id,)).fetchall()
    datasets = {}
    for row in rows:
        record = json.loads(row["record"])
        record["last_used_at"] = row["last_used_at"]
        datasets[row["id"]] = record
    return {"datasets": datasets, "active_dataset_id": active_id if active_id in datasets else None}


def save_registry(owner_id: int, session_hash: str, registry: dict) -> None:
    with connection() as con:
        existing = {r["id"] for r in con.execute("SELECT id FROM datasets WHERE owner_id=?", (owner_id,))}
        records = registry["datasets"]
        for dataset_id, record in records.items():
            owner = con.execute("SELECT owner_id FROM datasets WHERE id=?", (dataset_id,)).fetchone()
            if owner and owner["owner_id"] != owner_id:
                raise PermissionError("无权使用此数据集。")
            con.execute("""INSERT INTO datasets(id,owner_id,record) VALUES(?,?,?)
                ON CONFLICT(id) DO UPDATE SET record=excluded.record WHERE datasets.owner_id=excluded.owner_id""",
                        (dataset_id, owner_id, json.dumps(record, ensure_ascii=False)))
        for removed in existing - records.keys():
            con.execute("DELETE FROM datasets WHERE id=? AND owner_id=?", (removed, owner_id))
            con.execute("UPDATE sessions SET active_dataset_id=NULL WHERE active_dataset_id=? AND user_id=?",
                        (removed, owner_id))
        selected = registry.get("active_dataset_id") or None
        con.execute("UPDATE sessions SET active_dataset_id=? WHERE token_hash=? AND user_id=?",
                    (selected, session_hash, owner_id))
        if selected:
            con.execute("UPDATE datasets SET last_used_at=? WHERE id=? AND owner_id=?", (now(), selected, owner_id))


def load_job(owner_id: int, kind: str) -> dict:
    with connection() as con:
        row = con.execute("SELECT state FROM jobs WHERE owner_id=? AND kind=?", (owner_id, kind)).fetchone()
    return json.loads(row["state"]) if row else {
        "status": "idle", "message": "", "started_at": None, "finished_at": None, "progress": {}}


def save_job(owner_id: int, kind: str, state: dict) -> None:
    with connection() as con:
        con.execute("INSERT INTO jobs(owner_id,kind,state) VALUES(?,?,?) ON CONFLICT(owner_id,kind) DO UPDATE SET state=excluded.state",
                    (owner_id, kind, json.dumps(state, ensure_ascii=False)))


def recover_jobs() -> None:
    with connection() as con:
        for row in con.execute("SELECT owner_id,kind,state FROM jobs").fetchall():
            state = json.loads(row["state"])
            if state.get("status") == "running":
                state.update(status="error", message="服务重启中断了任务，请重新运行。", finished_at=now())
                con.execute("UPDATE jobs SET state=? WHERE owner_id=? AND kind=?",
                            (json.dumps(state, ensure_ascii=False), row["owner_id"], row["kind"]))
