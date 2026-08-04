"""Long-running unattended service for idle research and Self Curiosity."""
from __future__ import annotations

import fcntl
import os
import signal
import time
from pathlib import Path

from .idle_loop_manager import IdleResearchLoopManager
from .knowledge_store import KnowledgeStore
from .nicegui_app import DB_PATH, ENDPOINT, MODEL
from .ollama_node import OllamaNode
from .repository import SQLiteRepository

POLL_SECONDS = max(5, int(os.getenv('LISA_IDLE_POLL_SECONDS', '15')))
LOCK_PATH = Path(
    os.getenv('LISA_IDLE_LOCK', '~/.local/share/lisa-runtime/idle-loop-service.lock')
).expanduser()


def main() -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_handle = LOCK_PATH.open('w')
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('LISA idle research service is already running.', flush=True)
        return

    lock_handle.write(str(os.getpid()))
    lock_handle.flush()

    store = KnowledgeStore(DB_PATH)
    repo = SQLiteRepository(DB_PATH)
    node = OllamaNode(endpoint=ENDPOINT, model=MODEL)
    manager = IdleResearchLoopManager(store, repo, node)
    manager.control.mark_user_activity('idle_service_startup_grace')
    manager.control.add_event(
        'idle_service',
        'started',
        {'pid': os.getpid(), 'poll_seconds': POLL_SECONDS, 'model': MODEL},
    )
    manager.control.update_service_status(
        pid=os.getpid(),
        state='monitoring',
        poll_seconds=POLL_SECONDS,
        model=MODEL,
    )

    running = True

    def stop_service(signum: int, _frame: object) -> None:
        nonlocal running
        running = False
        manager.control.add_event(
            'idle_service', 'stopping', {'signal': signum, 'pid': os.getpid()}
        )

    signal.signal(signal.SIGTERM, stop_service)
    signal.signal(signal.SIGINT, stop_service)

    try:
        while running:
            try:
                result = manager.tick()
                manager.control.update_service_status(
                    pid=os.getpid(),
                    state=result.state,
                    detail=result.detail,
                    research_job_id=result.research_job_id,
                    curiosity_status=result.curiosity_status,
                    poll_seconds=POLL_SECONDS,
                    model=MODEL,
                )
            except Exception as exc:
                manager.control.add_event(
                    'idle_service', 'tick_failed', {'error': str(exc), 'pid': os.getpid()}
                )
                manager.control.update_service_status(
                    pid=os.getpid(), state='error', detail=str(exc)
                )
            for _ in range(POLL_SECONDS):
                if not running:
                    break
                time.sleep(1)
    finally:
        manager.control.update_service_status(
            pid=os.getpid(), state='stopped', detail='Service exited.'
        )
        manager.control.add_event('idle_service', 'stopped', {'pid': os.getpid()})
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()


if __name__ == '__main__':
    main()
