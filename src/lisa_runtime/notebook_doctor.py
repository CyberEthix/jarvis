"""Non-destructive readiness check for the LISA Cognitive Notebook layer."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .knowledge_store import KnowledgeStore
from .notebook_store import NotebookStore
from .open_notebook_adapter import OpenNotebookAdapter
from .repository import SQLiteRepository
from .runtime_control import RuntimeControlStore

DB_PATH = Path(
    os.getenv('LISA_RUNTIME_DB', '~/.local/share/lisa-runtime/lisa_runtime.db')
).expanduser()


def _parse_stamp(value: Any) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(value))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return stamp
    except Exception:
        return None


def run_checks() -> dict[str, Any]:
    KnowledgeStore(DB_PATH)
    SQLiteRepository(DB_PATH)
    control = RuntimeControlStore(DB_PATH)
    store = NotebookStore(DB_PATH)
    default_id = store.ensure_default_notebook()
    linked = store.link_existing_records(default_id)
    with sqlite3.connect(DB_PATH, timeout=30) as db:
        integrity = str(db.execute('PRAGMA integrity_check').fetchone()[0])
        foreign_keys = [
            list(row) for row in db.execute('PRAGMA foreign_key_check').fetchall()
        ]
        tables = {
            row[0]
            for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        job_columns = {
            row[1] for row in db.execute('PRAGMA table_info(research_jobs)').fetchall()
        }
        step_columns = {
            row[1] for row in db.execute('PRAGMA table_info(research_steps)').fetchall()
        }
    notebooks = store.list_notebooks()
    items = store.list_items(default_id)
    node = OpenNotebookAdapter().health()
    service = control.service_status()
    heartbeat = _parse_stamp(service.get('heartbeat_at'))
    heartbeat_age = (
        max(0.0, (datetime.now(UTC) - heartbeat).total_seconds())
        if heartbeat else None
    )
    required = {
        'ea_notebooks', 'ea_notebook_items', 'ea_notebook_activity',
        'runtime_leases', 'idle_loop_events', 'research_jobs', 'research_steps',
    }
    restart_columns = {
        'run_generation', 'restart_count', 'last_restarted_at', 'restart_reason'
    }
    ok = (
        integrity == 'ok'
        and not foreign_keys
        and required.issubset(tables)
        and restart_columns.issubset(job_columns)
        and 'run_generation' in step_columns
    )
    return {
        'ok': ok,
        'database': str(DB_PATH),
        'integrity': integrity,
        'foreign_key_errors': foreign_keys,
        'required_tables_present': sorted(required.intersection(tables)),
        'restart_job_columns_present': sorted(restart_columns.intersection(job_columns)),
        'research_step_generation_present': 'run_generation' in step_columns,
        'notebook_count': len(notebooks),
        'default_notebook_id': default_id,
        'default_notebook_items': len(items),
        'new_links': linked,
        'idle_service': {
            'state': service.get('state', 'not_started'),
            'pid': service.get('pid'),
            'heartbeat_at': service.get('heartbeat_at'),
            'heartbeat_age_seconds': heartbeat_age,
        },
        'open_notebook': {
            'online': node.online,
            'endpoint': node.endpoint,
            'detail': node.detail,
            'required_for_core': False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    result = run_checks()
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    print(f"LISA Cognitive Notebook: {'READY' if result['ok'] else 'CHECK FAILED'}")
    print(f"Database: {result['database']}")
    print(f"Integrity: {result['integrity']}")
    print(f"Notebooks: {result['notebook_count']}")
    print(f"Default notebook items: {result['default_notebook_items']}")
    print(f"Idle service: {result['idle_service']['state']}")
    print(
        f"Open Notebook node: "
        f"{'online' if result['open_notebook']['online'] else 'offline (optional)'}"
    )
    if result['foreign_key_errors']:
        print(f"Foreign key errors: {result['foreign_key_errors']}")


if __name__ == '__main__':
    main()
