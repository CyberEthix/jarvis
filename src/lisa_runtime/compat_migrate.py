"""Compatibility migrations for evolving LISA runtime schemas.

Preserves incompatible legacy registry tables before the 2030 core creates its
authoritative structures. The migration checks both columns and foreign-key
targets because SQLite rewrites dependent foreign keys when a referenced table
is renamed. Safe and idempotent.
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

EXPECTED_FOREIGN_KEYS: dict[str, dict[str, str]] = {
    'model_registry': {'node_id': 'node_registry'},
}


def table_exists(db: sqlite3.Connection, name: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def columns(db: sqlite3.Connection, name: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f'PRAGMA table_info("{name}")').fetchall()}


def foreign_keys(db: sqlite3.Connection, name: str) -> dict[str, str]:
    """Return {local_column: referenced_table} for a table."""
    return {
        str(row[3]): str(row[2])
        for row in db.execute(f'PRAGMA foreign_key_list("{name}")').fetchall()
    }


def next_legacy_name(db: sqlite3.Connection, base: str) -> str:
    candidate = f'{base}_legacy'
    counter = 1
    while table_exists(db, candidate):
        counter += 1
        candidate = f'{base}_legacy_{counter}'
    return candidate


def incompatible_reason(db: sqlite3.Connection, table: str, required: set[str]) -> str | None:
    actual = columns(db, table)
    missing = required - actual
    if missing:
        return f'missing columns: {", ".join(sorted(missing))}'

    expected_fks = EXPECTED_FOREIGN_KEYS.get(table, {})
    actual_fks = foreign_keys(db, table)
    mismatches = [
        f'{column}->{actual_fks.get(column, "none")} (expected {target})'
        for column, target in expected_fks.items()
        if actual_fks.get(column) != target
    ]
    if mismatches:
        return 'foreign key mismatch: ' + '; '.join(mismatches)
    return None


def migrate() -> list[str]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    actions: list[str] = []
    with sqlite3.connect(DB_PATH, timeout=30) as db:
        db.execute('PRAGMA foreign_keys=OFF')
        db.execute('PRAGMA busy_timeout=5000')

        # Evaluate in dependency order: preserve the parent first, then inspect
        # dependent tables for foreign keys SQLite may have retargeted.
        for table in ('node_registry', 'model_registry', 'tool_registry'):
            required = EXPECTED[table]
            if not table_exists(db, table):
                continue
            reason = incompatible_reason(db, table, required)
            if reason is None:
                continue
            legacy = next_legacy_name(db, table)
            db.execute(f'ALTER TABLE "{table}" RENAME TO "{legacy}"')
            actions.append(f'{table} -> {legacy} ({reason})')

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
