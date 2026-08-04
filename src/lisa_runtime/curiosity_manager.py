"""Governed, non-destructive self-curiosity cycle for unattended local research."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from .knowledge_store import KnowledgeStore
from .ollama_node import OllamaNode
from .orchestrator import CognitiveOrchestrator
from .repository import SQLiteRepository
from .runtime_control import RuntimeControlStore


DEFAULT_POLICY = {
    'enabled': False,
    'interval_minutes': 30,
    'idle_minutes': 5,
    'stale_job_minutes': 20,
    'max_runs_per_day': 8,
    'auto_execute_research': True,
    'process_existing_queue_on_idle': True,
    'local_only': True,
    'require_existing_context': True,
    'max_context_chars': 16000,
    'forbidden_topics': [
        'credentials', 'passwords', 'private keys', 'financial transactions',
        'purchases', 'messaging people', 'system modification', 'software installation',
        'destructive actions', 'surveillance', 'weapon construction', 'self-harm',
    ],
}


@dataclass(slots=True)
class CuriosityResult:
    status: str
    question: str = ''
    rationale: str = ''
    research_job_id: int | None = None
    report_summary: str = ''
    reason: str = ''


class SelfCuriosityManager:
    """Creates at most one bounded research job per due cycle.

    The manager never executes shell commands, changes files, sends messages,
    performs purchases, installs software, or reaches external services. Its
    only permitted action is to inspect local persisted context, propose one
    research question, queue it, and optionally run the existing bounded local
    research cycle.
    """

    SETTING_KEY = 'self_curiosity_policy'
    LAST_RUN_KEY = 'self_curiosity_last_run'
    CYCLE_LEASE_KEY = 'self_curiosity_cycle'

    def __init__(self, store: KnowledgeStore, repo: SQLiteRepository, node: OllamaNode) -> None:
        self.store = store
        self.repo = repo
        self.node = node
        self.control = RuntimeControlStore(store.path)

    def policy(self) -> dict[str, Any]:
        saved = self.store.get_setting(self.SETTING_KEY, {})
        merged = dict(DEFAULT_POLICY)
        if isinstance(saved, dict):
            merged.update(saved)
        return merged

    def set_enabled(self, enabled: bool) -> dict[str, Any]:
        policy = self.policy()
        policy['enabled'] = bool(enabled)
        self.store.set_setting(self.SETTING_KEY, policy)
        self.store.add_thought(
            'curiosity_control',
            f"Self Curiosity {'enabled' if enabled else 'disabled'}",
            'User changed the unattended curiosity mode from the main control.',
            metadata={'enabled': bool(enabled), 'policy': policy},
        )
        self.control.add_event(
            'curiosity_control',
            'enabled' if enabled else 'disabled',
            {'policy': policy},
        )
        return policy

    def update_policy(self, **changes: Any) -> dict[str, Any]:
        policy = self.policy()
        for key, value in changes.items():
            if key in DEFAULT_POLICY:
                policy[key] = value
        policy['interval_minutes'] = max(5, int(policy['interval_minutes']))
        policy['idle_minutes'] = max(1, min(1440, int(policy['idle_minutes'])))
        policy['stale_job_minutes'] = max(5, min(1440, int(policy['stale_job_minutes'])))
        policy['max_runs_per_day'] = max(1, min(48, int(policy['max_runs_per_day'])))
        policy['max_context_chars'] = max(2000, min(100000, int(policy['max_context_chars'])))
        policy['auto_execute_research'] = bool(policy['auto_execute_research'])
        policy['process_existing_queue_on_idle'] = bool(policy['process_existing_queue_on_idle'])
        self.store.set_setting(self.SETTING_KEY, policy)
        self.control.add_event('curiosity_policy', 'updated', {'changes': changes, 'policy': policy})
        return policy

    def is_due(self, now: datetime | None = None) -> bool:
        policy = self.policy()
        if not policy.get('enabled', False):
            return False
        now = now or datetime.now(timezone.utc)
        last_raw = self.store.get_setting(self.LAST_RUN_KEY)
        if not last_raw:
            return True
        try:
            last = datetime.fromisoformat(str(last_raw))
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
        except ValueError:
            return True
        return now >= last + timedelta(minutes=int(policy['interval_minutes']))

    def next_run_text(self) -> str:
        policy = self.policy()
        if not policy.get('enabled', False):
            return 'Paused'
        last_raw = self.store.get_setting(self.LAST_RUN_KEY)
        if not last_raw:
            return 'Due after idle threshold'
        try:
            last = datetime.fromisoformat(str(last_raw))
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            due = last + timedelta(minutes=int(policy['interval_minutes']))
            remaining = due - datetime.now(timezone.utc)
            if remaining.total_seconds() <= 0:
                return 'Due when system is idle'
            minutes = max(1, int(remaining.total_seconds() // 60))
            return f'About {minutes} min, then idle-gated'
        except ValueError:
            return 'Due when system is idle'

    def _runs_today(self) -> int:
        today = datetime.now(timezone.utc).date()
        count = 0
        for run in self.store.list_curiosity_runs(limit=200):
            try:
                stamp = datetime.fromisoformat(run['started_at'])
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                if stamp.astimezone(timezone.utc).date() == today:
                    count += 1
            except Exception:
                continue
        return count

    def _context(self, policy: dict[str, Any]) -> str:
        parts: list[str] = []
        for msg in self.store.list_recent_messages(limit=40):
            content = str(msg.get('content', '')).strip()
            if content:
                parts.append(f"MESSAGE [{msg.get('role','unknown')}]: {content}")
        for artifact in self.store.list_artifacts()[:30]:
            body = str(artifact.get('body', '')).strip()
            if body:
                parts.append(
                    f"ARTIFACT [{artifact.get('artifact_type','unknown')}] "
                    f"{artifact.get('title','')}: {body}"
                )
        try:
            with self.store.connect() as db:
                exists = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ea_notebook_items'"
                ).fetchone()
                if exists:
                    rows = db.execute(
                        '''SELECT item_type,title,body,status,updated_at
                           FROM ea_notebook_items
                           WHERE archived=0 AND item_type IN ('source','memo','evidence','decision','commitment')
                           ORDER BY updated_at DESC LIMIT 40'''
                    ).fetchall()
                    for row in rows:
                        body = str(row['body'] or '').strip()
                        if body:
                            parts.append(
                                f"NOTEBOOK {str(row['item_type']).upper()} [{row['status']}] "
                                f"{row['title']}: {body}"
                            )
        except Exception:
            pass
        joined = '\n\n'.join(parts)
        return joined[-int(policy['max_context_chars']):]

    def _duplicate(self, question: str) -> bool:
        normalized = re.sub(r'\W+', ' ', question.lower()).strip()
        if not normalized:
            return True
        for job in self.repo.list_jobs(1000):
            existing = re.sub(r'\W+', ' ', str(job.get('question', '')).lower()).strip()
            if existing == normalized:
                return True
        return False

    def _safe_question(self, question: str, policy: dict[str, Any]) -> tuple[bool, str]:
        lower = question.lower()
        for blocked in policy.get('forbidden_topics', []):
            if str(blocked).lower() in lower:
                return False, f'Blocked topic or action: {blocked}'
        action_terms = (
            'delete ', 'remove ', 'install ', 'purchase ', 'buy ', 'email ',
            'message ', 'call ', 'execute ', 'run command', 'change system',
        )
        if any(term in lower for term in action_terms):
            return False, 'Question requests an external or potentially destructive action.'
        return True, ''

    def _propose(self, context: str, policy: dict[str, Any]) -> dict[str, Any]:
        prompt = f"""
