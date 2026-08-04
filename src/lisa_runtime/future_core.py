"""LISA 2030 foundation bootstrap.

Creates the durable relational structures required for a local-first executive
assistant that can later route work to additional cognition nodes without
moving identity, governance, or authoritative memory away from LISA01.

This module is intentionally dependency-free and safe to run at every launch.
It performs idempotent schema migrations, inventories local capabilities, and
publishes a readable readiness report into the Research Library.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path(os.getenv('LISA_RUNTIME_DB', '~/.local/share/lisa-runtime/lisa_runtime.db')).expanduser()
OLLAMA_ENDPOINT = os.getenv('LISA_OLLAMA_ENDPOINT', 'http://127.0.0.1:11434').rstrip('/')
PRIMARY_MODEL = os.getenv('LISA_OLLAMA_MODEL', 'gemma4:e2b')
SCHEMA_VERSION = '2030.1'

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS schema_versions (
  component TEXT PRIMARY KEY,
  version TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS node_registry (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  node_type TEXT NOT NULL,
  endpoint TEXT,
  trust_level TEXT NOT NULL DEFAULT 'local_authoritative',
  capabilities_json TEXT NOT NULL DEFAULT '{}',
  data_classes_json TEXT NOT NULL DEFAULT '[]',
  cost_profile_json TEXT NOT NULL DEFAULT '{}',
  health_status TEXT NOT NULL DEFAULT 'unknown',
  last_heartbeat TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_registry (
  id TEXT PRIMARY KEY,
  node_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  model_name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'general',
  context_window INTEGER,
  capabilities_json TEXT NOT NULL DEFAULT '{}',
  benchmark_json TEXT NOT NULL DEFAULT '{}',
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(node_id) REFERENCES node_registry(id)
);

CREATE TABLE IF NOT EXISTS cognitive_tasks (
  id TEXT PRIMARY KEY,
  conversation_id TEXT,
  research_job_id INTEGER,
  cognition_graph_id TEXT NOT NULL,
  goal TEXT NOT NULL,
  task_mode TEXT NOT NULL DEFAULT 'deliberative',
  authority_level TEXT NOT NULL DEFAULT 'think_research_local_write',
  status TEXT NOT NULL DEFAULT 'draft',
  priority INTEGER NOT NULL DEFAULT 50,
  budget_json TEXT NOT NULL DEFAULT '{}',
  stop_conditions_json TEXT NOT NULL DEFAULT '{}',
  lease_owner TEXT,
  lease_expires_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_cognitive_tasks_status_priority ON cognitive_tasks(status, priority DESC, created_at);

CREATE TABLE IF NOT EXISTS cognition_nodes (
  id TEXT PRIMARY KEY,
  graph_id TEXT NOT NULL,
  task_id TEXT,
  node_type TEXT NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'recorded',
  confidence REAL,
  provenance_json TEXT NOT NULL DEFAULT '{}',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES cognitive_tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_cognition_nodes_graph ON cognition_nodes(graph_id, created_at);

CREATE TABLE IF NOT EXISTS cognition_edges (
  id TEXT PRIMARY KEY,
  graph_id TEXT NOT NULL,
  source_node_id TEXT NOT NULL,
  target_node_id TEXT NOT NULL,
  edge_type TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY(source_node_id) REFERENCES cognition_nodes(id),
  FOREIGN KEY(target_node_id) REFERENCES cognition_nodes(id)
);
CREATE INDEX IF NOT EXISTS idx_cognition_edges_graph ON cognition_edges(graph_id);

CREATE TABLE IF NOT EXISTS cognitive_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT,
  graph_id TEXT,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  correlation_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES cognitive_tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_cognitive_events_task ON cognitive_events(task_id, id);

CREATE TABLE IF NOT EXISTS evidence_items (
  id TEXT PRIMARY KEY,
  task_id TEXT,
  claim TEXT NOT NULL,
  source_uri TEXT,
  source_title TEXT,
  source_type TEXT NOT NULL DEFAULT 'unknown',
  freshness_at TEXT,
  quality_score REAL,
  support_status TEXT NOT NULL DEFAULT 'unverified',
  excerpt TEXT NOT NULL DEFAULT '',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES cognitive_tasks(id)
);

CREATE TABLE IF NOT EXISTS policy_decisions (
  id TEXT PRIMARY KEY,
  task_id TEXT,
  policy_version TEXT NOT NULL,
  authority_level TEXT NOT NULL,
  risk_level TEXT NOT NULL,
  decision TEXT NOT NULL,
  rationale TEXT NOT NULL,
  requested_action_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES cognitive_tasks(id)
);

CREATE TABLE IF NOT EXISTS evaluations (
  id TEXT PRIMARY KEY,
  task_id TEXT,
  evaluation_type TEXT NOT NULL,
  score REAL,
  passed INTEGER,
  criteria_json TEXT NOT NULL DEFAULT '{}',
  findings_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES cognitive_tasks(id)
);

CREATE TABLE IF NOT EXISTS memory_items (
  id TEXT PRIMARY KEY,
  memory_class TEXT NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT,
  confidence REAL,
  importance REAL,
  retention_policy TEXT NOT NULL DEFAULT 'reviewable',
  superseded_by TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_items_class ON memory_items(memory_class, updated_at DESC);

CREATE TABLE IF NOT EXISTS memory_links (
  id TEXT PRIMARY KEY,
  source_memory_id TEXT NOT NULL,
  target_memory_id TEXT NOT NULL,
  relationship TEXT NOT NULL,
  strength REAL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(source_memory_id) REFERENCES memory_items(id),
  FOREIGN KEY(target_memory_id) REFERENCES memory_items(id)
);

CREATE TABLE IF NOT EXISTS commitments (
  id TEXT PRIMARY KEY,
  owner TEXT NOT NULL DEFAULT 'lisa',
  beneficiary TEXT,
  title TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  due_at TEXT,
  waiting_on TEXT,
  task_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES cognitive_tasks(id)
);

CREATE TABLE IF NOT EXISTS tool_registry (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  protocol TEXT NOT NULL DEFAULT 'local_python',
  endpoint TEXT,
  capability_class TEXT NOT NULL,
  authority_required TEXT NOT NULL,
  input_schema_json TEXT NOT NULL DEFAULT '{}',
  output_schema_json TEXT NOT NULL DEFAULT '{}',
  enabled INTEGER NOT NULL DEFAULT 0,
  health_status TEXT NOT NULL DEFAULT 'unknown',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS service_metrics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  service_name TEXT NOT NULL,
  metric_name TEXT NOT NULL,
  metric_value REAL,
  metric_text TEXT,
  labels_json TEXT NOT NULL DEFAULT '{}',
  recorded_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _command(command: list[str], timeout: int = 5) -> str:
    try:
        return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ''


def _ollama_tags() -> list[dict[str, Any]]:
    try:
        with urllib.request.urlopen(f'{OLLAMA_ENDPOINT}/api/tags', timeout=3) as response:
            payload = json.loads(response.read().decode('utf-8'))
            return list(payload.get('models') or [])
    except (OSError, ValueError, urllib.error.URLError):
        return []


def collect_inventory() -> dict[str, Any]:
    models = _ollama_tags()
    gpu_text = _command(['nvidia-smi', '--query-gpu=name,memory.total,memory.free', '--format=csv,noheader,nounits'])
    memory_mb = _command(['sh', '-lc', "awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo"])
    disk = shutil.disk_usage(DB_PATH.parent if DB_PATH.parent.exists() else Path.home())
    return {
        'generated_at': utc_now(),
        'host': platform.node(),
        'platform': platform.platform(),
        'python': platform.python_version(),
        'cpu': platform.processor() or _command(['lscpu']),
        'memory_mb': int(memory_mb) if memory_mb.isdigit() else None,
        'disk_free_gb': round(disk.free / (1024 ** 3), 1),
        'gpu': gpu_text or 'not detected',
        'ollama_endpoint': OLLAMA_ENDPOINT,
        'ollama_online': bool(models),
        'primary_model': PRIMARY_MODEL,
        'models': [m.get('name') for m in models],
        'database': str(DB_PATH),
        'protocol_posture': {
            'mcp': 'adapter planned; use stateless MCP core and Tasks extension when adopted',
            'a2a': 'node-to-node interoperability planned; local core remains authoritative',
            'telemetry': 'OpenTelemetry-compatible semantic events planned',
        },
    }


def apply_schema(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)
    db.execute(
        '''INSERT INTO schema_versions(component,version,applied_at) VALUES(?,?,?)
           ON CONFLICT(component) DO UPDATE SET version=excluded.version, applied_at=excluded.applied_at''',
        ('lisa_future_core', SCHEMA_VERSION, utc_now()),
    )


def upsert_inventory(db: sqlite3.Connection, inventory: dict[str, Any]) -> None:
    now = utc_now()
    capabilities = {
        'roles': ['authoritative_core', 'conversation', 'research', 'governance', 'memory', 'routing'],
        'hardware': {
            'gpu': inventory['gpu'], 'memory_mb': inventory['memory_mb'], 'disk_free_gb': inventory['disk_free_gb'],
        },
        'protocols': inventory['protocol_posture'],
    }
    db.execute(
        '''INSERT INTO node_registry(id,name,node_type,endpoint,trust_level,capabilities_json,data_classes_json,cost_profile_json,health_status,last_heartbeat,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET capabilities_json=excluded.capabilities_json,
             health_status=excluded.health_status,last_heartbeat=excluded.last_heartbeat,updated_at=excluded.updated_at''',
        ('lisa01-core', inventory['host'] or 'LISA01', 'local_core', 'local://lisa01', 'local_authoritative',
         _json(capabilities), _json(['private_local', 'user_authorized']), _json({'type': 'owned_hardware'}),
         'online', now, now, now),
    )
    for model_name in inventory['models']:
        model_id = f"ollama:{model_name}"
        role = 'embedding' if 'embed' in model_name.lower() else ('primary' if model_name == PRIMARY_MODEL else 'available')
        db.execute(
            '''INSERT INTO model_registry(id,node_id,provider,model_name,role,capabilities_json,enabled,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET role=excluded.role,capabilities_json=excluded.capabilities_json,enabled=1,updated_at=excluded.updated_at''',
            (model_id, 'lisa01-core', 'ollama', model_name, role,
             _json({'local': True, 'tool_use': 'measure', 'structured_output': 'measure'}), 1, now, now),
        )


def readiness_inventory() -> list[dict[str, str]]:
    return [
        {'priority': 'P0', 'capability': 'Durable cognitive task kernel', 'status': 'foundation added', 'need': 'Lease-based tasks, crash recovery, cancellation, idempotency and one canonical state machine.'},
        {'priority': 'P0', 'capability': 'Governed authority envelope', 'status': 'schema added', 'need': 'Separate autonomy duration from agency/effect authority; policy decisions must be deterministic and versioned.'},
        {'priority': 'P0', 'capability': 'Stable cognition graph', 'status': 'schema added', 'need': 'Persist graph nodes and typed edges; mind map, records table, timeline and document view become projections.'},
        {'priority': 'P0', 'capability': 'Identity and persona continuity', 'status': 'missing runtime service', 'need': 'Versioned EA charter, user model, clan priorities, corrections, provenance and explicit CRUD history.'},
        {'priority': 'P1', 'capability': 'Tiered memory', 'status': 'schema added', 'need': 'Working, episodic, semantic, procedural, commitment, reflective and governance memory with visible retrieval reasons.'},
        {'priority': 'P1', 'capability': 'Evidence-centered research', 'status': 'schema added', 'need': 'Claims linked to sources, freshness, quality, contradiction state and citation validation.'},
        {'priority': 'P1', 'capability': 'Model and node registry', 'status': 'local inventory active', 'need': 'Measured capability profiles, routing by task, latency, quality, cost, context and data class.'},
        {'priority': 'P1', 'capability': 'Observability and evaluations', 'status': 'metrics schema added', 'need': 'OpenTelemetry traces, replayable evals, confidence calibration, unsupported-claim rate and user correction rate.'},
        {'priority': 'P1', 'capability': 'Self Curiosity portfolio manager', 'status': 'prototype exists', 'need': 'Grounded gap discovery, scoring, hard budgets, no recursive spawning and post-run usefulness evaluation.'},
        {'priority': 'P2', 'capability': 'MCP 2026 interoperability', 'status': 'planned', 'need': 'Stateless MCP adapter, Tasks extension, authorization, tool schemas and explicit trust registry.'},
        {'priority': 'P2', 'capability': 'A2A cognition nodes', 'status': 'planned', 'need': 'Bounded task packages, node identity, attestation, data classification, health, cost and local validation of returns.'},
        {'priority': 'P2', 'capability': 'Voice and multimodal interaction', 'status': 'deferred', 'need': 'Text-first acceptance, then local STT, Lisa-appropriate TTS, interruption, turn detection and provenance.'},
        {'priority': 'P2', 'capability': 'Skill learning', 'status': 'missing', 'need': 'Promote only validated workflows through sandbox, regression test, user approval, versioning and rollback.'},
        {'priority': 'P3', 'capability': 'PostgreSQL migration path', 'status': 'planned', 'need': 'Move authoritative queues/events when multiple machines write; retain SQLite for single-node caches and offline mode.'},
    ]


def build_report(inventory: dict[str, Any]) -> str:
    lines = [
        '# LISA 2026–2030 Readiness Inventory', '',
        f"**Generated:** {inventory['generated_at']}  ",
        f"**Core node:** {inventory['host']}  ",
        f"**Primary model:** {inventory['primary_model']}  ",
        f"**Ollama:** {'online' if inventory['ollama_online'] else 'offline'}  ",
        f"**Database:** `{inventory['database']}`", '',
        '## Current Hardware and Services', '',
        f"- GPU: {inventory['gpu']}",
        f"- RAM: {inventory['memory_mb']} MB",
        f"- Free disk: {inventory['disk_free_gb']} GB",
        f"- Installed models: {', '.join(inventory['models']) or 'none detected'}", '',
        '## Capability Gaps and Forward Requirements', '',
        '| Priority | Capability | Current status | Required outcome |',
        '|---|---|---|---|',
    ]
    for item in readiness_inventory():
        lines.append(f"| {item['priority']} | {item['capability']} | {item['status']} | {item['need']} |")
    lines += [
        '', '## Operating Architecture', '',
        '```text',
        'Human direction / Self Curiosity',
        '  -> Cognitive task kernel',
        '  -> Identity + memory + commitments',
        '  -> Fast/slow model router',
        '  -> Policy and authority decision',
        '  -> Evidence / tool / cognition node execution',
        '  -> Verification and evaluation',
        '  -> Cognition graph + artifacts + memory consolidation',
        '  -> Idle / wait / escalate',
        '```', '',
        '## Non-negotiable 2030 posture', '',
        'The local core remains authoritative for identity, user context, governance, task ownership, memory, audit and final decisions. Remote cognition nodes receive bounded task packages and return candidate work that is validated locally.',
    ]
    return '\n'.join(lines)


def save_report(db: sqlite3.Connection, report: str) -> None:
    now = utc_now()
    title = 'LISA 2026–2030 Readiness Inventory'
    existing = db.execute(
        "SELECT id,version FROM knowledge_artifacts WHERE artifact_type='system_readiness' AND title=? ORDER BY version DESC LIMIT 1",
        (title,),
    ).fetchone()
    if existing:
        db.execute(
            'UPDATE knowledge_artifacts SET body=?,updated_at=?,metadata_json=? WHERE id=?',
            (report, now, _json({'schema_version': SCHEMA_VERSION, 'generated_by': 'future_core'}), existing['id']),
        )
    else:
        db.execute(
            '''INSERT INTO knowledge_artifacts(artifact_type,title,body,version,created_at,updated_at,metadata_json)
               VALUES(?,?,?,?,?,?,?)''',
            ('system_readiness', title, report, 1, now, now,
             _json({'schema_version': SCHEMA_VERSION, 'generated_by': 'future_core'})),
        )


def bootstrap() -> dict[str, Any]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    inventory = collect_inventory()
    with sqlite3.connect(DB_PATH, timeout=30) as db:
        db.row_factory = sqlite3.Row
        apply_schema(db)
        upsert_inventory(db, inventory)
        save_report(db, build_report(inventory))
        db.commit()
    return inventory


def main() -> None:
    parser = argparse.ArgumentParser(description='Bootstrap LISA 2030 foundation')
    parser.add_argument('--bootstrap', action='store_true', help='apply schema and update readiness inventory')
    parser.add_argument('--json', action='store_true', help='print machine-readable inventory')
    args = parser.parse_args()
    inventory = bootstrap()
    if args.json:
        print(json.dumps(inventory, indent=2, ensure_ascii=False))
    else:
        print(f"LISA future core schema {SCHEMA_VERSION}: READY")
        print(f"Database: {DB_PATH}")
        print(f"Ollama: {'online' if inventory['ollama_online'] else 'offline'}")
        print(f"Models: {', '.join(inventory['models']) or 'none detected'}")
        print('Readiness report: Research Library -> LISA 2026–2030 Readiness Inventory')


if __name__ == '__main__':
    main()
