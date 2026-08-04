"""Compatibility migrations for evolving LISA runtime schemas.

Preserves incompatible legacy tables by renaming them before the 2030 core
creates its authoritative registry structures. Safe and idempotent.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv('LISA_RUNTIME_DB', '~/.local/share/lisa-runtime/lisa_runtime.db')).expanduser()

EXPECTED: dict[str, set[str]] = {
    'node_registry': {
        'id', 'name', 'node_type', 'endpoint', 'trust_level',
        'capabilities_json', 'data_classes_json', 'cost_profile_json',
        'health_status', 'last_heartbeat', 'created_at', 'updated_at',
    },
    'model_registry': {
        'id', 'node_id', 'provider', 'model_name', 'role', 'context_window',
        'capabilities_json', 'benchmark_json', 'enabled', 'created_at', 'updated_at',
    },
    'tool_registry': {
        'id', 'name', 'protocol', 'endpoint', 'capability_class',
        'authority_required', 'input_schema_json', 'output_schema_json',
        'enabled', 'health_status', 'metadata_json', 'created_at', 'updated_at',
    },
}


def table_exists(db: sqlite3.Connection, name: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def columns(db: sqlite3.Connection, name: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f'PRAGMA table_info("{name}")').fetchall()}


def next_legacy_name(db: sqlite3.Connection, base: str) -> str:
    candidate = f'{base}_legacy'
    counter = 1
    while table_exists(db, candidate):
        counter += 1
        candidate = f'{base}_legacy_{counter}'
    return candidate


def migrate() -> list[str]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    actions: list[str] = []
    with sqlite3.connect(DB_PATH, timeout=30) as db:
        db.execute('PRAGMA foreign_keys=OFF')
        db.execute('PRAGMA busy_timeout=5000')
        for table, required in EXPECTED.items():
            if not table_exists(db, table):
                continue
            actual = columns(db, table)
            if required.issubset(actual):
                continue
            legacy = next_legacy_name(db, table)
            db.execute(f'ALTER TABLE "{table}" RENAME TO "{legacy}"')
            actions.append(
                f'{table} -> {legacy} (legacy columns: {", ".join(sorted(actual))})'
            )
        db.commit()
    return actions


def main() -> None:
    actions = migrate()
    if actions:
        print('LISA compatibility migration:')
        for action in actions:
            print(f'  preserved {action}')
    else:
        print('LISA compatibility migration: no incompatible registry tables found')


if __name__ == '__main__':
    main()
