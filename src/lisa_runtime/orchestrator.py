"""Bounded text-first cognitive orchestration with observable progress events."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable

from .models import ResearchJob, RuntimeState
from .ollama_node import OllamaNode
from .repository import SQLiteRepository


StateListener = Callable[[RuntimeState, str], None]
ProgressListener = Callable[[dict], None]


@dataclass(slots=True)
class CognitiveOrchestrator:
    repository: SQLiteRepository
    node: OllamaNode
    state_listener: StateListener | None = None
    progress_listener: ProgressListener | None = None

    def _state(self, state: RuntimeState, detail: str = "") -> None:
        if self.state_listener:
            self.state_listener(state, detail)

    def _progress(self, **payload) -> None:
        if self.progress_listener:
            self.progress_listener(payload)

    def chat(self, message: str) -> str:
        text = message.strip()
        if not text:
            return ""
        started = time.monotonic()
        self._state(RuntimeState.UNDERSTANDING, "Processing text input")
        self._progress(kind="chat", stage="understanding", percent=20, elapsed=0.0)
        try:
            self._state(RuntimeState.RESPONDING, "Generating local response")
            self._progress(kind="chat", stage="generating", percent=55, elapsed=time.monotonic() - started)
            response = self.node.generate(
                text,
                system=(
                    "You are LISA, a local text-first cognitive assistant. "
                    "Be accurate, practical, transparent about uncertainty, "
                    "and never claim an action occurred unless it actually did."
                ),
            )
            self._progress(kind="chat", stage="complete", percent=100, elapsed=time.monotonic() - started)
            return response
        finally:
            self._state(RuntimeState.IDLE, "Ready")

    def queue_research(self, question: str, rationale: str = "User requested research") -> int:
        job = ResearchJob(question=question.strip(), rationale=rationale)
        job_id = self.repository.create_job(job)
        self._state(RuntimeState.RESEARCH_QUEUED, f"Research job {job_id} queued")
        self._progress(kind="research", job_id=job_id, stage="queued", percent=0, current_step=0, total_steps=job.max_iterations)
        return job_id

    def run_one_research_cycle(self) -> int | None:
        job = self.repository.claim_next_job()
        if job is None or job.id is None:
            self._state(RuntimeState.IDLE, "No research jobs queued")
            self._progress(kind="research", stage="idle", percent=0)
            return None

        self._state(RuntimeState.RESEARCHING, f"Running job {job.id}")
        started = time.monotonic()
        findings: list[dict] = []
        self._progress(
            kind="research",
            job_id=job.id,
            stage="planning",
            percent=5,
            current_step=0,
            total_steps=job.max_iterations,
            elapsed=0.0,
        )

        try:
            plan = self._create_plan(job)
            total_steps = max(1, min(len(plan), job.max_iterations))
            self._progress(
                kind="research",
                job_id=job.id,
                stage="plan_ready",
                percent=10,
                current_step=0,
                total_steps=total_steps,
                elapsed=time.monotonic() - started,
            )

            for iteration, action in enumerate(plan[: job.max_iterations], start=1):
                if time.monotonic() - started >= job.max_runtime_seconds:
                    findings.append({"type": "limit", "message": "Runtime limit reached"})
                    self._progress(
                        kind="research",
                        job_id=job.id,
                        stage="runtime_limit",
                        percent=min(90, 10 + int((iteration - 1) / total_steps * 75)),
                        current_step=iteration - 1,
                        total_steps=total_steps,
                        elapsed=time.monotonic() - started,
                    )
                    break

                percent = 10 + int(((iteration - 1) / total_steps) * 75)
                self._progress(
                    kind="research",
                    job_id=job.id,
                    stage=str(action.get("action", "analyze")),
                    percent=percent,
                    current_step=iteration,
                    total_steps=total_steps,
                    elapsed=time.monotonic() - started,
                    detail=str(action.get("query", "")),
                )

                result = self._execute_action(job, action)
                self.repository.save_step(job.id, iteration, action, result)
                self.repository.heartbeat(job.id)
                findings.append(result)

                self._progress(
                    kind="research",
                    job_id=job.id,
                    stage="step_complete",
                    percent=10 + int((iteration / total_steps) * 75),
                    current_step=iteration,
                    total_steps=total_steps,
                    elapsed=time.monotonic() - started,
                )

                if result.get("complete") is True:
                    break

            self._state(RuntimeState.CONSOLIDATING, f"Consolidating job {job.id}")
            self._progress(
                kind="research",
                job_id=job.id,
                stage="consolidating",
                percent=90,
                current_step=len(findings),
                total_steps=total_steps,
                elapsed=time.monotonic() - started,
            )
            summary = self._synthesize(job, findings)
            self.repository.complete_job(
                job.id,
                summary=summary,
                findings=findings,
                limitations=(
                    "Initial runtime uses bounded local-model analysis only. "
                    "External evidence tools are not yet connected."
                ),
            )
            self._progress(
                kind="research",
                job_id=job.id,
                stage="complete",
                percent=100,
                current_step=total_steps,
                total_steps=total_steps,
                elapsed=time.monotonic() - started,
            )
            return job.id
        except Exception as exc:
            retry = job.attempts < job.max_retries
            self.repository.fail_job(job.id, str(exc), retry=retry)
            self._state(RuntimeState.ERROR, f"Job {job.id} failed: {exc}")
            self._progress(
                kind="research",
                job_id=job.id,
                stage="failed",
                percent=0,
                elapsed=time.monotonic() - started,
                error=str(exc),
            )
            raise
        finally:
            self._state(RuntimeState.IDLE, "Ready")

    def _create_plan(self, job: ResearchJob) -> list[dict]:
        prompt = f"""
Create a bounded research plan for this question:
{job.question}

Return JSON only in this form:
{{"steps":[{{"action":"analyze","query":"..."}}]}}

Rules:
- Maximum {job.max_iterations} steps.
- Each step must be independently executable.
- Do not claim to browse the web.
- Use only local-model analysis in this initial release.
- The final step should request a synthesis.
""".strip()
        raw = self.node.generate(prompt, json_mode=True, temperature=0.1)
        parsed = json.loads(raw)
        steps = parsed.get("steps", [])
        if not isinstance(steps, list) or not steps:
            return [{"action": "analyze", "query": job.question}]
        return [step for step in steps if isinstance(step, dict)]

    def _execute_action(self, job: ResearchJob, action: dict) -> dict:
        query = str(action.get("query") or action.get("action") or job.question)
        response = self.node.generate(
            f"Research question: {job.question}\nCurrent bounded step: {query}",
            system=(
                "Perform careful local reasoning. Separate facts, inferences, "
                "assumptions, and missing evidence. Do not fabricate sources."
            ),
            temperature=0.2,
        )
        return {
            "action": action,
            "output": response,
            "complete": action.get("action") == "synthesize",
        }

    def _synthesize(self, job: ResearchJob, findings: list[dict]) -> str:
        prompt = (
            f"Question: {job.question}\n\n"
            f"Bounded findings:\n{json.dumps(findings, ensure_ascii=False)}\n\n"
            "Produce a concise report. Clearly label verified information, "
            "inference, missing evidence, and recommended next research."
        )
        return self.node.generate(prompt, temperature=0.1)
