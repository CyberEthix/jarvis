"""Authoritative notebook persistence for the LISA cognitive workspace.

The notebook layer is a user-facing container over the existing LISA RDBMS.
It does not replace conversations, cognition graphs, research jobs, evidence,
commitments, or audit records. Instead, it links those authoritative records
into bounded workspaces that can be viewed and managed consistently.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from .models import utc_now

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS ea_notebooks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    mission TEXT NOT NULL DEFAULT '',
    beneficiary TEXT NOT NULL DEFAULT '',
    success_criteria TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    open_notebook_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ea_notebooks_status
ON ea_notebooks(archived, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS ea_notebook_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notebook_id TEXT NOT NULL,
    item_type TEXT NOT NULL,
    external_table TEXT,
    external_id TEXT,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    provenance_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(notebook_id) REFERENCES ea_notebooks(id) ON DELETE CASCADE,
    UNIQUE(notebook_id, item_type, external_table, external_id)
);
CREATE INDEX IF NOT EXISTS idx_ea_notebook_items_scope
ON ea_notebook_items(notebook_id, item_type, archived, updated_at DESC);

CREATE TABLE IF NOT EXISTS ea_notebook_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notebook_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(notebook_id) REFERENCES ea_notebooks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ea_notebook_activity
ON ea_notebook_activity(notebook_id, id DESC);
"""


