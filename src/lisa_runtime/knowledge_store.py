"""CRUD persistence for the LISA human+AI research workspace."""
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
CREATE TABLE IF NOT EXISTS conversations (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages (
 id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
 created_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
 FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);
CREATE TABLE IF NOT EXISTS thought_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT, research_job_id INTEGER, stage TEXT NOT NULL,
 title TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'recorded',
 created_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_thought_events_job ON thought_events(research_job_id, created_at);
CREATE TABLE IF NOT EXISTS knowledge_artifacts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, artifact_type TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
 research_job_id INTEGER, conversation_id TEXT, version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_artifacts_job ON knowledge_artifacts(research_job_id, updated_at DESC);
"""

class KnowledgeStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def create_conversation(self, title: str = 'New conversation') -> str:
        cid, now = str(uuid4()), utc_now()
        with self.connect() as db:
            db.execute('INSERT INTO conversations(id,title,created_at,updated_at) VALUES(?,?,?,?)', (cid, title, now, now))
        return cid

    def list_conversations(self, include_archived: bool = False) -> list[dict[str, Any]]:
        sql = 'SELECT * FROM conversations' + ('' if include_archived else ' WHERE archived=0') + ' ORDER BY updated_at DESC'
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql).fetchall()]

    def update_conversation(self, conversation_id: str, *, title: str | None = None, archived: bool | None = None) -> None:
        fields, values = [], []
        if title is not None: fields += ['title=?']; values += [title]
        if archived is not None: fields += ['archived=?']; values += [int(archived)]
        if not fields: return
        fields += ['updated_at=?']; values += [utc_now(), conversation_id]
        with self.connect() as db:
            db.execute(f"UPDATE conversations SET {', '.join(fields)} WHERE id=?", values)

    def delete_conversation(self, conversation_id: str) -> None:
        with self.connect() as db:
            db.execute('DELETE FROM conversations WHERE id=?', (conversation_id,))

    def add_message(self, conversation_id: str, role: str, content: str, metadata: dict | None = None) -> int:
        with self.connect() as db:
            cur = db.execute('INSERT INTO messages(conversation_id,role,content,created_at,metadata_json) VALUES(?,?,?,?,?)',
                (conversation_id, role, content, utc_now(), json.dumps(metadata or {}, ensure_ascii=False)))
            db.execute('UPDATE conversations SET updated_at=? WHERE id=?', (utc_now(), conversation_id))
            return int(cur.lastrowid)

    def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY id', (conversation_id,)).fetchall()]

    def update_message(self, message_id: int, content: str) -> None:
        with self.connect() as db:
            db.execute('UPDATE messages SET content=? WHERE id=?', (content, message_id))

    def delete_message(self, message_id: int) -> None:
        with self.connect() as db:
            db.execute('DELETE FROM messages WHERE id=?', (message_id,))

    def add_thought(self, stage: str, title: str, content: str, *, conversation_id: str | None = None,
                    research_job_id: int | None = None, status: str = 'recorded', metadata: dict | None = None) -> int:
        with self.connect() as db:
            cur = db.execute('''INSERT INTO thought_events(conversation_id,research_job_id,stage,title,content,status,created_at,metadata_json)
                VALUES(?,?,?,?,?,?,?,?)''', (conversation_id, research_job_id, stage, title, content, status, utc_now(),
                json.dumps(metadata or {}, ensure_ascii=False)))
            return int(cur.lastrowid)

    def list_thoughts(self, research_job_id: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
        with self.connect() as db:
            if research_job_id is None:
                rows = db.execute('SELECT * FROM thought_events ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
            else:
                rows = db.execute('SELECT * FROM thought_events WHERE research_job_id=? ORDER BY id', (research_job_id,)).fetchall()
            return [dict(r) for r in rows]

    def save_artifact(self, artifact_type: str, title: str, body: str, *, research_job_id: int | None = None,
                      conversation_id: str | None = None, metadata: dict | None = None) -> int:
        now = utc_now()
        with self.connect() as db:
            existing = db.execute('''SELECT id,version FROM knowledge_artifacts WHERE artifact_type=? AND title=?
                AND research_job_id IS ? ORDER BY version DESC LIMIT 1''', (artifact_type, title, research_job_id)).fetchone()
            version = int(existing['version']) + 1 if existing else 1
            cur = db.execute('''INSERT INTO knowledge_artifacts(artifact_type,title,body,research_job_id,conversation_id,version,created_at,updated_at,metadata_json)
                VALUES(?,?,?,?,?,?,?,?,?)''', (artifact_type, title, body, research_job_id, conversation_id, version, now, now,
                json.dumps(metadata or {}, ensure_ascii=False)))
            return int(cur.lastrowid)

    def list_artifacts(self, research_job_id: int | None = None) -> list[dict[str, Any]]:
        with self.connect() as db:
            if research_job_id is None:
                rows = db.execute('SELECT * FROM knowledge_artifacts ORDER BY updated_at DESC').fetchall()
            else:
                rows = db.execute('SELECT * FROM knowledge_artifacts WHERE research_job_id=? ORDER BY version DESC', (research_job_id,)).fetchall()
            return [dict(r) for r in rows]

    def get_artifact(self, artifact_id: int) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute('SELECT * FROM knowledge_artifacts WHERE id=?', (artifact_id,)).fetchone()
            return dict(row) if row else None

    def update_artifact(self, artifact_id: int, *, title: str | None = None, body: str | None = None) -> None:
        fields, values = [], []
        if title is not None: fields += ['title=?']; values += [title]
        if body is not None: fields += ['body=?']; values += [body]
        if not fields: return
        fields += ['updated_at=?']; values += [utc_now(), artifact_id]
        with self.connect() as db:
            db.execute(f"UPDATE knowledge_artifacts SET {', '.join(fields)} WHERE id=?", values)

    def delete_artifact(self, artifact_id: int) -> None:
        with self.connect() as db:
            db.execute('DELETE FROM knowledge_artifacts WHERE id=?', (artifact_id,))
