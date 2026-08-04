"""Non-destructive readiness check for the LISA Cognitive Notebook layer."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .notebook_store import NotebookStore
from .open_notebook_adapter import OpenNotebookAdapter

DB_PATH = Path(os.getenv('LISA_RUNTIME_DB', '~/.local/share/lisa-runtime/lisa_runtime.db')).expanduser()


def run_checks() -> dict[str, Any]:
    store = NotebookStore(DB_PATH)
    default_id = store.ensure_default_notebook()
    linked = store.link_existing_records(default_id)
    with sqlite3.connect(DB_PATH, timeout=30) as db:
        integrity = str(db.execute('PRAGMA integrity_check').fetchone()[0])
        foreign_keys = [list(row) for row in db.execute('PRAGMA foreign_key_check').fetchall()]
        tables = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    notebooks = store.list_notebooks()
    items = store.list_items(default_id)
    node = OpenNotebookAdapter().health()
    required = {'ea_notebooks', 'ea_notebook_items', 'ea_notebook_activity'}
    return {
        'ok': integrity == 'ok' and not foreign_keys and required.issubset(tables),
        'database': str(DB_PATH),
        'integrity': integrity,
        'foreign_key_errors': foreign_keys,
        'required_tables_present': sorted(required.intersection(tables)),
        'notebook_count': len(notebooks),
        'default_notebook_id': default_id,
        'default_notebook_items': len(items),
        'new_links': linked,
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
    print(f"Open Notebook node: {'online' if result['open_notebook']['online'] else 'offline (optional)'}")
    if result['foreign_key_errors']:
        print(f"Foreign key errors: {result['foreign_key_errors']}")


if __name__ == '__main__':
    main()