class NotebookStore:
    """CRUD and linking service for LISA cognitive notebooks."""

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

    def _activity(self, db: sqlite3.Connection, notebook_id: str, event_type: str,
                  detail: dict[str, Any] | None = None, actor: str = 'lisa_runtime') -> None:
        db.execute(
            'INSERT INTO ea_notebook_activity(notebook_id,event_type,actor,detail_json,created_at) VALUES(?,?,?,?,?)',
            (notebook_id, event_type, actor, self._json(detail), utc_now()),
        )

    def create_notebook(self, title: str, *, description: str = '', mission: str = '',
                        beneficiary: str = '', success_criteria: str = '',
                        open_notebook_id: str | None = None) -> str:
        notebook_id, now = str(uuid4()), utc_now()
        with self.connect() as db:
            db.execute(
                '''INSERT INTO ea_notebooks(
                    id,title,description,mission,beneficiary,success_criteria,status,
                    open_notebook_id,created_at,updated_at,archived
                ) VALUES(?,?,?,?,?,?,'active',?,?,?,0)''',
                (notebook_id, title.strip() or 'Untitled Notebook', description.strip(), mission.strip(),
                 beneficiary.strip(), success_criteria.strip(), open_notebook_id, now, now),
            )
            self._activity(db, notebook_id, 'notebook_created', {'title': title}, actor='user')
        return notebook_id

    def ensure_default_notebook(self) -> str:
        notebooks = self.list_notebooks()
        if notebooks:
            return str(notebooks[0]['id'])
        return self.create_notebook(
            'LISA Core Development',
            description='Primary workspace for building the local LISA cognitive runtime.',
            mission='Build a governed, inspectable, local-first end-to-end executive assistant.',
            beneficiary='Charles James and clan',
            success_criteria='Durable cognition, visible evidence, bounded autonomy, reliable recovery, and useful outcomes.',
        )

    def list_notebooks(self, include_archived: bool = False) -> list[dict[str, Any]]:
        where = '' if include_archived else ' WHERE archived=0'
        with self.connect() as db:
            rows = db.execute(f'SELECT * FROM ea_notebooks{where} ORDER BY updated_at DESC').fetchall()
            return [dict(row) for row in rows]

    def get_notebook(self, notebook_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute('SELECT * FROM ea_notebooks WHERE id=?', (notebook_id,)).fetchone()
            return dict(row) if row else None

    def update_notebook(self, notebook_id: str, **changes: Any) -> None:
        allowed = {
            'title', 'description', 'mission', 'beneficiary', 'success_criteria',
            'status', 'open_notebook_id', 'archived',
        }
        fields: list[str] = []
        values: list[Any] = []
        for key, value in changes.items():
            if key not in allowed:
                continue
            fields.append(f'{key}=?')
            values.append(int(bool(value)) if key == 'archived' else value)
        if not fields:
            return
        fields.append('updated_at=?')
        values.extend([utc_now(), notebook_id])
        with self.connect() as db:
            db.execute(f"UPDATE ea_notebooks SET {', '.join(fields)} WHERE id=?", values)
            self._activity(db, notebook_id, 'notebook_updated', {'fields': sorted(changes)}, actor='user')

    def add_item(self, notebook_id: str, item_type: str, title: str, *, body: str = '',
                 external_table: str | None = None, external_id: str | int | None = None,
                 status: str = 'active', provenance: dict[str, Any] | None = None,
                 metadata: dict[str, Any] | None = None) -> int:
        now = utc_now()
        ext_id = None if external_id is None else str(external_id)
        with self.connect() as db:
            existing = None
            if external_table is not None and ext_id is not None:
                existing = db.execute(
                    '''SELECT id FROM ea_notebook_items
                       WHERE notebook_id=? AND item_type=? AND external_table=? AND external_id=?''',
                    (notebook_id, item_type, external_table, ext_id),
                ).fetchone()
            if existing:
                item_id = int(existing['id'])
                db.execute(
                    '''UPDATE ea_notebook_items SET title=?,body=?,status=?,provenance_json=?,
                       metadata_json=?,updated_at=?,archived=0 WHERE id=?''',
                    (title, body, status, self._json(provenance), self._json(metadata), now, item_id),
                )
                event_type = 'item_refreshed'
            else:
                cur = db.execute(
                    '''INSERT INTO ea_notebook_items(
                       notebook_id,item_type,external_table,external_id,title,body,status,
                       provenance_json,metadata_json,created_at,updated_at,archived
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,0)''',
                    (notebook_id, item_type, external_table, ext_id, title, body, status,
                     self._json(provenance), self._json(metadata), now, now),
                )
                item_id = int(cur.lastrowid)
                event_type = 'item_added'
            db.execute('UPDATE ea_notebooks SET updated_at=? WHERE id=?', (now, notebook_id))
            self._activity(db, notebook_id, event_type, {
                'item_id': item_id, 'item_type': item_type, 'external_table': external_table,
                'external_id': ext_id,
            })
            return item_id

    def list_items(self, notebook_id: str, item_type: str | None = None,
                   include_archived: bool = False) -> list[dict[str, Any]]:
        clauses = ['notebook_id=?']
        values: list[Any] = [notebook_id]
        if item_type:
            clauses.append('item_type=?')
            values.append(item_type)
        if not include_archived:
            clauses.append('archived=0')
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM ea_notebook_items WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC, id DESC",
                values,
            ).fetchall()
            return [dict(row) for row in rows]

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute('SELECT * FROM ea_notebook_items WHERE id=?', (item_id,)).fetchone()
            return dict(row) if row else None

    def update_item(self, item_id: int, *, title: str | None = None, body: str | None = None,
                    status: str | None = None, metadata: dict[str, Any] | None = None,
                    archived: bool | None = None) -> None:
        item = self.get_item(item_id)
        if not item:
            return
        fields: list[str] = []
        values: list[Any] = []
        for key, value in {'title': title, 'body': body, 'status': status}.items():
            if value is not None:
                fields.append(f'{key}=?')
                values.append(value)
        if metadata is not None:
            fields.append('metadata_json=?')
            values.append(self._json(metadata))
        if archived is not None:
            fields.append('archived=?')
            values.append(int(archived))
        if not fields:
            return
        fields.append('updated_at=?')
        values.extend([utc_now(), item_id])
        with self.connect() as db:
            db.execute(f"UPDATE ea_notebook_items SET {', '.join(fields)} WHERE id=?", values)
            self._activity(db, str(item['notebook_id']), 'item_updated', {'item_id': item_id}, actor='user')

    def archive_item(self, item_id: int) -> None:
        self.update_item(item_id, archived=True, status='archived')

    def activity(self, notebook_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                'SELECT * FROM ea_notebook_activity WHERE notebook_id=? ORDER BY id DESC LIMIT ?',
                (notebook_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def _table_exists(db: sqlite3.Connection, table: str) -> bool:
        return db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None

    def link_existing_records(self, notebook_id: str) -> dict[str, int]:
        """Idempotently link existing authoritative records to a notebook.

        This creates notebook references only. It does not copy, mutate, or delete
        the underlying LISA records.
        """
        linked: dict[str, int] = {}
        with self.connect() as db:
            specs: list[tuple[str, str, str, str, str]] = [
                ('conversations', 'conversation', 'id', 'title', 'updated_at'),
                ('knowledge_artifacts', 'memo', 'id', 'title', 'updated_at'),
                ('research_jobs', 'cognition', 'id', 'question', 'created_at'),
                ('curiosity_runs', 'run', 'id', 'proposed_question', 'started_at'),
                ('evidence_items', 'evidence', 'id', 'claim', 'created_at'),
                ('policy_decisions', 'decision', 'id', 'decision', 'created_at'),
                ('commitments', 'commitment', 'id', 'title', 'updated_at'),
                ('evaluations', 'evaluation', 'id', 'evaluation_type', 'created_at'),
            ]
            for table, item_type, id_col, title_col, time_col in specs:
                if not self._table_exists(db, table):
                    continue
                table_columns = {str(row[1]) for row in db.execute(f'PRAGMA table_info("{table}")')}
                if id_col not in table_columns:
                    continue
                title_expr = title_col if title_col in table_columns else id_col
                time_expr = time_col if time_col in table_columns else "''"
                rows = db.execute(
                    f'SELECT "{id_col}" AS external_id, "{title_expr}" AS title, "{time_expr}" AS stamp FROM "{table}"'
                ).fetchall()
                count = 0
                for row in rows:
                    title = str(row['title'] or f'{item_type.title()} {row["external_id"]}')
                    before = db.total_changes
                    now = utc_now()
                    db.execute(
                        '''INSERT OR IGNORE INTO ea_notebook_items(
                           notebook_id,item_type,external_table,external_id,title,body,status,
                           provenance_json,metadata_json,created_at,updated_at,archived
                           ) VALUES(?,?,?,?,?,'','linked',?,?,?, ?,0)''',
                        (notebook_id, item_type, table, str(row['external_id']), title,
                         self._json({'source': 'authoritative_lisa_rdbms'}),
                         self._json({'source_timestamp': row['stamp']}), now, now),
                    )
                    if db.total_changes > before:
                        count += 1
                linked[item_type] = count
            self._activity(db, notebook_id, 'existing_records_linked', linked)
        return linked
