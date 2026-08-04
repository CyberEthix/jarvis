"""SQLite persistence for jobs, findings, reports, and node state."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterator

from .models import JobStatus, ResearchJob, utc_now


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS research_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    source_conversation_id TEXT,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 3,
    max_iterations INTEGER NOT NULL DEFAULT 5,
    max_runtime_seconds INTEGER NOT NULL DEFAULT 600,
    max_retries INTEGER NOT NULL DEFAULT 2,
    max_sources INTEGER NOT NULL DEFAULT 8,
    attempts INTEGER NOT NULL DEFAULT 0,
    run_generation INTEGER NOT NULL DEFAULT 1,
    restart_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    heartbeat_at TEXT,
    last_restarted_at TEXT,
    restart_reason TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_research_jobs_status_priority
ON research_jobs(status, priority DESC, created_at ASC);

CREATE TABLE IF NOT EXISTS research_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    iteration INTEGER NOT NULL,
    run_generation INTEGER NOT NULL DEFAULT 1,
    action_json TEXT NOT NULL,
    result_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES research_jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS research_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL UNIQUE,
    summary TEXT NOT NULL,
    findings_json TEXT NOT NULL DEFAULT '[]',
    limitations TEXT NOT NULL DEFAULT '',
    next_questions_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES research_jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS node_registry (
    node_id TEXT PRIMARY KEY,
    descriptor_json TEXT NOT NULL,
    healthy INTEGER NOT NULL DEFAULT 0,
    last_seen TEXT
);
"""


class SQLiteRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=5000')
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
        existing = {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
        if column not in existing:
            connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {declaration}')

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._ensure_column(connection, 'research_jobs', 'run_generation', 'INTEGER NOT NULL DEFAULT 1')
            self._ensure_column(connection, 'research_jobs', 'restart_count', 'INTEGER NOT NULL DEFAULT 0')
            self._ensure_column(connection, 'research_jobs', 'last_restarted_at', 'TEXT')
            self._ensure_column(connection, 'research_jobs', 'restart_reason', 'TEXT')
            self._ensure_column(connection, 'research_steps', 'run_generation', 'INTEGER NOT NULL DEFAULT 1')

    def create_job(self, job: ResearchJob) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO research_jobs (
                    question, rationale, source_conversation_id, status,
                    priority, max_iterations, max_runtime_seconds,
                    max_retries, max_sources, attempts, run_generation,
                    restart_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.question.strip(),
                    job.rationale.strip(),
                    job.source_conversation_id,
                    job.status.value,
                    job.priority,
                    job.max_iterations,
                    job.max_runtime_seconds,
                    job.max_retries,
                    job.max_sources,
                    job.attempts,
                    job.run_generation,
                    job.restart_count,
                    job.created_at,
                ),
            )
            return int(cursor.lastrowid)

    def get_job(self, job_id: int) -> dict | None:
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM research_jobs WHERE id=?', (job_id,)).fetchone()
            return dict(row) if row else None

    def claim_next_job(self) -> ResearchJob | None:
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                """
                SELECT * FROM research_jobs
                WHERE status IN (?, ?)
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """,
                (JobStatus.NEW.value, JobStatus.READY.value),
            ).fetchone()
            if row is None:
                return None
            now = utc_now()
            connection.execute(
                """
                UPDATE research_jobs
                SET status = ?, started_at = COALESCE(started_at, ?),
                    heartbeat_at = ?, attempts = attempts + 1, error = NULL
                WHERE id = ?
                """,
                (JobStatus.RUNNING.value, now, now, row['id']),
            )
            data = dict(row)
            data.update(
                status=JobStatus.RUNNING,
                started_at=data['started_at'] or now,
                heartbeat_at=now,
                attempts=data['attempts'] + 1,
            )
            return ResearchJob(**data)

    def heartbeat(self, job_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                'UPDATE research_jobs SET heartbeat_at = ? WHERE id = ?',
                (utc_now(), job_id),
            )

    def save_step(
        self,
        job_id: int,
        iteration: int,
        action: dict,
        result: dict,
        *,
        run_generation: int = 1,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO research_steps (
                    job_id, iteration, run_generation, action_json, result_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    iteration,
                    max(1, int(run_generation)),
                    json.dumps(action, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                    utc_now(),
                ),
            )

    def complete_job(
        self,
        job_id: int,
        summary: str,
        findings: list[dict] | None = None,
        limitations: str = '',
        next_questions: list[str] | None = None,
    ) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO research_reports (
                    job_id, summary, findings_json, limitations,
                    next_questions_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    summary = excluded.summary,
                    findings_json = excluded.findings_json,
                    limitations = excluded.limitations,
                    next_questions_json = excluded.next_questions_json,
                    created_at = excluded.created_at
                """,
                (
                    job_id,
                    summary,
                    json.dumps(findings or [], ensure_ascii=False),
                    limitations,
                    json.dumps(next_questions or [], ensure_ascii=False),
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE research_jobs
                SET status = ?, completed_at = ?, heartbeat_at = ?, error = NULL
                WHERE id = ?
                """,
                (JobStatus.COMPLETED.value, now, now, job_id),
            )

    def fail_job(self, job_id: int, error: str, retry: bool) -> None:
        status = JobStatus.READY if retry else JobStatus.FAILED
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE research_jobs
                SET status = ?, error = ?, heartbeat_at = ?
                WHERE id = ?
                """,
                (status.value, error[:4000], utc_now(), job_id),
            )

    def restart_job(self, job_id: int, reason: str = 'User requested restart') -> dict:
        """Restart any non-completed job while preserving prior attempt records."""
        now = utc_now()
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM research_jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise ValueError(f'Research job {job_id} was not found.')
            if str(row['status']).upper() == JobStatus.COMPLETED.value:
                raise ValueError('Completed jobs are immutable; create a follow-up job instead.')
            connection.execute(
                """
                UPDATE research_jobs
                SET status=?, attempts=0, started_at=NULL, completed_at=NULL,
                    heartbeat_at=?, error=NULL,
                    run_generation=COALESCE(run_generation,1)+1,
                    restart_count=COALESCE(restart_count,0)+1,
                    last_restarted_at=?, restart_reason=?
                WHERE id=?
                """,
                (JobStatus.READY.value, now, now, reason[:1000], job_id),
            )
            updated = connection.execute('SELECT * FROM research_jobs WHERE id=?', (job_id,)).fetchone()
            return dict(updated)

    def mark_stale_jobs(self, stale_after_seconds: int) -> list[int]:
        """Move stale RUNNING jobs to NEEDS_REVIEW without discarding their audit trail."""
        cutoff = datetime.now(UTC) - timedelta(seconds=max(60, int(stale_after_seconds)))
        stale: list[int] = []
        with self.connect() as connection:
            rows = connection.execute(
                'SELECT id, heartbeat_at, started_at FROM research_jobs WHERE status=?',
                (JobStatus.RUNNING.value,),
            ).fetchall()
            for row in rows:
                raw = row['heartbeat_at'] or row['started_at']
                try:
                    stamp = datetime.fromisoformat(str(raw)) if raw else None
                    if stamp and stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=UTC)
                except Exception:
                    stamp = None
                if stamp is not None and stamp > cutoff:
                    continue
                stale.append(int(row['id']))
                connection.execute(
                    """
                    UPDATE research_jobs
                    SET status=?, error=?, heartbeat_at=?
                    WHERE id=? AND status=?
                    """,
                    (
                        JobStatus.NEEDS_REVIEW.value,
                        'The idle supervisor detected a stale research heartbeat. Review or restart this job.',
                        utc_now(),
                        row['id'],
                        JobStatus.RUNNING.value,
                    ),
                )
        return stale

    def has_running_job(self) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                'SELECT 1 FROM research_jobs WHERE status=? LIMIT 1',
                (JobStatus.RUNNING.value,),
            ).fetchone()
            return row is not None

    def list_incomplete_jobs(self, limit: int = 500) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM research_jobs
                WHERE status <> ?
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
                """,
                (JobStatus.COMPLETED.value, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_jobs(self, limit: int = 100) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM research_jobs
                ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_report(self, job_id: int) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                'SELECT * FROM research_reports WHERE job_id = ?',
                (job_id,),
            ).fetchone()
            return dict(row) if row else None
