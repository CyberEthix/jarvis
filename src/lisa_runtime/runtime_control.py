"""Durable control records for idle research, activity, and process leases."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .models import utc_now

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS runtime_leases (
    lease_key TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS idle_loop_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    research_job_id INTEGER,
    curiosity_run_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_idle_loop_events_created
ON idle_loop_events(id DESC);
"""


class RuntimeControlStore:
    USER_ACTIVITY_KEY = 'idle_loop_last_user_activity'
    SERVICE_STATUS_KEY = 'idle_loop_service_status'

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA busy_timeout=5000')
        try:
            yield db
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value or {}, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _parse(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            stamp = datetime.fromisoformat(str(value))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            return stamp
        except Exception:
            return None

    def set_setting(self, key: str, value: Any) -> None:
        with self.connect() as db:
            db.execute(
                '''INSERT INTO runtime_settings(key,value_json,updated_at) VALUES(?,?,?)
                   ON CONFLICT(key) DO UPDATE SET
                     value_json=excluded.value_json,
                     updated_at=excluded.updated_at''',
                (key, self._json(value), utc_now()),
            )

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as db:
            row = db.execute('SELECT value_json FROM runtime_settings WHERE key=?', (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row['value_json'])
        except Exception:
            return default

    def mark_user_activity(self, source: str = 'workspace') -> str:
        stamp = utc_now()
        self.set_setting(self.USER_ACTIVITY_KEY, {'at': stamp, 'source': source})
        return stamp

    def last_user_activity(self) -> dict[str, Any]:
        value = self.get_setting(self.USER_ACTIVITY_KEY, {})
        return value if isinstance(value, dict) else {}

    def idle_seconds(self, now: datetime | None = None) -> float:
        now = now or datetime.now(UTC)
        activity = self.last_user_activity()
        stamp = self._parse(str(activity.get('at') or ''))
        if stamp is None:
            self.mark_user_activity('idle_service_startup_grace')
            return 0.0
        return max(0.0, (now - stamp).total_seconds())

    def update_service_status(self, **status: Any) -> dict[str, Any]:
        current = self.get_setting(self.SERVICE_STATUS_KEY, {})
        if not isinstance(current, dict):
            current = {}
        current.update(status)
        current['heartbeat_at'] = utc_now()
        self.set_setting(self.SERVICE_STATUS_KEY, current)
        return current

    def service_status(self) -> dict[str, Any]:
        value = self.get_setting(self.SERVICE_STATUS_KEY, {})
        return value if isinstance(value, dict) else {}

    def add_event(
        self,
        event_type: str,
        status: str,
        detail: dict[str, Any] | None = None,
        *,
        research_job_id: int | None = None,
        curiosity_run_id: int | None = None,
    ) -> int:
        with self.connect() as db:
            cur = db.execute(
                '''INSERT INTO idle_loop_events(
                   event_type,status,detail_json,research_job_id,curiosity_run_id,created_at
                   ) VALUES(?,?,?,?,?,?)''',
                (
                    event_type,
                    status,
                    self._json(detail),
                    research_job_id,
                    curiosity_run_id,
                    utc_now(),
                ),
            )
            return int(cur.lastrowid)

    def list_events(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                'SELECT * FROM idle_loop_events ORDER BY id DESC LIMIT ?',
                (max(1, int(limit)),),
            ).fetchall()
            return [dict(row) for row in rows]

    def acquire_lease(
        self,
        lease_key: str,
        *,
        owner_id: str | None = None,
        ttl_seconds: int = 300,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        owner = owner_id or str(uuid4())
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=max(30, int(ttl_seconds)))
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute(
                'SELECT owner_id,expires_at FROM runtime_leases WHERE lease_key=?',
                (lease_key,),
            ).fetchone()
            if row:
                current_expiry = self._parse(row['expires_at'])
                if current_expiry and current_expiry > now and str(row['owner_id']) != owner:
                    return None
            db.execute(
                '''INSERT INTO runtime_leases(
                   lease_key,owner_id,acquired_at,expires_at,metadata_json
                   ) VALUES(?,?,?,?,?)
                   ON CONFLICT(lease_key) DO UPDATE SET
                     owner_id=excluded.owner_id,
                     acquired_at=excluded.acquired_at,
                     expires_at=excluded.expires_at,
                     metadata_json=excluded.metadata_json''',
                (lease_key, owner, now.isoformat(), expires.isoformat(), self._json(metadata)),
            )
        return owner

    def renew_lease(self, lease_key: str, owner_id: str, ttl_seconds: int = 300) -> bool:
        expires = datetime.now(UTC) + timedelta(seconds=max(30, int(ttl_seconds)))
        with self.connect() as db:
            cur = db.execute(
                'UPDATE runtime_leases SET expires_at=? WHERE lease_key=? AND owner_id=?',
                (expires.isoformat(), lease_key, owner_id),
            )
            return cur.rowcount == 1

    def release_lease(self, lease_key: str, owner_id: str) -> None:
        with self.connect() as db:
            db.execute(
                'DELETE FROM runtime_leases WHERE lease_key=? AND owner_id=?',
                (lease_key, owner_id),
            )