Review the following locally stored conversation, notebook, evidence, and research context.
Identify one genuinely useful, non-duplicative research project that could improve the user's
understanding, executive decisions, or the LISA cognitive runtime. This is an unattended,
local-only, non-destructive curiosity cycle.

Return JSON only:
{{
  "research_worthy": true,
  "question": "one bounded research question",
  "rationale": "why this follows from the context",
  "expected_value": "what useful decision or understanding it could support",
  "risk_level": "low",
  "requires_external_action": false
}}

Rules:
- Use only the supplied context.
- Do not request credentials, system changes, purchases, communications, surveillance, or executable downloads.
- Do not claim to browse the web or access sources not supplied.
- Prefer a project answerable through careful local analysis of stored material.
- One project only.
- If there is insufficient context, set research_worthy to false.

CONTEXT:
{context}
""".strip()
        raw = self.node.generate(prompt, json_mode=True, temperature=0.2)
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError('Curiosity model returned a non-object JSON value.')
        return parsed

    def run_once(self) -> CuriosityResult:
        policy = self.policy()
        now = datetime.now(timezone.utc)
        if not policy.get('enabled', False):
            return CuriosityResult(status='disabled', reason='Self Curiosity is off.')
        if self._runs_today() >= int(policy['max_runs_per_day']):
            return CuriosityResult(status='daily_limit', reason='Daily curiosity-run limit reached.')

        owner = self.control.acquire_lease(
            self.CYCLE_LEASE_KEY,
            owner_id=f'curiosity-{uuid4()}',
            ttl_seconds=max(900, int(policy.get('stale_job_minutes', 20)) * 60),
            metadata={'mode': 'self_curiosity'},
        )
        if owner is None:
            return CuriosityResult(status='busy', reason='Another curiosity cycle already holds the execution lease.')

        run_id: int | None = None
        try:
            context = self._context(policy)
            if policy.get('require_existing_context', True) and len(context.strip()) < 80:
                return CuriosityResult(
                    status='insufficient_context',
                    reason='Not enough local context to form a grounded question.',
                )

            run_id = self.store.start_curiosity_run(context[-2000:])
            self.store.set_setting(self.LAST_RUN_KEY, now.isoformat())
            self.control.add_event(
                'curiosity_cycle',
                'started',
                {'idle_gated': True, 'policy': policy},
                curiosity_run_id=run_id,
            )

            proposal = self._propose(context, policy)
            question = str(proposal.get('question', '')).strip()
            rationale = str(proposal.get('rationale', '')).strip()
            if not proposal.get('research_worthy', False) or not question:
                self.store.finish_curiosity_run(run_id, status='no_question', decision=proposal)
                self.control.add_event(
                    'curiosity_cycle', 'no_question', proposal, curiosity_run_id=run_id,
                )
                return CuriosityResult(
                    status='no_question',
                    reason='No sufficiently valuable grounded question was identified.',
                )
            if proposal.get('requires_external_action', False):
                self.store.finish_curiosity_run(
                    run_id, status='blocked', proposed_question=question, decision=proposal,
                )
                self.control.add_event(
                    'curiosity_cycle', 'blocked', proposal, curiosity_run_id=run_id,
                )
                return CuriosityResult(
                    status='blocked', question=question,
                    reason='Proposal required an external action.',
                )
            safe, reason = self._safe_question(question, policy)
            if not safe:
                self.store.finish_curiosity_run(
                    run_id, status='blocked', proposed_question=question,
                    decision=proposal, error=reason,
                )
                self.control.add_event(
                    'curiosity_cycle', 'blocked', {'reason': reason, 'proposal': proposal},
                    curiosity_run_id=run_id,
                )
                return CuriosityResult(status='blocked', question=question, reason=reason)
            if self._duplicate(question):
                self.store.finish_curiosity_run(
                    run_id, status='duplicate', proposed_question=question, decision=proposal,
                )
                self.control.add_event(
                    'curiosity_cycle', 'duplicate', proposal, curiosity_run_id=run_id,
                )
                return CuriosityResult(
                    status='duplicate', question=question,
                    reason='An equivalent research question already exists.',
                )

            orchestrator = CognitiveOrchestrator(repository=self.repo, node=self.node)
            job_id = orchestrator.queue_research(
                question, rationale=f'Self Curiosity: {rationale}',
            )
            self.store.add_thought(
                'curiosity_proposal',
                f'Self Curiosity proposed research job {job_id}',
                f'Question: {question}\n\nRationale: {rationale}',
                research_job_id=job_id,
                metadata={
                    'proposal': proposal, 'policy': policy,
                    'curiosity_run_id': run_id, 'origin': 'idle_loop',
                },
            )
            self.store.save_artifact(
                'curiosity_proposal',
                f'Self Curiosity Proposal #{run_id}',
                f'# Self Curiosity Proposal\n\n## Question\n{question}\n\n'
                f'## Rationale\n{rationale}\n\n## Expected Value\n'
                f'{proposal.get("expected_value", "")}',
                research_job_id=job_id,
                metadata={'proposal': proposal, 'policy': policy, 'origin': 'idle_loop'},
            )
            self.control.add_event(
                'curiosity_project', 'queued', proposal,
                research_job_id=job_id, curiosity_run_id=run_id,
            )

            summary = ''
            status = 'queued'
            if policy.get('auto_execute_research', True):
                completed_id = orchestrator.run_one_research_cycle()
                if completed_id:
                    report = self.repo.get_report(completed_id) or {}
                    summary = str(report.get('summary', ''))
                    self.store.add_thought(
                        'curiosity_complete',
                        f'Self Curiosity completed research job {completed_id}',
                        summary,
                        research_job_id=completed_id,
                        metadata={'curiosity_run_id': run_id, 'origin': 'idle_loop'},
                    )
                    self.store.save_artifact(
                        'curiosity_report',
                        f'Self Curiosity Report #{completed_id}',
                        summary,
                        research_job_id=completed_id,
                        metadata={
                            'curiosity_run_id': run_id,
                            'proposal': proposal,
                            'origin': 'idle_loop',
                        },
                    )
                    status = 'completed'
                    self.control.add_event(
                        'curiosity_project', 'completed',
                        {'question': question, 'summary_preview': summary[:1000]},
                        research_job_id=completed_id, curiosity_run_id=run_id,
                    )

            self.store.finish_curiosity_run(
                run_id,
                status=status,
                proposed_question=question,
                research_job_id=job_id,
                decision=proposal,
            )
            return CuriosityResult(
                status=status,
                question=question,
                rationale=rationale,
                research_job_id=job_id,
                report_summary=summary,
            )
        except Exception as exc:
            if run_id is not None:
                self.store.finish_curiosity_run(run_id, status='failed', error=str(exc))
                self.control.add_event(
                    'curiosity_cycle', 'failed', {'error': str(exc)},
                    curiosity_run_id=run_id,
                )
            raise
        finally:
            self.control.release_lease(self.CYCLE_LEASE_KEY, owner)
