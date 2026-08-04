"""Idle-aware supervisor for queued work and self-directed research projects."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from .curiosity_manager import CuriosityResult, SelfCuriosityManager
from .knowledge_store import KnowledgeStore
from .notebook_store import NotebookStore
from .ollama_node import OllamaNode
from .orchestrator import CognitiveOrchestrator
from .repository import SQLiteRepository
from .runtime_control import RuntimeControlStore


@dataclass(slots=True)
class IdleTickResult:
    state: str
    detail: str = ''
    research_job_id: int | None = None
    curiosity_status: str = ''


class IdleResearchLoopManager:
    """Runs bounded work only after verified application inactivity.

    Order of operations when Self Curiosity is enabled:
    1. Detect and quarantine stale RUNNING jobs as NEEDS_REVIEW.
    2. Wait until the configured user-idle threshold is reached.
    3. Complete one queued/restarted job if queue processing is enabled.
    4. When the queue is clear and the curiosity interval is due, create and
       manage one self-directed research project.

    The loop never auto-restarts failed or stale jobs. Restart is a visible,
    explicit user action. Once restarted, the job becomes READY and may be
    executed by this loop during the next idle window.
    """

    TICK_LEASE_KEY = 'idle_research_tick'

    def __init__(
        self,
        store: KnowledgeStore,
        repo: SQLiteRepository,
        node: OllamaNode,
    ) -> None:
        self.store = store
        self.repo = repo
        self.node = node
        self.curiosity = SelfCuriosityManager(store, repo, node)
        self.control = RuntimeControlStore(store.path)
        self.notebooks = NotebookStore(store.path)

    def mark_user_activity(self, source: str = 'workspace') -> str:
        return self.control.mark_user_activity(source)

    def status(self) -> dict[str, Any]:
        policy = self.curiosity.policy()
        idle_seconds = self.control.idle_seconds()
        service = self.control.service_status()
        return {
            'enabled': bool(policy.get('enabled', False)),
            'idle_seconds': idle_seconds,
            'idle_minutes': idle_seconds / 60.0,
            'idle_threshold_minutes': int(policy.get('idle_minutes', 5)),
            'stale_job_minutes': int(policy.get('stale_job_minutes', 20)),
            'process_existing_queue_on_idle': bool(
                policy.get('process_existing_queue_on_idle', True)
            ),
            'auto_execute_research': bool(policy.get('auto_execute_research', True)),
            'next_curiosity': self.curiosity.next_run_text(),
            'service': service,
            'incomplete_jobs': len(self.repo.list_incomplete_jobs()),
        }

    def _sync_notebook_links(self) -> None:
        try:
            notebook_id = self.notebooks.ensure_default_notebook()
            self.notebooks.link_existing_records(notebook_id)
        except Exception as exc:
            self.control.add_event(
                'notebook_sync', 'warning', {'error': str(exc)}
            )

    def _save_completed_job(self, job_id: int, origin: str) -> None:
        report = self.repo.get_report(job_id) or {}
        summary = str(report.get('summary') or '')
        self.store.add_thought(
            'idle_execution_complete',
            f'Idle loop completed research job {job_id}',
            summary,
            research_job_id=job_id,
            metadata={'origin': origin},
        )
        self.store.save_artifact(
            'research_report',
            f'Research Report #{job_id}',
            summary,
            research_job_id=job_id,
            metadata={'origin': origin},
        )
        self.control.add_event(
            'idle_job_execution',
            'completed',
            {'origin': origin, 'summary_preview': summary[:1000]},
            research_job_id=job_id,
        )
        self._sync_notebook_links()

    def _run_one_queued_job(self) -> IdleTickResult:
        orchestrator = CognitiveOrchestrator(repository=self.repo, node=self.node)
        self.control.add_event('idle_job_execution', 'started', {'origin': 'queued_or_restarted'})
        try:
            completed_id = orchestrator.run_one_research_cycle()
            if completed_id is None:
                return IdleTickResult('idle', 'No READY or NEW jobs were available.')
            self._save_completed_job(completed_id, 'idle_queue')
            return IdleTickResult(
                'job_completed',
                f'Completed queued research job {completed_id}.',
                research_job_id=completed_id,
            )
        except Exception as exc:
            self.control.add_event(
                'idle_job_execution', 'failed', {'error': str(exc)}
            )
            return IdleTickResult('job_failed', str(exc))

    def tick(self) -> IdleTickResult:
        policy = self.curiosity.policy()
        owner = self.control.acquire_lease(
            self.TICK_LEASE_KEY,
            owner_id=f'idle-tick-{uuid4()}',
            ttl_seconds=max(180, int(policy.get('stale_job_minutes', 20)) * 60),
            metadata={'started_at': datetime.now(UTC).isoformat()},
        )
        if owner is None:
            return IdleTickResult('busy', 'Another idle-loop tick is already active.')

        try:
            stale_ids = self.repo.mark_stale_jobs(
                int(policy.get('stale_job_minutes', 20)) * 60
            )
            for job_id in stale_ids:
                self.store.add_thought(
                    'stale_job_detected',
                    f'Research job {job_id} needs review',
                    'The idle supervisor detected a stale heartbeat. The job was not restarted automatically.',
                    research_job_id=job_id,
                    status='needs_review',
                    metadata={'restart_available': True},
                )
                self.control.add_event(
                    'stale_job',
                    'needs_review',
                    {'restart_available': True},
                    research_job_id=job_id,
                )

            enabled = bool(policy.get('enabled', False))
            idle_seconds = self.control.idle_seconds()
            idle_required = int(policy.get('idle_minutes', 5)) * 60
            status_payload = {
                'enabled': enabled,
                'idle_seconds': int(idle_seconds),
                'idle_required_seconds': idle_required,
                'state': 'paused' if not enabled else 'monitoring',
                'last_tick_at': datetime.now(UTC).isoformat(),
                'stale_jobs_marked': stale_ids,
            }

            if not enabled:
                self.control.update_service_status(**status_payload)
                return IdleTickResult('disabled', 'Self Curiosity is off.')
            if self.repo.has_running_job():
                status_payload['state'] = 'work_active'
                self.control.update_service_status(**status_payload)
                return IdleTickResult('work_active', 'A research job is already running.')
            if idle_seconds < idle_required:
                status_payload['state'] = 'waiting_for_idle'
                self.control.update_service_status(**status_payload)
                remaining = max(1, int((idle_required - idle_seconds) // 60) + 1)
                return IdleTickResult(
                    'waiting_for_idle',
                    f'Waiting about {remaining} more minute(s) of inactivity.',
                )

            if (
                policy.get('process_existing_queue_on_idle', True)
                and policy.get('auto_execute_research', True)
                and any(
                    str(job.get('status', '')).upper() in {'NEW', 'READY'}
                    for job in self.repo.list_incomplete_jobs()
                )
            ):
                status_payload['state'] = 'processing_queue'
                self.control.update_service_status(**status_payload)
                return self._run_one_queued_job()

            if not self.curiosity.is_due():
                status_payload['state'] = 'idle_waiting_for_interval'
                self.control.update_service_status(**status_payload)
                return IdleTickResult(
                    'idle_waiting_for_interval',
                    self.curiosity.next_run_text(),
                )

            status_payload['state'] = 'self_curiosity_running'
            self.control.update_service_status(**status_payload)
            result: CuriosityResult = self.curiosity.run_once()
            self._sync_notebook_links()
            self.control.update_service_status(
                enabled=True,
                state=f'curiosity_{result.status}',
                last_curiosity_status=result.status,
                last_curiosity_job_id=result.research_job_id,
                last_curiosity_question=result.question,
            )
            return IdleTickResult(
                f'curiosity_{result.status}',
                result.reason or result.rationale,
                research_job_id=result.research_job_id,
                curiosity_status=result.status,
            )
        finally:
            self.control.release_lease(self.TICK_LEASE_KEY, owner)
